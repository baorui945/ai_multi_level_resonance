# -*- coding: utf-8 -*-
"""
==============================================================================
mlr.morph —— MACD 柱体「绿柱逐步缩小 → 转红 → 红柱放大」完整形态检测
==============================================================================
针对「挖坑→起爆」的**柱体变化过程**做形态匹配（对应标准模板 G3→G4→G5→G6）：

    G3 绿柱放大到谷 → G4 绿柱逐步缩小 → G5 金叉转红 → G6 红柱放大见顶

对每个金叉 g 检查：

  1. 金叉前有绿柱谷（死叉→金叉段内 hist 最小值 < 0，可选坑深约束）；
  2. 谷值之后绿柱「逐步缩小」（相邻 |hist| 递减的对数 >= shrink_bars，
     且谷值→金叉累计收缩比例 >= shrink_ratio）；
  3. 金叉日或其后 red_grow_bars 根内 hist 转红（>0）；
  4. 转红后红柱放大见顶（red_grow_bars 根内峰值 >= red_grow_ratio × 转红日）。

只用截止当前可用的数据，无未来函数。
"""
import numpy as np

from .macd import cross_flags


def green_to_red_morphs(dif, dea, hist, close=None,
                        hole_ratio=0.0,
                        shrink_bars=3,
                        shrink_ratio=0.5,
                        red_grow_bars=15,
                        red_grow_ratio=1.0):
    """检测「绿柱逐步缩小 → 转红 → 红柱放大」完整形态的起爆点。

    Args:
        dif/dea/hist: MACD 序列（np.ndarray）。
        close:        收盘价（可选，用于坑深归一；None 或 hole_ratio=0 则跳过）。
        hole_ratio:   坑深阈值 = |hist 谷| / close（0 = 不约束坑深）。
        shrink_bars:  谷值后绿柱「逐步缩小」的最少递减对数。
        shrink_ratio: 谷值 → 金叉 的绿柱累计收缩比例阈值（0~1）。
        red_grow_bars:金叉后寻找红柱见顶的窗口根数。
        red_grow_ratio:红柱峰值 / 转红日红柱 的最低放大倍数。

    Returns:
        list[dict]，每个 dict:
          cross_idx 金叉索引, pit_idx 绿柱谷索引, pit_hist 谷值,
          shrink_bars 递减对数, shrink_ratio 收缩比例,
          red_idx 转红索引, peak_idx 红柱见顶索引, peak_hist 峰值,
          grow_ratio 放大倍数
    """
    dif = np.asarray(dif, dtype=np.float64)
    dea = np.asarray(dea, dtype=np.float64)
    hist = np.asarray(hist, dtype=np.float64)
    n = hist.size
    if n < shrink_bars + 3:
        return []
    x = cross_flags(dif, dea)
    out = []
    d = -1
    for g in range(n):
        if x[g] == 1 and d >= 0 and g > d:
            m = _check(hist, close, d, g, hole_ratio, shrink_bars,
                       shrink_ratio, red_grow_bars, red_grow_ratio)
            if m is not None:
                out.append(m)
        if x[g] == -1:
            d = g
    return out


