# -*- coding: utf-8 -*-
"""
==============================================================================
ma7daily.dw —— 日线 × 周线共振：信号匹配 + 交易模拟
==============================================================================
冻结版口径（doc/日线周线共振方案_冻结版.md，2026-11-03）：

  共振：日线信号的【确认日】必须落在某个周线上抛区间内
        weekly_start <= 确认日 <= weekly_end
  开仓：确认日的下一个交易日开盘
  平仓：连续 exit_below_days（默认 3）个交易日收盘 < 日线 MA7 → 次日开盘
  成本：0.2% 双边（resonance.cost）

单仓（resonance.position_mode）：
  ignore —— 同一票持仓中，新的共振信号不执行（与 600188_opt3_exit3.csv 一致）
  add    —— 不做持仓占用过滤（与 600188_opt3_trades.csv 一致）
"""
import numpy as np
import pandas as pd

from . import upthrow as UP
from . import weekly as W


def load_config(path=None):
    """读 config/ma7_dw.json（日线段/信号 + weekly + resonance）。"""
    return UP.load_config(path)


def _rc(cfg):
    return (cfg or {}).get('resonance', {}) or {}


def signal_idx(up):
    return np.flatnonzero(up['signal'].to_numpy())


def resonance_entries(up, wf, cfg=None):
    """单票：所有日线信号 + 周线区间匹配结果（不模拟交易）。"""
    rows = []
    for i in signal_idx(up):
        row = up.iloc[i]
        st = W.state_of(wf, row['confirm_date'])
        rows.append(dict(
            code=row.get('stock_code'), name=row.get('stock_name'),
            signal_date=(row['signal_date'] if 'signal_date' in row.index
                         and pd.notna(row['signal_date']) else row['seg_start_date']),
            confirm=row['confirm_date'],
            mode=row['mode'], entry=row['entry_date'],
            in_wk=bool(st['in_interval']), wk_start=st['start'], wk_end=st['end'],
            wk_week=st['week_no'],
        ))
    return pd.DataFrame(rows)


def simulate(up, wf, cfg=None, one_position=True):
    """单票：共振信号 → 逐笔交易（3 日跌破离场）。

    Args:
        up: upthrow.detect_frame 的单票结果（含 signal/above/open_price）
        wf: weekly.detect_frame 的单票结果
        one_position: True = 持仓中忽略新信号
    Returns:
        DataFrame: signal_date(D1), confirm(D3), mode, entry(D4), entry_px,
                   exit, exit_px, net, hold, status(closed/open),
                   wk_start, wk_end, wk_week
    """
    rc = _rc(cfg)
    cost = float(rc.get('cost', 0.002))
    exit_days = max(1, int(rc.get('exit_below_days', 3)))
    o = up['open_price'].to_numpy('float64')
    above = up['above'].to_numpy(bool)
    dates = pd.to_datetime(up['trade_date']).to_numpy()
    n = len(up)
    busy = -1
    rows = []
    for i in signal_idx(up):
        ei = i + 1
        if ei >= n:
            continue
        cd = pd.Timestamp(dates[i])
        st = W.state_of(wf, cd)
        if not st['in_interval']:
            continue
        if one_position and ei <= busy:
            continue
        run = 0
        xi = None
        for t in range(ei, n):
            run = run + 1 if not above[t] else 0
            if run >= exit_days:
                xi = t
                break
        sd = (up['signal_date'].iloc[i] if 'signal_date' in up.columns
              and pd.notna(up['signal_date'].iloc[i]) else up['seg_start_date'].iloc[i])
        rec = dict(signal_date=pd.Timestamp(sd), confirm=cd,
                   mode=up['mode'].iloc[i], entry=pd.Timestamp(dates[ei]),
                   entry_px=float(o[ei]), wk_start=st['start'], wk_end=st['end'],
                   wk_week=st['week_no'])
        if xi is None or xi + 1 >= n:
            rec.update(exit=None, exit_px=np.nan, net=np.nan, hold=np.nan, status='open')
            if one_position:
                busy = n
        else:
            xo = xi + 1
            rec.update(exit=pd.Timestamp(dates[xo]), exit_px=float(o[xo]),
                       net=float(o[xo] / o[ei] - 1.0 - cost), hold=int(xo - ei),
                       status='closed')
            if one_position:
                busy = xo
        rows.append(rec)
    cols = ['signal_date', 'confirm', 'mode', 'entry', 'entry_px', 'exit', 'exit_px',
            'net', 'hold', 'status', 'wk_start', 'wk_end', 'wk_week']
    if not rows:
        return pd.DataFrame(columns=cols)
    return pd.DataFrame(rows)[cols]


def open_position(trades, asof):
    """某票在 asof 收盘后是否仍持仓（按单仓规则），返回持仓记录或 None。"""
    if trades is None or not len(trades):
        return None
    asof = pd.Timestamp(asof)
    m = ((trades['entry'] <= asof)
         & (trades['exit'].isna() | (trades['exit'] > asof)))
    if not m.any():
        return None
    return trades[m].iloc[-1].to_dict()
