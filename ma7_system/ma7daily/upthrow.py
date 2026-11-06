# -*- coding: utf-8 -*-
"""
==============================================================================
ma7daily.upthrow —— 日线 MA7「上抛」扫描（C + B 兜底版）
==============================================================================
规则（2026-11 定稿）：

* 段边界（斜率段）：
  `s5[t] = (MA7[t]/MA7[t-5])**(1/5)-1`（单位 %/日）；
  起点 = 斜率由负转正且连续 `start_confirm` 天为正；
  持续 = 斜率 > 0，允许 `end_confirm-1` 天回踩不中断；
  终点 = 连续 `end_confirm` 天斜率不为正。

* 信号（价格确认）：
  - **C（主）**：段内**首次收盘 > MA7** 的那天为起点，从起点起
    连续 `close_above_days` 天收盘都 > MA7 → 第 N 天确认；
  - **B（兜底）**：C 没出信号时，允许“前一天跌破、当天重新站上”
    （默认只要求 1 日回踩），从重新站上日起连续 N 天收盘 > MA7
    → 第 N 天确认；每段只取最早的一次，且 C 成功时不再找 B。


* 段前若价格从未站上 MA7，则该斜率段不构成上抛段，直接跳过。

时间线（N=3）：

    D1 起点（C 的首次站上日 / B 的回踩站上日）→ 候选 1/3
    D2 继续站上 → 候选 2/3
    D3 继续站上 → 确认 3/3
    D4 开盘 → 最早可执行

逐日字段：

    seg_id          斜率段号（0 = 不在段内）
    slope_start_date 斜率段起点（仅参考）
    seg_start_date  信号窗口起点（C 的首次站上日 / B 的回踩站上日）
    seg_day         相对信号窗口起点的第几天（1/2/3...；窗口前为 0）
    cand_n          候选进度 1/2/3（3 = 当日确认）
    signal          仅确认日 True
    mode            'C' / 'B'（信号窗口类型）
    above           当日收盘 > MA7

三个日期（detect_frame 输出）：

    signal_date     信号日 D1 = 窗口起点（首次站上 / 回踩后重新站上）
    confirm_date    确认日 D3 = 窗口第 N 天（signal=True 的那根）
    entry_date      开仓日 D4 = 确认日的下一根 K 线（次日开盘）
    → 信号日、确认日、开仓日 = D1、D3、D4，三个不同的交易日，别混用。

参数在 `ma7_system/config/ma7_close3.json`：
    segment.slope_window=5, start_confirm=2, end_confirm=2
    signal.close_above_days=3
    signal.fallback_reclaim=true, fallback_min_dip=1
"""
import json
import os

import numpy as np
import pandas as pd

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DEF = {
    'ma7_window': 7,
    'segment': {
        'slope_window': 5,
        'start_confirm': 2,
        'end_confirm': 2,
    },
    'signal': {
        'close_above_days': 3,
        'fallback_reclaim': True,
        'fallback_min_dip': 1,
    },
    'scan': {
        'universe': 'hs300',
        'recent': 5,
    },
}

CONFIG_NAME = 'ma7_close3.json'


