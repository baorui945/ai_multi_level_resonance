# -*- coding: utf-8 -*-
"""
==============================================================================
squeeze.rule —— 「长期均线粘合 → 放量突破」的判定与打分
==============================================================================

形态定义（t 日收盘即可全部得出，无未来函数）：

    粘合度  stick = (max(MA7,MA21,MA120) − min(...)) / min(...)
    突破    C_t > max(MA7,MA21,MA120) 且 (C_t − C_{t−1})/C_{t−1} ≥ min_pct
    放量    V_t > MA(V,5)                     （通达信 V > MA(V,5) 口径）
    零轴    |DIF|/C ≤ macd_zero_band 且 DIF > DEA （归一化，跨股票可比）
    走平    |MA120_t / MA120_{t−5} − 1| ≤ ma120_flat
    多头    MA7 > MA21 > MA120（可选）
    振幅    近 range_win 日 (max H − min L)/min L ≤ max_range（可选）

形态硬条件（选股器默认开启，`use_form=false` 退回原式，见 `form_mask`）：

    粘合天数  ≥ min_run                    均线已贴合足够久，不是刚碰上
    箱体高度  ≤ max_box_h                  突破前 form_win 日不宽幅震荡
    站上箱体  C ≥ 箱体上沿 × (1 − box_tol)  突破的是箱体，不只是均线
    MA120    5 日变化 ≥ ma120_min_chg      长期均线没有明显走坏

蓄势质量（v0.1.4，`use_shape=false` 可关闭，见 `shape_mask`）：

    20/40 日振幅 ≤ 上限                    均线是靠平静横盘粘起来的
    120 日内无急拉（5 日涨幅上限）          不是冲高回落后的平台
    120 日内无急杀（5 日跌幅下限）          不是断头铡刀
    收盘距 120 日低点 ≤ 上限               不是刚从底部弹起来的第一波
    MA120 ≤ MA21 × (1+容差)               长均线已走平、不再压顶

对用户原始公式的两处必要修正：

1. `DIF > -0.5` 的 DIF 是**元**，5 元股与 100 元股不可比；改为 `DIF/C ≥ -0.5%`。
2. 原公式算出了 `COND_E`（MACD）却没有写进 `XG`，等于从未生效；这里作为
   显式开关 `use_macd`，默认关闭以保持与用户原式一致。

MACD 默认 (30,89,9)：与参考图标称 (30,90,9) 的结构一致，仅用 89 替代 90。
"""
import numpy as np
import pandas as pd

from coil import features as F


def _nz_div(a, b):
    """安全除法：分母为 0/非有限时返回 NaN。"""
    a = np.asarray(a, dtype=np.float64)
    b = np.asarray(b, dtype=np.float64)
    out = np.full(a.size, np.nan)
    ok = np.isfinite(a) & np.isfinite(b) & (b != 0)
    out[ok] = a[ok] / b[ok]
    return out


