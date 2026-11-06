# -*- coding: utf-8 -*-
"""分析达标事件金叉后第 1~N 个交易日的持有收益曲线，找出收益最高的持有天数。

用法：
    python analyze_hold_days.py
    python analyze_hold_days.py --list output/shape_gc_099_fwd10_wkmacd_2026.xlsx --maxd 20

口径：
    第 d 天收益 = 金叉日后第 d 个交易日收盘 / 金叉日收盘 - 1（按该股自身交易日计，停牌顺延）
"""
import argparse

import numpy as np
import pandas as pd

from mlr import data as D


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--list', default=r'output\shape_gc_099_fwd10_wkmacd_2026.xlsx',
                    help='事件清单（xlsx，第 1 个 sheet，需含 代码/名称/金叉日(=窗口终点)）')
    ap.add_argument('--maxd', type=int, default=20, help='最长持有交易日数')
    ap.add_argument('--out', default=None, help='输出 xlsx 路径，默认按清单名加 _hold')
    args = ap.parse_args()

    ev = pd.read_excel(args.list, sheet_name=0, dtype={'代码': str})
    ev['金叉日'] = pd.to_datetime(ev['金叉日(=窗口终点)']).dt.normalize()
    ev = ev.sort_values('金叉日').reset_index(drop=True)
    out = args.out or args.list.replace('.xlsx', '_hold.xlsx')

    px = D.load_daily(verbose=False).sort_values(['stock_code', 'trade_date'])
    md = args.maxd
    rows, peak = [], []
    for _, r in ev.iterrows():
        g = px[px.stock_code == r['代码']]
        dt = g.trade_date.values.astype('datetime64[D]')
        cl = g.close_price.values.astype(float)
        hit = np.where(dt == np.datetime64(r['金叉日'].date()))[0]
        if not len(hit):
            continue
        i = int(hit[0])
        base = cl[i]
        rec = {'代码': r['代码'], '名称': r['名称'], '金叉日': r['金叉日'],
               'score': r.get('窗口score', np.nan)}
        rets = []
        for d in range(1, md + 1):
            j = i + d
            v = cl[j] / base - 1 if j < len(cl) else np.nan
            rec[f'D{d}'] = v * 100 if np.isfinite(v) else np.nan
            rets.append(v)
        rows.append(rec)
        arr = np.array(rets, dtype=float)
        arr = np.where(np.isfinite(arr), arr, -np.inf)
        if arr.max() > -np.inf:
            k = int(arr.argmax()) + 1
            peak.append({'代码': r['代码'], '名称': r['名称'], '金叉日': r['金叉日'],
                         '峰值日': k, '峰值收益%': arr.max() * 100,
                         '第20日收益%': rec[f'D{md}']})
    res = pd.DataFrame(rows)
    res['峰值日'] = res[[f'D{d}' for d in range(1, md + 1)]].apply(
        lambda s: int(np.nanargmax(s.values)) + 1 if s.notna().any() else np.nan, axis=1)

    # 1) 按持有时长统计
    stat = []
    for d in range(1, md + 1):
        s = res[f'D{d}'].dropna()
        prev = res[f'D{d - 1}'].dropna() if d > 1 else pd.Series([0.0])
        stat.append({'持有天数': d, '有效样本': len(s), '均值%': s.mean(),
                     '中位%': s.median(), '胜率%': (s > 0).mean() * 100,
                     '25分位%': s.quantile(.25), '75分位%': s.quantile(.75),
                     '最小%': s.min(), '最大%': s.max(),
                     '跌>10%占比': (s < -10).mean() * 100})
    st = pd.DataFrame(stat).round(2)

    print('=' * 92)
    print(f'金叉后 1~{md} 个交易日持有收益（{args.list}，{len(res)} 笔）')
    print('=' * 92)
    print(st.to_string(index=False))
    best_md = st.loc[st['中位%'].idxmax()]
    best_mn = st.loc[st['均值%'].idxmax()]
    best_wr = st.loc[st['胜率%'].idxmax()]
    print(f"\n中位收益最高：第 {int(best_md['持有天数'])} 天（中位 {best_md['中位%']:+.2f}%，均值 {best_md['均值%']:+.2f}%，胜率 {best_md['胜率%']:.1f}%）")
    print(f"均值收益最高：第 {int(best_mn['持有天数'])} 天（均值 {best_mn['均值%']:+.2f}%，中位 {best_mn['中位%']:+.2f}%）")
    print(f"胜率最高：   第 {int(best_wr['持有天数'])} 天（胜率 {best_wr['胜率%']:.1f}%）")

    # 1b) 累计触及 +5%/+10% 概率 与 持有期最大回撤
    cols = [f'D{d}' for d in range(1, md + 1)]
    arr = res[cols].values
    touch = []
    for k in range(arr.shape[1]):
        seg = arr[:, :k + 1]
        run_max = np.nanmax(np.where(np.isnan(seg), -np.inf, seg), axis=1)
        run_min = np.nanmin(np.where(np.isnan(seg), np.inf, seg), axis=1)
        ok = np.isfinite(run_max)
        touch.append({'持有天数': k + 1,
                      '触及+5%概率': (run_max[ok] >= 5).mean() * 100,
                      '触及+10%概率': (run_max[ok] >= 10).mean() * 100,
                      '中位最大浮盈%': np.median(run_max[ok]),
                      '中位最大回撤%': np.median(run_min[ok]),
                      '回撤<-10%概率': (run_min[ok] < -10).mean() * 100})
    tc = pd.DataFrame(touch).round(2)
    print('\n持有期内「触及 +5%/+10%」概率与最大回撤：')
    print(tc.to_string(index=False))

    # 1c) 逐笔比较：更长持有是否真的更好
    print('\n逐笔比较（Dx - D10 的差值）：')
    for d in (3, 5, 7, 13, 16, 20):
        if d == 10:
            continue
        diff = (res[f'D{d}'] - res['D10']).dropna()
        print(f'  D{d:>2} vs D10: 更好 {int((diff > 0).sum()):>2}/{len(diff)} 笔，'
              f'差值中位 {diff.median():+.2f}%，差值均值 {diff.mean():+.2f}%')

    # 2) 单日边际（第 d 天相对第 d-1 天的涨跌）
    print('\n单日边际收益（第 d 天收盘 / 第 d-1 天收盘 - 1，第 1 天为金叉日→次日）：')
    marg = []
    for d in range(1, md + 1):
        s = (res[f'D{d}'] - res[f'D{d - 1}']) if d > 1 else res['D1']
        s = s.dropna()
        marg.append({'第几天': d, '均值%': s.mean(), '中位%': s.median(),
                     '胜率%': (s > 0).mean() * 100})
    mg = pd.DataFrame(marg).round(2)
    print(mg.to_string(index=False))

    # 3) 单笔峰值日分布
    pk = pd.DataFrame(peak)
    print('\n单笔最大浮盈出现日（1~%d 日内）分布：' % md)
    print(pk.groupby('峰值日').agg(笔数=('代码', 'size'),
                                   中位峰值收益=('峰值收益%', 'median')).round(2).to_string())
    print(f"峰值日中位：第 {pk['峰值日'].median():.0f} 天；"
          f"峰值收益中位 {pk['峰值收益%'].median():+.2f}%，"
          f"而第 {md} 日收益中位 {pk[f'第{md}日收益%'].median():+.2f}%")

    res.round(3).to_csv(out.replace('.xlsx', '.csv'), index=False, encoding='utf-8-sig')
    with pd.ExcelWriter(out) as w:
        st.to_excel(w, sheet_name='持有天数统计', index=False)
        tc.to_excel(w, sheet_name='触及概率与回撤', index=False)
        mg.to_excel(w, sheet_name='单日边际', index=False)
        pk.round(2).to_excel(w, sheet_name='单笔峰值日', index=False)
        res.round(3).to_excel(w, sheet_name='逐笔逐日收益', index=False)
    print('\n[OK] 已保存', out)


if __name__ == '__main__':
    main()
