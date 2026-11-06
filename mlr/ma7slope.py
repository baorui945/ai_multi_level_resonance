# -*- coding: utf-8 -*-
"""MA7 斜率「上抛」判定 · 独立工具（standalone，请勿与 parabola 日/周月上抛混用）。

思路（600188 校准，2026-10-25）：
  由 K 线（收盘价）机械带动 MA7 —— MA7 为 7 日收盘简单平均；
  当 MA7 的 k 日复利斜率 ≥ theta 且收盘位于 MA7 上方、且连续 persist 日
  达标时，判定该股处于「MA7 斜率上抛」状态：
      slope_daily = (MA7[t] / MA7[t-k]) ** (1/k) - 1
      up[t] = slope_daily >= theta AND close[t] > MA7[t] (可选)
              AND up_raw[t..t-persist+1] 全部达标 (persist>=2 时抑制闪灭)
  状态 False→True 的首日 = 上抛起点。

默认参数（600188 样本校准结果）：
  k = 3（三日斜率）、theta = 0.001（0.1%/日）、close_above = True、persist = 2。

刻意保持独立：
  * 只依赖收盘价序列，不依赖 MACD / parabola / 周月共振管线；
  * 不被 `resonance.py`、`backtest.py` 引用，不修改任何现有口径；
  * 参数独立给出（`PARAMS_DEF`），后续口径演进只动本文件。
"""
import numpy as np
import pandas as pd

PARAMS_DEF = {
    'k': 3,                    # 斜率跨度（日）
    'theta': 0.001,            # 每日复利斜率阈值（0.001 = 0.1%/日）
    'close_above': True,       # 要求收盘 > MA7
    'persist': 2,              # 连续达标天数（≥2 抑制单日闪灭）
    'ma_window': 7,            # MA7
}


# ── 核心计算 ────────────────────────────────────────────────────────────
def slope_stats(close, k=3, ma_window=7):
    """单序列：返回 (ma7, slope_daily) 两个 pd.Series（前 k+window-1 根为 NaN）。"""
    cl = pd.Series(np.asarray(close, dtype=np.float64))
    ma = cl.rolling(int(ma_window)).mean()
    slope = (ma / ma.shift(int(k))) ** (1.0 / int(k)) - 1.0
    return ma, slope


def upthrow_flags(close, k=3, theta=0.001, close_above=True, persist=2,
                  ma_window=7):
    """单序列 → dict(ma7, slope, raw, up)。

    raw   : 当日单根判据是否达标（slope>=theta 且可选收盘>MA7）；
    up    : 独立上抛状态（含 persist 驻留要求），False→True 首日即上抛起点。
    """
    ma, slope = slope_stats(close, k, ma_window)
    cl = pd.Series(np.asarray(close, dtype=np.float64))
    raw = (slope >= float(theta)) & np.isfinite(slope)
    if close_above:
        raw = raw & (cl > ma)
    raw = raw.fillna(False)
    up = raw.copy()
    for j in range(1, int(persist)):
        up = up & raw.shift(j).fillna(False)
    return {'ma7': ma, 'slope': slope, 'raw': raw,
            'up': up.fillna(False).astype(bool)}


def segments(up):
    """状态序列 → (起点索引数组, 末日索引数组)。输入可为 np/pd bool 序列。"""
    up = np.asarray(np.asarray(up), dtype=bool)
    T = np.flatnonzero(up)
    if T.size == 0:
        return np.empty(0, dtype=int), np.empty(0, dtype=int)
    starts = T[np.r_[True, np.diff(T) > 1]]
    ends = T[np.r_[np.diff(T) > 1, True]]
    return starts, ends


# ── 长表批量接口（对整只股票的日线 frame 使用）───────────────────────────
def upthrow_frame(daily_df, params=None):
    """对单只或多只股票的日线长表逐只计算，返回逐日明细长表。

    daily_df 需要列：stock_code, stock_name(可选), trade_date, close_price。
    输出列：
        ma7                 当日 MA7
        ma7_slope_pct_day   MA7 三日复利斜率（%/日）
        close_gt_ma7        收盘 > MA7
        daily_ok            当日单根达标（阈值 + 可选收盘>MA7）
        up                  独立上抛状态（含 persist 驻留）
        seg_no              上抛段号（0 = 非上抛状态日）
        is_start            上抛起点日
        seg_end_date        本段上抛末日（起点日才填）
        seg_len             本段持续交易日（起点日才填）
    """
    p = dict(PARAMS_DEF)
    p.update(params or {})
    out = []
    for code, g in daily_df.groupby('stock_code', sort=False):
        g = g.sort_values('trade_date')
        flags = upthrow_flags(
            g['close_price'].to_numpy(), k=p['k'], theta=p['theta'],
            close_above=p['close_above'], persist=p['persist'],
            ma_window=p['ma_window'])
        up = flags['up'].to_numpy(bool)
        starts, ends = segments(up)
        seg_no = np.zeros(len(g), dtype=int)
        for s in range(len(starts)):
            seg_no[starts[s]:ends[s] + 1] = s + 1
        is_start = np.zeros(len(g), dtype=bool)
        is_start[starts] = True
        name = (g['stock_name'].iloc[0] if 'stock_name' in g.columns
                else None)
        gf = pd.DataFrame({
            'stock_code': code,
            'stock_name': name,
            'trade_date': g['trade_date'].to_numpy(),
            'close_price': g['close_price'].to_numpy(),
            'ma7': flags['ma7'].to_numpy(),
            'ma7_slope_pct_day': flags['slope'].to_numpy() * 100.0,
            'daily_ok': flags['raw'].to_numpy(bool),
            'up': up,
            'seg_no': seg_no,
            'is_start': is_start,
        })
        cl_s = pd.Series(g.get('close_price').to_numpy())
        gf['close_gt_ma7'] = (cl_s > flags['ma7'].reset_index(
            drop=True)).fillna(False).to_numpy(bool)
        # 段级汇总（起点日回填）
        end_date = np.full(len(g), np.datetime64('NaT'), dtype='datetime64[ns]')
        seg_len = np.zeros(len(g), dtype=float)
        end_date[starts] = gf['trade_date'].to_numpy()[ends]
        seg_len[starts] = (ends - starts + 1).astype(float)
        gf['seg_end_date'] = end_date
        gf['seg_len'] = seg_len
        out.append(gf)
    res = pd.concat(out, ignore_index=True) if out else pd.DataFrame()
    return res


def segment_table(frame):
    """从 `upthrow_frame` 输出提取「上抛段起点」清单（每股每段一行）。"""
    st = frame[frame['is_start']].copy()
    st['上抛起点'] = st['trade_date']
    st['上抛末日'] = st['seg_end_date']
    st['持续(交易日)'] = st['seg_len'].astype(int)
    st = st.rename(columns={'stock_code': '股票代码', 'stock_name': '股票名称',
                            'close_price': '起点收盘', 'ma7': '起点MA7',
                            'ma7_slope_pct_day': '起点斜率(%/日)',
                            'stock_code': '股票代码'})
    cols = ['股票代码']
    if '股票名称' in st.columns:
        cols.append('股票名称')
    cols += ['上抛起点', '起点收盘', '起点MA7', '起点斜率(%/日)',
             '上抛末日', '持续(交易日)']
    return st[cols].reset_index(drop=True)


def turns(up):
    """状态 bool 序列 → False→True 转折 mask（含序列第一个 True）。"""
    up = np.asarray(up, dtype=bool)
    return up & ~np.r_[False, up[:-1]].astype(bool)