def features(c, h, l, v, p):
    """单只股票一次性算出全部判定所需序列（全部 float64 ndarray）。"""
    r, s = p['rule'], p['score']
    ma_s = F._roll(c, p['ma']['short'])
    ma_m = F._roll(c, p['ma']['mid'])
    ma_l = F._roll(c, p['ma']['long'])

    hi = np.maximum.reduce([ma_s, ma_m, ma_l])
    lo = np.minimum.reduce([ma_s, ma_m, ma_l])
    stick = _nz_div(hi - lo, lo)

    prev = pd.Series(c).shift(1).to_numpy('float64')
    pct = _nz_div(c - prev, prev)

    vma5 = F._roll(v, 5)
    vma20_prev = F._sh(F._roll(v, 20), 1)
    vol_mult = _nz_div(v, vma20_prev)

    dif, dea, _ = F.macd(c, p['macd']['fast'], p['macd']['slow'],
                         p['macd']['signal'])
    dif_n = _nz_div(dif, c)
    dea_n = _nz_div(dea, c)

    ma120_5 = F._sh(ma_l, 5)
    ma120_chg = _nz_div(ma_l - ma120_5, ma120_5)

    hh = F._roll(h, int(r['range_win']), 'max')
    ll = F._roll(l, int(r['range_win']), 'min')
    rng = _nz_div(hh - ll, ll)

    box = pd.Series(h).shift(1).rolling(int(r['box_win']),
                                        min_periods=int(r['box_win'])) \
        .quantile(float(r['box_q'])).to_numpy('float64')

    # 突破前的箱体高度（不含当日）：近 form_win 日 (maxH-minL)/minL。
    # 与 rng 的区别：rng 含当日，突破日本身会把振幅放大。
    fw = int(r.get('form_win', 60))
    box_hi = F._sh(F._roll(h, fw, 'max'), 1)
    box_lo = F._sh(F._roll(l, fw, 'min'), 1)
    box_h = _nz_div(box_hi - box_lo, box_lo)

    # ── 蓄势质量（v0.1.4，全部只用 T 日及以前数据）──
    # 回答的是“均线是靠什么粘起来的”：平静横盘，还是急跌急涨后碰巧收敛。
    cs, hs, ls = pd.Series(c), pd.Series(h), pd.Series(l)
    hm20, lm20 = hs.rolling(20).max().shift(1), ls.rolling(20).min().shift(1)
    hm40, lm40 = hs.rolling(40).max().shift(1), ls.rolling(40).min().shift(1)
    rng20 = _nz_div(hm20 - lm20, lm20)
    rng40 = _nz_div(hm40 - lm40, lm40)
    r5 = cs / cs.shift(5) - 1.0
    spike5 = r5.rolling(120).max().shift(1).to_numpy('float64')
    crash5 = r5.rolling(120).min().shift(1).to_numpy('float64')
    lo120 = ls.rolling(120).min().shift(1)
    off_low = _nz_div(cs, lo120) - 1.0
    ma120_over = _nz_div(ma_l, ma_m) - 1.0

    return {'ma_s': ma_s, 'ma_m': ma_m, 'ma_l': ma_l, 'ma_max': hi,
            'ma_min': lo, 'stick': stick, 'pct': pct, 'vma5': vma5,
            'vol_mult': vol_mult, 'dif': dif, 'dea': dea, 'dif_n': dif_n,
            'dea_n': dea_n, 'ma120_chg': ma120_chg, 'rng': rng, 'box': box,
            'box_hi': box_hi, 'box_lo': box_lo, 'box_h': box_h,
            'rng20': rng20, 'rng40': rng40, 'spike5': spike5,
            'crash5': crash5, 'off_low': off_low, 'ma120_over': ma120_over,
            'score': np.zeros(c.size)}


def mask(f, c, rule):
    """按规则 dict 生成信号布尔序列（缺项按关闭处理）。"""
    m = np.isfinite(f['stick']) & np.isfinite(f['pct'])
    if rule.get('max_stick') is not None:
        m &= f['stick'] <= float(rule['max_stick'])
    if rule.get('require_stack'):
        m &= (f['ma_s'] > f['ma_m']) & (f['ma_m'] > f['ma_l'])
    m &= c > f['ma_max']
    m &= f['pct'] >= float(rule['min_pct'])
    if rule.get('use_vol', True):
        m &= np.isfinite(f['vma5']) & (f['v'] > f['vma5'])
    if rule.get('use_macd'):
        m &= (f['dif'] > f['dea']) & \
            (f['dif_n'] >= -float(rule['macd_zero_band']))
    if rule.get('use_ma120_flat'):
        m &= np.isfinite(f['ma120_chg']) & \
            (np.abs(f['ma120_chg']) <= float(rule['ma120_flat']))
    if rule.get('max_range') is not None:
        m &= np.isfinite(f['rng']) & (f['rng'] <= float(rule['max_range']))
    return m


