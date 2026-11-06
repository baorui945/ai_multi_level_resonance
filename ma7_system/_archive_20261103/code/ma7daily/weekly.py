# -*- coding: utf-8 -*-
"""
==============================================================================
ma7daily.weekly —— 周线 MA7 / 斜率段 / 上抛区间（日线×周线共振 冻结版）
==============================================================================
规则来源：doc/日线周线共振方案_冻结版.md §1（2026-11-03 冻结）

* 周线 K 线：日线按 W-FRI 重采样；period_date = 该周最后一个交易日，
  period_end = 名义周五。判定“已收盘”用 period_end <= 当日，
  盘中/未走完的当周不能参与周线指标。
* 指标：MA7w = 7 周 SMA；s5w = (MA7w[t]/MA7w[t-slope_window])**(1/slope_window)-1（%）。
* 斜率段：s5w > 0 连续 start_confirm 周起；允许 end_confirm-1 周回踩；
  连续 end_confirm 周 <= 0 结束（段尾 = 最后一个 s5w > 0 的周）。
  段前若价格从未站上周 MA7，则跳过该段。
* 上抛区间：段内【首次】收盘 > MA7w 的那一周 s0 起；从 s0 起连续
  break_weeks 周收盘 < MA7w，第 break_weeks 周 e 为结束确认周；区间 = [s0, e]（含端点）。
  同一斜率段内若跌破后又重新站上 → 切分成第二个区间（不合并）。
* 数据末端仍未跌破的区间：end 记 NaT（进行中）。

⚠️ 因果口径：默认按冻结版字面实现（区间由完整周线序列确定），与 §6 的
600188 示例一致。若要求“段必须被下一周确认才算成立”（严格因果），
用 detect_frame(..., asof=...) 截断后再算，见 ma7_scan_dw.py 的 --strict-week。

逐周字段：
    ma7, s5, above      周线指标 / 是否站上
    seg_id              斜率段号（0 = 不在段内）
    iv_id               区间号（0 = 不在区间）
    iv_week             区间内第几周
    iv_start_date       区间起点（首次站上周的 period_date）
    iv_end_date         区间终点（结束确认周）；进行中为 NaT
"""
import json
import os

import numpy as np
import pandas as pd

from . import settings as S

DEF = {
    'window': 7,
    'slope_window': 5,
    'start_confirm': 2,
    'end_confirm': 2,
    'break_weeks': 3,
    'rule': 'W-FRI',
}

CONFIG_NAME = 'ma7_dw.json'


def load_config(cfg=None):
    """取周线参数。

    Args:
        cfg: 完整配置 dict（含 weekly 段，如 upthrow.load_config(ma7_dw.json)）；
             也可直接传 weekly 段本身；None 时读 config/ma7_dw.json。
    """
    if cfg is None:
        p = os.path.join(S.BASE_DIR, 'config', CONFIG_NAME)
        user = {}
        if os.path.isfile(p):
            with open(p, 'r', encoding='utf-8') as f:
                user = json.load(f)
        cfg = user
    sec = dict(DEF)
    part = cfg.get('weekly', cfg) if isinstance(cfg, dict) else {}
    sec.update({k: v for k, v in (part or {}).items() if k in DEF})
    for k in ('window', 'slope_window', 'start_confirm', 'end_confirm', 'break_weeks'):
        sec[k] = max(1, int(sec[k]))
    sec['rule'] = str(sec['rule'] or 'W-FRI')
    return sec


