# -*- coding: utf-8 -*-
"""
==============================================================================
ma7daily.resonance —— 日线 × 周线共振（日线开仓必须在周线上抛段内）
==============================================================================
规则（2026-11-03 定稿）：

  周线上抛段：s5w 连续 2 周 > 0 启动，连续 2 周 <= 0 结束；
              段区间 = [段起点, 最后一个 s5w > 0 的周]（与 ma7_scan_week 同口径）。
  日线上抛  ：v2 信号，D3 收盘确认，D4 开盘执行（与 ma7_scan_close3 同口径）。
  共振      ：日线的【确认日 D3】必须落在周线段内；
              若开仓日 D4 已知，还要求 D4 也在段内（“只能在启动和结束之间开仓”）。
              段仍在进行中（数据末端未出现连续 2 周 s5w<=0）→ 段没有上界。

用法（供 CLI 调用）::

    segs = weekly_segments(wk_frame)          # 从 weekly detect_frame 结果提取段
    hit  = segment_at(segs[code], date)       # 某日期落在哪个段（无则 None）
"""
import numpy as np
import pandas as pd


def weekly_segments(wk):
    """从周线 detect_frame 结果提取上抛段。

    Returns:
        DataFrame: stock_code, seg_id, start, end, ongoing, weeks
        - start/end 都是「该周最后交易日」
        - ongoing=True 表示扫描时该段还没结束（上界视为 +inf）
    """
    rows = []
    if wk is None or not len(wk):
        return pd.DataFrame(columns=['stock_code', 'seg_id', 'start', 'end',
                                     'ongoing', 'weeks'])
    for code, g in wk.groupby('stock_code', sort=False):
        g = g.sort_values('trade_date')
        last_bar = pd.Timestamp(g['trade_date'].iloc[-1])
        last_s5 = float(g['s5'].iloc[-1])
        for sid, gg in g[g['seg_id'] > 0].groupby('seg_id'):
            start = pd.Timestamp(gg['trade_date'].min())
            end = pd.Timestamp(gg['seg_end_date'].iloc[0])
            ongoing = bool(end >= last_bar and last_s5 > 0)
            rows.append(dict(stock_code=code, seg_id=int(sid), start=start,
                             end=end, ongoing=ongoing, weeks=int(len(gg))))
    return pd.DataFrame(rows, columns=['stock_code', 'seg_id', 'start', 'end',
                                       'ongoing', 'weeks'])


def segment_at(segs, date):
    """date 落在哪个周线段；进行中的段上界为 +inf。无则返回 None。"""
    if segs is None or not len(segs) or date is None:
        return None
    if isinstance(date, float) and not np.isfinite(date):
        return None
    try:
        d = pd.Timestamp(date)
    except Exception:                                        # noqa: BLE001
        return None
    if pd.isna(d):
        return None
    for s in segs.itertuples(index=False):
        if d < s.start:
            continue
        if s.ongoing or d <= s.end:
            return s
    return None
