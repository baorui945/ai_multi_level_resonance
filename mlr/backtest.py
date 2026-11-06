# -*- coding: utf-8 -*-
"""
==============================================================================
mlr.backtest —— 多周期共振信号的历史回测（复用 ai_backtest_model 回测口径）
==============================================================================
不再复刻回测引擎：**直接调用 ABM** 的

* ``backtest_model.data.build_panel``     —— 价格面板 / 沪深300 基准 / 交易日历
* ``backtest_model.engine.build_trades``  —— 去重建仓（持有期内不重复建仓）
* ``backtest_model.engine.run_backtest``  —— 日频等权净值（含成本/滑点）
* ``backtest_model.engine.perf_metrics``  —— 年化 / 夏普 / 回撤 / 利用率

执行口径（与 ABM 主口径一致）：
* ``close_close``：信号日 T 收盘建仓、T+N 收盘平仓
* ``open_open``  ：T+1 开盘建仓、T+N+1 开盘平仓（**默认主口径**）

成本默认双边合计 0.2%，单边滑点 `backtest.slippage_bp`（默认 0，传入 ABM
`run_backtest` 并计入逐笔净收益）。其余接线：

* **面板起点** = `backtest.start`（不是 ABM 的 DEF_START）：否则 2020 前的空仓期
  会被算进年化/夏普/α（实测同一策略 12.93% → 22.81%）；
* **右边界**：`signal_date > 面板末日` 的信号计入 `n_skip_after` 并提示，不再静默丢弃；
* **涨停分档**：`limit_up_by_board=True` 时主板 9.7%、创业板/科创板 19.7%
  （ABM 只支持单一阈值，故传 None 后在本模块按代码前缀补做）；
* **并发上限**：`concurrent_cap>0` 按建仓先后贪心截断，计入 `n_skip_cap`；
* **事件退出**：`exit` 非空时透传 ABM `build_trades(exit_cfg=...)`
  （trail_pct / chandelier / breakdown + time_stop / breakeven_atr），默认固定持有期。
"""
import numpy as np
import pandas as pd

from . import settings as S

from backtest_model import data as ABM_DATA          # noqa: E402
from backtest_model import engine as ABM_E           # noqa: E402
from backtest_model import settings as ABM_S         # noqa: E402


def alpha_tstat(nav, nav_base, ann=252.0):
    """CAPM 口径 α（年化 %）与其 t 值：策略日收益对基准日收益回归取截距。

    t 值 = 截距系数 / 同方差标准误（t 对分子分母的缩放不变，**不要**再乘
    √ann——重复年化会把 t 虚增 √252≈15.9 倍）。
    注意：持有期内的日收益存在自相关（H 日重叠交易），严格显著性检验应改用
    Newey-West/HAC 标准误，同方差口径下的 t普遍 **偏乐观**。
    """
    r = np.diff(nav) / nav[:-1]
    b = np.diff(nav_base) / nav_base[:-1]
    if len(r) < 3 or np.std(b) == 0:
        return np.nan, np.nan
    X = np.column_stack([np.ones_like(b), b])
    coef, *_ = np.linalg.lstsq(X, r, rcond=None)
    resid = r - X @ coef
    dof = len(r) - 2
    if dof <= 0:
        return np.nan, np.nan
    s2 = (resid @ resid) / dof
    xtx_inv = np.linalg.inv(X.T @ X)
    se = np.sqrt(s2 * xtx_inv[0, 0])
    if se == 0:
        return np.nan, np.nan
    alpha = coef[0] * ann * 100.0
    return float(alpha), float(coef[0] / se)


def trade_stats(detail):
    """逐笔净收益统计（笔数 / 均值 / t / 胜率）。"""
    if detail is None or not len(detail):
        return {'笔数': 0, '逐笔净均(%)': np.nan, '逐笔净t': np.nan,
                '逐笔净胜率(%)': np.nan}
    x = detail['净收益(%)'].to_numpy('float64') / 100.0
    x = x[np.isfinite(x)]
    n = len(x)
    if n == 0:
        return {'笔数': 0, '逐笔净均(%)': np.nan, '逐笔净t': np.nan,
                '逐笔净胜率(%)': np.nan}
    mean, sd = x.mean(), x.std(ddof=1) if n > 1 else np.nan
    t = mean / (sd / np.sqrt(n)) if (n > 1 and sd > 0) else np.nan
    return {'笔数': n, '逐笔净均(%)': round(mean * 100, 2),
            '逐笔净t': round(float(t), 2) if t == t else np.nan,
            '逐笔净胜率(%)': round((x > 0).mean() * 100, 1)}


