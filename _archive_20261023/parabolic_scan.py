# -*- coding: utf-8 -*-
"""
==============================================================================
parabolic_scan.py —— 日线 DIF「下抛 → 上抛」抛物线底 · 扫描
==============================================================================
只看日线单周期（不涉及周/月共振），逐只股票检测 MACD(30,89,9) 的 DIF 曲线：
先走一段「向下抛物线」（下抛 = 挖坑）→ 谷底 → 再走一段「向上抛物线」
（上抛 = 起爆），整体呈开口向上的 ∪ 形弧。**不要求 dif 破零轴**。

对 dif 的每个局部谷拟合二次多项式 y = a·x² + b·x + c：
    a    > 0   开口向上（∪，先下抛后上抛）
    r2          拟合优度（越接近 1 越像干净的抛物线弧）
    down        下抛幅度（谷前 dif 下降多少）
    up          上抛幅度（谷后 dif 上升多少）
    up_ratio    up / down（起爆强度；越大上抛相对越有力）

输出字段：
    stock_code, stock_name, pit(谷底日), dif_pit, dea_pit,
    down, up, up_ratio, a, r2, close_pit

用法::

    python parabolic_scan.py                       # 默认沪深300
    python parabolic_scan.py --universe coal       # 动力煤板块
    python parabolic_scan.py --universe all        # 全市场
    python parabolic_scan.py --start 2025-01-01    # 只看谷底 >= 该日
    python parabolic_scan.py --sort a              # 按曲率降序（弧最陡的排前）
    python parabolic_scan.py --min-r2 0.6          # 更严格的抛物线划线

默认输出 output/parabolic_scan.xlsx（工作表「形态明细」= 全部命中的抛物线底，
「每股最近」= 每只股票最近一个）；--out 指定 .csv 后缀则输出 CSV。

数据源：backward_tdx_daily（后复权），见 mlr/settings.py TABLE_DAILY。
"""
import argparse
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mlr import data as D, settings as S, universe as U
from mlr.macd import macd as calc_macd
from mlr import morph


def _write_excel(path, sheets):
    """把多个 DataFrame 写入 Excel（每表一个 sheet），复用 report 的表头美化。"""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with pd.ExcelWriter(path, engine='openpyxl') as xw:
        for name, df in sheets.items():
            df.to_excel(xw, sheet_name=name[:31], index=False)
    try:
        from mlr.report import _style_openpyxl
        _style_openpyxl(path)
    except Exception:                                # noqa: BLE001
        pass
    return path


