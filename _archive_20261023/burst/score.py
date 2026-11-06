# -*- coding: utf-8 -*-
"""
==============================================================================
burst.score —— 相似度评分与最低结构约束
==============================================================================

Score = 100 × Σ w_k · f_k(S_k) / Σ w_k ，f_k 为 [lo, hi] → [0, 1] 的线性映射。

任一维度为 NaN（预热期、长期停牌导致实体基准为 0 等）时总分记为 NaN，
不参与排名——宁可漏掉，也不要用 0 分把「无法度量」混进「形态很差」。
"""
import numpy as np

from . import features as F

# (权重键, 特征键, 展示名)
DIMS = [
    ('breakout', 'S_A', '突破强度'),
    ('accel',    'S_B', '加速度'),
    ('body',     'S_C', '实体膨胀'),
    ('bull',     'S_D', '阳线密度'),
    ('squeeze',  'S_E', '逼空强度'),
]


def compute(close, open_, high, low, volume, p, min_score=0.0):
    """返回 {'features','conv','score','hard','entry'}。"""
    sh = p['shape']
    f = F.shape_features(close, open_, high, low, volume,
                         n=sh['n'], m=sh['m'], eps=sh['eps'],
                         accel_mode=sh.get('accel_mode', 'slope'))
    conv = {}
    for key, sk, _ in DIMS:
        nz = p['norm'][key]
        conv[key] = F.norm01(f[sk], nz['lo'], nz['hi'])

    w = np.array([float(p['weights'][k]) for k, _, _ in DIMS])
    wsum = w.sum()
    mat = np.vstack([conv[k] for k, _, _ in DIMS])
    with np.errstate(invalid='ignore'):
        score = 100.0 * np.nansum(mat * w[:, None], axis=0) / wsum
    score[~np.isfinite(mat).all(axis=0)] = np.nan

    hard = np.isfinite(score)
    hc = p['hard']
    if hc.get('enabled', True):
        hard = (hard
                & (f['S_A'] >= float(hc['min_breakout']))
                & (f['S_D'] >= float(hc['min_bull']))
                & (f['S_E'] >= float(hc['min_squeeze'])))

    entry = hard & (score >= float(min_score))
    return {'features': f, 'conv': conv, 'score': score,
            'hard': hard, 'entry': entry}


def weights_of(p):
    """返回 {展示名: 权重}，用于输出「权重贡献」列。"""
    return {name: float(p['weights'][k]) for k, _, name in DIMS}
