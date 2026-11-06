# -*- coding: utf-8 -*-
"""
==============================================================================
ma7daily.signal —— 日线 MA7「上抛」判定（独立实现，不依赖 mlr.resonance）
==============================================================================
单只股票的判定逻辑
------------------
1. `MA7 = SMA(close, 7)`；
2. `slope = (MA7[t] / MA7[t-k]) ** (1/k) - 1`（k 日复利斜率，默认 k=3）；
3. 单日达标 `raw`：`close > MA7`（可关）且 `slope >= theta`；
   `theta` 默认 -0.05%/日，表示 MA7 **走平即可**，不要求已经明显上翘；
4. `persist` 默认 1：当日收盘满足即可确认，9/30 当天就是信号；
   若把 persist 调到 2，可选用 entry_backdate 把信号日回填到窗口首日
   （仅用于形态统计；回测默认关闭，避免未来函数）；
   `fresh_cross_days` 的口径由 `fresh_mode` 决定：
   * `since_turn`（默认）—— MA7 **连续上行**已持续的天数，抑制“段内回踩后
     重生”（回踩跌破 MA7 会把 above_run 清零，但 MA7 上行段并未重置）；
   * `above_run`（旧口径）—— 收盘连续站上 MA7 的天数；
5. 上抛状态 `up`：
   * 入场：确认窗口首日；
   * 持有：期间允许单日收盘跌破 MA7（图中回踩常见），
   * 退出：连续 `exit_below_days` 日收盘 < MA7，或连续 `exit_slope_days`
     日斜率 < exit_slope_threshold（默认 -0.05%/日，只有明确下弯才退出，
     不会因为 MA7 走平就误杀）；
6. `entry_flag` = 上抛状态 False→True 的首日（独立策略的信号日）。

输出的长表按 `stock_code, trade_date` 排序，包含判定所需的全部中间列，
便于扫描端展示、回测端取信号。
"""
import numpy as np
import pandas as pd


