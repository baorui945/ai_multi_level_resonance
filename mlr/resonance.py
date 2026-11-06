# -*- coding: utf-8 -*-
"""
==============================================================================
mlr.resonance —— 多周期「向上抛物线」判定 + 周月/日周月分层共振
==============================================================================

两种单周期判定方法（`timeframes.<tf>.method`）
----------------------------------------------
**A. hole_cross（「挖坑→起爆」，日线默认）**

在一根已成立的K线上，其 MACD(30,89,9) 满足以下**全部**条件即视为「上抛中」：

1. **金叉在有效期内**：最近一次 DIF 上穿 DEA（金叉）距当前不超过
   `max_cross_age` 根K线（日线宽、月线窄）。
2. **金叉后未死叉**：当前 DIF > DEA 且 MACD 红柱 hist > 0。
3. **确实挖过坑**：金叉前的「死叉→金叉」下跌段（绿柱段）内出现过足够深的
   绿柱（|谷值| / 价格 ≥ hole_min_depth_ratio），即先有红柱 → 绿柱坑 → 再金叉。
4. **坑前有红柱**：死叉之前 `hole_red_window` 根内出现过红柱（排除首次、
   非典型金叉）。
5. **金叉后净上行**：DIF、DEA 均高于金叉当日。
6. **动能未衰竭**（可选）：当日红柱 ≥ `momentum_retreat` × 金叉后红柱峰值
   （默认 0 = 关闭）。
7. **趋势过滤**（可选）：收盘价 > MA(long)，且 MA(short) > MA(long)。

**B. parabola（「向上抛物线」形态，周/月线默认）**

共振的本质是**同一抛物线结构在不同周期上的自相似**：周线与月线都沿 MA7
向上弯曲、MACD 红柱持续放大。判据只看 **DIF/DEA 曲线本身的几何形态**，
**不看金叉死叉**（金叉只是抛物线早期的一种副产品，零轴上方二次上抛时根本没有金叉）：

把 DIF 最近 `fit_window` 根做二次拟合

    y = a·x² + b·x + c        x 以窗口中点为中心，x ∈ [-(L-1)/2, +(L-1)/2]

要求（全部满足即「上抛中」）：

1. **开口向上**：`a > 0`；
2. **仍在上升支**：末端斜率 `2a·x_end + b > 0`，且顶点 `x_v = -b/(2a)` 位于窗口左侧
   （右侧已是上升段，而不是还在下跌或刚见顶）；
3. **拟合可信**：`r² ≥ min_r2`，相对抬升 `rel = a·x_end² / |DIF|尺度 ≥ min_rel_curvature`
   （`rel` 无量纲，高低价股、不同周期通用）；
4. **净上行且加速**：`dif[t] > dif[t-L+1]`，且后半段**每根平均涨幅** > 前半段
   （`require_steepen`；奇偶窗的左右半段根数不同，须按每根比较）；
5. **DEA 同步**：`dea[t] > dea[t-slope_bars]`（双线一起抬，不是 DIF 单线假突破）；
6. **红柱正强化**：`hist` 连续 `hist_grow_bars` 根逐根放大；
7. 可选：收盘 > MA7（`require_above_ma_short`）、MA7 上行（`require_ma_short_slope`），默认关。

与 hole_cross 的区别：**不要求挖坑、不要求金叉、不要求 MA7 > MA21、不要求收盘 > MA21**。
抛物线刚起步时 MA7 常仍在 MA21 下方，若等均线多头排列才确认，会漏掉起爆段
（600188 2021-02 即如此；月线在 2021-01~02 只有 0.003 的浅死叉，`hole_cross` 永远判不出来）。

指标暖机与分周期 MACD 参数
--------------------------
* `timeframes.<tf>.warmup_mult`：MACD 前 int(slow×mult) 根置 NaN（EMA(y0=x0)
  未收敛，残差≈(1−α)^n）。默认全部 2.0（周线 89+9 → 178 周、日线 178 天、
  月线 12/26/9 → 52 月，残差均 <2.3%）——否则月线只有 ~134 根，
  MACD(30,89,9) 的 EMA89 根本没收敛，同一只股票换数据起点月线状态会翻转；
* `timeframes.<tf>.macd`：分周期覆盖全局 `macd` 参数。月线太短，专用
  MACD(12,26,9)；30/89/9 在月线上需 178 个月 > 全史，永远处于半收敛态。

多周期分层
----------
周线 / 月线状态以 **period_date**（该周期最后一个真实交易日）为界，用
`merge_asof(backward)` 对齐到日线日历（绝不引用未收盘的周期）。

**周期完整性（`data.period_mode`，默认 closed）**：`data.build_timeframes` 给每根
周/月 K 打 `is_complete`（`period_end <= 数据末日`）；`closed` 时本模块在
`merge_asof` 前丢弃未完成周期，使实盘扫描与回测逐日一致（实测 0/2920 股票-日
差异）。`asof` 保留旧的「进行中周期」行为，只适合人工观察。

```
watch        = up_weekly  & ~up_monthly   # 周线先上抛 → 候选池（等月线确认）
wm_resonance = up_weekly  &  up_monthly   # 周月共振（月线滞后确认）
resonance    = up_daily   &  wm_resonance  # 三周期共振
entry        = resonance 首次 True 当日    # 日线买点（默认信号）
wm_entry     = wm_resonance 首次 True 当日 # 周月共振首日（不等日线）
```

`week_turn_date` / `month_turn_date` 记录本次上抛的起点，`w2m_weeks` 记录
「周线领先月线」的周数，用于衡量月线滞后确认的时滞。

**上抛过程（合并口径）**：月线一次上抛尚未实质衰竭（间隙内月线 hist 未回到
0 下、且未自峰值回撤 ≥ `signal.wm_process_drawdown`，默认 0.10）时，周线中途
退出/二次上抛只算同一过程——`wm_process_start` / `wm_process_end` 给出
过程级的时间点；`wm_entry` / `wm_first` 信号口径不受影响。

**日周共振（dw，同构下沉一层）**：把上面整套结构原样下沉一个周期——
先行腿 = 日线（`hole_cross` 或 `parabola` 均可，实测 hole_cross 更优）、
确认腿 = 周线（参数原样不动）；`dw_watch` / `dw_resonance` / `dw_entry`
与 `day_turn_date` / `d2w_days`（日线领先周线天数）逐项对应周月版字段；
过程合并 `dw_process_start` / `dw_process_end` 用**周线** hist 判重置
（同一 `wm_process_drawdown` 口径，共用 `_process_flags` 实现）。

`signal.dw_max_lead_days` 可按 `d2w_days` 过滤 dw 入场：hole_cross 日线保持
None（不过滤，实测 20.03% / MDD −26.2%），parabola 日线设 0（20.05% vs 18.58%）。

**分段启用（phase_wm / phase_entry）**：月线用 30/89/9 + `warmup_mult=1.0`
时，2015-09 起要到 2023-02 才有 89 根月线（`monthly_ready`），此前
`up_monthly` 恒 False，wm/三周期不可能出信号。`phase_wm` 在 ready 前用
`dw_entry`（日+周）、之后用 `wm_ready_entry`（周+月）；`phase_entry` 后半段
用 `entry_ready`（三周期）。切换按**每只股票**的月线根数逐只判定，不是全局
固定日期。`wm_ready_entry` / `entry_ready` 是"月线可用后首次进入对应共振"，
避免月线刚可用时 `wm_entry` 已错过首日。

**均线过滤的分工**：全局 `filters.require_above_ma` / `require_ma_align` /
`require_no_bearish_div` **只作用于 hole_cross**；parabola 腿用
`timeframes.<tf>.parabola.require_above_ma_long` / `require_ma_align` /
`require_above_ma_short` / `require_ma_short_slope`（默认全关），避免"开了
全局过滤但该腿静默忽略"。
"""
import numpy as np
import pandas as pd

