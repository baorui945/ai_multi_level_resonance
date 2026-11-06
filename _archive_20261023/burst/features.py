# -*- coding: utf-8 -*-
"""
==============================================================================
burst.features —— 5 个形态维度的向量化计算
==============================================================================

记号：T = 信号日（收盘后确认），N = 爆发窗口，M = 平台窗口。

* S_A 突破强度      = C_T / max(H_{T-M..T-1}) - 1
* S_B 加速度        = [slope(C, N) - slope(C_{T-M..T-N})] / C_T   （日均，%）
* S_C 实体膨胀      = mean|C-O|_{T-N+1..T} / mean|C-O|_{T-M..T-N}
* S_D 阳线密度      = mean(1[C>O])_{T-N+1..T}
* S_E 逼空强度      = mean((C-L)/(H-L))_{T-N+1..T}

与原始文字定义的两处必要修正（见 doc/burst_策略记录.md）：

1. S_B 原式 ``(C_t-C_{t-1})-(C_{t-1}-C_{t-2})`` 的单位是「元」，高价股天然
   得分更高，跨股票不可比；默认改为相对斜率差（除以 C_T），并提供
   ``accel_mode='diff2'`` 复现原式（同样做了除以 C_T 的归一化）。
2. S_E 在 ``H == L``（一字板）时原式 (C-L)/(H-L+eps) = 0，会把最强逼空
   判成最弱；这里特判为「C ≥ 前收 → 1.0，否则 0.0」。

所有量在 T 日只用 ≤ T 的数据；入场按 T+1 开盘，与 coil 口径一致。
"""
import numpy as np
import pandas as pd


# ═════════════════════════════════════════════════════════════════════════
# 滚动 OLS 斜率（O(n)，避免 rolling.apply 的逐窗 Python 调用）
# ═════════════════════════════════════════════════════════════════════════
def ols_slope(y, win):
    """y 对窗内序号 j=0..win-1 回归的斜率（单位：y/日）。

    窗口不足或含 NaN → NaN。用 Σj·y 的滚动和实现，复杂度 O(n)。
    """
    y = np.asarray(y, dtype=np.float64)
    n = y.size
    out = np.full(n, np.nan)
    win = int(win)
    if win < 2 or n < win:
        return out
    s = pd.Series(y)
    sy = s.rolling(win).sum().to_numpy()
    i = np.arange(n, dtype=np.float64)
    syi = pd.Series(i * y).rolling(win).sum().to_numpy()

    j = np.arange(win, dtype=np.float64)
    Sx = j.sum()
    Sxx = (j * j).sum()
    den = Sxx - Sx * Sx / win
    start = i - win + 1.0
    s1 = syi - start * sy                     # Σ j·y
    with np.errstate(invalid='ignore', divide='ignore'):
        out = (s1 - Sx * sy / win) / den
    return out


# ═════════════════════════════════════════════════════════════════════════
# 单只股票的 5 维形态特征
# ═════════════════════════════════════════════════════════════════════════
def shape_features(close, open_, high, low, volume,
                   n=5, m=20, eps=1e-9, accel_mode='slope'):
    """返回 dict[str, np.ndarray]，长度与输入一致，预热期为 NaN。"""
    c = np.asarray(close, dtype=np.float64)
    o = np.asarray(open_, dtype=np.float64)
    h = np.asarray(high, dtype=np.float64)
    l = np.asarray(low, dtype=np.float64)
    v = np.asarray(volume, dtype=np.float64)
    n, m = int(n), int(m)
    if m <= n:
        raise ValueError(f'm 必须大于 n（当前 n={n}, m={m}）')

    cs = pd.Series(c)

    # ── S_A 突破强度：前 M 日最高价（不含当日）─────────────────────────
    ref_high = pd.Series(h).rolling(m).max().shift(1).to_numpy()
    with np.errstate(invalid='ignore', divide='ignore'):
        s_a = c / ref_high - 1.0

    # ── S_B 加速度 ──────────────────────────────────────────────────
    if str(accel_mode).lower() == 'diff2':
        d2 = c - 2.0 * cs.shift(1).to_numpy() + cs.shift(2).to_numpy()
        with np.errstate(invalid='ignore', divide='ignore'):
            s_b = pd.Series(d2 / c).rolling(n).mean().to_numpy()
        slope_n = np.full(c.size, np.nan)
        slope_base = np.full(c.size, np.nan)
    else:
        base_win = m - n + 1                     # 与 [T-M, T-N] 等长
        slope_n = ols_slope(c, n)
        slope_base = pd.Series(ols_slope(c, base_win)).shift(n).to_numpy()
        with np.errstate(invalid='ignore', divide='ignore'):
            s_b = (slope_n - slope_base) / c
        d2 = np.full(c.size, np.nan)

    # ── S_C 实体膨胀（分子分母不重叠）────────────────────────────────
    body = np.abs(c - o)
    body_n = pd.Series(body).rolling(n).mean().to_numpy()
    body_base = pd.Series(body).rolling(m - n + 1).mean().shift(n).to_numpy()
    with np.errstate(invalid='ignore', divide='ignore'):
        s_c = np.where(body_base > eps, body_n / body_base, np.nan)

    # ── S_D 阳线密度 ────────────────────────────────────────────────
    valid = np.isfinite(c) & np.isfinite(o)
    bull = np.where(valid, (c > o).astype(np.float64), np.nan)
    s_d = pd.Series(bull).rolling(n).mean().to_numpy()

    # ── S_E 逼空强度（一字板特判）────────────────────────────────────
    prev = cs.shift(1).to_numpy()
    rng = h - l
    flat = rng <= eps
    with np.errstate(invalid='ignore', divide='ignore'):
        pos = (c - l) / rng
    pos = np.where(flat, np.where(c >= prev, 1.0, 0.0), pos)
    pos = np.where(np.isfinite(c) & np.isfinite(h) & np.isfinite(l)
                   & np.isfinite(prev), pos, np.nan)
    s_e = pd.Series(pos).rolling(n).mean().to_numpy()

    # ── 辅助（仅用于展示/验证，不直接进评分）─────────────────────────
    vol_mult = (v / pd.Series(v).rolling(20).mean().shift(1)).to_numpy()

    return {
        'S_A': s_a, 'S_B': s_b, 'S_C': s_c, 'S_D': s_d, 'S_E': s_e,
        'ref_high': ref_high, 'slope_n': slope_n,
        'slope_base': slope_base, 'accel_diff2': d2,
        'body_n': body_n, 'body_base': body_base, 'pos': pos,
        'vol_mult': vol_mult,
    }


# ═════════════════════════════════════════════════════════════════════════
# 归一化
# ═════════════════════════════════════════════════════════════════════════
def norm01(x, lo, hi):
    """线性映射到 [0,1]，超界截断；NaN 保持 NaN。"""
    x = np.asarray(x, dtype=np.float64)
    lo, hi = float(lo), float(hi)
    if not np.isfinite(lo) or not np.isfinite(hi) or hi == lo:
        return np.full(x.shape, np.nan)
    with np.errstate(invalid='ignore'):
        z = (x - lo) / (hi - lo)
    return np.clip(z, 0.0, 1.0)