def _detect_one(close, cfg, open_=None, high=None, low=None, volume=None):
    """单只股票 OHLCV 序列 → 判定列（numpy 数组）。"""
    m = cfg['ma7']
    st = cfg['state']
    win = int(m.get('window', 7))
    k = int(m.get('k', 3))
    theta = float(m.get('theta', -0.0005))
    persist = max(1, int(m.get('persist', 1)))
    close_above = bool(m.get('close_above', True))
    require_higher_low = bool(m.get('require_higher_low', True))
    confirm_next_up = bool(m.get('confirm_next_up', True))
    exit_below_days = max(1, int(st.get('exit_below_days', 2)))
    exit_slope_days = max(1, int(st.get('exit_slope_days', 2)))
    exit_slope_threshold = float(st.get('exit_slope_threshold', 0.0))
    exit_tol = float(st.get('exit_below_tol', 0.0) or 0.0)
    cooldown = max(0, int(st.get('cooldown_days', 0) or 0))

    close = np.asarray(close, dtype=np.float64)
    n = close.size
    s = pd.Series(close)
    ma7 = s.rolling(win, min_periods=win).mean()
    slope = (ma7 / ma7.shift(k)) ** (1.0 / k) - 1.0

    ma7v = ma7.to_numpy('float64')
    slopev = slope.to_numpy('float64')
    finite = np.isfinite(ma7v) & np.isfinite(slopev)
    dev = np.where(np.isfinite(ma7v) & (ma7v != 0),
                   (close / ma7v - 1.0) * 100.0, np.nan)

    if open_ is not None and high is not None and low is not None:
        open_ = np.asarray(open_, dtype=np.float64)
        high = np.asarray(high, dtype=np.float64)
        low = np.asarray(low, dtype=np.float64)
        rng = high - low
        close_pos = np.divide(close - low, rng, out=np.full(n, np.nan),
                              where=rng > 0)
        body_pct = np.divide(close, open_, out=np.full(n, np.nan),
                             where=open_ > 0) * 100.0 - 100.0
    else:
        close_pos = np.full(n, np.nan)
        body_pct = np.full(n, np.nan)
    if volume is not None:
        v = pd.Series(np.asarray(volume, dtype=np.float64))
        vma = v.rolling(5, min_periods=5).mean().shift(1)
        vol_ratio = (v / vma).to_numpy('float64')
    else:
        vol_ratio = np.full(n, np.nan)

    above = close > ma7v
    above_run = np.zeros(n, dtype=np.int64)
    for t in range(n):
        above_run[t] = above_run[t - 1] + 1 if (t and above[t]) else int(above[t])

    # MA7 拐头：当日 MA7 高于前一日；状态首次满足时即为起点。
    # 不强求是下跌后的“第一根”，因为第一根可能低点更低/收盘未站上，
    # 真正的起点可能是随后 1~2 日（如 000400 2026-09-29）。
    ma_prev = np.r_[np.nan, ma7v[:-1]]
    ma_prev2 = np.r_[np.nan, np.nan, ma7v[:-2]]
    ma_up = (np.isfinite(ma7v) & np.isfinite(ma_prev)
             & (ma7v > ma_prev))
    ma_first_up = (ma_up & np.isfinite(ma_prev) & np.isfinite(ma_prev2)
                   & (ma_prev <= ma_prev2))
    # MA7 连续上行天数：当前上行段已持续几根（fresh_mode=since_turn 用）
    ma_up_run = np.zeros(n, dtype=np.int64)
    for t in range(n):
        ma_up_run[t] = (ma_up_run[t - 1] + 1
                        if (t and ma_up[t]) else int(ma_up[t]))
    if low is not None:
        higher_low = np.r_[False, low[1:] > low[:-1]]
    else:
        higher_low = np.ones(n, dtype=bool)

    raw = finite & ma_up
    if close_above:
        raw &= above
    if require_higher_low:
        raw &= higher_low
    # 可选：仍用 3 日斜率做额外过滤（默认关，因为 7/14 的斜率仅 -0.06%）
    if m.get('use_slope_filter', False):
        raw &= slopev >= theta
    # K 线质量：收复 MA7 的当日需要是实体阳线 / 收盘靠上 / 放量
    if m.get('bullish_only', False) and open_ is not None:
        raw &= close > open_
    min_body = m.get('min_body_pct')
    if min_body is not None:
        raw &= body_pct >= float(min_body)
    min_pos = m.get('min_close_pos')
    if min_pos is not None:
        raw &= close_pos >= float(min_pos)
    min_vol = m.get('min_vol_ratio')
    if min_vol is not None:
        raw &= vol_ratio >= float(min_vol)
    # 可选过滤：入场时相对 MA7 的偏离上限（避免追高）
    max_dev = m.get('max_dev_pct')
    if max_dev is not None:
        raw &= dev <= float(max_dev)
    # 可选过滤：突破新鲜度（默认 since_turn = MA7 这段上行已持续几天；
    # above_run = 收盘连续站上 MA7 几天，旧口径，回踩后会重置）
    fresh = int(m.get('fresh_cross_days', 0) or 0)
    if fresh > 0:
        fresh_mode = str(m.get('fresh_mode', 'since_turn')).lower()
        if fresh_mode == 'above_run':
            raw &= above_run <= fresh
        else:
            raw &= ma_up_run <= fresh
    # 可选过滤：斜率加速（MA7 正在变陡，而非走平/衰竭）
    if m.get('accel', False):
        lag = max(1, int(m.get('accel_lag', k)))
        acc = np.zeros(n, dtype=bool)
        acc[lag:] = slopev[lag:] > slopev[:-lag]
        raw &= acc
    # 可选过滤：入场前 MA7 必须经历过一段下行（真正的“拐点”，而非趋势中继）
    reset_lb = int(m.get('reset_lookback', 0) or 0)
    if reset_lb > 0:
        reset_thr = float(m.get('reset_slope', -0.0005))
        reset_ok = np.zeros(n, dtype=bool)
        for t in range(n):
            seg = slopev[max(0, t - reset_lb):t]
            reset_ok[t] = bool(np.any(np.isfinite(seg) & (seg < reset_thr)))
        raw &= reset_ok
    # 可选过滤（级别/挖坑深度）：入场前 N 日内 MA7 自局部高点回撤 >= X%，
    # 用来区分「下跌→走平→收复」的真起点与上升趋势中的小回踩。
    # 校准约束：000858 2026-10-09 的 20 日 MA7 回撤仅 1.54%，阈值不要设到 1.5% 以上。
    reset_dd = m.get('reset_drawdown_pct')
    if reset_dd is not None:
        dd_lb = max(2, int(m.get('reset_dd_lookback', 20) or 20))
        dd_thr = float(reset_dd) / 100.0
        dd_ok = np.zeros(n, dtype=bool)
        for t in range(n):
            seg = ma7v[max(0, t - dd_lb):t]
            if not np.isfinite(ma7v[t]) or not np.any(np.isfinite(seg)):
                continue
            dd_ok[t] = bool(np.nanmax(seg) >= ma7v[t] * (1.0 + dd_thr))
        raw &= dd_ok
    raw = raw.astype(bool)

    # 确认：起点的下一交易日 MA7 继续上行（7/14→7/15、10/9→10/12）
    next_up = np.zeros(n, dtype=bool)
    if n > 1:
        next_up[:-1] = ma_up[1:]
    confirmed = (raw & next_up) if confirm_next_up else raw.copy()

    up = np.zeros(n, dtype=bool)
    entry = np.zeros(n, dtype=bool)
    exit_flag = np.zeros(n, dtype=bool)
    seg_no = np.zeros(n, dtype=np.int64)
    seg_len = np.zeros(n, dtype=np.int64)

    in_state = False
    seg = 0
    cur_len = 0
    below = 0
    slope_neg = 0
    last_exit = -10 ** 9
    for t in range(n):
        if in_state:
            # 收盘跌破 MA7 的连续性（允许 exit_below_tol 的容差）
            if np.isfinite(ma7v[t]) and close[t] < ma7v[t] * (1.0 - exit_tol):
                below += 1
            else:
                below = 0
            if np.isfinite(slopev[t]) and slopev[t] < exit_slope_threshold:
                slope_neg += 1
            else:
                slope_neg = 0
            if below >= exit_below_days or slope_neg >= exit_slope_days:
                in_state = False
                exit_flag[t] = True
                seg_no[t] = seg
                last_exit = t
                below = 0
                slope_neg = 0
                cur_len = 0
        if (not in_state and confirmed[t] and not exit_flag[t]
                and (t - last_exit) > cooldown):
            in_state = True
            entry[t] = True
            seg += 1
            seg_no[t] = seg
            cur_len = 1
            below = 0
            slope_neg = 0
        elif in_state:
            seg_no[t] = seg
            cur_len += 1
        up[t] = in_state
        if in_state:
            seg_len[t] = cur_len
        else:
            seg_len[t] = 0

    return {
        'ma7': ma7v,
        'slope': slopev,
        'slope_pct_day': slopev * 100.0,
        'dev_pct': dev,
        'close_gt_ma7': close > ma7v,
        'above_run': above_run,
        'ma7_up_run': ma_up_run,
        'raw': raw,
        'confirmed': confirmed,
        'up': up,
        'entry_flag': entry,
        'exit_flag': exit_flag,
        'seg_no': seg_no,
        'seg_len': seg_len,
    }