def _signal_coverage(panel, sig):
    """覆盖校验：signal_date 是否早于该股在价格面板中的首个有效交易日。

    ABM `fetch_prices` 命中缓存时只检查股票代码，**不检查日期区间**；缓存只剩
    最近一年时，早期信号会被 `build_trades` **静默跳过**（不计入 n_skip），
    净值在前几年恒为 1.0、全部收益挤进最后一年——整份回测退化成"只回测末段"，
    年化/回撤/α 全部不可用。

    Returns:
        dict(n_nostock=..., n_early=..., n_ok=...,
             first_gap=(stock_code, signal_date, panel_first_date) | None)
    """
    n_nostock = n_early = n_ok = 0
    first_gap = None
    for code, g in sig.groupby('stock_code', sort=False):
        j = panel.col.get(code)
        if j is None:
            n_nostock += len(g)
            continue
        seq = panel.pos_seq[j]
        if len(seq) == 0:
            n_nostock += len(g)
            continue
        first_day = pd.Timestamp(panel.calendar[int(seq[0])])
        early = g[g['signal_date'] < first_day]
        n_ok += len(g) - len(early)
        n_early += len(early)
        if len(early) and first_gap is None:
            first_gap = (code, g['signal_date'].min(), first_day)
    return {'n_nostock': n_nostock, 'n_early': n_early, 'n_ok': n_ok,
            'first_gap': first_gap}


def _limit_up_threshold(code, bt):
    """按板块取涨停剔除阈值：创业板(300/301)/科创板(688) 20cm，其余用主板值。"""
    c = str(code).zfill(6)
    if c.startswith(('300', '301', '688')):
        return float(bt.get('limit_up_pct_gem', 0.197))
    return float(bt.get('limit_up_pct', 0.097))


def _apply_board_limit_up(panel, trades, bt, exec_mode):
    """open_open/vwap 下按板块阈值剔除「开盘即涨停买不进」的建仓。

    ABM `build_trades` 只接受单一阈值；这里传 None 关掉它的剔除，再按代码
    前缀分档补做——否则创业板/科创板 +10% 的强势开盘会被误判成涨停。
    """
    if exec_mode not in ('open_open', 'vwap') or trades is None or not len(trades):
        return trades, 0
    keep, drop = [], 0
    for row in trades.itertuples():
        j = panel.col.get(str(row.stock_code))
        if j is None:
            keep.append(row.Index)
            continue
        p_in = panel.Popen[int(row.entry_pos), j]
        prev_c = panel.Pclose[int(row.in_pos), j]
        thr = _limit_up_threshold(row.stock_code, bt)
        if np.isfinite(p_in) and prev_c > 0 and p_in / prev_c - 1.0 >= thr:
            drop += 1
            continue
        keep.append(row.Index)
    return trades.loc[keep].reset_index(drop=True), drop


def _apply_concurrent_cap(trades, cap):
    """并发持仓上限：按建仓先后贪心截断（先到先得），返回 (trades, 剔除数)。"""
    if not cap or trades is None or not len(trades):
        return trades, 0
    import heapq
    t = trades.sort_values(['entry_pos', 'stock_code'])
    heap, keep = [], []
    for row in t.itertuples():
        while heap and heap[0] <= row.entry_pos:
            heapq.heappop(heap)
        if len(heap) >= int(cap):
            continue
        heapq.heappush(heap, row.exit_pos)
        keep.append(row.Index)
    return (t.loc[keep].sort_values(['entry_pos', 'stock_code'])
            .reset_index(drop=True), len(trades) - len(keep))