# ═════════════════════════════════════════════════════════════════════════
# 重采样
# ═════════════════════════════════════════════════════════════════════════
def resample(daily, rule='W-FRI'):
    """日线长表 → 周线长表。

    Returns:
        stock_code, period_date（该周最后交易日）, period_end（名义周五）,
        open/high/low/close/volume
    """
    daily = daily.sort_values(['stock_code', 'trade_date'])
    out = []
    for code, g in daily.groupby('stock_code', sort=False):
        g = g.set_index('trade_date')
        r = g.resample(rule)
        w = pd.DataFrame({
            'open': r['open_price'].first(),
            'high': r['high_price'].max(),
            'low': r['low_price'].min(),
            'close': r['close_price'].last(),
            'volume': r['volume'].sum(min_count=1),
        }).dropna(subset=['close'])
        if w.empty:
            continue
        w['period_date'] = (g.index.to_series()
                            .groupby(pd.Grouper(freq=rule)).last()
                            .reindex(w.index))
        w['period_end'] = pd.DatetimeIndex(w.index)
        w['stock_code'] = code
        out.append(w.reset_index(drop=True))
    if not out:
        return pd.DataFrame(columns=['stock_code', 'period_date', 'period_end',
                                     'open', 'high', 'low', 'close', 'volume'])
    res = pd.concat(out, ignore_index=True)
    return res[['stock_code', 'period_date', 'period_end',
                'open', 'high', 'low', 'close', 'volume']]


# ═════════════════════════════════════════════════════════════════════════
# 斜率段 / 区间
# ═════════════════════════════════════════════════════════════════════════
def segments(pos, start_confirm=2, end_confirm=2):
    """连续为正的斜率段 [(st, last)]；允许 end_confirm-1 周回踩。"""
    sc = max(1, int(start_confirm))
    ec = max(1, int(end_confirm))
    dip = max(0, ec - 1)
    n = len(pos)
    out = []
    i = 0
    while i < n - 1:
        if pos[i] and (i == 0 or not pos[i - 1]) and np.all(pos[i:i + sc]):
            st = i
            last = i + sc - 1
            j = last
            gap = 0
            while j + 1 < n:
                if pos[j + 1]:
                    gap = 0
                    last = j + 1
                    j += 1
                elif gap < dip:
                    gap += 1
                    j += 1
                else:
                    break
            out.append((st, last))
            i = last + 1
        else:
            i += 1
    return out


def detect_one(close, cfg):
    """单只股票的周线状态 → 数组 + 区间列表。"""
    win = max(2, int(cfg['window']))
    k = max(1, int(cfg['slope_window']))
    bw = max(1, int(cfg['break_weeks']))
    sc = max(1, int(cfg['start_confirm']))
    ec = max(1, int(cfg['end_confirm']))

    close = np.asarray(close, dtype=np.float64)
    n = close.size
    s = pd.Series(close)
    ma7 = s.rolling(win, min_periods=win).mean()
    s5 = ((ma7 / ma7.shift(k)) ** (1.0 / k) - 1.0) * 100.0
    ma7v = ma7.to_numpy('float64')
    s5v = s5.to_numpy('float64')
    pos = np.isfinite(s5v) & (s5v > 0)
    above = np.isfinite(ma7v) & (close > ma7v)

    seg_id = np.zeros(n, dtype=np.int64)
    iv_id = np.zeros(n, dtype=np.int64)
    iv_week = np.zeros(n, dtype=np.int64)
    intervals = []

    sid = 0
    cursor = 0                      # 上一段区间的结束周 +1，保证区间不重叠
    for st, last in segments(pos, sc, ec):
        if not above[st:last + 1].any():
            continue
        sid += 1
        seg_id[st:last + 1] = sid
        cur = max(st, cursor)
        while cur <= last:
            idx = np.flatnonzero(above[cur:last + 1])
            if not len(idx):
                break
            s0 = cur + idx[0]
            e = None
            run = 0
            for t in range(s0, n):
                run = run + 1 if not above[t] else 0
                if run >= bw:
                    e = t
                    break
            intervals.append(dict(start=s0, end=e, seg_st=st, seg_last=last))
            if e is None:            # 进行中：不再有后续区间
                break
            cursor = e + 1
            cur = cursor

    for iid, iv in enumerate(intervals, 1):
        s0 = iv['start']
        e = iv['end'] if iv['end'] is not None else n - 1
        iv_id[s0:e + 1] = iid
        iv_week[s0:e + 1] = np.arange(1, e - s0 + 2)

    return dict(ma7=ma7v, s5=s5v, above=above, seg_id=seg_id,
                iv_id=iv_id, iv_week=iv_week, intervals=intervals)


