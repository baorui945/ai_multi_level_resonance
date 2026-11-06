# -*- coding: utf-8 -*-
"""导出「窗口终点 = 金叉日 + 抛物线形状相似度 ≥ 阈值」的个股清单。

模板固定为某只股票的一段 DIF/DEA 走势（默认 601898 的 2025-11-11~2026-02-06，
共 62 根），对全市场逐个长度为 L 的窗口计算

    score = (corr(z(DIF_窗口), z(DIF_模板)) + corr(z(DEA_窗口), z(DEA_模板))) / 2

相关前各自 z-score，只看形状、不看 DIF/DEA 的绝对高低。只保留**窗口最后一根
正好是金叉日**且 score ≥ 阈值的窗口，即「向下抛物线走到拐头（金叉）那天」与
模板最像的股票。

注意：模板末根（2026-02-06）是金叉（2026-02-05）后一日，而窗口末根限定为金叉日，
两者错开一根，因此模板股自身的得分会略低于 1（约 0.999），属正常口径差异。

用法::

    python export_shape_list.py
    python export_shape_list.py --threshold 0.99
    python export_shape_list.py --from 2026-01-01 --to 2026-12-31 --out output\\shape_list.xlsx
"""
import argparse
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mlr import data as D                                        # noqa: E402
from mlr.macd import macd as calc_macd, cross_flags              # noqa: E402
from template_scan import _series, zscore, sliding_corr          # noqa: E402

COLS = ['代码', '名称', '金叉日(=窗口终点)', '窗口起点', '窗口score',
        'corr_DIF', 'corr_DEA', 'DIF', 'DEA', '柱(DIF-DEA)', '收盘价',
        '窗口涨跌幅%']


def _week_index(dt, rule='W-FRI'):
    """把每根日线映射到周序号（0..K-1，按时间升序）。"""
    per = pd.DatetimeIndex(dt).to_period(rule)
    codes, _ = pd.factorize(per, sort=True)
    return codes


def _week_last_close(c, codes):
    """每个完整周的最后收盘（用于构造截至某日的历史周线序列）。"""
    nw = int(codes.max()) + 1 if codes.size else 0
    last = np.full(nw, np.nan)
    for j in range(codes.size):
        last[codes[j]] = c[j]
    return last


def _wk_vals_at(c, codes, wk_last, i, wk_mp, need=2):
    """截至日线索引 i（含）的周线 MACD 最后 need 根数值。

    当前周的收盘用 c[i]（金叉日当天），前面各周用完整周收盘，
    因此不含未来数据。数据不足时返回 None。
    """
    k = int(codes[i])
    if k < need - 1:
        return None
    closes = np.concatenate([wk_last[:k], [c[i]]])
    if closes.size < wk_mp[1] + wk_mp[2]:
        return None
    dif, dea, hist = calc_macd(closes, *wk_mp)
    return {'dif_cur': dif[-1], 'dif_prev': dif[-2],
            'dea_cur': dea[-1], 'dea_prev': dea[-2],
            'hist_cur': hist[-1], 'hist_prev': hist[-2]}


def _wk_pass(wv, ref, weeks='both'):
    """周线 MACD 过滤。

    ref='macd'：排除「周线柱<0 且（周线DIF>0 或 周线DEA>0）」的高位死叉，
                即周线动能转弱但仍在零轴上方；周线在零轴下方震荡（DEA<=0）
                不排除。
    其余 ref（dif/dea/hist/both）：前一周与当前周的该值都需 >=0。
    weeks='both' 判定前一周+当前周，'cur' 只看当前周。
    NaN 视为不通过。
    """
    if wv is None:
        return False
    labels = ('cur', 'prev') if weeks == 'both' else ('cur',)
    if ref == 'macd':
        for s in labels:
            if wv[f'hist_{s}'] < 0 and (wv[f'dif_{s}'] > 0 or wv[f'dea_{s}'] > 0):
                return False
        return True
    pairs = {'dif': ('dif_cur', 'dif_prev'),
             'dea': ('dea_cur', 'dea_prev'),
             'hist': ('hist_cur', 'hist_prev'),
             'both': ('dif_cur', 'dif_prev', 'dea_cur', 'dea_prev')}[ref]
    keys = [k for k in pairs if k.endswith(labels[0])
            or (weeks == 'both' and k.endswith('prev'))]
    return all(wv[k] >= 0 for k in keys)


