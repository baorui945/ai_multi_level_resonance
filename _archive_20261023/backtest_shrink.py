# -*- coding: utf-8 -*-
"""
==============================================================================
backtest_shrink —— 「金叉买入 + MACD柱连续N日缩短卖出」· 实盘资金约束回测
==============================================================================
在 `template_scan` / `optimize_shrink` 已选定的策略之上，再加一层**实盘资金
约束**，回答「这套策略真拿一笔钱去做，能赚多少、回撤多大、钱用了几成」。

策略（与 template_scan 一致，但**成交日可切换**）
-------------------------------------------
1. 以某段 DIF/DEA 为模板做全市场形状匹配（`--threshold`）；
2. 命中窗口内寻找**最后一次金叉**（DIF 上穿 DEA），成交日由 `--entry-mode` 决定：

   * `confirm`（**默认，可实盘**）：形态确认日（窗口终点 e）收盘买入，且可要求
     金叉距确认日 `e - b <= --fresh-days`（默认 5 根）。卖出规则同样从 e 起算。
     这是真正能落地的口径 —— 与 `scan_buy.py` / `scan_buy_history.py` 完全一致；
   * `cross`（旧口径，**含前视偏差**）：金叉日 b 收盘买入（b 比 e 早 13~37 根，
     中位 24 根）。窗口要在 e 日才被 score 确认，却回到 b 日成交，实盘拿不到
     这个价格，仅用于复现历史 Excel。

3. 买入后第一次出现「MACD柱 2*(DIF-DEA) **连续 N 日**逐日缩短」那一天收盘卖出
   （`--shrink-days`，默认 **N=6**：全市场扫描的综合最优档）。

实盘资金约束（新增层）
----------------------
* **固定总资金** `--capital`（默认 100 万），净值以它为基数滚动；
* **并发持仓上限** `--max-concurrent` K（默认 5 只）：同一时点最多持有 K 只，
  名额按成交日区间 `[entry_pos, exit_pos]` 占用，平仓日即可释放（资金复用）；
  名额已满时新信号**直接丢弃**（计入「因并发上限丢弃笔数」），不排队、不补单；
* 单票名义仓位 = `capital / K`，因此日频组合即「等权 K 只」。

净值口径（复用 ai_backtest_model，与工程其他回测逐位一致）
---------------------------------------------------------
* 日频**等权**组合：当日成本率 = `(新开笔数 + 平仓笔数) / 当日持仓数 × cost/2`
* 空仓日收益记 0 → 年化 / 夏普 / 资金利用率都是「资金实际在策略里的表现」
* 逐日净值 → 总收益 / 年化 / 年化波动 / 夏普 / 最大回撤 / Calmar / 回撤时长 /
  日胜率 / 资金利用率 / 持仓日胜率，并给出 CAPM 口径 α 与 α-t（对沪深300）

成本（默认 A 股明细口径，`--cost` 可切统一双边口径）
---------------------------------------------------
买入 = max(额×佣金率, 最低佣金) + 额×过户费率
卖出 = max(额×佣金率, 最低佣金) + 额×过户费率 + 额×印花税率（印花税仅卖出）
默认 佣金万2.5/单笔最低5元、过户费0.001%、印花税0.05% -> 双边合计约 0.102%。
ABM 净值引擎只接受**单一双边合计成本**，故此处按上式折算：
    cost = (佣金+过户) + (佣金+过户+印花) = 0.00026 + 0.00076 = 0.00102
（单笔最低佣金在组合费率口径下无法逐笔体现，仅影响小资金/极小仓位，已注明）

用法::

    python backtest_shrink.py --universe all --threshold 0.90          # 可实盘口径（默认 confirm）
    python backtest_shrink.py --universe all --threshold 0.90 --fresh-days 3
    python backtest_shrink.py --universe all --threshold 0.90 --exec open_open   # 次日开盘买，更保守
    python backtest_shrink.py --universe all --threshold 0.98 --entry-mode cross # 复现旧（含前视）结果
    python backtest_shrink.py --universe all --threshold 0.90 --max-concurrent 10
    python backtest_shrink.py --universe all --threshold 0.90 --capital 500000
    python backtest_shrink.py --universe hs300,coal --threshold 0.90 --out output/bs.xlsx
    python backtest_shrink.py --universe all --threshold 0.90 --step 3 --limit 200   # 冒烟
    python backtest_shrink.py --universe all --threshold 0.98 --entry-mode cross \
        --template-part pre --dump-matches output/match_pre.csv   # 只取金叉前段模板
    python backtest_shrink.py --universe all --threshold 0.98 --entry-mode shape \
        --end 2026-02-06 --shape-curv 0.3                        # 纯抛物线形态模板

产物（output/）：backtest_shrink_*.xlsx / charts/shrink_nav_*.png
"""
import argparse
import os
import sys
import time

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mlr import backtest as BT, data as D, report as RP, settings as S   # noqa: E402
from mlr.macd import cross_flags, macd as calc_macd                     # noqa: E402
from template_scan import (_fee_desc, _resolve_pool, _scan, _series,  # noqa: E402
                           zscore)

# backtest_model（ABM）默认不在本工程目录下，用 mlr.settings 已解析的根目录补 sys.path，
# 避免必须手工设置 PYTHONPATH
if S.ABM_ROOT not in sys.path:
    sys.path.insert(0, S.ABM_ROOT)

