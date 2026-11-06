# -*- coding: utf-8 -*-
"""
==============================================================================
coil.detect —— 蓄势状态机：coil（蓄势）→ breakout（突破）→ entry（首日）
==============================================================================

判定链（t 日收盘即可全部得出，无未来函数）：

    coil_ok  = 窗口 [t-N, t-1] 的硬条件全部达标：cv / box_w / vol_compress /
               ma_gap / macd_band（软条件 beta、curv、ma120_slope、hist_band、
               n_cross 进入 coil_strength），且 coil_strength >= 阈值；
    coil_run = coil_ok 的连续成立天数 >= min_coil_bars（"横有多长"）；
    breakout = close_t > box_high*(1+eps)
               且 vol_mult >= vol_mult_min          （放量）
               且 bars_since_cross <= fresh_days     （金叉新鲜）
               且 dif>dea 且 hist>0 且 hist_t>hist_{t-1}（红柱放大=二阶导转正）
               且 MA7>MA21>MA120                     （多头排列发散）；
    entry    = coil_ok & coil_run>=min & breakout，为 False→True 的第一天，
               且距上一信号 >= cooldown_days（同一波突破只算一次）。

`state(..., price_only=True)` 会关闭 coil 与 MACD 条件，只保留"价格突破 + 量能
+ 均线排列"，用于验证「蓄势/MACD 这一层到底有没有增量信息」。
"""
import numpy as np
import pandas as pd

from . import features as F


# ═════════════════════════════════════════════════════════════════════════
# 小工具
# ═════════════════════════════════════════════════════════════════════════
def _run_length(flags):
    """连续 True 的长度（False 处为 0），向量化实现。"""
    s = pd.Series(np.asarray(flags, dtype=bool))
    run = s.groupby((~s).cumsum()).cumcount() + 1
    return run.where(s, 0).to_numpy('int64')


def _bars_since_cross(dif, dea):
    """距最近一次金叉的根数（无金叉时为 10^9）。"""
    x = F.cross_flags(dif, dea)
    n = x.size
    out = np.full(n, 10 ** 9, dtype=np.int64)
    since = 10 ** 9
    for t in range(n):
        if x[t] == 1:
            since = 0
        elif since < 10 ** 9:
            since += 1
        out[t] = since
    return out


def _u01(v, bad=np.nan):
    """NaN → 0，并裁到 [0,1]（评分用）。"""
    v = np.asarray(v, dtype=np.float64)
    v = np.where(np.isfinite(v), v, bad)
    return np.clip(v, 0.0, 1.0)


def _ratio_score(value, limit):
    """value 越小越好：达到 limit 时得 0 分，0 时得 1 分。"""
    lim = max(float(limit), 1e-12)
    return _u01(1.0 - np.asarray(value, dtype=np.float64) / lim, 0.0)