from . import settings as S
from .macd import macd as _macd, cross_flags


# ═════════════════════════════════════════════════════════════════════════
# 单周期判定
# ═════════════════════════════════════════════════════════════════════════
def _indicator_block(close, macd_p, ma_p, warmup_mult=0.0):
    dif, dea, hist = _macd(close, macd_p[0], macd_p[1], macd_p[2],
                           warmup_mult=warmup_mult)
    s = pd.Series(close)
    ma_s = s.rolling(ma_p[0], min_periods=1).mean().to_numpy('float64')
    ma_l = s.rolling(ma_p[1], min_periods=1).mean().to_numpy('float64')
    return dif, dea, hist, ma_s, ma_l


def _bearish_div(close, hist, t, W=60):
    """顶背离：近期价创新高但红柱小于前高（与 ABM F3-5 同口径，简化版）。"""
    lo = max(0, t - W)
    if t - lo < 10:
        return False
    sc, sh = close[lo:t + 1], hist[lo:t + 1]
    imax = int(np.argmax(sc))
    if imax < len(sc) - 5:
        return False
    excl = set(range(max(0, imax - 2), min(len(sc), imax + 3)))
    best_i, best_c = -1, -np.inf
    for i in range(len(sc)):
        if i in excl:
            continue
        if sc[i] > best_c:
            best_c, best_i = sc[i], i
    if best_i < 0:
        return False
    return sh[imax] < sh[best_i]


