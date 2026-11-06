# -*- coding: utf-8 -*-
"""
==============================================================================
backtest.py —— 多周期共振信号 · 历史回测主入口
==============================================================================
用法::

    python backtest.py                          # 全市场 · 默认持有期(20/30/40/60)
    python backtest.py --horizons 40            # 只跑 H40
    python backtest.py --exec close_close       # 换执行口径
    python backtest.py --limit 300              # 只跑前 300 只（冒烟）
    python backtest.py --refresh                # 强制重拉日线缓存
    python backtest.py --codes 600188,000001    # 指定股票

产物（output/）：mlr_backtest_*.xlsx / mlr_backtest_*.png / mlr_backtest_*.txt
"""
import argparse
import os
import sys
import time

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mlr import data as D, settings as S, resonance as R, backtest as BT, report as RP, universe as U


def build_parser():
    ap = argparse.ArgumentParser(prog='backtest', description='多周期共振回测')
    ap.add_argument('--refresh', action='store_true',
                    help='强制重拉日线缓存（MLR 日线缓存 **和** ABM 价格缓存）')
    ap.add_argument('--horizons', default=None, help='逗号分隔，如 20,40,60')
    ap.add_argument('--exec', dest='exec_mode', default=None,
                    help='close_close / open_open')
    ap.add_argument('--cost', type=float, default=None, help='双边合计成本，如 0.002')
    ap.add_argument('--start', default=None, help='信号起始日（默认配置 backtest.start）')
    ap.add_argument('--mode', default=None,
                    help='入场模式 any_entry / fresh_daily / slowest_turn / '
                         'wm_first（周月共振首日，不等日线）')
    ap.add_argument('--limit', type=int, default=None, help='只跑前 N 只（冒烟）')
    ap.add_argument('--codes', default=None, help='逗号分隔指定股票')
    ap.add_argument('--universe', default=None,
                    help='股票池 hs300 / all（默认取配置 universe.default）')
    ap.add_argument('--out', default=None, help='Excel 输出路径')
    ap.add_argument('--no-chart', action='store_true')
    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)
    cfg = S.load_config()
    bt_cfg = cfg['backtest']
    if args.exec_mode:
        bt_cfg['exec_mode'] = args.exec_mode
    if args.cost is not None:
        bt_cfg['cost'] = args.cost
    horizons = ([int(x) for x in args.horizons.split(',')] if args.horizons
                else bt_cfg['horizons'])

    # 回测只认「已收盘」周期：asof 口径无法在 merge_asof 里逐日重建，强制回落
    pm = str(cfg['data'].get('period_mode', 'closed')).lower()
    if pm != 'closed':
        print(f'[提示] data.period_mode={pm}：回测无法复现进行中周期，本次按 closed 计算')
        cfg['data']['period_mode'] = 'closed'
    for w in S.validate_config(cfg):
        print(f'[口径] {w}')

    print('=' * 78)
    print('  多周期（日/周/月）MACD 共振模型 —— 历史回测')
    print('=' * 78)
    print(f"  数据源     : {S.ABM_ROOT}  (表 {S.TABLE_DAILY})")
    print(f"  MACD/MA    : {tuple(cfg['macd'].values())} / "
          f"{cfg['ma']['short']},{cfg['ma']['long']}")
    print(f"  执行口径   : {bt_cfg['exec_mode']}   成本 {bt_cfg['cost'] * 100:.2f}%"
          f"  滑点 {bt_cfg.get('slippage_bp', 0):.0f}bp   持有期 {horizons}"
          f"   主口径 H{bt_cfg.get('main_horizon', horizons[0])}")
    print(f"  周期口径   : {cfg['data'].get('period_mode', 'closed')}"
          f"   并发上限 {bt_cfg.get('concurrent_cap', 0) or '不限'}"
          f"   退出 {bt_cfg.get('exit') or '固定持有期'}")
    print('=' * 78)

    t0 = time.perf_counter()
    codes = args.codes.split(',') if args.codes else None
    if codes is None:
        universe = args.universe or cfg.get('universe', {}).get('default', 'hs300')
        if universe == 'hs300':
            codes, upd = U.get_hs300_codes()
            print(f'[股票池] 沪深300成分股 {len(codes)} 只 (update_date={upd})')
        elif universe == 'coal':
            codes, upd = U.get_coal_codes()
            print(f'[股票池] 动力煤板块 {len(codes)} 只 (update_date={upd})')
        elif universe != 'all':
            raise SystemExit(f'未知 --universe {universe!r}（可选 hs300 / coal / all）')
    daily = D.load_daily(codes=codes, refresh=args.refresh)
    if args.limit:
        keep = sorted(daily['stock_code'].unique())[:args.limit]
        daily = daily[daily['stock_code'].isin(set(keep))]
        print(f'[限制] 只处理前 {len(keep)} 只')
    print(f'[载入] {len(daily):,} 行 / {daily["stock_code"].nunique()} 只')

    print('[重采样] 生成周线 / 月线 ...', flush=True)
    tfs = D.build_timeframes(daily, cfg)
    print(f'[重采样] 日 {len(tfs["daily"]):,} / 周 {len(tfs["weekly"]):,} / '
          f'月 {len(tfs["monthly"]):,}')

    print('[判定] 三周期「上抛」+ 共振 ...', flush=True)
    res = R.build_resonance(tfs, cfg)
    start = args.start or bt_cfg['start']
    bt_cfg['start'] = start          # 面板起点与信号起点保持一致（否则空仓期稀释年化）
    mode = args.mode or cfg.get('signal', {}).get('mode', 'any_entry')
    sig = R.signals(res, start=start, mode=mode)
    print(f'[信号] 模式={mode}  共振首日 {len(sig):,} 条 / '
          f'{sig["stock_code"].nunique()} 只  （{start} 起）')
    if len(sig):
        print('       按年 ' + str(sig['signal_date'].dt.year
                                    .value_counts().sort_index().to_dict()))

    print('[回测] ...', flush=True)
    bt = BT.run(sig, cfg, horizons=horizons, refresh=args.refresh)

    S.ensure_dirs()
    stamp = time.strftime('%Y%m%d_%H%M%S')
    xlsx = args.out or os.path.join(S.OUTPUT_DIR, f'mlr_backtest_{stamp}.xlsx')
    RP.write_excel(xlsx, bt, extra={'共振信号': sig})
    print(f'\n[OK] Excel : {xlsx}')
    if not args.no_chart:
        png = os.path.join(S.CHART_DIR, f'mlr_nav_{stamp}.png')
        RP.plot_nav(bt, png)
        print(f'[OK] 净值图: {png}')
    txt = os.path.splitext(xlsx)[0] + '.txt'
    with open(txt, 'w', encoding='utf-8') as f:
        f.write(RP.summary_text(bt, signal_count=len(sig)))
    print(f'[OK] 汇总  : {txt}')
    print(f'[用时] {(time.perf_counter() - t0) / 60:.1f} 分钟')
    return 0


if __name__ == '__main__':
    sys.exit(main())