def run(signals, cfg=None, horizons=None, refresh=False, verbose=True):
    """对共振信号跑多持有期回测。

    Args:
        signals: `mlr.resonance.signals` 的输出（含 stock_code / signal_date）
        horizons: 持有期列表；默认取配置 backtest.horizons
    Returns:
        dict(panel, nav_base, benchmark, horizons, start, results)
        results[h] = dict(nav, metrics, alpha, alpha_t, trades, detail, n_sig, ...)
    """
    cfg = cfg or S.load_config()
    bt = cfg['backtest']
    horizons = horizons or bt['horizons']
    exec_mode = bt['exec_mode']
    cost = bt['cost']
    slippage_bp = float(bt.get('slippage_bp', 0.0) or 0.0)
    cap = int(bt.get('concurrent_cap', 0) or 0)
    exit_cfg = bt.get('exit') or None
    board_lu = bool(bt.get('limit_up_by_board', True))
    lu = None if board_lu else bt['limit_up_pct']
    # 面板起点 = 信号统计起点：否则 2020 前的空仓期会被算进年化/夏普/α
    start = pd.Timestamp(bt.get('start') or ABM_S.DEF_START)

    sig = signals.copy()
    sig['stock_code'] = sig['stock_code'].astype(str).str.zfill(6)
    sig['signal_date'] = pd.to_datetime(sig['signal_date'])
    # 若模式本身起步晚于 backtest.start（如月线暖机到 2023 才可用），
    # 面板也跟到首个信号日，避免前面几年空仓期稀释年化/夏普/α。
    if len(sig):
        start = max(start, pd.Timestamp(sig['signal_date'].min()))

    panel, idx, nav_base = ABM_DATA.build_panel(sig, start=start,
                                                refresh=refresh)
    cov = _signal_coverage(panel, sig)
    cal_last = pd.Timestamp(panel.calendar[-1])
    n_after = int((sig['signal_date'] > cal_last).sum())
    if verbose:
        print(f'  [覆盖] 信号{len(sig)}条：面板内可用{cov["n_ok"]}'
              f' / 股票无报价{cov["n_nostock"]} / 早于面板首日{cov["n_early"]}'
              f' / 晚于面板末日{n_after}')
        if n_after:
            print(f'  [提示] {n_after} 条信号晚于价格面板最后日 '
                  f'{cal_last.date()}（ABM 缓存未更新？用 --refresh 重建），'
                  f'已剔除且不计入交易')
    if cov['n_early']:
        code, sdate, pdate = cov['first_gap']
        first_day = pd.Timestamp(panel.calendar[0]).date()
        raise RuntimeError(
            f'价格面板覆盖不足：{cov["n_early"]}/{len(sig)} 条信号的 signal_date '
            f'早于该股在 ABM 价格缓存中的首个交易\n'
            f'  （例：{code} 信号 {sdate.date()} < 面板首日 {pdate}；'
            f'日历首日 {first_day}）。样本被截断，指标全部失真。\n'
            f'  → 传入 refresh=True（CLI 加 --refresh）重建 ABM 价格缓存，'
            f'或检查 F:\\ai_backtest_model\\data\\_cache\\bt_daily_px.parquet '
            f'是否覆盖 2015-09 至今的全历史。')
    base_m = ABM_E.perf_metrics(nav_base, panel.calendar,
                                ABM_E.years_of(panel.calendar))

    results = {}
    for h in horizons:
        trades, sk, sk_lu = ABM_E.build_trades(sig, panel, h, exec_mode, lu,
                                               exit_cfg=exit_cfg)
        if board_lu:
            trades, n_lu2 = _apply_board_limit_up(panel, trades, bt, exec_mode)
            sk_lu += n_lu2
        trades, n_cap = _apply_concurrent_cap(trades, cap)
        r = ABM_E.run_backtest(panel, trades, cost, exec_mode,
                               slippage_bp=slippage_bp)
        m = ABM_E.perf_metrics(r['nav'], panel.calendar, r['years'],
                               r['hold_cnt'])
        detail = ABM_E.trades_detail(panel, trades, cost, exec_mode,
                                     slippage_bp=slippage_bp)
        a, at = alpha_tstat(r['nav'], nav_base)
        # 面板内信号 - 成交 - 数据不足 - 涨停 - 并发上限 = 持有期内去重丢弃
        n_dedup = max(0, len(sig) - n_after - len(trades) - sk - sk_lu - n_cap)
        results[h] = {
            'nav': r['nav'], 'metrics': m, 'alpha': a, 'alpha_t': at,
            'trades': trades, 'detail': detail, 'n_sig': len(sig),
            'n_trade': r['n_trade'], 'n_skip': sk, 'n_skip_lu': sk_lu,
            'n_skip_cap': n_cap, 'n_dedup': n_dedup, 'n_skip_after': n_after,
            'trade_stats': trade_stats(detail),
            'n_skip_nostock': cov['n_nostock'], 'n_skip_early': cov['n_early'],
        }
        if verbose:
            print(f'  H{h:<3} 信号{len(sig):>6} → 交易{r["n_trade"]:>5}  '
                  f'年化{m["年化收益(%)"]:>7.2f}%  α{a:>7.2f}(t {at:>5.2f})  '
                  f'夏普{m["夏普"]:>5.2f}  回撤{m["最大回撤(%)"]:>7.2f}%  '
                  f'逐笔{results[h]["trade_stats"]["逐笔净均(%)"]:>6.2f}%'
                  f'/{results[h]["trade_stats"]["笔数"]}笔  '
                  f'[去重{n_dedup} 涨停{sk_lu} 并发{n_cap} 面板外{n_after}]')
    return {'panel': panel, 'nav_base': nav_base, 'benchmark': base_m,
            'horizons': horizons, 'exec_mode': exec_mode, 'cost': cost,
            'slippage_bp': slippage_bp, 'concurrent_cap': cap,
            'exit': exit_cfg, 'start': start,
            'results': results, 'cfg': cfg}