def _check(hist, close, d, g, hole_ratio, shrink_bars, shrink_ratio,
           red_grow_bars, red_grow_ratio):
    n = hist.size
    seg = hist[d:g + 1]                      # 死叉 → 金叉（含金叉日）
    if seg.size < shrink_bars + 2 or not np.all(np.isfinite(seg)):
        return None
    i = int(np.argmin(seg))
    hmin = seg[i]
    if not (hmin < 0):                       # 必须有绿柱谷
        return None
    if hole_ratio > 0 and close is not None:
        px = close[d + i]
        if not (np.isfinite(px) and px > 0 and abs(hmin) / px >= hole_ratio):
            return None

    # G4 绿柱逐步缩小：谷值 → 金叉
    h_cross = seg[-1]                        # 金叉日 hist
    shrink = (abs(hmin) - abs(h_cross)) / abs(hmin) if abs(hmin) > 0 else 0.0
    if shrink < shrink_ratio:
        return None
    tail = seg[i:g]                          # 谷值 → 金叉前
    dec = 0
    for k in range(1, len(tail)):
        if abs(tail[k]) < abs(tail[k - 1]):
            dec += 1
    if dec < shrink_bars:
        return None

    # G5 转红：金叉日或其后 red_grow_bars 根内 hist > 0
    red_idx = g
    if hist[g] <= 0:
        found = False
        for k in range(g + 1, min(n, g + red_grow_bars + 1)):
            if np.isfinite(hist[k]) and hist[k] > 0:
                red_idx, found = k, True
                break
        if not found:
            return None

    # G6 红柱放大见顶
    end = min(n, red_idx + red_grow_bars + 1)
    seg2 = hist[red_idx:end]
    if seg2.size == 0 or not np.all(np.isfinite(seg2)):
        return None
    pk = int(np.argmax(seg2)) + red_idx
    peak_hist = hist[pk]
    base = hist[red_idx]
    if base <= 0 or peak_hist < red_grow_ratio * base:
        return None

    return {
        'cross_idx': g, 'pit_idx': d + i,
        'pit_hist': round(float(hmin), 4),
        'shrink_bars': dec, 'shrink_ratio': round(float(shrink), 4),
        'red_idx': red_idx,
        'peak_idx': pk, 'peak_hist': round(float(peak_hist), 4),
        'grow_ratio': round(float(peak_hist / base), 2),
    }


def red_segment_metrics(hist, x, cross_idx, head_n=5):
    """统计金叉后「完整红柱段」的强度度量（金叉日 → 下一死叉）。

    区别于检测阶段的 red_grow_bars 短窗口，本函数会用到金叉之后的
    **完整红柱段**，因此属于事后统计（衡量形态强度 / 后续表现），
    不参与「是否命中」的实时判定。

    Args:
        hist: MACD 柱体序列。
        x:    cross_flags 输出（1 金叉 / -1 死叉 / 0 无）。
        cross_idx: 金叉索引。
        head_n: 「前 N 根红柱均值」里的 N。

    Returns:
        dict: peak_idx / peak_hist(完整段红柱峰值), head_mean(前 N 根正红柱均值),
              end_idx(下一死叉索引，无则序列末尾)。
    """
    hist = np.asarray(hist, dtype=np.float64)
    x = np.asarray(x, dtype=np.int64)
    n = hist.size
    end = n - 1
    for k in range(cross_idx + 1, n):
        if x[k] == -1:
            end = k
            break
    red = hist[cross_idx:end + 1]
    red = red[np.isfinite(red)]
    if red.size == 0:
        return {'peak_idx': cross_idx, 'peak_hist': float('nan'),
                'head_mean': float('nan'), 'end_idx': end}
    peak_idx = int(np.argmax(red)) + cross_idx
    peak_hist = float(red.max())
    pos = red[red > 0]
    head_mean = float(pos[:head_n].mean()) if pos.size else float('nan')
    return {'peak_idx': peak_idx, 'peak_hist': peak_hist,
            'head_mean': head_mean, 'end_idx': end}


def _parabola_fit(y):
    """对序列拟合二次多项式，返回 (a, b, c, r2)。a>0 开口向上（∪），a<0 开口向下（∩）。"""
    y = np.asarray(y, dtype=np.float64)
    if y.size < 6 or not np.all(np.isfinite(y)):
        return None
    x = np.arange(y.size, dtype=np.float64)
    a, b, c = np.polyfit(x, y, 2)
    yhat = np.polyval((a, b, c), x)
    ss_res = float(np.sum((y - yhat) ** 2))
    ss_tot = float(np.sum((y - y.mean()) ** 2))
    r2 = 1 - ss_res / ss_tot if ss_tot > 1e-12 else 0.0
    return a, b, c, r2


