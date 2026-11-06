# -*- coding: utf-8 -*-
"""
==============================================================================
daily_patterns.py —— 日线「挖坑→起爆」上抛形态 · 历史区间扫描
==============================================================================
只看日线单周期（不涉及周/月共振），逐只股票检测 MACD(30,89,9) 柱体
「绿柱逐步缩小 → 转红 → 红柱放大」的完整变化过程（对应模板 G3→G4→G5→G6），
输出每个形态的完整时间线清单：

    stock_code, stock_name, pit(绿柱谷), cross(金叉转红), peak(完整红柱峰),
    shrink_bars(绿柱递减根数), shrink_pct(收缩比例), pit_hist(谷值),
    peak_hist(完整红柱峰), head_mean(前N红柱均), m1(峰/|谷|), m2(峰/前N红柱均),
    ret_pct(金叉→完整峰值涨幅), close_cross(起爆价)

用法::

    python daily_patterns.py                          # 默认沪深300
    python daily_patterns.py --start 2025-01-01       # 只看该日起爆的形态
    python daily_patterns.py --shrink-bars 5          # 绿柱至少连续缩小5根
    python daily_patterns.py --shrink-ratio 0.7       # 绿柱收缩至少70%
    python daily_patterns.py --red-grow-ratio 2       # 红柱至少放大2倍
    python daily_patterns.py --universe all           # 全市场

数据源：backward_tdx_daily（后复权），见 mlr/settings.py TABLE_DAILY。
"""
import argparse
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mlr import data as D, settings as S, universe as U
from mlr.macd import macd as calc_macd, cross_flags
from mlr import morph


def main(argv=None):
    ap = argparse.ArgumentParser(prog='daily_patterns',
                                 description='日线柱体形态「绿柱缩小→转红→红柱放大」扫描')
    ap.add_argument('--universe', default=None,
                    help='股票池 hs300 / all（默认取配置 universe.default）')
    ap.add_argument('--start', default=None, help='只看金叉日 >= 该日的形态')
    ap.add_argument('--hole-ratio', type=float, default=0.0,
                    help='绿柱坑深阈值 |hist谷|/close（0=不约束坑深）')
    ap.add_argument('--shrink-bars', type=int, default=3,
                    help='谷值后绿柱「逐步缩小」的最少递减根数（默认3）')
    ap.add_argument('--shrink-ratio', type=float, default=0.5,
                    help='谷值→金叉 绿柱累计收缩比例阈值（默认0.5）')
    ap.add_argument('--red-grow-bars', type=int, default=15,
                    help='金叉后寻找红柱见顶的窗口根数（默认15）')
    ap.add_argument('--red-grow-ratio', type=float, default=1.0,
                    help='红柱峰值/转红日红柱 最低放大倍数（默认1.0）')
    ap.add_argument('--head-n', type=int, default=5,
                    help='「峰/前N红柱均值」度量里的 N（默认5）')
    ap.add_argument('--refresh', action='store_true')
    ap.add_argument('--limit', type=int, default=None, help='只处理前 N 只（冒烟）')
    ap.add_argument('--show', type=int, default=40, help='控制台打印前 N 条')
    ap.add_argument('--out', default=None)
    args = ap.parse_args(argv)

    cfg = S.load_config()
    print('=' * 78)
    print('  日线「挖坑→起爆」上抛形态 · 历史区间扫描（单周期，无周/月共振）')
    print('=' * 78)
    print(f'  数据表: {S.TABLE_DAILY}   MACD={tuple(cfg["macd"].values())}  '
          f'MA={cfg["ma"]["short"]},{cfg["ma"]["long"]}')

    universe = args.universe or cfg.get('universe', {}).get('default', 'hs300')
    codes = None
    if universe == 'hs300':
        codes, upd = U.get_hs300_codes()
        print(f'  股票池: 沪深300成分股 {len(codes)} 只 (update_date={upd})')
    daily = D.load_daily(codes=codes, refresh=args.refresh)
    if args.limit:
        keep = sorted(daily['stock_code'].unique())[:args.limit]
        daily = daily[daily['stock_code'].isin(set(keep))]
        print(f'[限制] 只处理前 {len(keep)} 只')
    print(f'[载入] {len(daily):,} 行 / {daily["stock_code"].nunique()} 只')

    print('[检测] 日线柱体形态「绿柱缩小→转红→红柱放大」...', flush=True)
    macd_p = (cfg['macd']['fast'], cfg['macd']['slow'], cfg['macd']['signal'])

    rows = []
    for code, g in daily.groupby('stock_code', sort=False):
        g = g.sort_values('trade_date')
        name = g['stock_name'].iloc[-1]
        close = g['close_price'].to_numpy('float64')
        dates = g['trade_date'].to_numpy()
        dif, dea, hist = calc_macd(close, *macd_p)
        ms = morph.green_to_red_morphs(
            dif, dea, hist, close=close,
            hole_ratio=args.hole_ratio,
            shrink_bars=args.shrink_bars,
            shrink_ratio=args.shrink_ratio,
            red_grow_bars=args.red_grow_bars,
            red_grow_ratio=args.red_grow_ratio)
        x = cross_flags(dif, dea)
        for m in ms:
            cross_d = pd.Timestamp(dates[m['cross_idx']])
            if args.start and cross_d < pd.Timestamp(args.start):
                continue
            seg = morph.red_segment_metrics(hist, x, m['cross_idx'],
                                            head_n=args.head_n)
            pk_idx = seg['peak_idx']
            ret = (close[pk_idx] / close[m['cross_idx']] - 1) * 100.0
            pit_hist = m['pit_hist']
            peak_hist = seg['peak_hist']
            head_mean = seg['head_mean']
            m1 = peak_hist / abs(pit_hist) if abs(pit_hist) > 0 else float('nan')
            m2 = (peak_hist / head_mean
                  if (head_mean == head_mean and head_mean > 0) else float('nan'))
            rows.append({
                'stock_code': code, 'stock_name': name,
                'pit': pd.Timestamp(dates[m['pit_idx']]).date(),
                'cross': cross_d.date(),
                'peak': pd.Timestamp(dates[pk_idx]).date(),
                'shrink_bars': m['shrink_bars'],
                'shrink_pct': round(m['shrink_ratio'] * 100, 1),
                'pit_hist': pit_hist,
                'peak_hist': round(peak_hist, 4),
                'head_mean': round(head_mean, 4) if head_mean == head_mean else None,
                'm1': round(m1, 3),
                'm2': round(m2, 3),
                'ret_pct': round(float(ret), 2),
                'close_cross': round(float(close[m['cross_idx']]), 2),
            })

    df = pd.DataFrame(rows).sort_values(['cross', 'stock_code']).reset_index(drop=True)
    n_hit = df['stock_code'].nunique() if len(df) else 0
    print(f'\n[结果] 共 {len(df)} 个形态 / 覆盖 {n_hit} 只股票')
    if args.start:
        print(f'       （金叉起爆日 >= {args.start}）')
    if len(df):
        print('\n' + df.head(args.show).to_string(index=False))
        if len(df) > args.show:
            print(f'  ... 其余 {len(df) - args.show} 条见输出文件')

    S.ensure_dirs()
    out = args.out or os.path.join(S.OUTPUT_DIR, 'daily_patterns.csv')
    df.to_csv(out, index=False, encoding='utf-8-sig')
    print(f'\n✅ 已保存: {out}   （共 {len(df)} 条）')
    return 0


if __name__ == '__main__':
    sys.exit(main())