def detect_arrays(close, macd_p, ma_p, tf_cfg, filt, warmup_mult=0.0):
    """对单只股票的单周期收盘序列做「上抛」判定。

    Returns: dict(dif, dea, hist, ma_short, ma_long, upthrow, cross)
    """
    close = np.asarray(close, dtype=np.float64)
    n = close.size
    dif, dea, hist, ma_s, ma_l = _indicator_block(close, macd_p, ma_p,
                                                  warmup_mult)
    x = cross_flags(dif, dea)
    up = np.zeros(n, dtype=bool)
    bars_since = np.full(n, 10 ** 9, dtype=np.int64)

    max_age = int(tf_cfg.get('max_cross_age', 120))
    red_win = int(tf_cfg.get('hole_red_window', 12))
    hole_ratio = float(tf_cfg.get('hole_min_depth_ratio', 0.005))
    retreat = float(tf_cfg.get('momentum_retreat', 0.0))
    req_above = bool(filt.get('require_above_ma', True))
    req_align = bool(filt.get('require_ma_align', True))
    req_div = bool(filt.get('require_no_bearish_div', False))

    g = d = -1            # 最近金叉 / 死叉
    hole_ok = False       # 当前金叉段的「挖坑」是否成立
    peak = 0.0            # 金叉以来 hist 峰值
    for t in range(n):
        if x[t] == 1:
            g = t
            peak = 0.0
            hole_ok = _hole_ok(hist, close, d, g, red_win, hole_ratio)
        elif x[t] == -1:
            d = t
        if g >= 0:
            bars_since[t] = t - g
        h = hist[t]
        if np.isfinite(h) and h > peak:
            peak = h
        if g < 0 or d < 0 or d >= g or not hole_ok:
            continue
        if t - g > max_age:
            continue
        if not (dif[t] > dea[t] and h > 0):
            continue
        if not (dif[t] > dif[g] and dea[t] > dea[g]):
            continue
        if retreat > 0 and peak > 0 and h < retreat * peak:
            continue
        if req_above and not (np.isfinite(ma_l[t]) and close[t] > ma_l[t]):
            continue
        if req_align and not (np.isfinite(ma_s[t]) and ma_s[t] > ma_l[t]):
            continue
        if req_div and _bearish_div(close, hist, t):
            continue
        up[t] = True

    return {'dif': dif, 'dea': dea, 'hist': hist, 'ma_short': ma_s,
            'ma_long': ma_l, 'upthrow': up, 'cross': x,
            'bars_since_cross': bars_since}


def _hole_ok(hist, close, d, g, red_win, hole_ratio):
    """金叉 g 之前的下跌段 [d, g] 是否构成有效「坑」（深度按价格归一，尺度不变）。"""
    if d < 0 or g <= d:
        return False
    seg = hist[d:g + 1]
    fin = np.isfinite(seg)
    if fin.sum() < 3:
        return False
    seg = seg[fin]
    hmin = float(seg.min())
    if hmin >= 0:
        return False
    # 用谷值当根的价格归一（MACD 对价格线性 ⇒ 比值尺度不变）
    arms = np.flatnonzero(fin)
    i_min = arms[int(np.argmin(seg))]
    px = close[d + i_min]
    if not np.isfinite(px) or px <= 0:
        return False
    if abs(hmin) / px < hole_ratio:
        return False
    # 坑前应有红柱段（排除首次、非典型金叉）
    lo = max(0, d - red_win)
    red = hist[lo:d + 1]
    return bool(np.any(np.isfinite(red) & (red > 0)))


# 「向上抛物线」默认参数（可被 config 的 parabola 段覆盖）
# 判据不是事件（金叉/死叉），而是**曲线几何**：把 DIF 最近 L 根做二次拟合
#     y = a·x² + b·x + c        （x 以窗口中点为中心，x∈[-(L-1)/2, +(L-1)/2]）
# 要求 a>0（开口向上）、末端斜率 2a·x_end+b>0（仍在抬升）、顶点 x_v=-b/(2a)
# 落在窗口左侧（已过拐点、处在上升支）、拟合优度 r² 与相对抬升 rel 达标；
# DEA 同步上行、红柱连续放大 ⇒ 双线张口 + 红柱强化的“上抛”。
PARABOLA_DEF = {
    'fit_window': 6,             # 二次拟合窗口根数（周线 6、月线 4，由 config 覆盖）
    'min_r2': 0.5,               # 拟合优度下限
    'min_rel_curvature': 0.05,   # a·x_end² / |DIF|稳健尺度（无量纲，尺度不变）
    'require_net_up': True,      # 窗口末端 > 起点（净上行）
    'require_steepen': True,     # 后半段涨幅 > 前半段（确实在加速）
    'require_dea_up': True,      # DEA 同步上行
    'hist_grow_bars': 2,         # 红柱连续放大根数（正强化）
    'require_hist_positive': True,   # 红柱为正（DIF 在 DEA 上方，属“状态”非“金叉事件”）
    'slope_bars': 2,             # DEA / MA 斜率比较回看根数
    'require_above_ma_short': False,   # 收盘 > MA7（可选）
    'require_ma_short_slope': False,   # MA7 上行（可选）
    # 与 hole_cross 的全局 filters 对应，但默认关（抛物线刚起步时 MA7 常仍在
    # MA21 下方，等均线多头排列会漏掉起爆段）。全局 filters 不会作用于本腿。
    'require_above_ma_long': False,    # 收盘 > MA(long)
    'require_ma_align': False,         # MA(short) > MA(long)
}


