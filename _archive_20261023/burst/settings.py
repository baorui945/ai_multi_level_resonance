# -*- coding: utf-8 -*-
"""
==============================================================================
burst.settings —— 爆发形态评分的路径与参数（唯一来源）
==============================================================================
只做两件事：

1. 读取 `config/burst.json`，与 `DEF` 深合并（缺项自动回落）；
2. 从 `mlr.settings` 单向引入**数据管道**常量（表名 / parquet 缓存 /
   沪深300 基准），避免另写一套数据连接造成口径漂移。

本模块不读取 `config/coil.json`，也不读取 `config/resonance.json`。
"""
import json
import os

from mlr import settings as _MLR

ROOT_DIR = _MLR.ROOT_DIR
CONFIG_PATH = os.path.join(ROOT_DIR, 'config', 'burst.json')
OUTPUT_DIR = os.path.join(_MLR.OUTPUT_DIR, 'burst')

TABLE_DAILY = _MLR.TABLE_DAILY        # backward_tdx_daily（后复权）
PX_CACHE = _MLR.PX_CACHE              # data/_cache/daily_px.parquet
IDX_PKL = _MLR.IDX_PKL                # data/idx_000300.pkl（沪深300 收盘）
HS300_JSON = _MLR.HS300_JSON


DEF = {
    'data': {
        'start': '2015-09-01',
        'min_bars': 260,              # 需要 m 平台窗口 + 预热
    },
    'shape': {
        'n': 5,                       # N：爆发窗口（含信号日 T）
        'm': 20,                      # M：平台窗口（突破参照 / 实体基准）
        'eps': 1e-9,
        'accel_mode': 'slope',        # slope（相对斜率差）| diff2（原式二阶差分）
    },
    # 归一化锚点：lo → 0 分，hi → 100 分，线性，超界截断
    'norm': {
        'breakout': {'lo': 0.000, 'hi': 0.070},   # 突破幅度（p50=1.5%，p95=9%）
        'accel':    {'lo': 0.000, 'hi': 0.045},   # 日均加速度（p50=1.5%，p95=5.2%）
        'body':     {'lo': 0.900, 'hi': 2.800},   # 实体膨胀（p50=1.50，p90=2.76）
        'bull':     {'lo': 0.400, 'hi': 1.000},   # 阳线密度（N=5 时只有 0/0.4/…/1.0）
        'squeeze':  {'lo': 0.550, 'hi': 0.850},   # 收盘位置（p20=0.57，p95=0.82）
    },
    'weights': {
        'breakout': 0.30, 'accel': 0.20, 'body': 0.20,
        'bull': 0.15, 'squeeze': 0.15,
    },
    'weights_preset': '',
    'weights_presets': {
        'v01': {'breakout': 0.30, 'accel': 0.20, 'body': 0.20,
                'bull': 0.15, 'squeeze': 0.15},
        'a-heavy': {'breakout': 0.55, 'accel': 0.25, 'body': 0.10,
                    'bull': 0.05, 'squeeze': 0.05},
        'a-only': {'breakout': 1.00, 'accel': 0.00, 'body': 0.00,
                   'bull': 0.00, 'squeeze': 0.00},
    },
    # 纯评分之外的「最低结构约束」：可整体关闭做对照
    'hard': {
        'enabled': True,
        'min_breakout': 0.0,          # 必须收在前 M 日最高价之上
        'min_bull': 0.4,              # N 日内至少 40% 阳线
        'min_squeeze': 0.5,           # N 日平均收盘位置不低于振幅中点
    },
    'signal': {
        'min_score': 55.0,
        'cooldown_days': 10,          # 同一股票两次信号最小间隔
    },
    'exit': {
        'horizons': [5, 10, 20, 40],
        'main_horizon': 20,
        'max_hold': 60,
        'cost': 0.002,
    },
    'backtest': {
        'date_start': '2017-01-01',
        'split': '2023-01-01',
        'seed': 42,
    },
    'reference': {'desc': '', 'code': '', 'date': ''},
}


def _deep_merge(base, over):
    """递归深合并：over 覆盖 base，dict 逐层合并，其余直接替换。"""
    out = dict(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config(path=None):
    """读取 config/burst.json，与内置 DEF 深合并（缺项回落默认）。"""
    p = path or CONFIG_PATH
    user = {}
    if os.path.isfile(p):
        with open(p, 'r', encoding='utf-8') as f:
            user = json.load(f)
    return _deep_merge(DEF, user)


def ensure_dirs():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