def main(argv=None):
    ap = argparse.ArgumentParser(prog='parabolic_scan',
                                 description='日线 DIF「下抛→上抛」抛物线底扫描')
    ap.add_argument('--universe', default=None,
                    help='股票池 hs300 / coal / all（默认取配置 universe.default）')
    ap.add_argument('--start', default=None, help='只看谷底日 >= 该日')
    ap.add_argument('--end', default=None, help='只看谷底日 <= 该日')
    ap.add_argument('--win', type=int, default=12,
                    help='抛物线拟合窗口（谷前/谷后各取 win 根，默认12）')
    ap.add_argument('--min-r2', type=float, default=0.5,
                    help='最小拟合优度 R²（默认0.5）')
    ap.add_argument('--min-down', type=float, default=0.0,
                    help='最小下抛幅度（默认0=不约束尺度）')
    ap.add_argument('--min-up', type=float, default=0.0,
                    help='最小上抛幅度（默认0=不约束尺度）')
    ap.add_argument('--min-up-ratio', type=float, default=0.0,
                    help='上抛/下抛 最低比例（默认0=不约束）')
    ap.add_argument('--min-seg-r2', type=float, default=0.0,
                    help='下抛段/上抛段各自独立拟合的最小 R²（默认0=不约束）')
    ap.add_argument('--min-up-a', type=float, default=None,
                    help='上抛段独立拟合的曲率下限（如 0=要求凹向上）')
    ap.add_argument('--sort', default='r2',
                    choices=['r2', 'a', 'date', 'up_ratio'],
                    help='排序键（默认 r2 降序；date 升序）')
    ap.add_argument('--latest', action='store_true',
                    help='每只股票只保留最近一个抛物线底（当前形态）')
    ap.add_argument('--refresh', action='store_true')
    ap.add_argument('--limit', type=int, default=None, help='只处理前 N 只（冒烟）')
    ap.add_argument('--show', type=int, default=40, help='控制台打印前 N 条')
    ap.add_argument('--out', default=None,
                    help='输出路径（默认 output/parabolic_scan.xlsx；'
                         '给 .csv 后缀则输出 CSV）')
    args = ap.parse_args(argv)

    cfg = S.load_config()
    print('=' * 78)
    print('  日线 DIF「下抛 → 上抛」抛物线底 · 扫描（单周期，不要求破零轴）')
    print('=' * 78)
    print(f'  数据表: {S.TABLE_DAILY}   MACD={tuple(cfg["macd"].values())}')

    universe = args.universe or cfg.get('universe', {}).get('default', 'hs300')
    codes = None
    if universe == 'hs300':
        codes, upd = U.get_hs300_codes()
        print(f'  股票池: 沪深300成分股 {len(codes)} 只 (update_date={upd})')
    elif universe == 'coal':
        codes, upd = U.get_coal_codes()
        print(f'  股票池: 动力煤板块 {len(codes)} 只 (update_date={upd})')
    elif universe == 'all':
        print('  股票池: 全市场')
    else:
        raise SystemExit(f'未知 --universe: {universe}（可选 hs300/coal/all）')

    daily = D.load_daily(codes=codes, refresh=args.refresh)
    if args.limit:
        keep = sorted(daily['stock_code'].unique())[:args.limit]
        daily = daily[daily['stock_code'].isin(set(keep))]
        print(f'[限制] 只处理前 {len(keep)} 只')
    print(f'[载入] {len(daily):,} 行 / {daily["stock_code"].nunique()} 只')

    print(f'[检测] DIF 抛物线底  win={args.win}  min_r2={args.min_r2}  '
          f'min_down={args.min_down}  min_up={args.min_up}  '
          f'min_up_ratio={args.min_up_ratio} ...', flush=True)
    macd_p = (cfg['macd']['fast'], cfg['macd']['slow'], cfg['macd']['signal'])
    t0 = pd.Timestamp(args.start) if args.start else None
    t1 = pd.Timestamp(args.end) if args.end else None

    rows = []
    for code, g in daily.groupby('stock_code', sort=False):
        g = g.sort_values('trade_date')
        name = g['stock_name'].iloc[-1]
        close = g['close_price'].to_numpy('float64')
        dates = g['trade_date'].to_numpy()
        dif, dea, hist = calc_macd(close, *macd_p)
        bs = morph.parabolic_bottoms(
            dif, dea, win=args.win, min_r2=args.min_r2,
            min_down=args.min_down, min_up=args.min_up,
            min_up_ratio=args.min_up_ratio,
            min_seg_r2=args.min_seg_r2, min_up_a=args.min_up_a)
        for b in bs:
            pit_d = pd.Timestamp(dates[b['pit_idx']])
            if t0 is not None and pit_d < t0:
                continue
            if t1 is not None and pit_d > t1:
                continue
            rows.append({
                'stock_code': code, 'stock_name': name,
                'pit': pit_d.date(),
                'dif_pit': b['dif_pit'], 'dea_pit': b['dea_pit'],
                'down': b['down'], 'up': b['up'],
                'up_ratio': b['up_ratio'],
                'a': b['a'], 'r2': b['r2'],
                'down_a': b['down_a'], 'down_r2': b['down_r2'],
                'up_a': b['up_a'], 'up_r2': b['up_r2'],
                'lag_bars': len(g) - 1 - b['pit_idx'],
                'close_pit': round(float(close[b['pit_idx']]), 2),
            })

    df = pd.DataFrame(rows)
    if args.latest and len(df):
        df = (df.sort_values(['stock_code', 'pit'])
                .groupby('stock_code', as_index=False).tail(1)
                .reset_index(drop=True))
    if len(df):
        if args.sort == 'date':
            df = df.sort_values(['pit', 'stock_code']).reset_index(drop=True)
        else:
            df = df.sort_values([args.sort, 'stock_code'],
                                ascending=[False, True]).reset_index(drop=True)
    n_hit = df['stock_code'].nunique() if len(df) else 0
    print(f'\n[结果] 共 {len(df)} 个抛物线底 / 覆盖 {n_hit} 只股票')
    if len(df):
        print('\n' + df.head(args.show).to_string(index=False))
        if len(df) > args.show:
            print(f'  ... 其余 {len(df) - args.show} 条见输出文件')

    S.ensure_dirs()
    out = args.out or os.path.join(S.OUTPUT_DIR, 'parabolic_scan.xlsx')
    if not len(df):
        print('\n（无结果）')
        return 0
    if out.lower().endswith(('.xlsx', '.xls')):
        latest = (df.sort_values(['stock_code', 'pit'])
                    .groupby('stock_code', as_index=False).tail(1)
                    .sort_values('r2', ascending=False).reset_index(drop=True))
        _write_excel(out, {'形态明细': df, '每股最近': latest})
    else:
        df.to_csv(out, index=False, encoding='utf-8-sig')
    print(f'\n[OK] 已保存: {out}   （{len(df)} 个形态 / '
          f'{df["stock_code"].nunique()} 只）')
    return 0


if __name__ == '__main__':
    sys.exit(main())
