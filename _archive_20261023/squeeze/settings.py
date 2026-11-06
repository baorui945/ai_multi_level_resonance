# -*- coding: utf-8 -*-
"""
==============================================================================
squeeze.settings —— 「均线粘合 + 放量突破」策略的参数与路径（唯一来源）
==============================================================================
只做两件事：

1. 读取 `config/squeeze.json`，与 `DEF` 深合并（缺项自动回落）；
2. 单向引入 `mlr.settings` 的数据层常量（parquet 缓存 / 沪深300 基准），
   避免另写一套数据管道。

本模块不读 `config/coil.json`、`config/burst.json`，也不改 `mlr` 的任何对象。
"""
import json
import os

from mlr import settings as _MLR

ROOT_DIR = _MLR.ROOT_DIR
CONFIG_PATH = os.path.join(ROOT_DIR, 'config', 'squeeze.json')
OUTPUT_DIR = os.path.join(_MLR.OUTPUT_DIR, 'squeeze')

PX_CACHE = _MLR.PX_CACHE
IDX_PKL = _MLR.IDX_PKL

DEF = {
    'data': {'start': '2017-01-01', 'min_bars': 260},
    'ma': {'short': 7, 'mid': 21, 'long': 120},
    'macd': {'fast': 30, 'slow': 89, 'signal': 9},
    'rule': {
        'max_stick': 0.05,          # (max MA - min MA)/min MA 上限
        'min_pct': 0.05,            # 当日涨幅下限
        'use_vol': True,            # 放量：V_t > MA(V,5)
        'use_macd': False,          # DIF>DEA 且在零轴附近（归一化）
        'macd_zero_band': 0.005,    # |DIF|/C <= 该值
        'use_ma120_flat': False,    # MA120 5 日变化率上限
        'ma120_flat': 0.01,
        'require_stack': False,     # MA7 > MA21 > MA120
        'max_range': None,          # 近 range_win 日振幅上限（None=不启用）
        'range_win': 60,
        'box_win': 120,             # 展示用：箱体上沿窗口
        'box_q': 0.90,
        # ── 形态硬条件（选股器默认开启；use_form=false 退回原式）──
        'use_form': True,
        'min_run': 10,              # 粘合至少持续 N 个交易日
        'max_box_h': 0.30,          # 突破前 form_win 日箱体高度上限
        'form_win': 60,             # 箱体高度统计窗口（不含当日）
        'need_box': True,           # 收盘必须站上箱体上沿
        'box_tol': 0.02,            # 允许低于箱体上沿 2%
        'ma120_min_chg': -0.005,    # MA120 5 日变化下限（不走坏）
        # ── 蓄势质量（v0.1.4；use_shape=false 退回 v0.1.3 的四条件）──
        'use_shape': True,
        'shape_range20': 0.12,      # 突破前 20 日振幅上限（横盘要平）
        'shape_range40': 0.15,      # 突破前 40 日振幅上限
        'shape_spike5': 0.12,       # 120 日内最大 5 日涨幅上限（无急拉）
        'shape_crash5': -0.10,      # 120 日内最大 5 日跌幅下限（无急杀）
        'shape_off_low': 0.20,      # 收盘距 120 日低点上限（不是反弹第一波）
        'shape_ma120_over': 0.01,   # MA120 高于 MA21 的容差（长均线不压顶）
        'cooldown_days': 20,
    },
    'score': {'w_stick': 0.45, 'w_pct': 0.30, 'w_vol': 0.25,
              'pct_full': 0.10, 'vol_full': 3.0},
    'grade': {
        'a_max_stick': 0.03, 'a_min_run': 20, 'a_min_vol': 1.5,
        'a_need_box': False, 'a_need_stack': False,
        'b_max_stick': 0.05, 'b_min_run': 10, 'b_min_vol': 1.2,
        'b_need_box': False, 'b_need_stack': False,
    },
    'pick': {
        'min_price': 0.0,           # 收盘价下限（元）
        'min_amount': 0.0,          # 当日成交额下限（亿元）
        'exclude_st': False,        # 剔除 ST / *ST / 退市整理
        'exclude_limit': False,     # 剔除当日已涨停（次日难买）
        'min_grade': '',            # A / B / 空
        'watch_band': 0.03,         # 观察池：收盘距均线束上下不超过该比例
        'watch_min_days': 10,       # 观察池：粘合至少持续 N 个交易日
    },
    'signal': {'min_score': 0.0},
    'exit': {'horizons': [5, 10, 20, 40], 'main_horizon': 20, 'cost': 0.002},
    'backtest': {'date_start': '2017-01-01', 'split': '2023-01-01'},
    'reference': {'code': '600188', 'coil_date': '2026-01-13',
                  'breakout_date': '2026-02-04'},
}


def _deep_merge(base, over):
    out = dict(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config(path=None):
    p = path or CONFIG_PATH
    user = {}
    if os.path.isfile(p):
        with open(p, 'r', encoding='utf-8') as f:
            user = json.load(f)
    return _deep_merge(DEF, user)


def ensure_dirs():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
