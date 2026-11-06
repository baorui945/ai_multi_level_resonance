# -*- coding: utf-8 -*-
"""
==============================================================================
ma7daily.report —— 日线 MA7 上抛回测的文本汇总
==============================================================================
Excel / 净值图直接复用 `mlr.report`（与主工程同版式），本模块只提供
MA7 口径的汇总文本，避免误显示 MACD/多周期参数。
"""
import pandas as pd

from mlr import report as RP


def _exit_desc(bt):
    """把 backtest.exit 配置翻译成一行中文（None = 固定持有期）。"""
    ex = bt.get('exit')
    if not ex:
        hs = bt.get('horizons') or []
        return f"固定持有期 {hs}" if hs else '固定持有期'
    rule = str(ex.get('rule', 'fixed')).lower()
    if rule == 'breakdown':
        return (f"跌破 MA{ex.get('breakdown_ma', 21)} 连续 "
                f"{ex.get('breakdown_days', 2)} 日收盘，"
                f"eps {float(ex.get('breakdown_eps', 0.0)) * 100:.2f}%")
    if rule == 'trail_pct':
        return f"回撤 {float(ex.get('trail_pct', 0.0)) * 100:.1f}% 离场"
    if rule == 'chandelier':
        return (f"吊灯止损 k={ex.get('k', 3.0)} ATR"
                f"{ex.get('atr_window', 22)}")
    return rule


def summary_text(bt, n_sig, cfg):
    m = cfg.get('ma7') or {}
    st = cfg.get('state') or {}
    lines = ['=' * 78,
             '  日线 MA7「上抛」—— 独立策略回测汇总',
             '=' * 78,
             f"  判定规则   : close>MA{m.get('window', 7)} 且 MA{m.get('window', 7)}"
             f" 首根上拐、当日更高低点；下一交易日 MA7 继续上行确认"
             f"（信号日=首根上拐日）",
             f"  退出规则   : {_exit_desc(bt)}"
             + (f"    （最长持有上限 {sorted(bt['horizons'])}）"
                if bt.get('exit') else '')
             + (f"；扫描状态另有斜率退出（连续 {st.get('exit_slope_days', 2)} 日 "
                f"{float(st.get('exit_slope_threshold', 0.0)) * 100:.2f}%/日，"
                f"回测未建模）" if bt.get('exit') else ''),
             f"  执行口径   : {bt['exec_mode']}    成本: {bt['cost'] * 100:.2f}% (双边)"
             f"    滑点: {bt.get('slippage_bp', 0):.0f}bp (单边)",
             f"  样本区间   : {bt['panel'].calendar[0].date()} ~ "
             f"{bt['panel'].calendar[-1].date()}"
             f"    （信号起点 {pd.Timestamp(bt.get('start', bt['panel'].calendar[0])).date()}）",
             f"  上抛信号数 : {n_sig}",
             f"  并发上限   : {bt.get('concurrent_cap', 0) or '不限'}",
             '-' * 78]
    lines.append(RP.perf_table(bt).to_string(index=False))
    lines.append('-' * 78)
    lines.append('年度收益(%)')
    lines.append(RP.annual_table(bt).to_string(index=False))
    return '\n'.join(lines)