from backtest_model import data as ABM_DATA                              # noqa: E402
from backtest_model import engine as ABM_E                               # noqa: E402
from backtest_model import portfolio as ABM_P                            # noqa: E402


def eff_cost(args):
    """把（非对称）A股明细费率折算成 ABM 单参数口径的「双边合计成本」。

    卖出含印花税，故买卖本不对称；ABM 净值引擎按 `cost/2` 两侧各扣一半，
    这里取两侧之和作为等效双边成本（偏差约 0.5 个基点，见模块 docstring）。
    """
    if getattr(args, 'cost', None) is not None:
        return float(args.cost)
    buy = args.commission + args.transfer
    sell = args.commission + args.transfer + args.stamp
    return buy + sell


def eff_rate(args, side='buy'):
    """单边费率（小数）：买入 = 佣金 + 过户费；卖出再加印花税。"""
    if getattr(args, 'cost', None) is not None:
        return float(args.cost) / 2.0
    r = args.commission + args.transfer
    return r + (args.stamp if side == 'sell' else 0.0)


def build_trades(comb, panel, exec_mode='close_close', limit_up_pct=None):
    """把 `template_scan` 的匹配表（仅已了结交易）转成 ABM 的 trades 结构。

    ABM 交易表列：``stock_code / signal_date / in_pos / out_pos /
    entry_pos / exit_pos``（pos = **全局交易日历**下标）。

    * ``close_close``：信号日收盘买、目标日收盘卖 -> in=entry=买入日、out=exit=卖出日
    * ``open_open``  ：次日开盘买、目标次日开盘卖 -> entry=买入日+1、exit=卖出日+1
      （``limit_up_pct`` 生效：成交价较前收涨幅 >= 阈值视为一字板买不进并剔除）

    Returns:
        (trades, 日期未落在交易日历/区间无效的剔除笔数, 涨停剔除笔数)
    """
    cal = panel.calendar
    cal_pos = panel.cal_pos
    T = len(cal)
    intraday = exec_mode in ('open_open', 'vwap')
    rows, n_unmapped, n_lu = [], 0, 0
    for r in comb.itertuples():
        code = str(r.stock_code).zfill(6)
        if code not in panel.col:
            n_unmapped += 1               # 价格数据缺失（未上市 / 已退市）
            continue
        b = cal_pos.get(pd.Timestamp(r.buy_date))
        s = cal_pos.get(pd.Timestamp(r.sell_date))
        if b is None or s is None or s <= b:
            n_unmapped += 1
            continue
        if intraday:
            e, x = min(b + 1, T - 1), min(s + 1, T - 1)
            if x <= e:
                n_unmapped += 1
                continue
            if limit_up_pct is not None:
                j = panel.col.get(code)
                if j is not None:
                    px = panel.Popen[e, j]
                    prev = panel.Pclose[b, j]
                    if (np.isfinite(px) and prev > 0
                            and px / prev - 1.0 >= limit_up_pct):
                        n_lu += 1
                        continue
        else:
            e, x = b, s
        rows.append((code, cal[b].strftime('%Y-%m-%d'), b, s, e, x))
    t = pd.DataFrame(rows, columns=['stock_code', 'signal_date', 'in_pos',
                                    'out_pos', 'entry_pos', 'exit_pos'])
    if len(t):
        t['hold_days'] = t['exit_pos'] - t['entry_pos']
    return t, n_unmapped, n_lu