def _deep_merge(base, over):
    out = dict(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config(path=None):
    """读 config/ma7_close3.json，与内置默认合并。"""
    p = path or os.path.join(BASE_DIR, 'config', CONFIG_NAME)
    user = {}
    if os.path.isfile(p):
        with open(p, 'r', encoding='utf-8') as f:
            user = json.load(f)
    cfg = _deep_merge(DEF, user)
    cfg['ma7_window'] = max(2, int(cfg.get('ma7_window', 7)))
    return cfg


def _detect_one(close, cfg):
    """单只股票 → 段/候选/信号数组。"""
    win = max(2, int(cfg['ma7_window']))
    k = max(1, int(cfg['segment']['slope_window']))
    sc = max(1, int(cfg['segment']['start_confirm']))
    ec = max(1, int(cfg['segment']['end_confirm']))
    dip = max(0, ec - 1)
    need = max(1, int(cfg['signal']['close_above_days']))
    fb = bool(cfg['signal'].get('fallback_reclaim', True))
    fb_dip = max(1, int(cfg['signal'].get('fallback_min_dip', 1)))

    close = np.asarray(close, dtype=np.float64)
    n = close.size
    s = pd.Series(close)
    ma7 = s.rolling(win, min_periods=win).mean()
    slope = (ma7 / ma7.shift(k)) ** (1.0 / k) - 1.0
    s5 = (slope * 100.0).to_numpy('float64')
    ma7v = ma7.to_numpy('float64')
    pos = np.isfinite(s5) & (s5 > 0)
    above = np.isfinite(ma7v) & (close > ma7v)

    seg_id = np.zeros(n, dtype=np.int64)
    seg_day = np.zeros(n, dtype=np.int64)
    seg_start = np.full(n, -1, dtype=np.int64)
    seg_end = np.full(n, -1, dtype=np.int64)
    slope_start = np.full(n, -1, dtype=np.int64)
    cand_n = np.zeros(n, dtype=np.int64)
    signal = np.zeros(n, dtype=bool)
    prov = np.zeros(n, dtype=bool)
    mode = np.full(n, '', dtype=object)

    sid = 0
    i = 0
    while i < n - 1:
        # 斜率段起点：连续 sc 天 >0，且前一天不是 >0
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

            # 段前从未站上 MA7 → 不是上抛段
            idx = np.flatnonzero(above[st:last + 1])
            if not len(idx):
                i = last + 1
                continue
            s0 = st + idx[0]

            # v2 口径（doc/日线扫描方案_v2.md §3.3）：
            # C 优先（段内首次站上起连续 need 根）；C 未出信号才找最早的 B；
            # 每段只取最早一个，C 成功则不再找 B。
            sid += 1
            w0, sig, md = None, None, ''
            if s0 + need - 1 <= last and np.all(above[s0:s0 + need]):
                w0, sig, md = s0, s0 + need - 1, 'C'
            elif fb:
                for t in range(s0 + 1, last - need + 2):
                    if not above[t]:
                        continue
                    if any(t - d < st or above[t - d]
                           for d in range(1, fb_dip + 1)):
                        continue
                    if np.all(above[t:t + need]):
                        w0, sig, md = t, t + need - 1, 'B'
                        break
            if w0 is None:                # 段内价格没站稳 → 不给信号
                w0 = s0
            for t in range(st, last + 1):
                seg_id[t] = sid
                slope_start[t] = st
                seg_start[t] = w0
                seg_end[t] = last
                seg_day[t] = (t - w0 + 1) if t >= w0 else 0
                if md:
                    mode[t] = md
            if sig is not None:
                signal[sig] = True
                cand_n[sig] = need
            for d in range(need - 1):
                t = w0 + d
                if t > last:
                    break
                if np.all(above[w0:t + 1]):
                    cand_n[t] = d + 1
                else:
                    break

            i = last + 1
        else:
            i += 1

    # 最新一根：斜率刚转正、收盘在 MA7 上，但还没有 D2 来确认段起点
    if n >= 1 and seg_id[-1] == 0 and pos[-1] and above[-1] and (
            n == 1 or not pos[-2]):
        prov[-1] = True
        cand_n[-1] = 1

    return {
        'ma7': ma7v,
        's5': s5,
        'above': above,
        'seg_id': seg_id,
        'seg_day': seg_day,
        'seg_start_idx': seg_start,
        'seg_end_idx': seg_end,
        'slope_start_idx': slope_start,
        'cand_n': cand_n,
        'signal': signal,
        'prov': prov,
        'mode': mode,
    }


def detect_frame(daily, cfg=None):
    """对日线长表逐只判定，返回带段/信号信息的 long DataFrame。"""
    cfg = cfg or load_config()
    cols = ['ma7', 's5', 'above', 'seg_id', 'seg_day',
            'cand_n', 'signal', 'prov', 'mode']
    out = []
    daily = daily.sort_values(['stock_code', 'trade_date'])
    for code, g in daily.groupby('stock_code', sort=False):
        g = g.copy()
        res = _detect_one(g['close_price'].to_numpy('float64'), cfg)
        for c in cols:
            g[c] = res[c]

        td = pd.to_datetime(g['trade_date']).to_numpy()
        for idx_key, name in (('seg_start_idx', 'seg_start_date'),
                              ('seg_end_idx', 'seg_end_date'),
                              ('slope_start_idx', 'slope_start_date')):
            idx = res[idx_key]
            ok = idx >= 0
            vals = np.where(ok,
                            td[np.clip(idx, 0, len(td) - 1)],
                            np.datetime64('NaT'))
            g[name] = pd.to_datetime(vals)

        # 最新一根的临时候选：起点记为当天、段内第 1 天
        prov = res['prov']
        if prov.any():
            g.loc[prov, 'seg_start_date'] = g.loc[prov, 'trade_date']
            g.loc[prov, 'seg_day'] = 1

        # 三个日期：
        #   signal_date  信号日 D1 = 窗口起点（首次站上 / 回踩后重新站上）
        #   confirm_date 确认日 D3 = 窗口第 N 天（signal=True 那根）
        #   entry_date   开仓日 D4 = 确认日的下一根 K 线（次日开盘）
        g['signal_date'] = g['seg_start_date'].where(g['seg_day'] > 0)
        g['confirm_date'] = g['trade_date'].where(g['signal'])
        g['entry_date'] = g['trade_date'].shift(-1).where(g['signal'])
        out.append(g)

    if not out:
        z = daily.assign(**{c: pd.Series(dtype='float64') for c in cols})
        z['seg_start_date'] = pd.NaT
        z['seg_end_date'] = pd.NaT
        z['slope_start_date'] = pd.NaT
        z['signal_date'] = pd.NaT
        z['confirm_date'] = pd.NaT
        z['entry_date'] = pd.NaT
        return z
    return pd.concat(out, ignore_index=True)


def scan_day(frame, date):
    """指定交易日的 确认 / 候选2 / 候选1（含最新一根的临时候选）。"""
    d = pd.Timestamp(date)
    day = frame[frame['trade_date'] == d].copy()
    conf = day[day['signal']].copy()
    cand2 = day[(day['seg_day'] == 2) & (day['cand_n'] == 2)].copy()
    cand1 = day[((day['seg_day'] == 1) & (day['cand_n'] == 1))
                | day['prov']].copy()
    return conf, cand2, cand1


def active(frame, date):
    """指定交易日仍处于上抛段内的股票（段已开始、尚未结束）。"""
    d = pd.Timestamp(date)
    m = ((frame['trade_date'] == d)
         & (frame['seg_id'] > 0)
         & (frame['seg_start_date'] <= d)
         & ((frame['seg_end_date'].isna()) | (frame['seg_end_date'] >= d)))
    return frame[m].copy()