def detect_frame(daily, cfg):
    """对日线长表逐只判定，返回带 `up` / `entry_flag` 等列的长表。"""
    cols = ['ma7', 'slope', 'slope_pct_day', 'dev_pct', 'close_gt_ma7',
            'above_run', 'ma7_up_run',
            'raw', 'confirmed', 'up', 'entry_flag', 'exit_flag',
            'seg_no', 'seg_len']
    out = []
    daily = daily.sort_values(['stock_code', 'trade_date'])
    for code, g in daily.groupby('stock_code', sort=False):
        g = g.copy()
        res = _detect_one(
            g['close_price'].to_numpy('float64'), cfg,
            open_=g['open_price'].to_numpy('float64')
            if 'open_price' in g else None,
            high=g['high_price'].to_numpy('float64')
            if 'high_price' in g else None,
            low=g['low_price'].to_numpy('float64')
            if 'low_price' in g else None,
            volume=g['volume'].to_numpy('float64')
            if 'volume' in g else None,
        )
        for c in cols:
            g[c] = res[c]
        # 确认日 = 下一交易日（该日 MA7 继续上行）；仅 confirmed 行有意义
        g['confirm_date'] = g['trade_date'].shift(-1)
        out.append(g)
    if not out:
        z = daily.assign(**{c: pd.Series(dtype='float64') for c in cols})
        z['confirm_date'] = pd.NaT
        return z
    return pd.concat(out, ignore_index=True)


def entries(frame, start=None, for_trade=False):
    """上抛起点信号表（entry_flag=True）。

    * 默认 `signal_date` = 起点日（如 7/14、10/9）；
    * `for_trade=True` 时 `signal_date` = 确认日（下一交易日，如 7/15、
      10/12），供回测避免未来函数；起点日保留在 `start_date`。
    """
    df = frame[frame['entry_flag']].copy()
    if start is not None:
        df = df[df['trade_date'] >= pd.Timestamp(start)]
    df = df.rename(columns={'trade_date': 'start_date',
                            'close_price': 'signal_close'})
    if 'confirm_date' not in df:
        df['confirm_date'] = pd.NaT
    df['signal_date'] = (df['confirm_date'] if for_trade
                         else df['start_date'])
    if for_trade:
        df = df[df['signal_date'].notna()]
    cols = ['stock_code', 'stock_name', 'signal_date', 'start_date',
            'confirm_date', 'signal_close', 'ma7', 'slope_pct_day',
            'dev_pct', 'seg_no']
    return (df[cols].sort_values(['signal_date', 'stock_code'])
            .reset_index(drop=True))


def state_on(frame, date):
    """指定交易日仍处于「上抛中」的全部股票。"""
    d = pd.Timestamp(date)
    return frame[(frame['trade_date'] == d) & frame['up']].copy()


def latest_date(frame):
    return pd.Timestamp(frame['trade_date'].max())