def run_slots(panel, trades, K, capital, buy_rate, sell_rate, exec_mode,
              slot_base='equity'):
    """**固定资金槽位**口径的日频净值（实盘资金约束的核心）。

    与 ABM ``run_backtest``（等权、资金永远满额）的区别：

    * 本口径每笔建仓金额为「**建仓基准 / K**」——K 个仓位各占一份，
      未建满时剩余资金以**现金**闲置（收益 0，但占用/回撤统计里如实体现）；
    * ABM 等权口径的单票权重是「1 / 当日实际持仓数」，持仓只剩 1 只时即
      100% 满仓押注，会把年化抬到失真（并隐含每日再平衡成本）。

    ``slot_base`` 决定「建仓基准」用哪个（**这是实盘假设的关键分歧点**）：

    * ``'equity'``（默认）：用**当日总权益 / K**。账户赚钱后下一笔投入自动
      变大，等于"盈利按比例复投"——复利被完整放大，报出的年化偏乐观；
    * ``'fixed'``：用**初始资金 / K**。每笔金额恒定，赚到的钱只进现金池、
      不再扩大下注，对应"每笔固定投 20 万"的常见实盘习惯。

    逐日顺序：先按成交价平仓（资金当日即可复用）-> 重估权益 -> 建仓
    -> 收盘重估。买入按 ``amt * (1 - buy_rate)`` 折价成交、卖出按
    ``sh * px * (1 - sell_rate)`` 计价，因此**买卖非对称成本（含印花税）被精确
    还原**，无需像 ABM 那样折算成单一半额。

    Returns:
        dict(nav, hold_cnt, open_cnt, close_cnt, exposure, n_trade,
             slot_base, years)
    """
    T = len(panel.calendar)
    mode = {'close_close': 'close', 'open_open': 'open', 'vwap': 'vwap'}[exec_mode]
    opens, closes = {}, {}
    for r in trades.itertuples():
        j = panel.col[r.stock_code]
        opens.setdefault(int(r.entry_pos), []).append(
            (j, panel.exec_price(j, int(r.entry_pos), mode)))
        closes.setdefault(int(r.exit_pos), []).append(
            (j, panel.exec_price(j, int(r.exit_pos), mode)))

    cash = float(capital)
    pos = {}                                   # 股票列号 -> 持股数
    nav = np.zeros(T)
    hold_cnt = np.zeros(T)
    open_cnt = np.zeros(T)
    close_cnt = np.zeros(T)
    exposure = np.zeros(T)
    for t in range(T):
        for j, px in closes.get(t, ()):        # 1) 平仓（先释放现金）
            sh = pos.pop(j, 0.0)
            if sh <= 0 or not np.isfinite(px):
                continue
            cash += sh * px * (1.0 - sell_rate)
            close_cnt[t] += 1.0
        mtm = 0.0
        for j, sh in pos.items():
            p = panel.Pclose[t, j]
            mtm += sh * (p if np.isfinite(p) else 0.0)
        equity = cash + mtm
        base_amt = (equity if slot_base == 'equity' else float(capital)) / K
        for j, px in opens.get(t, ()):         # 2) 按「建仓基准 / K」建仓
            if j in pos or not np.isfinite(px) or px <= 0:
                continue
            amt = min(base_amt, cash)
            if amt <= 1e-9:
                continue
            pos[j] = amt * (1.0 - buy_rate) / px
            cash -= amt
            open_cnt[t] += 1.0
        mtm = 0.0
        for j, sh in pos.items():
            p = panel.Pclose[t, j]
            mtm += sh * (p if np.isfinite(p) else 0.0)
        nav[t] = cash + mtm
        hold_cnt[t] = float(len(pos))
        exposure[t] = (mtm / nav[t]) if nav[t] > 0 else 0.0
    return {'nav': nav / float(capital), 'hold_cnt': hold_cnt,
            'open_cnt': open_cnt, 'close_cnt': close_cnt,
            'exposure': exposure, 'n_trade': len(trades),
            'slot_base': slot_base,
            'years': ABM_E.years_of(panel.calendar)}



def constraint_table(comb, done, trades, trades_c, cap_info, r, metrics,
                     capital, K, exec_mode, cost, shrink_days,
                     entry_mode='cross', fresh_days=None, stale=0,
                     shape_curv=None):
    """「实盘资金约束」说明表（键值两列，便于写入 Excel）。"""
    hold = np.asarray(r['hold_cnt'], dtype='float64')
    act = hold > 0
    full = hold >= (K if K else max(float(hold.max()), 1.0))

    def _f(v, n=2):
        return round(float(v), n) if v == v else np.nan

    if 'exposure' not in r:
        nav_desc = 'ABM 等权（资金永远满额）'
    elif r.get('slot_base') == 'fixed':
        nav_desc = '固定金额槽位（每笔=初始资金/K，盈利不追加）'
    else:
        nav_desc = '固定资金槽位（每笔=权益/K，盈利复投）'
    if entry_mode == 'confirm':
        entry_desc = '形态确认日收盘（可实盘）'
    elif entry_mode == 'shape':
        entry_desc = '形态完成日收盘（不看金叉，可实盘）'
    else:
        entry_desc = '窗口内最后金叉日收盘（含前视偏差）'

    rows = [
        ('初始总资金(元)', f'{capital:,.0f}'),
        ('并发持仓上限 K(只)', K if K else '不限'),
        ('单票名义仓位(元)', f'{capital / K:,.0f}' if K else '按当日持仓数均分'),
        ('执行口径', exec_mode),
        ('买入口径', entry_desc),
        ('曲率权重 w', (f'{shape_curv:.2f}（{(1 - shape_curv):.2f}×水平相关 + '
                        f'{shape_curv:.2f}×二阶差分相关）'
                        if entry_mode == 'shape' and shape_curv else
                        ('0（只看 DIF/DEA 水平形状）'
                         if entry_mode == 'shape' else '不适用'))),
        ('金叉新鲜度(根)', ('不限' if fresh_days is None or fresh_days < 0
                            else int(fresh_days)) if entry_mode == 'confirm'
                           else f'不适用（{entry_mode} 口径不看金叉新鲜度）'),
        ('卖出规则', f'MACD柱连续{shrink_days}日缩短'
                     f'（第{shrink_days}个缩短日收盘卖）'),
        ('等效双边成本(%)', _f(cost * 100, 4)),
        ('净值口径', nav_desc),
        ('', '————————'),
        ('匹配命中窗口', len(comb)),
        ('金叉过旧被剔除窗口', int(stale) if entry_mode == 'confirm' else '不适用'),
        ('参与回测交易', len(done)),
        ('资金约束前笔数', len(trades)),
        ('资金约束后参与回测笔数', len(trades_c)),
        ('因并发上限丢弃笔数', cap_info.get('skipped_capacity', 0)),
        ('', '————————'),
        ('样本交易日数', len(hold)),
        ('有持仓交易日数', int(act.sum())),
        ('最大同时持仓(只)', int(hold.max()) if len(hold) else 0),
        ('平均同时持仓(只)', _f(hold[act].mean()) if act.any() else 0.0),
        ('资金利用率(%)', _f(metrics['资金利用率(%)'], 1)),
        ('平均资金占用率(%)', _f(np.mean(r['exposure']) * 100, 1)
         if 'exposure' in r else np.nan),
        ('满仓天数占比(%)', _f(full.mean() * 100, 1) if len(hold) else 0.0),
        ('日胜率含空仓(%)', _f(metrics['日胜率含空仓(%)'], 1)),
        ('持仓日胜率(%)', _f(metrics['持仓日胜率(%)'], 1)),
        ('', '————————'),
        ('总收益(%)', _f(metrics['总收益(%)'])),
        ('年化收益(%)', _f(metrics['年化收益(%)'])),
        ('年化波动(%)', _f(metrics['年化波动(%)'])),
        ('夏普', _f(metrics['夏普'])),
        ('最大回撤(%)', _f(metrics['最大回撤(%)'])),
        ('Calmar', _f(metrics['Calmar'])),
        ('回撤时长(交易日)', metrics['回撤时长(交易日)']),
        ('期末净值(初始=1)', _f(r['nav'][-1], 4)),
        ('期末资产(元)', f'{capital * float(r["nav"][-1]):,.0f}'),
    ]
    return pd.DataFrame(rows, columns=['项目', '值'])