def _parabola_stats(v, L):
    """对 v 的每个长度 L 窗口做二次拟合，返回向量化结果。

    用闭式解（x 关于窗口中点对称 ⇒ Σx=0、Σx³=0），避免逐窗 polyfit：
        a = (L·Σx²y − Σx²·Σy) / (L·Σx⁴ − (Σx²)²)
        b = Σxy / Σx²
        c = (Σy − a·Σx²) / L
    返回 (w, a, b, r2, slope_end, x_vertex, rel)：
        w          窗口矩阵 (n-L+1, L)，w[:, -1] 为当前根
        slope_end  末端斜率 dy/dx
        x_vertex   顶点横坐标（对称坐标，端点 = ±(L-1)/2）
        r2         1 − SSE/SST
        rel        a·x_end²（窗口中心→末端的曲率抬升）/ 稳健尺度（无量纲，
                   可跨股比较）。注意顶点 x_v 可能在窗口外，故用曲率式而非
                   "顶点→末端的实际抬升"
    """
    w = np.lib.stride_tricks.sliding_window_view(
        np.asarray(v, dtype=np.float64), L)
    x = np.arange(L, dtype=np.float64) - (L - 1) / 2.0
    S2 = float((x ** 2).sum())
    S4 = float((x ** 4).sum())
    Sy = w.sum(1)
    Sxy = w @ x
    Sx2y = w @ (x * x)
    Sy2 = (w * w).sum(1)
    den = L * S4 - S2 * S2
    a = (L * Sx2y - S2 * Sy) / den
    b = Sxy / S2
    c = (Sy - a * S2) / L
    mean = Sy / L
    fit2 = c * c * L + b * b * S2 + a * a * S4 + 2.0 * a * c * S2
    sse = np.maximum(Sy2 - fit2, 0.0)
    sst = np.maximum(Sy2 - Sy * mean, 1e-12)
    r2 = 1.0 - sse / sst
    x_end = x[-1]
    slope_end = 2.0 * a * x_end + b
    x_vertex = np.where(a > 0, -b / (2.0 * a), np.inf)
    scale = np.maximum(np.abs(mean), 0.5 * np.abs(w).max(1))
    rel = a * x_end * x_end / np.maximum(scale, 1e-9)
    return w, a, b, r2, slope_end, x_vertex, rel


def _parabola_up(dif, dea, hist, ma_s, ma_l, close, pcfg):
    """纯几何「上抛」判定：DIF 抛物线开口向上且仍在上升支，DEA 同步、红柱放大。

    完全不看金叉死叉，因此 **高位零轴上方的二次上抛** 与 **零轴附近的初次
    上抛** 用同一套公式（自相似），这也是周/月可共用同一参数框架的原因。

    require_steepen：后半段涨幅 > 前半段，按**每根平均涨幅**比较（左/右半段
    的区间根数分别为 mid 与 L-1-mid，L 为偶数时不同，直接比涨幅会偏向右半段
    短的窗口——原实现 L=4 时"3 根 vs 1 根"因此失真）。
    """
    n = dif.size
    L = int(pcfg.get('fit_window', 6))
    if L < 4:
        raise ValueError(f'parabola.fit_window 必须 ≥4（该周期 {L} 根无法判'
                         f'凹凸性），想在配置里用更小的窗口不合逻辑')
    out = np.zeros(n, dtype=bool)
    if n < L:
        return out

    w, a, b, r2, slope_end, x_vertex, rel = _parabola_stats(dif, L)
    mid = L // 2
    left_span = mid
    right_span = L - 1 - mid
    x_end = (L - 1) / 2.0

    ok = (np.isfinite(a) & np.isfinite(r2)
          & (a > 0)                                  # 开口向上
          & (slope_end > 0)                          # 末端仍在抬升
          & (x_vertex < x_end)                       # 顶点在左，处于上升支
          & (r2 >= float(pcfg.get('min_r2', 0.5)))
          & (rel >= float(pcfg.get('min_rel_curvature', 0.05))))
    if pcfg.get('require_net_up', True):
        ok &= w[:, -1] > w[:, 0]
    if pcfg.get('require_steepen', True):
        ok &= ((w[:, -1] - w[:, mid]) / right_span
               > (w[:, mid] - w[:, 0]) / left_span)

    # DEA / MA / 红柱条件按 t 对齐（长度 n），最后与 ok 的尾部对齐
    gate = np.ones(n, dtype=bool)
    k = max(1, int(pcfg.get('slope_bars', 2)))
    if pcfg.get('require_dea_up', True):
        gate[k:] &= dea[k:] > dea[:-k]
    grow = max(0, int(pcfg.get('hist_grow_bars', 2)))
    for j in range(1, grow + 1):
        gate[j:] &= hist[j:] > hist[:-j]
    if pcfg.get('require_hist_positive', True):
        gate &= hist > 0
    if pcfg.get('require_above_ma_short', False):
        gate &= np.isfinite(ma_s) & (close > ma_s)
    if pcfg.get('require_ma_short_slope', False):
        gate[k:] &= ma_s[k:] > ma_s[:-k]
    # 与 hole_cross 的全局 filters 对应（默认关；全局 filters 不作用于本腿）
    if pcfg.get('require_above_ma_long', False):
        gate &= np.isfinite(ma_l) & (close > ma_l)
    if pcfg.get('require_ma_align', False):
        gate &= (np.isfinite(ma_s) & np.isfinite(ma_l) & (ma_s > ma_l))

    out[L - 1:] = ok & gate[L - 1:]
    return out