# ═════════════════════════════════════════════════════════════════════════
# 长表与状态查询
# ═════════════════════════════════════════════════════════════════════════
def detect_frame(daily, cfg=None, asof=None):
    """逐票计算周线状态，返回长表（每周一行）。

    Args:
        daily: 日线长表（stock_code / trade_date / OHLC）
        cfg:   完整配置或 weekly 段
        asof:  只用到该日为止的数据（严格因果口径）；None = 用完整序列
    """
    cfg = load_config(cfg)
    d = daily
    if asof is not None:
        d = d[pd.to_datetime(d['trade_date']) <= pd.Timestamp(asof)]
    w = resample(d, cfg['rule'])
    if asof is not None and len(w):
        # 严格口径：未收盘的当周（period_end > 当日）不参与段/区间判定
        w = w[w['period_end'].to_numpy() <= np.datetime64(pd.Timestamp(asof))]
        w = w.reset_index(drop=True)
    if not len(w):
        return w
    cols = ['ma7', 's5', 'above', 'seg_id', 'iv_id', 'iv_week',
            'iv_start_date', 'iv_end_date', 'iv_ongoing']
    out = []
    for code, g in w.groupby('stock_code', sort=False):
        g = g.reset_index(drop=True)
        res = detect_one(g['close'].to_numpy('float64'), cfg)
        for c in ('ma7', 's5', 'above', 'seg_id', 'iv_id', 'iv_week'):
            g[c] = res[c]
        starts = pd.Series(pd.NaT, index=g.index, dtype='datetime64[ns]')
        ends = pd.Series(pd.NaT, index=g.index, dtype='datetime64[ns]')
        for iid, iv in enumerate(res['intervals'], 1):
            m = res['iv_id'] == iid
            if not m.any():
                continue
            starts.loc[m] = g.loc[m, 'period_date'].iloc[0]
            if iv['end'] is not None:
                ends.loc[m] = g.loc[m, 'period_date'].iloc[-1]
        g['iv_start_date'] = starts
        g['iv_end_date'] = ends
        g['iv_ongoing'] = (g['iv_id'] > 0) & g['iv_end_date'].isna()
        out.append(g[['stock_code', 'period_date', 'period_end', 'close'] + cols])
    return pd.concat(out, ignore_index=True)


EMPTY_STATE = {
    'ok': False, 'in_interval': False, 'iv_id': 0, 'week_no': 0,
    'start': None, 'end': None, 'ongoing': False,
    'week_date': None, 'close': np.nan, 'ma7': np.nan, 's5': np.nan, 'above': False,
}


def state_of(wframe, date):
    """某只股票在 date 当日可见的周线状态（只用已收盘周）。

    Args:
        wframe: 单只股票的周线长表（detect_frame 的子集）
        date:   交易日
    """
    if wframe is None or not len(wframe):
        return dict(EMPTY_STATE)
    date = pd.Timestamp(date)
    closed = wframe[wframe['period_end'].to_numpy() <= np.datetime64(date)]
    if not len(closed):
        return dict(EMPTY_STATE)
    row = closed.iloc[-1]
    out = dict(EMPTY_STATE)
    out.update(ok=True, week_date=row['period_date'], close=float(row['close']),
               ma7=row['ma7'], s5=row['s5'], above=bool(row['above']),
               iv_id=int(row['iv_id']))
    if out['iv_id'] <= 0:
        return out
    start = row['iv_start_date']
    end = row['iv_end_date']
    out['start'] = start
    out['end'] = None if pd.isna(end) else end
    out['ongoing'] = bool(pd.isna(end))
    out['week_no'] = int(row['iv_week'])
    out['in_interval'] = bool(pd.notna(start) and start <= date
                              and (pd.isna(end) or date <= end))
    return out