def k_scan_table(panel, trades, k_list, args, cost):
    """逐 K 回测（固定资金槽位口径）的并发上限对比表。

    匹配 / 面板 / 交易表只算一次，只对 K 重跑「并发筛选 + 资金槽位净值」，
    用于回答「资金分成几个仓位最划算」：K 越大越能容纳信号（丢弃更少），
    但单票仓位越小、单笔贡献越低。

    **每个 K 都跑两种建仓基准**（见 ``run_slots`` 的 ``slot_base``）：

    * ``equity``（主口径，列名无后缀）：每笔投「当日权益/K」，盈利按比例
      复投 —— 对应"有多少钱就按 K 份投出去"，复利被完整放大；
    * ``fixed``（列名带 ``_固定金额`` 后缀）：每笔恒投「初始资金/K」，
      赚到的钱只进现金池 —— 可作为**收益下界**，用来隔离"复利放大"的贡献。
    """
    rows = []
    for kk in sorted(set(k_list)):
        tc, ci = ABM_P.cap_trades(trades, {'max_concurrent': kk,
                                           'weighting': 'equal'}, sig=None)
        got = {}
        for sb in ('equity', 'fixed'):          # 两种建仓基准（见 run_slots）
            rr = run_slots(panel, tc, kk, args.capital, eff_rate(args, 'buy'),
                           eff_rate(args, 'sell'), args.exec_mode, sb)
            got[sb] = (rr, ABM_E.perf_metrics(rr['nav'], panel.calendar,
                                              rr['years'], rr['hold_cnt']))
        rr, mm = got['equity']                  # 主口径：盈利按比例复投
        _, mf = got['fixed']                    # 对照口径：每笔金额恒定
        tt = BT.trade_stats(ABM_E.trades_detail(panel, tc, cost,
                                                args.exec_mode))
        hold = np.asarray(rr['hold_cnt'], dtype='float64')
        act = hold > 0
        n_skip = ci.get('skipped_capacity', 0)
        rows.append({
            'K(并发上限)': kk,
            '参与笔数': len(tc),
            '丢弃笔数': n_skip,
            '丢弃占比%': round(n_skip / max(len(trades), 1) * 100, 1),
            '最大同时持仓': int(hold.max()) if len(hold) else 0,
            '平均同时持仓': round(float(hold[act].mean()), 2) if act.any() else 0.0,
            '资金利用率%': round(mm['资金利用率(%)'], 1),
            '平均资金占用率%': round(float(rr['exposure'].mean()) * 100, 1),
            '满仓天数占比%': round(float((hold >= kk).mean()) * 100, 1),
            '总收益%': round(mm['总收益(%)'], 1),
            '年化收益%': round(mm['年化收益(%)'], 2),
            '年化波动%': round(mm['年化波动(%)'], 2),
            '夏普': round(mm['夏普'], 2),
            '最大回撤%': round(mm['最大回撤(%)'], 2),
            'Calmar': (round(mm['Calmar'], 2) if mm['Calmar'] == mm['Calmar']
                       else np.nan),
            '期末资产(元)': f'{args.capital * float(rr["nav"][-1]):,.0f}',
            '年化%_固定金额': round(mf['年化收益(%)'], 2),
            '夏普_固定金额': round(mf['夏普'], 2),
            '回撤%_固定金额': round(mf['最大回撤(%)'], 2),
            '逐笔净均%': tt['逐笔净均(%)'],
            '逐笔净胜率%': tt['逐笔净胜率(%)'],
        })
    return pd.DataFrame(rows)


