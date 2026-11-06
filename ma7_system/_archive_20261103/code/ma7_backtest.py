# -*- coding: utf-8 -*-
"""
==============================================================================
ma7_backtest.py —— 独立日线 MA7「上抛」历史回测
==============================================================================
与 backtest.py（日/周/月 MACD 共振）完全独立：信号来自 ma7daily 的
`entry_flag`，撮合/净值/成本直接复用 ABM（经 mlr.backtest 只读调用），
参数来自 config/ma7_daily.json，不修改任何既有逻辑。

用法::

    python ma7_backtest.py                       # 沪深300 · H20/30/40/60
    python ma7_backtest.py --horizons 40
    python ma7_backtest.py --universe coal
    python ma7_backtest.py --codes 600985 --start 2020-01-01
    python ma7_backtest.py --limit 300 --no-chart
"""
import argparse
import os
import sys
import time

import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _ROOT)
sys.path.insert(0, _HERE)

from ma7daily import settings as S, signal as SG, report as RPT
from mlr import data as D, universe as U, backtest as BT, report as RP


def build_parser():
    ap = argparse.ArgumentParser(prog='ma7_backtest',
                                 description='日线 MA7 上抛回测')
    ap.add_argument('--refresh', action='store_true',
                    help='强制重拉日线缓存（MLR 缓存和 ABM 价格缓存）')
    ap.add_argument('--horizons', default=None, help='逗号分隔，如 20,40,60')
    ap.add_argument('--exit-mode', dest='exit_mode', default=None,
                    choices=['state', 'fixed'],
                    help='state=收盘跌破MA7连续N日退出（默认，与扫描状态一致）；'
                         'fixed=固定持有期（旧口径）')
    ap.add_argument('--exec', dest='exec_mode', default=None,
                    help='close_close / open_open')
    ap.add_argument('--cost', type=float, default=None,
                    help='双边合计成本，如 0.002')
    ap.add_argument('--start', default=None, help='信号/面板起始日')
    ap.add_argument('--limit', type=int, default=None, help='只跑前 N 只（冒烟）')
    ap.add_argument('--codes', default=None, help='逗号分隔指定股票')
    ap.add_argument('--universe', default=None,
                    help='股票池 hs300 / coal / all（默认 hs300）')
    ap.add_argument('--preset', default=None,
                    help='叠加 config 中的预设口径（如 strict=降噪）')
    ap.add_argument('--out', default=None, help='Excel 输出路径')
    ap.add_argument('--no-chart', action='store_true')
    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        cfg = S.load_config(preset=args.preset)
    except ValueError as e:
        raise SystemExit(str(e))
    bt_cfg = cfg['backtest']
    if args.exec_mode:
        bt_cfg['exec_mode'] = args.exec_mode
    if args.cost is not None:
        bt_cfg['cost'] = args.cost
    if args.start:
        bt_cfg['start'] = args.start
    # 退出口径：state=事件驱动（收盘跌破 MA7 连续 N 日），fixed=固定持有期
    exit_mode = (args.exit_mode or str(bt_cfg.get('exit_mode', 'fixed'))).lower()
    state_exit = dict(bt_cfg.get('state_exit') or {})
    if exit_mode == 'state':
        bt_cfg['exit'] = state_exit or {
            'rule': 'breakdown', 'breakdown_ma': int(cfg['ma7'].get('window', 7)),
            'breakdown_eps': 0.0,
            'breakdown_days': int(cfg['state'].get('exit_below_days', 2))}
        default_h = bt_cfg.get('state_horizons') or [60, 120, 250]
        if bt_cfg.get('main_horizon') not in list(default_h):
            bt_cfg['main_horizon'] = list(default_h)[0]
    else:
        bt_cfg['exit'] = None
        default_h = bt_cfg['horizons']
    horizons = ([int(x) for x in args.horizons.split(',')]
                if args.horizons else list(default_h))
    m = cfg['ma7']
    st = cfg['state']

    for w in S.validate_config(cfg):
        print(f'[参数] {w}')
    print('=' * 78)
    print('  日线 MA7「上抛」—— 独立策略回测'
          + (f"    [preset={cfg['preset']}]" if cfg.get('preset') else ''))
    print('=' * 78)
    print(f"  数据源     : {S.ABM_ROOT}  (表 {S.TABLE_DAILY})")
    print(f"  判定规则   : close>MA{m['window']} & MA{m['window']}首根上拐"
          f" & 更高低点"
          + (f" & 突破 {m['fresh_cross_days']} 日内"
             f"({m.get('fresh_mode', 'since_turn')})"
             if m.get('fresh_cross_days') else ''))
    print("  信号起点   : MA7 上拐且状态首次满足日；下一交易日 MA7 继续上行确认"
          if m.get('confirm_next_up', True) else "  信号起点   : 首根上拐日")
    print(f"  回测入场   : 确认日（避免未来函数，起点日见明细 start_date）")
    if exit_mode == 'state':
        _se = bt_cfg['exit']
        print(f"  退出规则   : {'state'} —— 收盘<MA{_se.get('breakdown_ma', 7)} 连续 "
              f"{_se.get('breakdown_days', 2)} 日离场（eps="
              f"{float(_se.get('breakdown_eps', 0.0)) * 100:.2f}%），"
              f"最长持有上限 {horizons}")
        print(f"  扫描退出   : 连续 {st['exit_below_days']} 日收盘<MA7 或 "
              f"连续 {st['exit_slope_days']} 日斜率<"
              f"{float(st.get('exit_slope_threshold', 0.0)) * 100:.2f}%/日"
              f"（斜率退出未在回测中建模，扫描展示用）")
    else:
        print(f"  退出规则   : fixed —— 固定持有期 {horizons}"
              f"（旧口径，仅用于对照）")
        print(f"  扫描退出   : 连续 {st['exit_below_days']} 日收盘<MA7 或 "
              f"连续 {st['exit_slope_days']} 日斜率<"
              f"{float(st.get('exit_slope_threshold', 0.0)) * 100:.2f}%/日"
              f"（回测未使用）")
    print(f"  执行口径   : {bt_cfg['exec_mode']}   成本 {bt_cfg['cost'] * 100:.2f}%"
          f"   滑点 {bt_cfg.get('slippage_bp', 0):.0f}bp"
          f"   持有期 {horizons}"
          f"   主口径 H{bt_cfg.get('main_horizon', horizons[0])}")
    print('=' * 78)

    t0 = time.perf_counter()
    codes = args.codes.split(',') if args.codes else None
    if codes is None:
        universe = args.universe or 'hs300'
        if universe == 'hs300':
            codes, upd = U.get_hs300_codes()
            print(f'[股票池] 沪深300成分股 {len(codes)} 只 (update_date={upd})')
        elif universe == 'coal':
            codes, upd = U.get_coal_codes()
            print(f'[股票池] 动力煤板块 {len(codes)} 只 (update_date={upd})')
        elif universe != 'all':
            raise SystemExit(f'未知 --universe {universe!r}（可选 hs300 / coal / all）')

    if not args.refresh:
        D.sync_latest_daily()
    daily = D.load_daily(codes=codes, refresh=args.refresh)
    if args.limit:
        keep = sorted(daily['stock_code'].unique())[:args.limit]
        daily = daily[daily['stock_code'].isin(set(keep))]
        print(f'[限制] 只处理前 {len(keep)} 只')
    print(f'[载入] {len(daily):,} 行 / {daily["stock_code"].nunique()} 只')

    print('[判定] 日线 MA7 上抛 ...', flush=True)
    frame = SG.detect_frame(daily, cfg)
    start = args.start or bt_cfg['start']
    sig = SG.entries(frame, start=start, for_trade=True)
    n_up_days = int(frame['up'].sum())
    print(f'[信号] 上抛起点 {len(sig):,} 条 / '
          f'{sig["stock_code"].nunique() if len(sig) else 0} 只  （{start} 起）'
          f'；上抛状态日 {n_up_days:,}')
    if len(sig):
        print('       按年 ' + str(sig['signal_date'].dt.year
                                    .value_counts().sort_index().to_dict()))
    if len(sig) == 0:
        print('[退出] 无信号，跳过回测')
        return 0

    print('[回测] ...', flush=True)
    bt_cfg['start'] = start
    bt = BT.run(sig, cfg, horizons=horizons, refresh=args.refresh)

    S.ensure_dirs()
    stamp = time.strftime('%Y%m%d_%H%M%S')
    xlsx = args.out or os.path.join(S.OUTPUT_DIR, f'ma7_backtest_{stamp}.xlsx')
    RP.write_excel(xlsx, bt, extra={'MA7上抛信号': sig})
    print(f'\n[OK] Excel : {xlsx}')
    if not args.no_chart:
        png = os.path.join(S.CHART_DIR, f'ma7_nav_{stamp}.png')
        RP.plot_nav(bt, png, title='日线 MA7 上抛')
        print(f'[OK] 净值图: {png}')
    txt = os.path.splitext(xlsx)[0] + '.txt'
    with open(txt, 'w', encoding='utf-8') as f:
        f.write(RPT.summary_text(bt, len(sig), cfg))
    print(f'[OK] 汇总  : {txt}')
    print(f'[用时] {(time.perf_counter() - t0) / 60:.1f} 分钟')
    return 0


if __name__ == '__main__':
    sys.exit(main())