def parabolic_bottoms(dif, dea, win=12, min_r2=0.5,
                      min_down=0.0, min_up=0.0, min_up_ratio=0.0,
                      min_seg_r2=0.0, min_up_a=None):
    """检测 DIF 曲线的「下抛 → 上抛」抛物线底（∪ 形弧）。

    DIF 先走一段**向下**的抛物线（下抛 = 挖坑），到达谷底，再走一段
    **向上**的抛物线（上抛 = 起爆），整体形成一个开口向上的 ∪ 形抛物线。
    **不要求 dif 跌破零轴**——任何位置的「下抛→上抛」抛物线都算。

    对 dif 的每个局部谷 p，取 [p-win, p+win] 窗口拟合二次多项式
    y = a·x² + b·x + c：
      a > 0          → 开口向上（∪，先下抛后上抛）
      R² >= min_r2   → 拟合干净（确为抛物线弧，而非 V 形 / 直线 / 杂乱）
      down > 0       → 下抛段存在（谷前 dif 下降）
      up   > 0       → 上抛段存在（谷后 dif 上升）
      up/down 比例   → 起爆强度（上抛相对下抛的力度）

    Args:
        dif/dea: MACD 快慢线序列。
        win:          抛物线拟合窗口（谷前/谷后各取 win 根）。
        min_r2:       最小拟合优度（0~1）。
        min_down:     最小下抛幅度（dif 谷前下降多少）。
        min_up:       最小上抛幅度（dif 谷后上升多少）。
        min_up_ratio: 上抛/下抛 最低比例（起爆强度，0=不约束）。
        min_seg_r2:   下抛段/上抛段**各自**独立拟合的最小 R²（默认0=不约束）。
        min_up_a:     上抛段独立拟合的曲率下限（如 0 表示上抛段必须凹向上）。

    Returns:
        list[dict]: pit_idx, dif_pit, dea_pit, a(整体曲率), r2(整体拟合优度),
                    down(下抛幅度), up(上抛幅度), up_ratio(上抛/下抛),
                    down_a/down_r2(下抛段独立拟合), up_a/up_r2(上抛段独立拟合)
    """
    dif = np.asarray(dif, dtype=np.float64)
    dea = np.asarray(dea, dtype=np.float64)
    n = dif.size
    if n < 2 * win + 6:
        return []
    out = []
    for p in range(win, n - win):
        if not (dif[p] < dif[p - 1] and dif[p] < dif[p + 1]):
            continue
        lo, hi = p - win, p + win
        fit = _parabola_fit(dif[lo:hi + 1])
        if fit is None:
            continue
        a, b, c, r2 = fit
        if a <= 0 or r2 < min_r2:
            continue
        down = dif[lo] - dif[p]
        up = dif[hi] - dif[p]
        if down < min_down or up < min_up:
            continue
        ratio = up / down if down > 0 else None
        if ratio is not None and ratio < min_up_ratio:
            continue
        # 两阶段分别独立拟合：下抛段 [lo,p] / 上抛段 [p,hi]
        fd = _parabola_fit(dif[lo:p + 1])
        fu = _parabola_fit(dif[p:hi + 1])
        if (fd is not None and fd[3] < min_seg_r2) or \
           (fu is not None and fu[3] < min_seg_r2):
            continue
        if min_up_a is not None and fu is not None and fu[0] < min_up_a:
            continue
        out.append({
            'pit_idx': p, 'dif_pit': round(float(dif[p]), 4),
            'dea_pit': round(float(dea[p]), 4),
            'a': round(float(a), 6), 'r2': round(float(r2), 4),
            'down': round(float(down), 4), 'up': round(float(up), 4),
            'up_ratio': round(float(ratio), 4) if ratio is not None else None,
            'down_a': round(float(fd[0]), 6) if fd else None,
            'down_r2': round(float(fd[3]), 4) if fd else None,
            'up_a': round(float(fu[0]), 6) if fu else None,
            'up_r2': round(float(fu[3]), 4) if fu else None,
        })
    return out