def build_parser():
    ap = argparse.ArgumentParser(
        prog='backtest_shrink',
        description='金叉买入 + MACD柱连续N日缩短卖出 · 实盘资金约束回测')
    ap.add_argument('--code', default='601898', help='模板股票代码')
    ap.add_argument('--start', default='2025-11-11', help='模板起始日')
    ap.add_argument('--end', default='2026-03-16', help='模板结束日')
    ap.add_argument('--template-part', choices=['full', 'pre'], default='full',
                    help='模板取整段（full，默认）或只取「金叉前段」'
                         '（pre：起点 → 区间内最后一个金叉日，去掉金叉后上抛段）')
    ap.add_argument('--pre-cross', choices=['last', 'first'], default='last',
                    help='pre 模式用区间内最后一个（默认，与 cross 买点一致）'
                         '还是第一个金叉切分')
    ap.add_argument('--universe', default=None,
                    help='匹配池 hs300 / coal / all，可逗号分隔多池'
                         '（如 hs300,coal；默认取配置 universe.default）')
    ap.add_argument('--threshold', type=float, default=0.98,
                    help='形状相似度 score 阈值（默认0.98；注意 confirm 口径下'
                         '0.98 基本无信号，跨年回放建议 0.90~0.95）')
    ap.add_argument('--entry-mode', dest='entry_mode', default='confirm',
                    choices=['confirm', 'cross', 'shape'],
                    help='买入口径：confirm=形态确认日收盘买入（可实盘，默认）'
                         ' / cross=窗口内金叉日收盘买入（旧口径，含前视偏差，'
                         '实盘不可复现，仅用于对照历史结果）'
                         ' / shape=纯形态口径：不要求窗口内有金叉，'
                         '窗口终点（抛物线走完当天）收盘买入（可实盘）')
    ap.add_argument('--shape-curv', dest='shape_curv', type=float, default=0.0,
                    help='shape 口径下把 DIF/DEA 二阶差分（抛物线弯曲）'
                         '相关性并入 score 的权重 w（0~1，默认0=只看水平形状；'
                         'score=(1-w)*水平相关 + w*曲率相关）')
    ap.add_argument('--fresh-days', type=int, default=5,
                    help='confirm 口径下要求窗口内最后一次金叉距确认日 <= N 根'
                         '（默认5，与 scan_buy_history 一致；0=确认日当天金叉，'
                         '即「金叉当日买点」，与扫描对拍请同时加 --overlap 0；'
                         '-1=不限制）')
    ap.add_argument('--overlap', type=float, default=0.5,
                    help='同股窗口重叠超过该比例即去重，仅留最高分（0=不去重；'
                         '与 scan_buy_history 对拍建议设 0：NMS 只在同股内合并'
                         '重叠窗口，换阈值/模板后可能少对）')
    ap.add_argument('--step', type=int, default=1, help='滑动步长（默认1）')
    ap.add_argument('--latest', action='store_true',
                    help='每只股票只保留最近一个匹配窗口')
    ap.add_argument('--exclude-self', action='store_true',
                    help='排除模板股票自身')
    ap.add_argument('--limit', type=int, default=None,
                    help='每个池只处理前 N 只（冒烟）')
    ap.add_argument('--include-holding', dest='include_holding',
                    action='store_true',
                    help='把「未触发卖出（持有中）」的交易也纳入回测，按该股'
                         '样本末收盘价了结 —— 等价于「永不卖出」基线，'
                         '用来判断「连续N日缩短」卖出规则本身是否加分')
    ap.add_argument('--shrink-days', type=int, default=6,
                    help='MACD柱连续缩短日数 N（默认6，全市场综合最优档）')
    ap.add_argument('--commission', type=float, default=0.00025,
                    help='佣金率（双边，默认万2.5=0.00025）')
    ap.add_argument('--stamp', type=float, default=0.0005,
                    help='印花税率（仅卖出，默认0.05%%=0.0005）')
    ap.add_argument('--transfer', type=float, default=0.00001,
                    help='过户费率（双边，默认0.001%%=0.00001）')
    ap.add_argument('--min-commission', type=float, default=5.0,
                    help='单笔最低佣金（元，默认5；组合费率口径下不逐笔生效）')
    ap.add_argument('--cost', type=float, default=None,
                    help='统一双边合计成本（如0.002），给出后覆盖上述明细费率，'
                         '口径同 ABM 的 backtest.cost')
    ap.add_argument('--capital', type=float, default=1000000.0,
                    help='初始总资金（元，默认100万）')
    ap.add_argument('--max-concurrent', type=int, default=5,
                    help='并发持仓上限 K 只（默认5；0/负数 = 不设限，对拍基线）')
    ap.add_argument('--budget', type=float, default=100000.0,
                    help='单笔买入金额上限（元，逐笔明细参考，默认10万）')
    ap.add_argument('--exec', dest='exec_mode', default='close_close',
                    choices=['close_close', 'open_open', 'vwap'],
                    help='close_close=信号日收盘买卖（同 template_scan，默认）/ '
                         'open_open=次日开盘买卖（更贴近实盘）')
    ap.add_argument('--limit-up', type=float, default=0.097,
                    help='open_open/vwap 下建仓涨幅 >= 该阈值视为一字板买不进并'
                         '剔除（默认0.097；设 1 关闭）')
    ap.add_argument('--nav-mode', dest='nav_mode', default='slots',
                    choices=['slots', 'equal'],
                    help='slots=固定资金槽位（每笔 权益/K，闲置为现金；实盘口径，'
                         '默认）/ equal=ABM 等权（资金永远满额，作对照）')
    ap.add_argument('--slot-base', dest='slot_base', default='equity',
                    choices=['equity', 'fixed'],
                    help='slots 口径下每笔建仓基准：equity=当日权益/K'
                         '（默认，盈利按比例复投、复利放大）'
                         '/ fixed=初始资金/K（每笔金额恒定，盈利闲置）')
    ap.add_argument('--dump-matches', dest='dump_matches', default=None,
                    help='把匹配窗口清单（含 buy_date/score）导到指定 CSV，便于两种模板对拍')
    ap.add_argument('--k-scan', dest='k_scan', default=None,
                    help='并发上限对比：逗号分隔的 K 列表（如 3,5,8,12,20），'
                         '只做一次匹配/面板，逐 K 回测并输出对比表'
                         '（不改变主结果，主结果仍用 --max-concurrent）')
    ap.add_argument('--bt-start', default=None,
                    help='价格面板起始日（默认取配置 data.start）')
    ap.add_argument('--refresh', action='store_true', help='强制重拉日线缓存')
    ap.add_argument('--out', default=None, help='Excel 输出路径')
    ap.add_argument('--no-chart', action='store_true', help='不输出净值图')
    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)
    S.ensure_dirs()
    t0 = time.perf_counter()
    cfg = S.load_config()
    K = (args.max_concurrent if (args.max_concurrent and args.max_concurrent > 0)
         else None)
    cost = eff_cost(args)
    if args.nav_mode == 'slots' and K is None:
        raise SystemExit('--nav-mode slots 需要 --max-concurrent > 0'
                         '（槽位数即 K；不限并发时槽位数无法确定，'
                         '可用 --nav-mode equal 作等权对照）')

    # ── 1. 模板 ─────────────────────────────────────────────────────────
    _, t_dates, t_dif, t_dea, t_name, mp = _series(args.code)
    m = (t_dates >= np.datetime64(args.start)) & (t_dates <= np.datetime64(args.end))
    if int(m.sum()) < 8:
        raise SystemExit(f'模板区间 {args.start}~{args.end} 内交易日不足'
                         f'（{int(m.sum())}）')
    idx = np.where(m)[0]
    tpl_cross = None
    if args.template_part == 'pre':
        # 只保留「区间起点 → 区间内最后一个金叉日」：模板里没有金叉后的上抛段，
        # 于是窗口终点必须落在金叉附近才可能高分 → 买点不再依赖未来走势。
        xs = np.where(cross_flags(t_dif, t_dea) == 1)[0]
        xs = xs[(xs >= idx[0]) & (xs <= idx[-1])]
        if xs.size == 0:
            raise SystemExit(f'模板区间 {args.start}~{args.end} 内无金叉，'
                             '无法取前段；可放宽区间或用 --template-part full')
        b = int(xs[-1] if args.pre_cross == 'last' else xs[0])
        if b - int(idx[0]) + 1 < 8:
            raise SystemExit(f'模板前段过短（金叉距起点 {b - int(idx[0]) + 1} 根）')
        idx = idx[idx <= b]
        tpl_cross = pd.Timestamp(t_dates[b]).date()
    tpl_dif_z, tpl_dea_z = zscore(t_dif[idx]), zscore(t_dea[idx])
    L = int(idx.size)
    tpl_curv = None
    if args.shape_curv > 0:
        if L < 5:
            raise SystemExit(f'模板过短（{L} 根），无法计算曲率')
        tpl_curv = (zscore(np.diff(t_dif[idx], 2)),
                    zscore(np.diff(t_dea[idx], 2)))

    print('=' * 100)
    print('  「金叉买入 + MACD柱连续N日缩短卖出」· 实盘资金约束回测')
    print('=' * 100)
    print(f'  模板       : {args.code} {t_name}  {args.start} ~ {args.end} '
          f'（L={L} 交易日）   MACD={mp}')
    if tpl_cross:
        print(f'  模板口径   : 只取「金叉前段」（~{tpl_cross} 的'
              f'{args.pre_cross} 个金叉为止，去掉金叉后上抛段）')
    elif args.entry_mode == 'shape':
        print('  模板口径   : 整段 DIF/DEA 抛物线形状'
              f'（{args.start} ~ {args.end}，只看曲线像不像，不判金叉）')
    else:
        print('  模板口径   : 整段（含金叉后上抛段）')
    if args.entry_mode == 'shape':
        print(f'  曲率权重   : w={args.shape_curv:.2f}'
              f'（{(1 - args.shape_curv):.2f}×水平相关 + '
              f'{args.shape_curv:.2f}×二阶差分相关）')
    if args.template_part == 'pre' and args.entry_mode == 'confirm' \
            and (args.fresh_days < 0 or args.fresh_days > 2):
        print(f'  [提示] pre 模板下窗口终点应贴近金叉日，'
              f'当前 --fresh-days={args.fresh_days} 过松，建议 0')
    if args.entry_mode == 'confirm':
        fresh_desc = ('金叉不限新鲜度' if args.fresh_days < 0
                      else f'金叉距确认日 <= {args.fresh_days} 根')
        print(f'  买入口径   : 形态确认日收盘（可实盘）  {fresh_desc}')
    elif args.entry_mode == 'shape':
        print('  买入口径   : 形态完成日收盘（不看金叉，可实盘）')
    else:
        print('  买入口径   : 窗口内最后一次金叉日收盘（旧口径，含前视偏差，'
              '实盘不可执行）')
    print(f'  卖出规则   : MACD柱连续 {args.shrink_days} 日缩短'
          f'（第 {args.shrink_days} 个缩短日收盘卖）')
    print('  资金约束   : 初始 {:,.0f} 元 / 并发上限 {}'.format(
        args.capital,
        f'K={K} 只（单票 {args.capital / K:,.0f} 元）' if K else '不限（对拍基线）'))
    print(f'  执行口径   : {args.exec_mode}    等效双边成本 {cost * 100:.4f}%')
    if args.nav_mode != 'slots':
        nav_desc = 'ABM 等权（资金永远满额，仅作对照）'
    elif args.slot_base == 'fixed':
        nav_desc = (f'固定金额槽位（每笔=初始资金/K，恒定 '
                    f'{args.capital / K:,.0f} 元；盈利不追加）')
    else:
        nav_desc = '固定资金槽位（每笔=当日权益/K，盈利复投；闲置为现金）'
    print(f'  净值口径   : {nav_desc}')
    print(f'  成本明细   : {_fee_desc(args)}')
    print('=' * 100, flush=True)

    # ── 2. 模板匹配（支持多池，合并后按 股票+买入日 去重）───────────────
    scan_args = argparse.Namespace(
        threshold=args.threshold, step=args.step, overlap=args.overlap,
        latest=args.latest, exclude_self=args.exclude_self, code=args.code,
        budget=args.budget, sell_rule='shrink', lead=4,
        shrink_days=args.shrink_days, limit=None,
        entry=args.entry_mode, fresh_days=args.fresh_days,
        shape_curv=args.shape_curv,
        commission=args.commission, stamp=args.stamp, transfer=args.transfer,
        min_commission=args.min_commission, cost=args.cost)
    pools = [p.strip() for p in (args.universe or
                                 cfg.get('universe', {}).get('default', 'hs300')
                                 ).split(',') if p.strip()]
    frames = []
    for pool in pools:
        codes, label = _resolve_pool(pool)
        daily = D.load_daily(codes=codes, refresh=args.refresh)
        if args.limit:
            keep = sorted(daily['stock_code'].unique())[:args.limit]
            daily = daily[daily['stock_code'].isin(set(keep))]
            print(f'[限制] {label} 只处理前 {len(keep)} 只')
        print(f'\n--- 匹配池【{label}】{daily["stock_code"].nunique()} 只 / '
              f'{len(daily):,} 行 ---', flush=True)
        df = _scan(daily, tpl_dif_z, tpl_dea_z, L, mp, scan_args,
                   keep_meta=True, tpl_curv=tpl_curv)
        if not len(df):
            if args.entry_mode == 'confirm' and args.threshold >= 0.95:
                hint = ('（confirm 口径下 0.98 通常无命中，可试 --threshold 0.90；'
                        '若样本本就少，可放宽 --fresh-days 或换更长的模板区间）')
            elif args.entry_mode == 'confirm':
                hint = '（可放宽 --fresh-days，或降低 --threshold）'
            else:
                hint = '（可降低 --threshold）'
            print(f'（无结果 {hint}）')
            continue
        print(f'[结果] {label}: {len(df)} 个匹配窗口 / '
              f'{df["stock_code"].nunique()} 只股票')
        frames.append(df.assign(pool=label))
    if not frames:
        print('\n（全部匹配池均无结果）')
        return 0

    comb = pd.concat(frames, ignore_index=True)
    comb['stock_code'] = comb['stock_code'].astype(str).str.zfill(6)
    if args.include_holding:
        done = comb.copy()
        tag_hold = ('（含未触发的「持有中」，按该股样本末收盘价了结 '
                    '= 永不卖出基线）')
    else:
        done = comb[comb['status'] == '已了结'].copy()
        tag_hold = '（未触发的「持有中」不计）'
    n0 = len(done)
    done = (done.sort_values('score', ascending=False)
                .drop_duplicates(['stock_code', 'buy_date'], keep='first'))
    if len(done) < n0:
        print(f'[去重] 多池重复信号 {n0} -> {len(done)} 笔')
    print(f'[汇总] 匹配窗口 {len(comb)} 个；参与回测 {len(done)} 笔 / '
          f'{done["stock_code"].nunique()} 只{tag_hold}')
    if args.dump_matches:
        cols = [c for c in ('stock_code', 'stock_name', 'start', 'end', 'score',
                            'buy_date', 'buy_price', 'status', 'bars_since_cross')
                if c in comb.columns]
        os.makedirs(os.path.dirname(os.path.abspath(args.dump_matches)),
                    exist_ok=True)
        comb[cols].to_csv(args.dump_matches, index=False, encoding='utf-8-sig')
        print(f'[导出] 匹配清单 -> {args.dump_matches}（{len(comb)} 行）')
    if not len(done):
        print('\n（没有已了结交易，无法回测）')
        return 0

    # ── 3. 价格面板（复用 ABM 数据层）───────────────────────────────────
    bt_start = args.bt_start or cfg['data']['start']
    sig_for_panel = pd.DataFrame({'stock_code': sorted(done['stock_code'].unique())})
    print(f'\n[面板] 拉取 {len(sig_for_panel)} 只价格（start={bt_start}）...',
          flush=True)
    panel, _idx, nav_base = ABM_DATA.build_panel(sig_for_panel, start=bt_start,
                                                 refresh=False)
    base_m = ABM_E.perf_metrics(nav_base, panel.calendar,
                                ABM_E.years_of(panel.calendar))
    print(f'[面板] 交易日 {len(panel.calendar)} 天 '
          f'（{panel.calendar[0].date()} ~ {panel.calendar[-1].date()}）')

    # ── 4. 变长持有交易表 + 并发上限（实盘资金约束核心）───────────────
    lu = args.limit_up if args.exec_mode in ('open_open', 'vwap') else None
    trades, n_unmapped, n_lu = build_trades(done, panel, args.exec_mode, lu)
    if n_unmapped or n_lu:
        print(f'[交易] 剔除 {n_unmapped} 笔（日期不在日历/区间无效）、'
              f'{n_lu} 笔（一字板买不进）')
    if not len(trades):
        print('\n（没有可用交易）')
        return 0
    trades_c, cap_info = ABM_P.cap_trades(
        trades, {'max_concurrent': K, 'weighting': 'equal'}, sig=None)
    if cap_info.get('enabled'):
        print(f'[约束] 并发上限 K={K}：保留 {len(trades_c)} 笔 / 丢弃 '
              f'{cap_info["skipped_capacity"]} 笔（名额已满）')
    else:
        print(f'[约束] 不设并发上限：{len(trades_c)} 笔全部参与')

    # ── 5. 日频净值（固定资金槽位 / ABM 等权对照）──────────────────────
    if args.nav_mode == 'slots':
        r = run_slots(panel, trades_c, K, args.capital,
                      eff_rate(args, 'buy'), eff_rate(args, 'sell'),
                      args.exec_mode, args.slot_base)
    else:
        r = ABM_E.run_backtest(panel, trades_c, cost, args.exec_mode)
    metrics = ABM_E.perf_metrics(r['nav'], panel.calendar, r['years'],
                                 r['hold_cnt'])
    detail = ABM_E.trades_detail(panel, trades_c, cost, args.exec_mode)
    alpha, alpha_t = BT.alpha_tstat(r['nav'], nav_base)
    ts = BT.trade_stats(detail)

    h = args.shrink_days
    bt = {'panel': panel, 'nav_base': nav_base, 'benchmark': base_m,
          'horizons': [h], 'exec_mode': args.exec_mode, 'cost': cost,
          'cfg': cfg,
          'results': {h: {'nav': r['nav'], 'metrics': metrics, 'alpha': alpha,
                          'alpha_t': alpha_t, 'trades': trades_c,
                          'detail': detail, 'n_sig': len(comb),
                          'n_trade': r['n_trade'], 'n_skip': n_unmapped,
                          'n_skip_lu': n_lu, 'trade_stats': ts}}}
    ctab = constraint_table(comb, done, trades, trades_c, cap_info, r, metrics,
                            args.capital, K, args.exec_mode, cost, h,
                            entry_mode=args.entry_mode,
                            fresh_days=args.fresh_days,
                            stale=getattr(scan_args, '_n_stale', 0),
                            shape_curv=args.shape_curv)

    # ── 6b. 并发上限 K 对比（可选）─────────────────────────────────────
    ktab = None
    if args.k_scan:
        k_list = [int(x) for x in args.k_scan.split(',') if x.strip()]
        print(f'\n[对比] 逐 K 扫描并发上限 {sorted(set(k_list))} ...', flush=True)
        ktab = k_scan_table(panel, trades, k_list, args, cost)
        print('\n' + '=' * 150)
        print('  并发上限 K 对比（固定资金槽位口径）：K 越大越能容纳信号，'
              '但单票仓位（=权益/K）越小')
        print('=' * 150)
        print(ktab.to_string(index=False))

    # ── 6. 控制台汇总 ───────────────────────────────────────────────────
    print('\n' + '=' * 120)
    print(f'  绩效总览（H{h} = 「MACD柱连续{h}日缩短」卖出规则；'
          f'基准末行 = 沪深300）')
    print('=' * 120)
    print(RP.perf_table(bt).to_string(index=False))
    print('\n' + '-' * 120)
    print('  实盘资金约束')
    print('-' * 120)
    print(ctab.to_string(index=False))
    print('\n' + '-' * 120)
    print('  年度收益(%)')
    print('-' * 120)
    print(RP.annual_table(bt).to_string(index=False))

    # ── 7. 产物 ─────────────────────────────────────────────────────────
    stamp = time.strftime('%Y%m%d_%H%M%S')
    tag = '_'.join(pools)
    mode_tag = ('live' if args.entry_mode == 'confirm'
                else 'shape' if args.entry_mode == 'shape' else 'cross')
    part_tag = 'pre' if args.template_part == 'pre' else ''
    xlsx = args.out or os.path.join(
        S.OUTPUT_DIR,
        f'backtest_shrink_{tag}_K{K or "all"}_{mode_tag}'
        f'{("_" + part_tag) if part_tag else ""}_{stamp}.xlsx')
    extra = {'资金约束': ctab, '逐笔明细': detail}
    if ktab is not None:
        extra['并发上限对比'] = ktab
    RP.write_excel(xlsx, bt, extra=extra)
    print(f'\n[OK] Excel : {xlsx}')
    if not args.no_chart:
        png = os.path.join(S.CHART_DIR, f'shrink_nav_{stamp}.png')
        RP.plot_nav(bt, png, title=f'金叉买入 + MACD柱连续{h}日缩短卖出'
                                   f'（K={K}）')
        print(f'[OK] 净值图: {png}')
    print(f'[用时] {(time.perf_counter() - t0) / 60:.2f} 分钟')
    return 0


if __name__ == '__main__':
    sys.exit(main())