def parabola_arrays(close, macd_p, ma_p, tf_cfg, filt=None, warmup_mult=0.0):
    """「向上抛物线」形态判定（周/月线默认，可被 config 切到任意周期）。

    与 `detect_arrays` 返回同结构（并接受相同的 warmup_mult），便于
    `detect_frame` 统一分派。
    判据见模块 docstring §B 与 `_parabola_up`：只判 DIF/DEA 曲线的几何形态，
    不要求金叉、不要求挖坑、不比均线排列。
    """
    close = np.asarray(close, dtype=np.float64)
    n = close.size
    dif, dea, hist, ma_s, ma_l = _indicator_block(close, macd_p, ma_p,
                                                  warmup_mult)
    x = cross_flags(dif, dea)

    pcfg = dict(PARABOLA_DEF)
    pcfg.update(tf_cfg.get('parabola') or {})
    up = _parabola_up(dif, dea, hist, ma_s, ma_l, close, pcfg)

    # 距离最近金叉的根数（仅作辅助列，供 fresh_daily 模式使用；不参与判定）
    idx = np.where(x == 1, np.arange(n), -1)
    last = np.maximum.accumulate(idx)
    bars_since = np.where(last >= 0, np.arange(n) - last, 10 ** 9)

    return {'dif': dif, 'dea': dea, 'hist': hist, 'ma_short': ma_s,
            'ma_long': ma_l, 'upthrow': up, 'cross': x,
            'bars_since_cross': bars_since.astype(np.int64)}


def detect_frame(tf_df, tf_name, cfg):
    """对某周期长表逐只股票做「上抛」判定，返回带指标/upthrow 列的长表。

    每周期可有独立 MACD 参数与暖机：`timeframes.<tf>.macd`（缺省继承全局
    `macd`）、`timeframes.<tf>.warmup_mult`。
    按 `cfg['timeframes'][tf_name]['method']` 分派：
    `hole_cross` → `detect_arrays`（日线默认）；`parabola` → `parabola_arrays`（周/月默认）。
    """
    tcfg = cfg['timeframes'].get(tf_name, {})
    tm = tcfg.get('macd') or cfg['macd']
    macd_p = (int(tm['fast']), int(tm['slow']), int(tm['signal']))
    warmup = float(tcfg.get('warmup_mult', 0.0))
    ma_p = (cfg['ma']['short'], cfg['ma']['long'])
    filt = cfg['filters']
    cols = ['dif', 'dea', 'hist', 'ma_short', 'ma_long', 'upthrow', 'cross',
            'bars_since_cross']
    method = str(tcfg.get('method', 'hole_cross')).lower()
    fn = parabola_arrays if method == 'parabola' else detect_arrays
    out = []
    for code, g in tf_df.groupby('stock_code', sort=False):
        g = g.sort_values('trade_date')
        res = fn(g['close_price'].to_numpy('float64'),
                 macd_p, ma_p, tcfg, filt, warmup_mult=warmup)
        gg = g.copy()
        for c in cols:
            gg[c] = res[c]
        out.append(gg)
    if not out:
        return tf_df.assign(**{c: pd.Series(dtype='float64') for c in cols})
    return pd.concat(out, ignore_index=True)