def shape_mask(f, c, rule, breakout=True):
    """蓄势质量（v0.1.4，默认开启，``use_shape=False`` 可关闭）。

    ``form_mask`` 只回答“均线现在是否靠得近”，回答不了“是靠什么粘起来的”。
    一条从 110 跌到 78 再反弹 15% 的票，同样能让三条均线靠在一起；图形上那
    不是“横盘蓄势”，是 V 型反弹的第一波。这里补五条“过程”证据：

        1. 近 20 日振幅 <= shape_range20      横盘段要平（不是反弹通道）
        2. 近 40 日振幅 <= shape_range40      同上，稍长窗口
        3. 120 日内最大 5 日涨幅 <= shape_spike5   没有急拉（排除冲高回落）
        4. 120 日内最大 5 日跌幅 >= shape_crash5   没有急杀（排除断头铡刀）
        5. 收盘距 120 日低点 <= shape_off_low     不是刚从底部弹起来
        6. MA120 <= MA21 × (1+shape_ma120_over)   长均线已经走平、不再压顶

    这六条一起，才把“长期横盘箱体”和“跌多了碰巧收敛”分开。
    """
    if not rule.get('use_shape', True):
        return np.ones(np.asarray(c).size, dtype=bool)
    m = np.ones(np.asarray(c).size, dtype=bool)

    def _le(key, field, default=None):
        thr = rule.get(key, default)
        if thr is None:
            return
        m_ = np.isfinite(f[field]) & (f[field] <= float(thr))
        nonlocal m
        m = m & m_

    def _ge(key, field, default=None):
        thr = rule.get(key, default)
        if thr is None:
            return
        m_ = np.isfinite(f[field]) & (f[field] >= float(thr))
        nonlocal m
        m = m & m_

    _le('shape_range20', 'rng20')
    _le('shape_range40', 'rng40')
    _le('shape_spike5', 'spike5')
    _ge('shape_crash5', 'crash5')
    _le('shape_off_low', 'off_low')
    _le('shape_ma120_over', 'ma120_over')
    return m


def form_mask(f, c, rule, breakout=True):
    """形态硬条件（选股器口径，默认开启，``use_form=False`` 可关闭）。

    原公式只要求「当日三条均线靠得近 + 大涨站上均线」，会把
    “跌了很久后的第一根反弹”“均线刚贴合 1~3 天”也选进来，图形上
    并不是长期横盘箱体。这里补上红框形态必须同时具备的四个证据：

        1. 粘合天数 >= min_run          均线已经贴合足够久（默认 10 日）
        2. 箱体高度 <= max_box_h        突破前 form_win 日振幅不宽
        3. 收盘 >= 箱体上沿 * (1-tol)   突破的是箱体，不只是均线
        4. MA120 5 日变化 >= 下限       长期均线没有明显走坏

    Args:
        breakout: True 为突破日清单；False 为观察池（股价仍在箱体内，
            不检查第 3 条）。
    """
    if not rule.get('use_form', True):
        return np.ones(np.asarray(c).size, dtype=bool)

    m = np.ones(np.asarray(c).size, dtype=bool)
    run = f.get('run')
    if run is None:
        run = stick_run(f['stick'], float(rule.get('max_stick') or 0.05))
    if rule.get('min_run'):
        m &= np.asarray(run) >= float(rule['min_run'])
    if rule.get('max_box_h') is not None:
        m &= np.isfinite(f['box_h']) & (f['box_h'] <= float(rule['max_box_h']))
    if breakout and rule.get('need_box'):
        tol = float(rule.get('box_tol') or 0.0)
        m &= np.isfinite(f['box']) & (c >= f['box'] * (1.0 - tol))
    if rule.get('ma120_min_chg') is not None:
        m &= np.isfinite(f['ma120_chg']) & \
            (f['ma120_chg'] >= float(rule['ma120_min_chg']))
    if rule.get('use_shape', True):
        m &= shape_mask(f, c, rule, breakout=breakout)
    return m


