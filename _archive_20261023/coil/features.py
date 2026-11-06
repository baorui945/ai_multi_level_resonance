# -*- coding: utf-8 -*-
"""
==============================================================================
coil.features —— 把「横盘蓄势」的数学描述落成可计算的滚动特征
==============================================================================

设 P_t 为后复权收盘价，r_t = ln P_t - ln P_{t-1}，窗口 W = [t-N, t-1]
（**只用 t-1 及以前的数据**，当日的大阳线不会污染蓄势统计）：

数学表述                          工程特征（全部尺度不变，跨股票可比）
--------------------------------  --------------------------------------------
P'(t) ≈ 0                          beta  = ln P 对时间的 OLS 斜率（日均）
P''(t) ≈ 0                        curv  = ln P 二次拟合的二次项系数 a
                                   r2    = 该二次拟合的 R²
Var(P) → min                      cv    = std(P)/mean(P)
                                   box_w = (q90-q10)/q50
                                   rng   = (max-min)/q50
波动率压缩（能量积累）             vol_compress = std(r,后半窗)/std(r,前半窗)
                                   energy = mean(|r|)（仅供观察，不做阈值）
|MA7-MA21|→0, |MA21-MA120|→0       ma_gap = max(两者)/close
MA120 走平                        ma120_slope = (MA120_t/MA120_{t-k}-1)/k
M(t) ∈ [-ε, ε]（零轴粘合）         macd_band = mean(|DIF|)/close
                                   hist_band = std(HIST)/close
                                   n_cross   = 窗口内金叉/死叉总次数
突破（P > P_break）                box_high = q90(P, W)，dist_high = P_t/box_high-1
放量突破                          vol_mult = volume_t / mean(volume, 前20日)

所有 `rolling_coil` 返回的蓄势类特征都已 `shift(1)`；突破类特征（`vol_mult`、
`dist_high`）用当日值，因此 `detect.state` 在 t 日即可完成判定 —— 无未来函数。

本模块**不 import 旧策略任何模块**，`cross_flags` 是本地 12 行实现。
"""
import numpy as np
import pandas as pd


# ═════════════════════════════════════════════════════════════════════════
# 基础工具
# ═════════════════════════════════════════════════════════════════════════
def cross_flags(dif, dea):
    """交叉标记：+1 = DIF 上穿 DEA（金叉），-1 = 下穿（死叉），0 = 无。"""
    dif = np.asarray(dif, dtype=np.float64)
    dea = np.asarray(dea, dtype=np.float64)
    x = np.zeros(dif.size, dtype=np.int8)
    if dif.size < 2:
        return x
    a, b = dif[1:], dea[1:]
    pa, pb = dif[:-1], dea[:-1]
    x[1:] = np.where((a > b) & (pa <= pb), 1,
                     np.where((a < b) & (pa >= pb), -1, 0))
    return x


def _s(x):
    return pd.Series(np.asarray(x, dtype=np.float64))


def _sh(x, k=1):
    """向后移动 k 根，返回 ndarray（前 k 个为 NaN）。"""
    return _s(x).shift(k).to_numpy('float64')


def _roll(y, win, how='mean'):
    """统一 min_periods=win 的滚动统计，返回 ndarray。"""
    r = _s(y).rolling(int(win), min_periods=int(win))
    if how == 'mean':
        return r.mean().to_numpy('float64')
    if how == 'std':
        return r.std(ddof=0).to_numpy('float64')
    if how == 'sum':
        return r.sum().to_numpy('float64')
    if how == 'min':
        return r.min().to_numpy('float64')
    if how == 'max':
        return r.max().to_numpy('float64')
    raise ValueError(f'unknown how: {how}')



