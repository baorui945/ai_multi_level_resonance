# -*- coding: utf-8 -*-
"""
==============================================================================
coil.settings —— 独立策略的路径与参数（唯一来源，与旧策略互不共享）
==============================================================================
只做两件事：

1. 读取 `config/coil.json`，与 `DEF` 深合并（缺项自动回落）；
2. 把**数据管道**相关常量从 `mlr.settings` 单向引入（表名 / parquet 缓存 /
   沪深300 基准），避免另写一套数据库连接造成口径漂移。

注意：本模块**不读取** `config/resonance.json`，也不修改 `mlr` 的任何对象。
"""
import json
import os

# ── 单向复用：只取数据层常量，不取任何策略参数 ───────────────────────────
from mlr import settings as _MLR

ROOT_DIR = _MLR.ROOT_DIR
CONFIG_PATH = os.path.join(ROOT_DIR, 'config', 'coil.json')
OUTPUT_DIR = os.path.join(_MLR.OUTPUT_DIR, 'coil')

TABLE_DAILY = _MLR.TABLE_DAILY        # backward_tdx_daily（后复权）
PX_CACHE = _MLR.PX_CACHE              # data/_cache/daily_px.parquet
IDX_PKL = _MLR.IDX_PKL                # data/idx_000300.pkl（沪深300 收盘）
HS300_JSON = _MLR.HS300_JSON

# ── 默认参数（可被 config/coil.json 覆盖）─────────────────────────────────
DEF = {
    'data': {
        'start': '2015-09-01',
        'min_bars': 400,              # 单只股票最少日线根数（MA120 + 蓄势窗口预热）
    },
    'macd': {'fast': 30, 'slow': 89, 'signal': 9},
    'ma': {'short': 7, 'mid': 21, 'long': 120},
    'coil': {
        'win': 120,                  # 蓄势窗口 N：只用 [t-N, t-1]
        'max_cv': 0.086,             # std(P)/mean(P)              → 方差极小化
        'max_box_w': 0.223,          # (q90-q10)/q50               → 箱体宽度
        'max_abs_beta': 0.0015,      # |dlnP/dt| 日均斜率          → P'(t)≈0
        'max_abs_curv': 0.00003,     # |二次拟合 a|                → P''(t)≈0
        'vol_compress': 1.06,        # std(r,后半)/std(r,前半)     → 波动率压缩
        'max_ma_gap': 0.080,         # max(|MA7-MA21|,|MA21-MA120|)/close → 均线粘合
        'max_ma120_slope': 0.0014,   # MA120 走平
        'max_macd_band': 0.041,      # mean(|DIF|)/close           → 零轴粘合
        'max_hist_band': 0.0142,     # std(HIST)/close
        'max_cross_n': 6,            # 窗口内金叉次数上限
        'min_coil_bars': 60,         # 蓄势最短持续根数
        'min_coil_strength': 0.50,   # 蓄势强度下限
    },
    'breakout': {
        'box_q': 0.90,               # 箱体上沿分位（只用 t-1 及以前）
        'eps': 0.005,                # close > box_high*(1+eps)
        'vol_mult': 1.5,             # 当日量 / 前20日均量
        'fresh_days': 5,             # MACD 金叉新鲜度
        'require_hist_grow': True,   # hist_t > hist_{t-1} > 0
        'require_ma_stack': True,    # MA7 > MA21 > MA120
        'cooldown_days': 20,         # 同一股票两次信号最小间隔
    },
    'score': {
        'w_compress': 0.30, 'w_deriv': 0.15, 'w_ma': 0.20,
        'w_macd': 0.20, 'w_hist': 0.075, 'w_cross': 0.075,
        'w_break': 0.65, 'w_vol': 0.35, 'w_coil_total': 0.70,
    },
    'exit': {
        'horizons': [5, 10, 20, 40, 60],
        'main_horizon': 40,
        'shrink_days': 6,
        'max_hold': 120,
        'ma_exit_period': 21,
        'cost': 0.002,
    },
    'backtest': {
        'date_start': '2017-01-01',
        'split': '2023-01-01',
        'seed': 42,
    },
    'reference': {'desc': '', 'code': '', 'breakout_date': ''},
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
    """读取 config/coil.json，与内置 DEF 深合并（缺项回落默认）。"""
    p = path or CONFIG_PATH
    user = {}
    if os.path.isfile(p):
        with open(p, 'r', encoding='utf-8') as f:
            user = json.load(f)
    return _deep_merge(DEF, user)


def ensure_dirs():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