# ═════════════════════════════════════════════════════════════════════════
# 状态机
# ═════════════════════════════════════════════════════════════════════════
def state_from_features(f, close, dif, dea, hist, ma_s, ma_m, ma_l, p,
                        use_coil=True, use_macd=True, use_vol=True,
                        use_ma_stack=None):
    """由已算好的特征 + 当日价格/指标做判定（网格搜索时复用特征，避免重算）。

    Args:
        f:      `features.rolling_coil` 的返回值。
        p:      配置 dict（需含 coil / breakout / score 三段）。
        use_coil / use_macd / use_vol / use_ma_stack:
                开关，用于基准对照（price_only 时关闭 coil 与 macd）。

    Returns:
        dict：entry / coil / breakout / coil_run / score / bars_since_cross
              以及 break_px / vol_ok / macd_ok / ma_ok 各分项（便于归因）。
    """
    close = np.asarray(close, dtype=np.float64)
    ma_s = np.asarray(ma_s, dtype=np.float64)
    ma_m = np.asarray(ma_m, dtype=np.float64)
    ma_l = np.asarray(ma_l, dtype=np.float64)
    dif = np.asarray(dif, dtype=np.float64)
    dea = np.asarray(dea, dtype=np.float64)
    hist = np.asarray(hist, dtype=np.float64)
    n = close.size

    c, b = p['coil'], p['breakout']
    if use_ma_stack is None:
        use_ma_stack = bool(b.get('require_ma_stack', True))

    # ── 蓄势：硬条件（交集不能为空）+ 软条件进入 coil_strength ──────────
    w = p['score']
    s_comp = (_ratio_score(f['cv'], c['max_cv'])
              + _ratio_score(f['box_w'], c['max_box_w'])) / 2.0
    s_deriv = (_ratio_score(np.abs(f['beta']), c['max_abs_beta'])
               + _ratio_score(np.abs(f['curv']), c['max_abs_curv'])
               + _ratio_score(np.abs(f['ma120_slope']),
                              c['max_ma120_slope'])) / 3.0
    s_ma = _ratio_score(f['ma_gap'], c['max_ma_gap'])
    s_macd = _ratio_score(f['macd_band'], c['max_macd_band'])
    s_hist = _ratio_score(f['hist_band'], c['max_hist_band'])
    s_cross = _u01(1.0 - f['n_cross'] / max(float(c['max_cross_n']), 1.0), 0.0)
    coil_strength = (w['w_compress'] * s_comp + w['w_deriv'] * s_deriv
                     + w['w_ma'] * s_ma + w['w_macd'] * s_macd
                     + w['w_hist'] * s_hist + w['w_cross'] * s_cross)

    if use_coil:
        core = (np.isfinite(f['cv']) & np.isfinite(f['box_w'])
                & np.isfinite(f['vol_compress']) & np.isfinite(f['ma_gap'])
                & np.isfinite(f['macd_band']))
        core &= f['cv'] <= c['max_cv']
        core &= f['box_w'] <= c['max_box_w']
        core &= f['vol_compress'] <= c['vol_compress']
        core &= f['ma_gap'] <= c['max_ma_gap']
        core &= f['macd_band'] <= c['max_macd_band']
        coil = core & (coil_strength >= float(c['min_coil_strength']))
        coil = coil.astype(bool)
    else:
        coil = np.ones(n, dtype=bool)

    coil_run = _run_length(coil)
    run_ok = coil_run >= int(c['min_coil_bars']) if use_coil else coil
    run_ok = np.asarray(run_ok, dtype=bool)

    # ── 突破分项 ───────────────────────────────────────────────────────
    bh = f['box_high']
    break_px = (np.isfinite(bh) & (close > bh * (1.0 + float(b['eps']))))

    vm = f['vol_mult']
    if use_vol:
        vol_ok = np.isfinite(vm) & (vm >= float(b['vol_mult']))
    else:
        vol_ok = np.ones(n, dtype=bool)

    bsc = _bars_since_cross(dif, dea)
    if use_macd:
        macd_ok = (bsc <= int(b['fresh_days'])) & (dif > dea) & (hist > 0)
        if b.get('require_hist_grow', True):
            grow = np.zeros(n, dtype=bool)
            grow[1:] = hist[1:] > hist[:-1]
            macd_ok &= grow
    else:
        macd_ok = np.ones(n, dtype=bool)

    ma_ok = (ma_s > ma_m) & (ma_m > ma_l) if use_ma_stack else \
        np.ones(n, dtype=bool)

    breakout = break_px & vol_ok & macd_ok & ma_ok
    raw = breakout & coil & run_ok
    entry = raw.copy()
    entry[1:] &= ~raw[:-1]                     # 只留 False→True 的第一天
    entry[0] = False                           # 首根无前值，不建仓
    raw_entry = entry.copy()

    # 冷却期：同一只股票两次信号至少间隔 cooldown_days 根
    cd = int(b.get('cooldown_days', 0))
    if cd > 0:
        idx = np.flatnonzero(entry)
        entry = np.zeros(n, dtype=bool)
        last = -10 ** 9
        for i in idx:
            if i - last >= cd:
                entry[i] = True
                last = i

    # ── 连续评分 0~100（仅排序用，不参与硬条件）────────────────────────
    s_break = _u01(f['dist_high'] / 0.05)
    vmin = float(b['vol_mult'])
    s_vol = _u01((vm - 1.0) / max(2.0 * vmin - 1.0, 1e-9))
    break_strength = w['w_break'] * s_break + w['w_vol'] * s_vol
    score = 100.0 * (float(w['w_coil_total']) * coil_strength
                     + (1.0 - float(w['w_coil_total'])) * break_strength)

    return {'entry': entry, 'entry_raw': raw_entry,
            'coil': coil, 'breakout': breakout,
            'coil_run': coil_run, 'score': score,
            'coil_strength': coil_strength,
            'bars_since_cross': bsc, 'break_px': break_px,
            'vol_ok': vol_ok, 'macd_ok': macd_ok, 'ma_ok': ma_ok,
            'features': f}


def state(close, volume, p, macd_p, ma_p, use_coil=True, use_macd=True,
          use_vol=True, use_ma_stack=None, dif=None, dea=None, hist=None,
          ma_s=None, ma_m=None, ma_l=None):
    """端到端：算指标 → 算特征 → 判定。返回 `state_from_features` 的 dict。

    允许外部传入已算好的 dif/dea/hist/均线（多窗口网格时复用 MACD）。
    """
    close = np.asarray(close, dtype=np.float64)
    if dif is None or dea is None or hist is None:
        dif, dea, hist = F.macd(close, *macd_p)
    if ma_s is None or ma_m is None or ma_l is None:
        ma_s, ma_m, ma_l = F.moving_averages(close, ma_p)
    f = F.rolling_coil(close, dif, dea, hist, ma_s, ma_m, ma_l,
                       volume=volume, win=p['coil']['win'],
                       box_q=p['breakout']['box_q'])
    return state_from_features(f, close, dif, dea, hist, ma_s, ma_m, ma_l, p,
                               use_coil=use_coil, use_macd=use_macd,
                               use_vol=use_vol, use_ma_stack=use_ma_stack)