# ═════════════════════════════════════════════════════════════════════════
# 一阶导 / 二阶导（对数价 OLS 与二次拟合，向量化）
# ═════════════════════════════════════════════════════════════════════════
def _slope_curv(y, win):
    """滚动 OLS：返回 (beta, a, r2)。

    * beta：y = c0 + c1·j 的 c1（j = 0..win-1 为窗内序号）；
    * a   ：y = c0 + c1·j + c2·j² 的 c2；
    * r2  ：二次拟合的拟合优度。

    用固定设计矩阵的 XtX 逆 + 滚动和实现，复杂度 O(n)，不调用 rolling.apply。
    """
    y = np.asarray(y, dtype=np.float64)
    n = y.size
    beta = np.full(n, np.nan)
    a = np.full(n, np.nan)
    r2 = np.full(n, np.nan)
    win = int(win)
    if n < win or win < 3:
        return beta, a, r2

    i = np.arange(n, dtype=np.float64)
    sy = _roll(y, win, 'sum')
    syi = _roll(i * y, win, 'sum')
    syii = _roll(i * i * y, win, 'sum')
    syy = _roll(y * y, win, 'sum')

    start = i - win + 1.0
    s1 = syi - start * sy                       # Σ j·y
    s2 = syii - 2.0 * start * syi + start * start * sy   # Σ j²·y

    j = np.arange(win, dtype=np.float64)
    Sx = j.sum()
    Sxx = (j * j).sum()
    den = Sxx - Sx * Sx / win
    with np.errstate(invalid='ignore', divide='ignore'):
        beta = (s1 - Sx * sy / win) / den

        X = np.column_stack([np.ones(win), j, j * j])
        inv = np.linalg.inv(X.T @ X)
        b0 = inv[0, 0] * sy + inv[0, 1] * s1 + inv[0, 2] * s2
        b1 = inv[1, 0] * sy + inv[1, 1] * s1 + inv[1, 2] * s2
        b2 = inv[2, 0] * sy + inv[2, 1] * s1 + inv[2, 2] * s2
        a = b2
        ss_res = syy - (b0 * sy + b1 * s1 + b2 * s2)
        ss_tot = syy - sy * sy / win
        r2 = np.where(ss_tot > 1e-12,
                      1.0 - ss_res / np.maximum(ss_tot, 1e-12), np.nan)
    return beta, a, r2