# ═════════════════════════════════════════════════════════════════════════
# 多周期共振
# ═════════════════════════════════════════════════════════════════════════
def build_resonance(tfs, cfg=None):
    """输入 `data.build_timeframes` 的三周期长表，输出日频共振面板。

    Returns:
        DataFrame（每只股票每个交易日一行）：
        stock_code, stock_name, trade_date, close_price,
        dif/dea/hist（日线）、up_daily, up_weekly, up_monthly,
        resonance（三周期同时上抛）, entry（共振首日，False→True）
    """
    cfg = cfg or S.load_config()
    # 最少日线根数：按**截至当日**的累计根数逐行屏蔽（不是整只剔除），
    # 这样 scan 在任意历史日重算与全历史回测的股票集合一致。
    minb = int((cfg.get('data') or {}).get('min_daily_bars', 0) or 0)

    d = detect_frame(tfs['daily'], 'daily', cfg).rename(
        columns={c: c for c in ('dif', 'dea', 'hist', 'ma_short', 'ma_long')})
    if minb:
        d = d.sort_values(['stock_code', 'trade_date'])
        d['_bar_no'] = d.groupby('stock_code').cumcount() + 1
    w = detect_frame(tfs['weekly'], 'weekly', cfg)
    m = detect_frame(tfs['monthly'], 'monthly', cfg)

    # 周期完整性：closed 口径下，用**名义周期末**（周五 / 自然月末）作为对齐键，
    # 只有 period_end <= 当日，该周期 K 才算已收盘。这样规则是逐日求值的，
    # 全历史回测与任意历史日重算（scan）完全一致；月末/长假最多滞后 1 个交易日。
    period_mode = str((cfg.get('data') or {}).get('period_mode',
                                                  'closed')).lower()
    if period_mode == 'closed':
        if 'period_end' in w.columns:
            w = w.assign(trade_date=pd.to_datetime(w['period_end']))
        if 'period_end' in m.columns:
            m = m.assign(trade_date=pd.to_datetime(m['period_end']))

    d = d.rename(columns={'upthrow': 'up_daily',
                          'ma_short': 'ma_short_d', 'ma_long': 'ma_long_d'})
    # 月线「暖机完成」标记：累计可用月线根数 > int(slow×warmup_mult)。
    # 月线用 30/89/9 时该点为 2022-02（2015-09 起 178 个月），此前的月线
    # 状态全 NaN（up_monthly=False），需要靠它区分「不可用」与「可用但未上抛」。
    tmc = cfg['timeframes'].get('monthly') or {}
    pm = tmc.get('macd') or cfg['macd']
    mwarm = int(round(float(pm['slow']) * float(tmc.get('warmup_mult', 0.0))))
    m = m.sort_values(['stock_code', 'trade_date']).copy()
    m['monthly_ready'] = ((m.groupby('stock_code').cumcount() + 1) > mwarm)

    w = w[['stock_code', 'trade_date', 'upthrow', 'hist']].rename(
        columns={'upthrow': 'up_weekly', 'hist': 'hist_weekly'})
    m = m[['stock_code', 'trade_date', 'upthrow', 'hist', 'monthly_ready']].rename(
        columns={'upthrow': 'up_monthly', 'hist': 'hist_monthly'})

    # pandas>=3.0 的 merge_asof 要求 on 键**全局**有序（by 分组再各自匹配）
    d = d.sort_values(['trade_date', 'stock_code'])
    w = w.sort_values(['trade_date', 'stock_code'])
    m = m.sort_values(['trade_date', 'stock_code'])

    # 周期状态按 period_date 向后对齐到日线（asof backward，无未来函数）
    out = pd.merge_asof(d, w, on='trade_date', by='stock_code',
                        direction='backward')
    out = pd.merge_asof(out, m, on='trade_date', by='stock_code',
                        direction='backward')
    out['up_weekly'] = out['up_weekly'].fillna(False).astype(bool)
    out['up_monthly'] = out['up_monthly'].fillna(False).astype(bool)
    out['monthly_ready'] = out['monthly_ready'].fillna(False).astype(bool)

    if cfg['timeframes'].get('daily', {}).get('enabled', True) is False:
        out['up_daily'] = True
    if cfg['timeframes'].get('weekly', {}).get('enabled', True) is False:
        out['up_weekly'] = True
    if cfg['timeframes'].get('monthly', {}).get('enabled', True) is False:
        out['up_monthly'] = True

    # 次新股屏蔽：截至当日日线根数不足 min_daily_bars 的行，三周期状态一律 False
    if minb and '_bar_no' in out.columns:
        short = out['_bar_no'].to_numpy() < minb
        for c in ('up_daily', 'up_weekly', 'up_monthly'):
            out.loc[short, c] = False
        out = out.drop(columns=['_bar_no'])

    # ── 分层：候选池 → 周月共振 → 三周期买点 ──────────────────────────
    # watch        : 周线已上抛、月线尚未上抛（周线先动 → 候选池）
    # wm_resonance : 周线 ∧ 月线同时上抛（月线滞后确认）
    # resonance    : 日线 ∧ 周线 ∧ 月线（三周期共振）
    out['wm_resonance'] = out['up_weekly'] & out['up_monthly']
    out['watch'] = out['up_weekly'] & ~out['up_monthly']
    out['resonance'] = out['up_daily'] & out['wm_resonance']
    # ── 日周共振层（同一结构下沉一个周期：日线先动、周线滞后确认）────────
    out['dw_resonance'] = out['up_daily'] & out['up_weekly']
    out['dw_watch'] = out['up_daily'] & ~out['up_weekly']
    out = out.sort_values(['stock_code', 'trade_date']).reset_index(drop=True)

    # 各状态「开启首日」（False→True）
    # 注意：bool 列 shift 后会变 object，必须先 astype(bool) 再取反；
    # 否则 ~False 得到 -1（truthy），entry 会退化成「共振中每一天」。
    def _turn(col):
        prev = (out.groupby('stock_code')[col].shift(1)
                .fillna(False).astype(bool))
        return out[col].astype(bool) & ~prev

    for tf in ('up_daily', 'up_weekly', 'up_monthly',
               'wm_resonance', 'watch', 'dw_resonance', 'dw_watch'):
        out[tf + '_turn'] = _turn(tf)

    out['entry'] = _turn('resonance')        # 三周期共振首日（默认信号）
    out['wm_entry'] = out['wm_resonance_turn']   # 周月共振首日（不等日线）
    out['watch_entry'] = out['watch_turn']       # 候选池新增首日
    out['dw_entry'] = out['dw_resonance_turn']   # 日周共振首日（周线确认）
    out['dw_watch_entry'] = out['dw_watch_turn']  # 日线先动候选新增首日

    # 本次上抛起点（最近一次 False→True），用于衡量「周线领先月线」的时滞
    for tf, name in (('up_weekly', 'week_turn_date'),
                     ('up_monthly', 'month_turn_date'),
                     ('up_daily', 'day_turn_date')):
        s = out['trade_date'].where(out[tf + '_turn'])
        out[name] = s.groupby(out['stock_code']).ffill()

    out['week_age_weeks'] = ((out['trade_date'] - out['week_turn_date'])
                             .dt.days / 7).round(1)
    out.loc[~out['up_weekly'], 'week_age_weeks'] = np.nan
    # 月线确认当日：周线已经领先了多少周（周先月后的时滞）
    out['w2m_weeks'] = out['week_age_weeks'].where(out['up_monthly_turn'])
    # 日周共振首日：日线已经领先了多少天（日先周后的时滞）
    out['day_age_days'] = ((out['trade_date'] - out['day_turn_date'])
                           .dt.days).where(out['up_daily'], np.nan)
    out['d2w_days'] = out['day_age_days'].where(out['dw_entry'])

    # dw 领先天数过滤：只做「周线已上抛、日线当日刚转」（d2w_days <= N）。
    # HS300 实测：d2w_days>0 的「日线先动、周线滞后确认」半边年化 10.86%、
    # 逐笔 1.80%、t 1.69；d2w_days==0 半边 18.79%、3.14%、t 2.96。
    lead_max = (cfg.get('signal') or {}).get('dw_max_lead_days')
    if lead_max is not None:
        out['dw_entry'] = (out['dw_entry']
                           & (out['d2w_days'] <= float(lead_max)))

    # 更严的入场模式（供对比）
    fresh = int(cfg.get('signal', {}).get('fresh_daily_days', 5))
    out['entry_fresh'] = out['resonance'] & (
        out['bars_since_cross'] <= fresh)              # 日线金叉后 N 根内
    out['entry_slowest'] = out['resonance'] & out['up_monthly_turn']  # 月线刚转多

    # ── 分段启用（月线暖机完成前只做日周，完成后做周月/三周期）─────────
    # 月线用 30/89/9 时，2015-09 起要到 2022-02 才有 178 根月线；此前
    # up_monthly 恒 False，不能用 wm/三周期。这里按**每只股票**的暖机完成日
    # 切换：未完成 → dw_entry；完成 → wm_ready_entry / entry_ready。
    def _turn_mask(mask):
        out['_tmp_mask'] = mask.astype(bool)
        prev = (out.groupby('stock_code')['_tmp_mask'].shift(1)
                .fillna(False).astype(bool))
        out.drop(columns=['_tmp_mask'], inplace=True)
        return mask.astype(bool) & ~prev

    wm_ready = out['wm_resonance'] & out['monthly_ready']
    entry_ready = out['resonance'] & out['monthly_ready']
    out['wm_ready_entry'] = _turn_mask(wm_ready)
    out['entry_ready'] = _turn_mask(entry_ready)
    out['phase_wm'] = ((out['dw_entry'] & ~out['monthly_ready'])
                       | out['wm_ready_entry'])
    out['phase_entry'] = ((out['dw_entry'] & ~out['monthly_ready'])
                          | out['entry_ready'])

    # ── 上抛过程级起点/终点（合并口径）───────────────────────────────
    # 同一确认腿上抛过程中，先行腿的二次/多次上抛不再另记起点；
    # 仅当确认腿红柱回 0 下、或自过程峰值回撤 ≥ *process_drawdown 后，
    # 才允许出现新的过程起点。周月版 / 日周版同构共用一套实现。
    dd = float(cfg.get('signal', {}).get('wm_process_drawdown', 0.10))
    out['wm_process_start'], out['wm_process_end'] = _process_flags(
        out, drawdown=dd, col_res='wm_resonance', col_hist='hist_monthly')
    out['dw_process_start'], out['dw_process_end'] = _process_flags(
        out, drawdown=dd, col_res='dw_resonance', col_hist='hist_weekly')
    return out


