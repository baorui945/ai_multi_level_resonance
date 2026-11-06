# -*- coding: utf-8 -*-
"""
==============================================================================
mlr.settings —— 路径、数据源定位、参数加载（全局唯一来源）
==============================================================================
本工程 `ai_multi_level_resonance`（简称 MLR）不复刻数据层，**直接复用
`ai_backtest_model`（ABM）的数据源**：

* 日线：ABM `.env` 的 `TABLE_BACKWARD_DAILY`（默认 backward_tdx_daily，后复权）
* 基准：ABM `data/idx_000300.pkl`（沪深300 收盘）
* 指标：ABM `backtest_model.factors.indicators.calc_ema`（EMA 递推 y0=x0，逐位一致）

周线 / 月线由**日线重采样**生成（口径统一、可更新到最新交易日）。
"""
import importlib.util
import json
import os
import sys

# ── 工程根 ────────────────────────────────────────────────────────────────
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(ROOT_DIR, 'config', 'resonance.json')

DATA_DIR = os.path.join(ROOT_DIR, 'data')
CACHE_DIR = os.path.join(DATA_DIR, '_cache')
OUTPUT_DIR = os.path.join(ROOT_DIR, 'output')
CHART_DIR = os.path.join(OUTPUT_DIR, 'charts')

# ── 数据源（ai_backtest_model）定位 ──────────────────────────────────────
ABM_ROOT = os.environ.get('ABM_ROOT') or r'F:\ai_backtest_model'