def form_ok(f, i, c, rule, breakout=True):
    """单日形态硬条件判定（观察池/复核用）。"""
    return bool(form_mask(f, c, rule, breakout=breakout)[i])


def score_at(f, i, p):
    """0~100 排序分：粘合越紧、涨幅越足、量放得越大，分越高。"""
    s = p['score']
    thr = float(p['rule'].get('max_stick') or 0.05)
    st = f['stick'][i]
    s_stick = 0.0 if not np.isfinite(st) else \
        float(np.clip(1.0 - st / max(thr, 1e-9), 0.0, 1.0))
    pc = f['pct'][i]
    s_pct = 0.0 if not np.isfinite(pc) else \
        float(np.clip(pc / float(s['pct_full']), 0.0, 1.0))
    vm = f['vol_mult'][i]
    s_vol = 0.0 if not np.isfinite(vm) else \
        float(np.clip((vm - 1.0) / max(float(s['vol_full']) - 1.0, 1e-9),
                      0.0, 1.0))
    return 100.0 * (float(s['w_stick']) * s_stick
                    + float(s['w_pct']) * s_pct
                    + float(s['w_vol']) * s_vol)


def stick_run(stick, thr):
    """截至每日、连续处于粘合阈值内的天数（当日不满足则为 0）。

    用于回答“这只票已经粘了多久”，是选股时区分“刚贴合”和
    “长期横盘沉淀”的关键证据，不是收益预测。
    """
    st = np.asarray(stick, dtype=np.float64)
    ok = np.isfinite(st) & (st <= float(thr))
    idx = np.arange(st.size)
    last_bad = np.maximum.accumulate(np.where(ok, -1, idx))
    run = (idx - last_bad).astype(np.float64)
    run[~ok] = 0.0
    return run


def grade_at(f, i, c, g):
    """形态完整度 A/B/C（只看形态要素是否齐全，不预测涨幅）。

    A：粘合 <= a_max_stick、粘合天数 >= a_min_run、量比 >= a_min_vol、
       收盘 >= 箱体上沿（可选）、多头排列（可选）
    B：同样的条件放宽到 b_*
    C：通过信号但未达到 B（例如粘合天数不足、量能偏弱）
    """
    if not g:
        return 'C'

    def _ok(prefix):
        thr = g.get(f'{prefix}_max_stick')
        if thr is not None and not (np.isfinite(f['stick'][i])
                                    and f['stick'][i] <= float(thr)):
            return False
        run = g.get(f'{prefix}_min_run')
        if run is not None and not (f['run'][i] >= float(run)):
            return False
        vol = g.get(f'{prefix}_min_vol')
        if vol is not None and not (np.isfinite(f['vol_mult'][i])
                                    and f['vol_mult'][i] >= float(vol)):
            return False
        if g.get(f'{prefix}_need_box'):
            box = f['box'][i]
            if not (np.isfinite(box) and c[i] >= box):
                return False
        if g.get(f'{prefix}_need_stack'):
            if not (f['ma_s'][i] > f['ma_m'][i] > f['ma_l'][i]):
                return False
        return True

    if _ok('a'):
        return 'A'
    if _ok('b'):
        return 'B'
    return 'C'


def compute(c, h, l, v, p):
    """特征 + 信号 + 分数。`v` 需同时放进 features 供 mask 使用。"""
    f = features(c, h, l, v, p)
    f['v'] = np.asarray(v, dtype=np.float64)
    f['run'] = stick_run(f['stick'], float(p['rule'].get('max_stick') or 0.05))
    sig = mask(f, c, p['rule'])
    n = c.size
    sc = np.zeros(n)
    for i in np.flatnonzero(sig):
        sc[i] = score_at(f, int(i), p)
    return {'features': f, 'signal': sig, 'score': sc}