def _process_flags(out, drawdown=0.10,
                   col_res='wm_resonance', col_hist='hist_monthly'):
    """两腿共振的「上抛过程」起点/终点（逐只股票、合并口径）。

    定义：每段共振（`col_res` False→True）默认并入上一个过程；只有上一段
    结束之后，**确认腿**的 hist（`col_hist`，周月版=月线红柱、日周版=周线
    红柱）满足以下其一，下一个共振段首才算 **新过程起点**：
      1) 确认腿 hist 在间隙中回到 ≤0（动能线收力、确认转弱）；
      2) 或自间隙内运行的 hist 峰值回撤 ≥ drawdown（默认 0.10，
         红柱缩水到九折以内）。
    同一过程内的段只记第一个开始日；过程终点 = 该过程最后一段的
    退出首日（次日）。

    Returns:
        (start_flag, end_flag) —— 两个 bool 数组，长度同 `out`。
    """
    start_flag = np.zeros(len(out), dtype=bool)
    end_flag = np.zeros(len(out), dtype=bool)
    for _, g in out.groupby('stock_code', sort=False):
        pos = g.index.to_numpy()
        wr = g[col_res].to_numpy(bool)
        if not wr.any():
            continue
        h = g[col_hist].to_numpy(float)
        T = np.flatnonzero(wr)
        starts = T[np.r_[True, np.diff(T) > 1]]       # 每段段首索引
        ends = T[np.r_[np.diff(T) > 1, True]]         # 每段段尾索引
        new_process = np.zeros(len(starts), dtype=bool)
        for k in range(len(starts)):
            if k == 0:
                new_process[k] = True                 # 首段必为新过程
                continue
            seg = h[ends[k - 1] + 1: starts[k]]       # 间隙只看已收盘周期
            vals = seg[np.isfinite(seg)]
            if not vals.size:
                continue                              # 无确认腿信息（NaN）→ 不判重置
            if np.any(vals <= 0):
                new_process[k] = True
            else:
                peak = np.maximum.accumulate(vals)
                if np.any(vals < (1.0 - drawdown) * peak):
                    new_process[k] = True
        # 起点：各过程的第一段段首
        for k in range(len(starts)):
            if new_process[k]:
                start_flag[pos[starts[k]]] = True
        # 终点：每一过程的最后一个段段尾之后的首日（共振退出日）
        # 段 → 过程号：新过程首段自增；其后并入段沿用同一过程号
        phase_of = np.cumsum(new_process) - 1
        for p in range(int(phase_of.max()) + 1):
            members = np.flatnonzero(phase_of == p)
            e = ends[members[-1]]
            if e + 1 < len(wr):
                end_flag[pos[e + 1]] = True
    return start_flag, end_flag