def _load_abm_config():
    """以独立模块名加载 ABM 的 config.py，避免与本工程同名模块冲突。"""
    path = os.path.join(ABM_ROOT, 'config.py')
    if not os.path.isfile(path):
        raise FileNotFoundError(
            f'找不到数据源工程 config.py: {path}\n'
            f'请设置环境变量 ABM_ROOT 指向 ai_backtest_model 目录')
    spec = importlib.util.spec_from_file_location('abm_config', path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ABM = _load_abm_config()
if ABM_ROOT not in sys.path:
    sys.path.insert(0, ABM_ROOT)

# 日线真源表名（后复权）与基准指数资产
TABLE_DAILY = ABM.TABLE_BACKWARD_DAILY
IDX_PKL = os.path.join(ABM_ROOT, 'data', 'idx_000300.pkl')
PX_CACHE = os.path.join(CACHE_DIR, 'daily_px.parquet')

# ── 股票池（成分股）来源 ────────────────────────────────────────────────
HS300_JSON = os.path.join(ROOT_DIR, 'config', 'hs300_constituents.json')
# 本工程未自带成分股文件时，回退复用 realTimeTradeFactor_V4.1 的同一份文件
HS300_JSON_V41 = r'F:\realTimeTradeFactor_V4.1\monitor\hs300_constituents.json'
# 动力煤板块（手工整理，见 config/coal_constituents.json）
COAL_JSON = os.path.join(ROOT_DIR, 'config', 'coal_constituents.json')

# ── 默认参数（可被 config/resonance.json 覆盖）───────────────────────────
DEF = {
    # universe：默认股票池。'hs300'=沪深300成分股（数据量小、口径同 V4.1）；
    #           'all'=全市场。可被命令行 --universe 覆盖。
    'universe': {'default': 'hs300'},
    'data': {
        'start': '2015-09-01',       # 与 ABM 日线表起点对齐
        'weekly_rule': 'W-FRI',      # 周线聚合（按周最后交易日标记）
        'monthly_rule': 'ME',        # 月线聚合（按自然月末最后交易日标记）
        # period_mode：周/月状态的对齐口径
        #   closed —— 只使用**已收盘**的周/月 K（名义周期末 <= 数据末日）。
        #             scan 与 backtest 完全一致；月末/节假日可能滞后 1 个交易日。
        #   asof   —— 使用**进行中**的周/月 K（截至当日重采样）。更灵敏，但
        #             回测（merge_asof 只用已收盘周期）无法复现同一状态。
        'period_mode': 'closed',
        'min_daily_bars': 400,       # 截至当日日线根数不足 → 三周期状态置 False
        # 月线历史：日线缓存从 2015-09 起，重采样只有 ~134 根；若要把月线
        # MACD 也改成 30/89/9（EMA89 需 178 根），需用 ABM backward_monthly
        # （2000 起）补历史。默认关：月线用 12/26/9 只需 52 个月暖机，且实测
        # 30/89/9 月线会把策略拖垮（年化 −4.6%/回撤 −70%，见 doc §10.3）。
        'monthly_history': False,
        'monthly_history_start': '2000-01-01',
    },
    'macd': {'fast': 30, 'slow': 89, 'signal': 9},
    'ma': {'short': 7, 'long': 21},
    # 单周期判定方法（method）：
    #   hole_cross —— 「挖坑→起爆」：金叉 + 有效绿柱坑 + 金叉后净上行（日线默认）
    #   parabola   —— 「向上抛物线」形态：DIF 二次拟合 a>0 且在上升支、DEA 同步、
    #                 红柱连续放大（周/月线默认，同一公式跨周期自相似）
    # hole_min_depth_ratio：绿柱坑「深度」= |hist 谷值| / 该根收盘价。
    # MACD 对价格线性 ⇒ 该比值对价格尺度不变，可跨高低价股通用。
    'timeframes': {
        'daily':   {'enabled': True, 'method': 'hole_cross',
                    'warmup_mult': 2.0,        # EMA89 冷启动：前 178 日 NaN
                    'max_cross_age': 120,
                    'hole_red_window': 12, 'hole_min_depth_ratio': 0.005,
                    'momentum_retreat': 0.0,
                    # 日周共振（dw）方案用：method 切到 'parabola' 后生效。
                    # 参数与周线同值——同一公式跨周期自相似：steepen 按每根
                    # 平均涨幅、rel 抬升无量纲，可直接移植到日线粒度。
                    'parabola': {'fit_window': 6, 'min_r2': 0.5,
                                 'min_rel_curvature': 0.05,
                                 'require_net_up': True,
                                 'require_steepen': True,
                                 'require_dea_up': True,
                                 'hist_grow_bars': 2, 'slope_bars': 2,
                                 'require_hist_positive': True,
                                 'require_above_ma_short': False,
                                 'require_ma_short_slope': False}},
        'weekly':  {'enabled': True, 'method': 'parabola',
                    'warmup_mult': 2.0,        # EMA89 冷启动：前 178 周 NaN
                    'parabola': {'fit_window': 6, 'min_r2': 0.5,
                                 'min_rel_curvature': 0.05,
                                 'require_net_up': True,
                                 'require_steepen': True,
                                 'require_dea_up': True,
                                 'hist_grow_bars': 2, 'slope_bars': 2,
                                 'require_hist_positive': True,
                                 'require_above_ma_short': False,
                                 'require_ma_short_slope': False}},
        'monthly': {'enabled': True, 'method': 'parabola',
                    # 三周期统一 MACD(30,89,9)。月线暖机用 1.0×slow=89 根：
                    # 2.0×89=178 根 > 全史 ~134 根，月线会永远不可用；89 根
                    # 版本 2015-09 起第 89 根（2023-02）可用，与补 2000 历史、
                    # 完全收敛版的状态一致率 97.6%。
                    'macd': {'fast': 30, 'slow': 89, 'signal': 9},
                    'warmup_mult': 1.0,        # 30/89/9 → 前 89 个月 NaN
                    'parabola': {'fit_window': 4, 'min_r2': 0.5,
                                 'min_rel_curvature': 0.02,
                                 'require_net_up': True,
                                 'require_steepen': True,
                                 'require_dea_up': True,
                                 'hist_grow_bars': 2, 'slope_bars': 1,
                                 'require_hist_positive': True,
                                 'require_above_ma_short': False,
                                 'require_ma_short_slope': False}},
    },
    'filters': {
        'require_above_ma': True,     # 收盘价 > MA_long
        'require_ma_align': True,     # MA_short > MA_long
        'require_no_bearish_div': False,   # 顶背离过滤（默认关）
    },
    # 入场模式：any_entry(共振首日) / fresh_daily(日线金叉后N根内) /
    #          slowest_turn(月线刚转多当日)
    'signal': {'mode': 'phase_wm', 'fresh_daily_days': 5,
               # 周月「上抛过程」合并阈值：间隙内月线红柱自运行峰值回撤超过
               # 该比例 → 新过程起点；回 0 下也算新过程。0.10=回撤 10%
               'wm_process_drawdown': 0.10,
               # dw 入场时「日线已领先周线的天数」上限（None=不过滤）。
               # 与日线腿口径强相关：hole_cross（默认）不要过滤（HS300 实测
               # 19.00%/MDD-26.3%；lead=0 仅 12.60%）；parabola 日线设 0
               # （18.64% vs 不过滤 16.58%，因 parabola 日线状态本就只有 1-2 天）。
               'dw_max_lead_days': None},
    'backtest': {
        'exec_mode': 'open_open',     # close_close / open_open
        'horizons': [20, 30, 40, 60],
        'main_horizon': 40,
        'cost': 0.002,                # 双边合计（0.1%+0.1%）
        'slippage_bp': 0.0,           # 单边滑点（bp），0=不计（与历史口径一致）
        'limit_up_pct': 0.097,        # 主板 open_open 建仓涨停剔除阈值
        'limit_up_by_board': True,    # True=按板块分档（主板 9.7% / 创业科创 19.7%）
        'limit_up_pct_gem': 0.197,    # 创业板(300/301)、科创板(688) 阈值
        'concurrent_cap': 0,          # 0=不限并发；>0 时按建仓先后截断
        # 事件驱动退出（复用 ABM build_trades 的 exit_cfg）；None=固定持有期。
        # 例：{"rule": "breakdown", "breakdown_ma": 21, "breakdown_eps": 0.01,
        #      "breakdown_days": 2, "time_stop": 0}
        'exit': None,
        'start': '2020-01-01',        # 信号统计起始（warmup 2×slow 后：周线
                                      #   EMA89 于 ~2019-04 收敛、月线 12/26/9
                                      #   于 ~2019-11 收敛，留 1 个月余量）
    },
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
    """读取 resonance.json，与内置默认深合并（缺项回落到默认）。"""
    p = path or CONFIG_PATH
    user = {}
    if os.path.isfile(p):
        with open(p, 'r', encoding='utf-8') as f:
            user = json.load(f)
    return _deep_merge(DEF, user)


def validate_config(cfg):
    """口径校验：返回提示列表（不改变行为，CLI 启动时打印一次）。

    主要覆盖「参数存在但静默不生效」的坑：
    * 全局 filters 只作用于 hole_cross，parabola 腿需用自己的均线开关；
    * period_mode 只接受 closed / asof；
    * 并发上限 / 滑点 / 退出规则是否已接线（仅提示当前取值）。
    """
    out = []
    filters = cfg.get('filters') or {}
    parab_tfs = [tf for tf, t in (cfg.get('timeframes') or {}).items()
                 if str(t.get('method', 'hole_cross')).lower() == 'parabola']
    if parab_tfs and (filters.get('require_above_ma')
                      or filters.get('require_ma_align')
                      or filters.get('require_no_bearish_div')):
        out.append(
            'parabola 腿（' + ', '.join(parab_tfs) + '）不使用全局 filters '
            '（require_above_ma / require_ma_align / require_no_bearish_div）；'
            '如需均线过滤请设 timeframes.<tf>.parabola.require_above_ma_long / '
            'require_ma_align / require_above_ma_short / require_ma_short_slope')
    mode = str((cfg.get('data') or {}).get('period_mode', 'closed')).lower()
    if mode not in ('closed', 'asof'):
        out.append(f'data.period_mode={mode!r} 非法（只接受 closed / asof），按 closed 处理')
    bt = cfg.get('backtest') or {}
    if mode == 'asof' and not bt.get('_allow_asof_backtest'):
        out.append('data.period_mode=asof：实盘用进行中周期，回测只用已收盘周期，'
                   '两者状态不可复现；回测请用 closed')
    return out


def ensure_dirs():
    for d in (DATA_DIR, CACHE_DIR, OUTPUT_DIR, CHART_DIR):
        os.makedirs(d, exist_ok=True)
