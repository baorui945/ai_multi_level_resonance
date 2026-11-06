# -*- coding: utf-8 -*-
"""
==============================================================================
ma7daily/cycle.py —— 周线「周期性」筛选（正弦拟合版）
==============================================================================
目标：找出 000400 那种「大波段、来回走、净趋势很小」的周线形态。

做法：
1. 取最近 156 周（3 年）周线收盘，取对数后**去掉线性趋势**；
2. 在 45~170 周的周期网格上做**正弦最小二乘拟合**，取 R² 最高的那一档：
   - R²        ：形态有多像一条正弦波（0~1）；
   - period    ：拟合出的周期（周）；
   - amp       ：去趋势后的**峰谷振幅**（%）；
   - drift     ：这 3 年的**净趋势**（log 收益绝对值，越小越是「来回走」）；
3. 辅助：MACD(30,90,9) 的 DIF 穿越 0 的次数；
4. 打分：
   R² 40（0.35 以下记 0）+ 振幅 30（80% 封顶）+ 净趋势 15（0.8 以上记 0）
   + 周期合理度 8（80 周最佳）+ MACD 7。
"""
import numpy as np
import pandas as pd


def _ema(s, n):
    return s.ewm(span=n, adjust=False).mean()


def _crossings(a):
    s = np.sign(np.asarray(a, dtype=float))
    s = s[np.isfinite(s) & (s != 0)]
    return int((s[1:] != s[:-1]).sum()) if len(s) > 1 else 0


def sine_fit(closes, lo=45, hi=170, step=2):
    """返回 dict(r2, period, amp, drift, phase, yhat)。"""
    y = np.log(np.asarray(closes, dtype=float))
    n = len(y)
    t = np.arange(n)
    coef = np.polyfit(t, y, 1)
    yd = y - np.polyval(coef, t)
    best = None
    for T in range(lo, hi + 1, step):
        w = 2 * np.pi / T
        X = np.column_stack([np.sin(w * t), np.cos(w * t), np.ones(n)])
        beta, *_ = np.linalg.lstsq(X, yd, rcond=None)
        yhat = X @ beta
        ss_res = float(((yd - yhat) ** 2).sum())
        ss_tot = float(((yd - yd.mean()) ** 2).sum())
        r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0
        if best is None or r2 > best['r2']:
            best = dict(r2=r2, T=T, yhat=yhat)
    amp = float((np.exp(yd.max() - yd.min()) - 1) * 100)
    drift = float(abs(coef[0] * n))
    yhat = best['yhat']
    up = bool(len(yhat) >= 10 and yhat[-1] > yhat[-10])
    return dict(r2=float(best['r2']), period=int(best['T']), amp=amp,
                drift=drift, phase='上行' if up else '下行', yhat=yhat)


def stats(wk, lookback=156, macd=(30, 90, 9), ma=21):
    """wk: 周线 DataFrame（trade_date / close_price），已按时间升序、只含已收盘周。"""
    w = wk.tail(lookback)
    if len(w) < 80:
        return None
    close = w['close_price'].astype(float)
    closes = close.to_numpy()

    # 104 周与 156 周各拟合一次，取 R² 更高者
    fa = sine_fit(closes[-104:])
    fb = sine_fit(closes[-156:]) if len(closes) >= 120 else fa
    f = fb if fb['r2'] >= fa['r2'] else fa

    dif = _ema(close, macd[0]) - _ema(close, macd[1])
    dea = _ema(dif, macd[2])
    macd_x = _crossings(dif)
    ma_line = close.rolling(ma).mean()
    ma_x = _crossings(close.to_numpy() - ma_line.to_numpy())

    r2, period, amp, drift = f['r2'], f['period'], f['amp'], f['drift']
    s_shape = max(0.0, (r2 - 0.35) / 0.55) * 40
    s_amp = min(amp / 80.0, 1.0) * 30
    s_pure = max(0.0, 1 - drift / 0.8) * 15
    s_period = max(0.0, 1 - abs(period - 80) / 70) * 8
    s_macd = min(macd_x / 5.0, 1.0) * 7
    score = s_shape + s_amp + s_pure + s_period + s_macd
    if r2 < 0.45:
        score *= 0.7

    last_close = float(closes[-1])
    ma_last = float(ma_line.iloc[-1]) if np.isfinite(ma_line.iloc[-1]) else np.nan
    dev_ma = (last_close / ma_last - 1) * 100 if ma_last and np.isfinite(ma_last) else np.nan

    # ---- 周期位置与拐头（用于「周线上涨初期」） ----
    yh = np.asarray(f['yhat'], dtype=float)
    lo_y, hi_y = float(yh.min()), float(yh.max())
    pos = float((yh[-1] - lo_y) / (hi_y - lo_y)) if hi_y > lo_y else 0.5   # 0=波谷 1=波峰
    slope4 = float(yh[-1] - yh[-5]) if len(yh) >= 5 else 0.0              # 拟合波近 4 周斜率
    low52 = float(close.tail(52).min())
    up_from_low = (last_close / low52 - 1) * 100 if low52 > 0 else np.nan
    hist = dif - dea
    hist_rise = bool(len(hist) >= 5 and hist.iloc[-1] > hist.iloc[-4])

    # ---- 启动信号：上穿 MA21 + MACD 柱翻红（对应「周线上抛」的信号周） ----
    above = (close > ma_line).to_numpy()
    h = hist.to_numpy()
    n_idx = len(close) - 1
    cx = [i for i in range(1, len(above)) if above[i] and not above[i - 1]]
    rc = [i for i in range(1, len(h)) if h[i] > 0 and not (h[i - 1] > 0)]
    wk_cross = int(n_idx - cx[-1]) if cx else 999     # 距最近一次上穿 MA21 几周
    wk_red = int(n_idx - rc[-1]) if rc else 999       # 距最近一次 MACD 柱翻红几周
    early = bool(
        above[-1] and wk_red <= 4 and wk_cross <= 12
        and np.isfinite(dev_ma) and dev_ma <= 12 and pos <= 0.55
    )
    # 更早一步：刚站上 MA21，但 MACD 柱还没翻红（左肩观察）
    pre_start = bool(
        above[-1] and wk_cross <= 6 and wk_red > 4
        and np.isfinite(dev_ma) and dev_ma <= 10 and pos <= 0.55
    )

    return dict(
        pos=round(pos, 3),
        slope4=round(slope4, 3),
        up_from_low=round(float(up_from_low), 1) if np.isfinite(up_from_low) else None,
        hist_rise=hist_rise,
        wk_cross=wk_cross,
        wk_red=wk_red,
        early=early,
        pre_start=pre_start,
        score=round(float(score), 1),
        r2=round(float(r2), 3),
        period=int(period),
        amp=round(float(amp), 1),
        drift=round(float(drift), 2),
        phase=f['phase'],
        macd_x=int(macd_x),
        ma_x=int(ma_x),
        close=round(last_close, 2),
        ma21=round(ma_last, 2) if np.isfinite(ma_last) else None,
        dev_ma=round(dev_ma, 2) if np.isfinite(dev_ma) else None,
        weeks=len(w),
    )
