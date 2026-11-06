# -*- coding: utf-8 -*-
"""
==============================================================================
reproduce_600188 —— 复现参考形态：600188 日线 2025-11-11 ~ 2026-03-13
==============================================================================
用法：
    python reproduce_600188.py                # 打印三周期「上抛」区间 + 共振日期
    python reproduce_600188.py --chart         # 额外输出 MACD 形态图
    python reproduce_600188.py --code 600188 --start 2024-01-01
"""
import argparse
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mlr import data as D, settings as S, resonance as R


def ranges(dates, flags):
    out, i, n = [], 0, len(flags)
    while i < n:
        if flags[i]:
            j = i
            while j + 1 < n and flags[j + 1]:
                j += 1
            out.append((pd.Timestamp(dates[i]).date(), pd.Timestamp(dates[j]).date()))
            i = j + 1
        else:
            i += 1
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(prog='reproduce_600188')
    ap.add_argument('--code', default='600188')
    ap.add_argument('--start', default='2024-01-01', help='展示起始日')
    ap.add_argument('--chart', action='store_true')
    args = ap.parse_args(argv)

    cfg = S.load_config()
    daily = D.load_daily(codes=[args.code])
    if daily.empty:
        print(f'无 {args.code} 数据'); return 1
    tfs = D.build_timeframes(daily, cfg)

    states = {}
    for tf in ('daily', 'weekly', 'monthly'):
        det = R.detect_frame(tfs[tf], tf, cfg)
        states[tf] = det

    print('=' * 78)
    print(f'  {args.code} 多周期「上抛」(挖坑→起爆) 判定   MACD={tuple(cfg["macd"].values())}')
    print('=' * 78)
    for tf in ('daily', 'weekly', 'monthly'):
        d = states[tf].sort_values('trade_date')
        rs = [r for r in ranges(d['trade_date'].to_numpy(), d['upthrow'].to_numpy())
              if r[1] >= pd.Timestamp(args.start).date()]
        print(f'\n[{tf.upper()}] 上抛区间（{args.start} 起，共 {len(rs)} 段）:')
        for a, b in rs:
            print(f'    {a} ~ {b}')

    res = R.build_resonance(tfs, cfg)
    rs = [r for r in ranges(res['trade_date'].to_numpy(), res['resonance'].to_numpy())
          if r[1] >= pd.Timestamp(args.start).date()]
    print(f'\n[共振] 日∧周∧月 同时上抛区间（{args.start} 起，共 {len(rs)} 段）:')
    for a, b in rs:
        print(f'    {a} ~ {b}   ← 共振首日 {a}')

    print('\n[参考窗口核对] 600188 日线 2025-11-11 ~ 2026-03-13 的 MACD(30,89,9) 关键节点：')
    sub = states['daily']
    sub = sub[(sub['trade_date'] >= '2025-11-01') & (sub['trade_date'] <= '2026-03-20')]
    for _, r in sub.iterrows():
        mark = ''
        if r['cross'] == 1:
            mark = '  <== 金叉'
        elif r['cross'] == -1:
            mark = '  <== 死叉'
        print(f"    {r['trade_date'].date()}  close={r['close_price']:7.2f}  "
              f"dif={r['dif']:7.3f}  dea={r['dea']:7.3f}  hist={r['hist']:7.3f}{mark}")

    if args.chart:
        _chart(args.code, states, res)

    S.ensure_dirs()
    out = os.path.join(S.OUTPUT_DIR, f'reproduce_{args.code}.csv')
    states['daily'][['trade_date', 'close_price', 'dif', 'dea', 'hist',
                     'ma_short', 'ma_long', 'cross', 'upthrow']].to_csv(
        out, index=False, encoding='utf-8-sig')
    print(f'\n✅ 日线指标已保存: {out}')
    return 0


def _chart(code, states, res):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    for f in ('Microsoft YaHei', 'SimHei'):
        try:
            font_manager.findfont(f, fallback_to_default=False)
            matplotlib.rcParams['font.sans-serif'] = [f]
            break
        except Exception:                           # noqa: BLE001
            continue
    matplotlib.rcParams['axes.unicode_minus'] = False

    fig, axes = plt.subplots(4, 1, figsize=(16, 13), sharex=False)
    for ax, tf in zip(axes[:3], ('daily', 'weekly', 'monthly')):
        d = states[tf].sort_values('trade_date')
        x = d['trade_date']
        ax.plot(x, d['close_price'], color='#333', lw=1.1, label='close')
        ax.plot(x, d['ma_short'], color='#e67e22', lw=0.9, label='MA7')
        ax.plot(x, d['ma_long'], color='#2980b9', lw=0.9, label='MA21')
        for a, b in ranges(x.to_numpy(), d['upthrow'].to_numpy()):
            ax.axvspan(pd.Timestamp(a), pd.Timestamp(b), color='#2ecc71', alpha=0.18)
        ax.set_title(f'{code} {tf}  绿色阴影=上抛中', fontsize=10)
        ax.legend(loc='upper left', fontsize=7)
    d = states['daily'].sort_values('trade_date')
    ax = axes[3]
    ax.bar(d['trade_date'], d['hist'], color=['#e74c3c' if v > 0 else '#27ae60'
                                              for v in d['hist'].fillna(0)], width=1.0)
    ax.plot(d['trade_date'], d['dif'], color='#e67e22', lw=1.0, label='DIF')
    ax.plot(d['trade_date'], d['dea'], color='#2980b9', lw=1.0, label='DEA')
    rr = res[res['resonance']]
    ax.scatter(rr['trade_date'], rr['dif'], color='red', s=18, zorder=5,
               label='共振')
    ax.set_title('日线 MACD(30,89,9) 红绿柱 + 共振点', fontsize=10)
    ax.legend(loc='upper left', fontsize=7)

    S.ensure_dirs()
    p = os.path.join(S.CHART_DIR, f'reproduce_{code}.png')
    fig.tight_layout()
    fig.savefig(p, dpi=110)
    print(f'✅ 形态图已保存: {p}')


if __name__ == '__main__':
    sys.exit(main())
