# -*- coding: utf-8 -*-
"""
==============================================================================
mlr.macd —— MACD 指标与交叉判定（口径与 ai_backtest_model 逐位一致）
==============================================================================
EMA 递推实现直接复用 ABM `backtest_model.factors.indicators.calc_ema`
（`ema[0]=x[0]`，alpha=2/(period+1)），保证与 ABM 三因子模型 / quantTrade
「慢速MACD」分析器完全同源。
"""
import numpy as np

from . import settings as S

# 复用数据源工程的 EMA（逐位一致）；失败则用本文件同实现兜底
try:
    from backtest_model.factors.indicators import calc_ema as _abm_ema
    calc_ema = _abm_ema
except Exception:                                   # noqa: BLE001
    def calc_ema(prices, period):
        prices = np.asarray(prices, dtype=np.float64)
        if prices.size == 0:
            return prices
        alpha = 2.0 / (period + 1)
        ema = np.empty_like(prices)
        ema[0] = prices[0]
        for i in range(1, prices.size):
            ema[i] = prices[i] * alpha + ema[i - 1] * (1 - alpha)
        return ema


def macd(close, fast=30, slow=89, signal=9, warmup_mult=0.0):
    """返回 (dif, dea, hist)。数据不足时返回全 NaN。

    warmup_mult>0 时，把前 int(slow*warmup_mult) 根置 NaN：EMA(y0=x0) 递推
    使慢线长期带着序列首价的权重，在收敛前（残差≈(1-α)^n）指标值不可信，
    交叉/形态判定会大量失真。默认 0 = 不掩码（与 ABM 老口径逐位一致）；
    经验值 2.0 → 残差 < 2.3%（如月线 12/26/9 → 52 根、周线 89 → 178 根）。
    """
    close = np.asarray(close, dtype=np.float64)
    n = close.size
    if n < slow + signal:
        nan = np.full(n, np.nan)
        return nan, nan, nan
    dif = calc_ema(close, fast) - calc_ema(close, slow)
    dea = calc_ema(dif, signal)
    hist = (dif - dea) * 2.0
    m = int(round(slow * float(warmup_mult))) if warmup_mult else 0
    if m >= n:                       # 整条序列都不够暖机 → 全部不可判
        nan = np.full(n, np.nan)
        return nan, nan, nan
    if 0 < m < n:
        nan = np.full(m, np.nan)
        dif[:m], dea[:m], hist[:m] = nan, nan, nan
    return dif, dea, hist


def cross_flags(dif, dea):
    """交叉标记数组：+1 = DIF 上穿 DEA（金叉），-1 = 下穿（死叉），0 = 无。"""
    n = len(dif)
    x = np.zeros(n, dtype=np.int8)
    if n < 2:
        return x
    a, b = dif[1:], dea[1:]
    pa, pb = dif[:-1], dea[:-1]
    x[1:] = np.where((a > b) & (pa <= pb), 1,
                     np.where((a < b) & (pa >= pb), -1, 0))
    return x