# ═════════════════════════════════════════════════════════════════════════
# 主入口：滚动蓄势特征
# ═════════════════════════════════════════════════════════════════════════
def rolling_coil(close, dif, dea, hist, ma_s, ma_m, ma_l,
                 volume=None, win=120, box_q=0.90, vol_win=20,
                 ma120_slope_k=10):
    """计算单只股票的滚动蓄势特征（蓄势类特征均为「截至 t-1」的统计量）。

    Args:
        close/dif/dea/hist: 收盘价与 MACD 序列（等长 ndarray）。
        ma_s/ma_m/ma_l:     MA(short/mid/long) 序列，已由调用方计算。
        volume:             成交量（None 时 vol_mult 全 NaN）。
        win:                蓄势窗口 N。
        box_q:              箱体上沿分位（0.90 = q90）。
        vol_win:            均量窗口（默认 20）。
        ma120_slope_k:      MA120 走平的比较跨度。

    Returns:
        dict[str, np.ndarray]，长度与 close 相同：
        cv, box_w, rng, beta, curv, r2, vol_compress, energy,
        ma_gap, ma120_slope, macd_band, hist_band, n_cross,
        box_high, box_low, dist_high, vol_mult
    """
    close = np.asarray(close, dtype=np.float64)
    n = close.size
    win = max(10, int(win))
    px = _s(close)
    lp = _s(np.log(np.where(close > 0, close, np.nan)))
    lr = lp.diff()

    out = {}

    # ── 水平波动：cv / 箱体宽度 / 区间幅度（窗口 [t-win, t-1]）─────────
    mu = _roll(close, win, 'mean')
    sd = _roll(close, win, 'std')
    q_lo = px.rolling(win, min_periods=win).quantile(1.0 - box_q).to_numpy('float64')
    q_mid = px.rolling(win, min_periods=win).quantile(0.50).to_numpy('float64')
    q_hi = px.rolling(win, min_periods=win).quantile(box_q).to_numpy('float64')
    p_max = _roll(close, win, 'max')
    p_min = _roll(close, win, 'min')
    with np.errstate(invalid='ignore', divide='ignore'):
        out['cv'] = _sh(sd / mu)
        out['box_w'] = _sh((q_hi - q_lo) / np.maximum(q_mid, 1e-12))
        out['rng'] = _sh((p_max - p_min) / np.maximum(q_mid, 1e-12))

    # ── 导数：beta（一阶） / curv + r2（二阶）──────────────────────────
    beta, curv, r2 = _slope_curv(lp.to_numpy('float64'), win)
    out['beta'] = _sh(beta)
    out['curv'] = _sh(curv)
    out['r2'] = _sh(r2)

    # ── 波动率压缩：后半窗 std / 前半窗 std ─────────────────────────────
    h = max(2, win // 2)
    r_first = lr.rolling(h, min_periods=h).std(ddof=0)
    ratio = (lr.rolling(h, min_periods=h).std(ddof=0)
             / r_first.shift(h).replace(0.0, np.nan))
    out['vol_compress'] = _sh(ratio.clip(upper=5.0).to_numpy('float64'))
    out['energy'] = _sh(lr.abs().rolling(win, min_periods=win).mean()
                        .to_numpy('float64'))

    # ── 均线粘合 / 长期均线斜率 ────────────────────────────────────────
    ma_s = np.asarray(ma_s, dtype=np.float64)
    ma_m = np.asarray(ma_m, dtype=np.float64)
    ma_l = np.asarray(ma_l, dtype=np.float64)
    gap = np.maximum(np.abs(ma_s - ma_m), np.abs(ma_m - ma_l))
    with np.errstate(invalid='ignore', divide='ignore'):
        out['ma_gap'] = _sh(gap / close)
        out['ma120_slope'] = _sh(
            (_s(ma_l) / _s(ma_l).shift(ma120_slope_k) - 1.0).to_numpy('float64')
            / ma120_slope_k)

    # ── MACD 零轴粘合：DIF 带宽 / 红绿柱离散度 / 交叉次数 ───────────────
    dif = np.asarray(dif, dtype=np.float64)
    hist = np.asarray(hist, dtype=np.float64)
    x = cross_flags(dif, dea)
    band = _roll(np.abs(dif), win, 'mean')
    hband = _roll(hist, win, 'std')
    ncross = _s(np.abs(x.astype(np.float64))).rolling(
        win, min_periods=win).sum().to_numpy('float64')
    with np.errstate(invalid='ignore', divide='ignore'):
        out['macd_band'] = _sh(band / close)
        out['hist_band'] = _sh(hband / close)
    out['n_cross'] = _sh(ncross)

    # ── 突破要件（当日值）：箱体上沿 / 距上沿 / 量比 ────────────────────
    out['box_high'] = _sh(q_hi)
    out['box_low'] = _sh(q_lo)
    with np.errstate(invalid='ignore', divide='ignore'):
        out['dist_high'] = close / out['box_high'] - 1.0
    if volume is None:
        out['vol_mult'] = np.full(n, np.nan)
    else:
        vol = np.asarray(volume, dtype=np.float64)
        vol_ma = _s(vol).rolling(int(vol_win), min_periods=int(vol_win)).mean()
        with np.errstate(invalid='ignore', divide='ignore'):
            out['vol_mult'] = vol / vol_ma.shift(1).to_numpy('float64')
    return out



# ═════════════════════════════════════════════════════════════════════════
# MACD（本地实现，口径：ema[0]=x[0]，alpha=2/(period+1)）
# ═════════════════════════════════════════════════════════════════════════
def ema(x, period):
    x = np.asarray(x, dtype=np.float64)
    if x.size == 0:
        return x
    a = 2.0 / (float(period) + 1.0)
    out = np.empty_like(x)
    out[0] = x[0]
    for i in range(1, x.size):
        out[i] = x[i] * a + out[i - 1] * (1.0 - a)
    return out


def macd(close, fast=30, slow=89, signal=9):
    """返回 (dif, dea, hist)；数据不足 slow+signal 根时返回全 NaN。"""
    close = np.asarray(close, dtype=np.float64)
    n = close.size
    if n < slow + signal:
        nan = np.full(n, np.nan)
        return nan, nan, nan
    dif = ema(close, fast) - ema(close, slow)
    dea = ema(dif, signal)
    return dif, dea, (dif - dea) * 2.0


def moving_averages(close, periods=(7, 21, 120)):
    """等长滚动均值元组（min_periods=1，与旧体系 rolling 口径一致）。"""
    s = pd.Series(np.asarray(close, dtype=np.float64))
    return tuple(s.rolling(int(p), min_periods=1).mean().to_numpy('float64')
                 for p in periods)