ENTRY_COLS = {'any_entry': 'entry', 'fresh_daily': 'entry_fresh',
              'slowest_turn': 'entry_slowest', 'wm_first': 'wm_entry',
              'dw_first': 'dw_entry', 'dw_watch': 'dw_watch_entry',
              # 分段：月线暖机完成前用 dw，完成后用 wm / 三周期
              'phase_wm': 'phase_wm', 'phase_entry': 'phase_entry'}


def signals(resonance, start=None, mode='any_entry'):
    """从共振面板抽出信号（stock_code, stock_name, signal_date, close）。

    mode: any_entry（默认，共振首日）/ fresh_daily（日线金叉后 N 根内）/
          slowest_turn（月线刚转多当日共振）/ wm_first（周月共振首日）/
          dw_first（日周共振首日）/ dw_watch（日线先动候选新增首日）。
    """
    col = ENTRY_COLS.get(mode)
    if col is None:
        raise ValueError(f'未知 mode={mode!r}；可选：{sorted(ENTRY_COLS)}')
    df = resonance[resonance[col]].copy()
    if start:
        df = df[df['trade_date'] >= pd.Timestamp(start)]
    df = df.rename(columns={'trade_date': 'signal_date',
                            'close_price': 'signal_close'})
    return df[['stock_code', 'stock_name', 'signal_date', 'signal_close',
               'dif', 'dea', 'hist', 'bars_since_cross']].sort_values(
        ['signal_date', 'stock_code']).reset_index(drop=True)