def _ladder(all_ev, thresholds):
    """按阈值给「事件数 / 涉及个股数」阶梯（事件 = 股票+金叉日）。"""
    rows = []
    for t in thresholds:
        d = all_ev[all_ev['窗口score'] >= t]
        rows.append({'score阈值': t, '相似事件数': len(d),
                     '涉及个股数': d['代码'].nunique()})
    return pd.DataFrame(rows)


def _fwd_stats(v, col, label):
    """未来 N 日涨跌幅的分布统计（NaN 剔除）。"""
    v = pd.Series(v).dropna()
    if v.empty:
        return pd.DataFrame([{'分组': label, '有效样本': 0}])
    return pd.DataFrame([{
        '分组': label, '有效样本': len(v),
        '均值%': round(v.mean(), 2), '中位%': round(v.median(), 2),
        '胜率(>0)%': round((v > 0).mean() * 100, 1),
        '25分位%': round(v.quantile(.25), 2),
        '75分位%': round(v.quantile(.75), 2),
        '最大%': round(v.max(), 2), '最小%': round(v.min(), 2),
        '涨>10%占比': f'{(v > 10).mean() * 100:.1f}%',
        '跌<-10%占比': f'{(v < -10).mean() * 100:.1f}%',
        '列名': col,
    }])


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog='export_shape_list',
        description='导出「窗口终点=金叉日」且形状相似度≥阈值的个股清单')
    ap.add_argument('--code', default='601898', help='模板股票代码')
    ap.add_argument('--start', default='2025-11-11', help='模板起点（含）')
    ap.add_argument('--end', default='2026-02-06', help='模板终点（含）')
    ap.add_argument('--threshold', type=float, default=0.98, help='相似度阈值')
    ap.add_argument('--fwd', type=int, default=0,
                    help='统计金叉日后 N 个交易日的涨跌幅（0=不算）')
    ap.add_argument('--above-zero', action='store_true',
                    help='只保留金叉日 DIF>0 且 DEA>0 的事件（零轴上方的高位回调）')
    ap.add_argument('--weekly-filter', action='store_true',
                    help='启用周线 MACD 过滤：前一周/当前周 周线 MACD<0 则剔除')
    ap.add_argument('--wk-macd', default='30,90,9', help='周线 MACD 参数 fast,slow,signal')
    ap.add_argument('--wk-ref', default='macd',
                    choices=['dif', 'dea', 'hist', 'both', 'macd'],
                    help='周线判定：macd(默认)=排除「周线柱<0 且 DIF或DEA>0」的高位死叉；'
                         'dif/dea/hist/both=两周该值均需>=0')
    ap.add_argument('--wk-weeks', default='both', choices=['both', 'cur'],
                    help='macd 口径判定哪几周（默认前一周+当前周）')
    ap.add_argument('--wk-rule', default='W-FRI', help='周线聚合规则（与 mlr.data.resample 一致）')
    ap.add_argument('--from', dest='d0', default='2026-01-01', help='金叉日起点（含）')
    ap.add_argument('--to', dest='d1', default='2026-12-31', help='金叉日终点（含）')
    ap.add_argument('--out', default=os.path.join('output', 'shape_list_2026.xlsx'))
    args = ap.parse_args(argv)

    code = args.code.zfill(6)
    _, t_dates, t_dif, t_dea, t_name, mp = _series(code)
    td_all = pd.to_datetime(t_dates)
    m = (td_all >= pd.Timestamp(args.start)) & (td_all <= pd.Timestamp(args.end))
    if not m.any():
        raise SystemExit(f'模板区间 {args.start}~{args.end} 内没有数据')
    tpl_dif = zscore(t_dif[m])
    tpl_dea = zscore(t_dea[m])
    L = int(m.sum())
    d0, d1 = np.datetime64(args.d0), np.datetime64(args.d1)
    wk_mp = tuple(int(x) for x in args.wk_macd.split(','))
    if len(wk_mp) != 3:
        raise SystemExit('--wk-macd 需要三个整数，如 30,90,9')

    daily = D.load_daily()
    rows = []
    for sc_code, g in daily.groupby('stock_code', sort=False):
        g = g.sort_values('trade_date')
        c = g['close_price'].to_numpy('float64')
        dt = pd.to_datetime(g['trade_date']).to_numpy()
        name = g['stock_name'].iloc[-1]
        dif, dea, _ = calc_macd(c, *mp)
        if dif.size < L:
            continue
        gold = np.where(cross_flags(dif, dea) == 1)[0]
        gold = gold[(dt[gold] >= d0) & (dt[gold] <= d1) & (gold >= L - 1)]
        if not gold.size:
            continue
        cd = sliding_corr(dif, tpl_dif)
        ce = sliding_corr(dea, tpl_dea)
        score = (cd + ce) / 2.0
        fcol = f'{args.fwd}日涨跌幅%' if args.fwd > 0 else None
        if args.weekly_filter:
            wk_codes = _week_index(dt, args.wk_rule)
            wk_last = _week_last_close(c, wk_codes)
        for i in gold:
            k = i - L + 1
            row = {
                '代码': sc_code, '名称': name,
                '金叉日(=窗口终点)': pd.Timestamp(dt[i]),
                '窗口起点': pd.Timestamp(dt[k]),
                '窗口score': score[k],
                'corr_DIF': cd[k], 'corr_DEA': ce[k],
                'DIF': dif[i], 'DEA': dea[i],
                '柱(DIF-DEA)': dif[i] - dea[i],
                '收盘价': c[i],
                '窗口涨跌幅%': (c[i] / c[k] - 1) * 100,
                '零轴上方': bool(dif[i] > 0 and dea[i] > 0),
            }
            if args.weekly_filter:
                wv = _wk_vals_at(c, wk_codes, wk_last, i, wk_mp)
                row['周线DIF(前一周)'] = wv['dif_prev'] if wv else np.nan
                row['周线DIF(当前周)'] = wv['dif_cur'] if wv else np.nan
                row['周线DEA(前一周)'] = wv['dea_prev'] if wv else np.nan
                row['周线DEA(当前周)'] = wv['dea_cur'] if wv else np.nan
                row['周线柱(前一周)'] = wv['hist_prev'] if wv else np.nan
                row['周线柱(当前周)'] = wv['hist_cur'] if wv else np.nan
                row['周线通过'] = _wk_pass(wv, args.wk_ref, args.wk_weeks)
            if fcol:
                row[fcol] = (c[i + args.fwd] / c[i] - 1) * 100 \
                    if i + args.fwd < c.size else np.nan
            rows.append(row)

    all_ev = pd.DataFrame(rows)
    if all_ev.empty:
        raise SystemExit('2026 区间内没有任何金叉事件，检查 --from/--to')

    hit = all_ev[all_ev['窗口score'] >= args.threshold].copy()
    if args.above_zero:
        hit = hit[hit['零轴上方']].copy()
    n_before_wk = len(hit)
    if args.weekly_filter:
        hit = hit[hit['周线通过']].copy()
    n_wk_cut = n_before_wk - len(hit) if args.weekly_filter else 0
    hit['备注'] = np.where(hit['代码'] == code, '模板股（窗口末根=金叉日）', '')
    hit = hit.sort_values(['金叉日(=窗口终点)', '窗口score'],
                          ascending=[True, False])
    cols = list(COLS)
    if args.weekly_filter:
        cols += ['周线DIF(前一周)', '周线DIF(当前周)',
                 '周线DEA(前一周)', '周线DEA(当前周)',
                 '周线柱(前一周)', '周线柱(当前周)']
    if args.fwd > 0:
        cols.append(f'{args.fwd}日涨跌幅%')
    cols.append('备注')
    out = hit.reindex(columns=cols).reset_index(drop=True)
    out['备注'] = out['备注'].fillna('')

    base = all_ev[all_ev['零轴上方']] if args.above_zero else all_ev
    if args.weekly_filter:
        base = base[base['周线通过']]
    ladder = _ladder(base, [0.98, 0.99, 0.995, 0.998])
    self_ev = all_ev[all_ev['代码'] == code].sort_values('窗口score', ascending=False)
    if self_ev.empty:
        self_txt = '无'
    else:
        top = self_ev.iloc[0]
        self_txt = (f"{top['金叉日(=窗口终点)'].date()} score={top['窗口score']:.4f}"
                    f"（自身共 {len(self_ev)} 个金叉）")

    stat = pd.DataFrame()
    meta_extra = []
    if args.fwd > 0:
        col = f'{args.fwd}日涨跌幅%'
        stat_rows = [
            _fwd_stats(out[col], col, f'score>={args.threshold}（达标）'),
            _fwd_stats(all_ev[all_ev['零轴上方']][col], col,
                       '全市场金叉·零轴上方（基准）'),
        ]
        if args.weekly_filter:
            stat_rows.append(_fwd_stats(base[col], col,
                                        '全市场金叉·通过周线（基准）'))
        stat_rows.append(_fwd_stats(all_ev[col], col, '全市场金叉·全部（基准）'))
        stat = pd.concat(stat_rows, ignore_index=True)
        h = out[col].dropna()
        if not h.empty:
            b = base[col].dropna()
            btag = '全市场金叉·同过滤条件' if (args.above_zero or args.weekly_filter) else '全市场金叉'
            meta_extra = [
                (f'{args.fwd}日有效样本', f'{len(h)} / {len(out)}（其余金叉日距今不足 {args.fwd} 根）'),
                (f'{args.fwd}日中位/均值', f'{h.median():.2f}% / {h.mean():.2f}%'),
                (f'{args.fwd}日胜率(>0)', f'{(h > 0).mean() * 100:.1f}%'),
                (f'基准：{btag} {args.fwd}日中位/均值',
                 f'{b.median():.2f}% / {b.mean():.2f}%'),
                (f'基准：{btag} {args.fwd}日胜率', f'{(b > 0).mean() * 100:.1f}%'),
            ]

    cond = f'窗口末根=金叉日 且 score>={args.threshold}'
    if args.above_zero:
        cond += ' 且 金叉日 DIF>0、DEA>0'
    if args.weekly_filter:
        if args.wk_ref == 'macd':
            wk_txt = ('排除「周线MACD柱<0 且 周线DIF或DEA>0」（高位死叉）'
                      + ('（前一周/当前周任一出现即剔除）' if args.wk_weeks == 'both'
                         else '（只看当前周）'))
            cond += ' 且 ' + wk_txt
        else:
            cond += (f' 且 周线MACD({args.wk_macd})的 {args.wk_ref} '
                     f'前一周/当前周均>=0')
            wk_txt = (f'判定值={args.wk_ref}；前一周与当前周均需>=0')
    meta = pd.DataFrame([
        ('模板', f'{code} {t_name}'),
        ('模板区间', f'{args.start} ~ {args.end}（{L} 根）'),
        ('相似度口径', 'score=(corr_DIF+corr_DEA)/2，两条线各自 z-score 后算 Pearson'),
        ('匹配条件', cond),
        ('金叉日范围', f'{args.d0} ~ {args.d1}'),
        ('区间内全市场金叉事件', len(all_ev)),
        ('其中零轴上方（DIF>0且DEA>0）', int(all_ev['零轴上方'].sum())),
        ('周线过滤', (f'周线由日线按 {args.wk_rule} 聚合，MACD={args.wk_macd}；'
                      f'{wk_txt}；'
                      '当前周收盘取金叉日当天（不含未来数据）')
         if args.weekly_filter else '未启用'),
        ('周线过滤剔除笔数', f'{n_wk_cut} / {n_before_wk}' if args.weekly_filter else '—'),
        ('达标事件数', len(out)),
        ('达标个股数', out['代码'].nunique()),
        ('占区间金叉事件比例', f'{len(out) / len(all_ev) * 100:.1f}%'),
        ('模板股自身金叉日得分', self_txt),
        *meta_extra,
        ('提示', '同名股票多次金叉会多次出现；「窗口涨跌幅%」= 金叉日收盘 / 窗口起点收盘 -1；'
                 f'「{args.fwd}日涨跌幅%」= 金叉日后第 {args.fwd} 个交易日收盘 / 金叉日收盘 -1'
         if args.fwd > 0 else
         '同名股票多次金叉会多次出现；「窗口涨跌幅%」= 金叉日收盘 / 窗口起点收盘 -1'),
    ], columns=['项目', '值'])

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with pd.ExcelWriter(args.out, engine='openpyxl') as xw:
        out.to_excel(xw, sheet_name='金叉日相似清单', index=False)
        ladder.to_excel(xw, sheet_name='阈值阶梯', index=False)
        if not stat.empty:
            stat.to_excel(xw, sheet_name=f'{args.fwd}日收益统计', index=False)
        meta.to_excel(xw, sheet_name='口径说明', index=False)
    try:
        from mlr.report import _style_openpyxl
        _style_openpyxl(args.out)
    except Exception:                                   # noqa: BLE001
        pass
    print(f'[OK] 已保存 {args.out}：达标 {len(out)} 事件 / {out["代码"].nunique()} 只'
          f'（区间金叉事件 {len(all_ev)}，阈值 {args.threshold}）')
    if not stat.empty:
        print(stat.drop(columns=['列名']).to_string(index=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
