# -*- coding: utf-8 -*-
"""
==============================================================================
template_scan.py —— 以指定股票某段 DIF/DEA 曲线为「基准模板」全市场匹配
==============================================================================
把某只股票在 [start, end] 区间内的 DIF、DEA 曲线取出来，各自做 z-score
归一化（消除价格尺度差异，只保留**形状**），再拿它当模板，用**向量化滑动
互相关**在股票池里逐窗口计算 Pearson 形状相似度：

    corr_dif = corr( 窗口内 DIF 归一化 , 模板 DIF 归一化 )
    corr_dea = corr( 窗口内 DEA 归一化 , 模板 DEA 归一化 )
    score    = (corr_dif + corr_dea) / 2          ← 综合形状相似度

只有 score >= --threshold 的窗口才算命中，因此信号远少于通用抛物线扫描。

相似度阈值经验档（沪深300 / 82日模板 / 已去重）::

    0.95 -> ~516条 / 240只      0.97 -> ~163条 / 123只
    0.98 ->  ~57条 /  50只      0.99 ->   ~3条 /   3只
推荐 --threshold 0.97 ~ 0.98 作为严格筛选档。

输出字段：
    stock_code, stock_name, start, end, score, corr_dif, corr_dea,
    bars(窗口交易日数), win_ret_pct(窗口内收盘涨幅%), dif_min, dif_max,
    buy_date, buy_price, sell_date, sell_price, trade_ret_pct(毛涨幅%),
    shares, invest(投入本金元), buy_fee(买入费用元), sell_fee(卖出费用元),
    cost(该笔税费合计元), cost_pct(费用/本金%), profit(毛收益元),
    net_profit(净收益元 = 毛收益 - 税费), net_ret_pct(净涨幅%),
    hold_bars(持有交易日), status

收益口径（金叉买入 / MACD柱连续6日缩短卖出，N=6 为全市场最优档）：
    买入口径（--entry）：
        cross   → 匹配窗口内 DIF 上穿 DEA（金叉）当日的收盘价【旧口径】。
                  注意：窗口是在「终点日」才由 score 确认命中的，却回到更早的
                  金叉日成交 → 存在**前视偏差**，实盘不可执行，仅用于复现历史结论；
        confirm → 形态确认日（窗口终点）收盘价买入，卖出规则也从该日计时，
                  并可用 --fresh-days 要求金叉距确认日 <= N 根（默认5，-1=不限）
                  —— 这是**可实盘**口径（与 scan_buy / scan_buy_history 一致）。
    卖出点：买入后第一次出现「MACD柱 2*(DIF-DEA) 连续 shrink 个交易日逐日缩短」
            （--shrink-days 默认6），在满足条件的那一天（第6个缩短日）按收盘价卖出；
            若至今未出现则记最新价为浮动价，status='持有中'；窗口内无金叉则
            status='无金叉'
            （--sell-rule 可切换：shrink=MACD柱连续N日缩短(默认) / lead=死叉前N天 /
             bar=MACD柱<DEA / dif=DIF<DEA / hist0=MACD柱<0）
    资金：单次买入不超过 --budget（默认10万元），且按 A 股 100 股整手买入
    交易成本（默认「明细口径」，可用 --cost 切换为统一双边口径）：
        买入费用 = max(成交额 × 佣金率, 最低佣金) + 成交额 × 过户费率
        卖出费用 = max(成交额 × 佣金率, 最低佣金) + 成交额 × 过户费率
                   + 成交额 × 印花税率          ← 印花税仅卖出方收取
        默认 佣金万2.5/单笔最低5元、过户费0.001%、印花税0.05%
        -> 买入约 0.026%、卖出约 0.076%，双边合计约 0.102%；
        --cost 0.002 则改为买卖各 0.1%（同 ABM backtest.cost）
    trade_ret_pct = (sell_price / buy_price - 1) * 100            ← 毛涨幅
    profit        = shares * (sell_price - buy_price)             ← 毛收益
    cost          = buy_fee + sell_fee                            ← 双边税费
    net_profit    = profit - cost                                 ← 净收益
    net_ret_pct   = net_profit / (shares × buy_price + buy_fee) × 100   ← 净涨幅

用法::

    python template_scan.py                                    # 默认模板 601898（沪深300）
    python template_scan.py --universe hs300,coal              # 多池一次输出到同一 Excel
    python template_scan.py --universe all                     # 全市场
    python template_scan.py --threshold 0.98                   # 更严的相似度
    python template_scan.py --overlap 0.3                      # 更激进的去重
    python template_scan.py --latest                           # 每股只留最近一个
    python template_scan.py --code 600188 --start 2025-09-01 --end 2026-03-01

默认输出 output/template_<code>.xlsx，**一个文件多工作表**：
    「回测统计」（全部 / 分年度 / 分池 三层的毛净对比指标）
    「全部汇总」（所有池合并，含 pool 列）
    「<池名>_明细」+「<池名>_每股最佳」（如 沪深300_明细 / 动力煤_每股最佳）
给 --out 加 .csv 后缀则输出单一 CSV。

数据源：backward_tdx_daily（后复权），见 mlr/settings.py TABLE_DAILY。
"""
import argparse
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mlr import data as D, settings as S, universe as U
from mlr.macd import macd as calc_macd


def zscore(y):
    """z-score 归一化（均值为0、标准差为1）；退化为常数时仅去均值。"""
    y = np.asarray(y, dtype=np.float64)
    mu = float(y.mean())
    sd = float(y.std())
    return (y - mu) / sd if sd > 1e-12 else y - mu


def sliding_corr(x, t):
    """x: 长序列；t: 模板（已 z-score，均值0、std1）。

    返回每个长度为 len(t) 的滑动窗口与模板的 Pearson 相关系数数组
    （长度 len(x)-len(t)+1）。完全向量化（累积和 + 互相关）。
    """
    L = t.size
    n = x.size
    if n < L:
        return np.empty(0, dtype=np.float64)
    dot = np.correlate(x, t, 'valid')                 # sum(x[s:s+L] * t)
    cs = np.concatenate(([0.0], np.cumsum(x)))
    cs2 = np.concatenate(([0.0], np.cumsum(x * x)))
    s1 = cs[L:] - cs[:-L]
    s2 = cs2[L:] - cs2[:-L]
    mean = s1 / L
    var = s2 / L - mean * mean
    std = np.sqrt(np.maximum(var, 0.0))
    denom = L * std
    out = np.zeros_like(dot)
    ok = denom > 1e-12
    out[ok] = dot[ok] / denom[ok]
    return out


def _dedup(df, overlap=0.5):
    """非极大值抑制：同一只股票内高度重叠的窗口只保留分数最高的那个。"""
    if df.empty:
        return df
    keep = []
    for _, g in df.groupby('stock_code', sort=False):
        g = g.sort_values('score', ascending=False)
        sel = []
        for idx, r in g.iterrows():
            i0, i1 = int(r['_i0']), int(r['_i1'])
            dup = False
            for a, b in sel:
                inter = min(i1, b) - max(i0, a) + 1
                if inter > overlap * min(i1 - i0 + 1, b - a + 1):
                    dup = True
                    break
            if not dup:
                sel.append((i0, i1))
                keep.append(idx)
    return df.loc[keep].reset_index(drop=True)


def _write_excel(path, sheets):
    """把多个 DataFrame 写入 Excel（每表一个 sheet），复用 report 的表头美化。"""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with pd.ExcelWriter(path, engine='openpyxl') as xw:
        for name, df in sheets.items():
            df.to_excel(xw, sheet_name=name[:31], index=False)
    try:
        from mlr.report import _style_openpyxl
        _style_openpyxl(path)
    except Exception:                                # noqa: BLE001
        pass
    return path


def _series(code):
    """加载单只股票的 (close, dates, dif, dea, name, macd_p)。"""
    cfg = S.load_config()
    mp = (cfg['macd']['fast'], cfg['macd']['slow'], cfg['macd']['signal'])
    daily = D.load_daily(codes=[code])
    g = daily[daily['stock_code'] == code].sort_values('trade_date')
    if g.empty:
        raise SystemExit(f'数据中找不到模板股票 {code}')
    close = g['close_price'].to_numpy('float64')
    dates = pd.to_datetime(g['trade_date']).to_numpy()
    dif, dea, _ = calc_macd(close, *mp)
    return close, dates, dif, dea, g['stock_name'].iloc[-1], mp


def _resolve_pool(name):
    """把池名解析为 (codes, 中文标签)；codes=None 表示全市场。"""
    if name == 'hs300':
        codes, _ = U.get_hs300_codes()
        return codes, '沪深300'
    if name == 'coal':
        codes, _ = U.get_coal_codes()
        return codes, '动力煤'
    if name == 'all':
        return None, '全市场'
    raise SystemExit(f'未知匹配池: {name}（hs300 / coal / all，可逗号分隔多池）')


def _fee_model(args):
    """构造 (买入费用函数, 卖出费用函数)，入参为该笔成交金额（元），返回费用（元）。

    两种口径：
    * 明细口径（默认）——A股实盘费率：佣金（双边，有单笔最低）+ 过户费（双边）
      + 印花税（**仅卖出**）。默认 佣金万2.5/最低5元、过户费0.001%、印花税0.05%，
      即买入约 0.026%、卖出约 0.076%，双边合计约 0.102%。
    * 统一口径 —— 显式给出 --cost（如 0.002）时，买卖各摊一半、不打底最低佣金，
      与 mlr/backtest.py 的 `backtest.cost`（双边合计 0.2%）口径一致。
    """
    if getattr(args, 'cost', None) is not None:
        half = float(args.cost) / 2.0
        return (lambda amt: amt * half), (lambda amt: amt * half)
    comm = float(getattr(args, 'commission', 0.00025))
    stamp = float(getattr(args, 'stamp', 0.0005))
    trans = float(getattr(args, 'transfer', 0.00001))
    minc = float(getattr(args, 'min_commission', 5.0))

    def _pay(amt, extra):
        fee = amt * comm
        if comm > 0:                                  # 单笔最低佣金
            fee = max(fee, minc)
        return fee + amt * (trans + extra)

    return (lambda amt: _pay(amt, 0.0)), (lambda amt: _pay(amt, stamp))


def _fee_desc(args):
    """成本口径的可读描述，用于打印。"""
    if getattr(args, 'cost', None) is not None:
        return f'统一双边合计 {args.cost * 100:.3f}%（买卖各半）'
    comm = float(getattr(args, 'commission', 0.00025))
    stamp = float(getattr(args, 'stamp', 0.0005))
    trans = float(getattr(args, 'transfer', 0.00001))
    minc = float(getattr(args, 'min_commission', 5.0))
    return (f'佣金 {comm * 100:.4f}%（单笔最低{minc:.0f}元）'
            f' + 印花税 {stamp * 100:.3f}%（仅卖出）'
            f' + 过户费 {trans * 100:.4f}%（双边）'
            f' => 买入≈{(comm + trans) * 100:.4f}% / '
            f'卖出≈{(comm + trans + stamp) * 100:.4f}%')


def _trade_calc(close, dates, dif, dea, hist, i0, i1, budget=100000.0,
                rule='last', sell_rule='shrink', lead=4, shrink=6, fees=None,
                entry='cross', fresh_days=None):
    """窗口内金叉买入 → 按指定规则卖出，按 budget 上限整手算收益。

    买入点：窗口 [i0, i1] 内 DIF 上穿 DEA（前一日 DIF<=DEA 且当日 DIF>DEA）的
            收盘价；rule='last' 取窗口内最后一次金叉，'first' 取第一次。
    ``entry`` 决定**成交日**（这是「可实盘 vs 含前视」的分水岭）：

    * ``'cross'``（旧口径，默认）：成交日 = 窗口内金叉日 b。
      命中窗口在 i1 日才由 score 确认，却在更早的 b 日成交 → **前视偏差**，
      实盘不可执行，仅用于复现历史结论；
    * ``'confirm'``（可实盘）：成交日 = 形态确认日 i1，成交价 = close[i1]；
      卖出规则的计时起点同步改为 i1。若给出 ``fresh_days``（>=0），还要
      求 ``i1 - b <= fresh_days``（金叉必须「新鲜」），否则返回
      ``status='金叉过旧'`` 由调用方过滤。
    * ``'shape'``（可实盘）：**不看金叉**，只按模板形状打分，成交日 = 窗口
      终点 i1（抛物线形态走完那天）。用于「DIF/DEA 向下抛物线」这类
      不以金叉为必要条件的模板；找不到金叉也照样成交，不返回「无金叉」。

    返回字段 ``bars_since_cross = i1 - b``（金叉距确认日根数，便于核对口径）。
    卖出点：以下规则均取目标交易日的**收盘价**：
              sell_rule='shrink'→ MACD柱 2*(DIF-DEA) 连续 shrink 日逐日缩短
                                  （默认 shrink=6，即"连续6日缩短"），在满足条件的
                                  那一天（第 shrink 个缩短日）收盘卖出    ← 默认
              sell_rule='lead'  → 买入后第一次 DIF 下穿 DEA（死叉）那天，往前退
                                  lead 个交易日卖出（默认 lead=4，即"死叉前4天"）；
                                  若买入日距死叉不足 lead 个交易日，则退到买入日
                                  （hold_bars=0、收益0）
              sell_rule='bar'   → MACD柱 2*(DIF-DEA) < DEA 当日
                                  注意：柱与 DEA 量纲不同，DEA>0 时极易在买入次日
                                  即满足，会出现极短持有期
              sell_rule='dif'   → DIF < DEA 当日
              sell_rule='hist0' → MACD柱 < 0 当日
            若至今未触发则用最新收盘价（status='持有中'）。
    资金：单次买入金额不超过 budget，且按 A 股 100 股整手买入。
    """
    xs = np.where((dif[i0 + 1:i1 + 1] > dea[i0 + 1:i1 + 1]) &
                  (dif[i0:i1] <= dea[i0:i1]))[0] + i0 + 1
    if entry == 'shape':
        # 纯形态口径：模板是「DIF/DEA 向下的抛物线」，窗口终点 i1 即形态走完那天，
        # 不要求窗口内出现金叉（抛物线底与金叉可能不同日），买在 i1 收盘。
        b, bars_since = int(i1), 0
    else:
        if xs.size == 0:
            return {'buy_date': None, 'buy_price': None, 'sell_date': None,
                    'sell_price': None, 'trade_ret_pct': None, 'shares': 0,
                    'invest': 0.0, 'buy_fee': 0.0, 'sell_fee': 0.0, 'cost': 0.0,
                    'cost_pct': None, 'profit': 0.0, 'net_profit': 0.0,
                    'net_ret_pct': None, 'hold_bars': None,
                    'status': '无金叉', 'bars_since_cross': None, '_b': None}
        b = int(xs[-1] if rule == 'last' else xs[0])
        bars_since = int(i1 - b)
        if (entry == 'confirm' and fresh_days is not None
                and bars_since > int(fresh_days)):
            return {'buy_date': None, 'buy_price': None, 'sell_date': None,
                    'sell_price': None, 'trade_ret_pct': None, 'shares': 0,
                    'invest': 0.0, 'buy_fee': 0.0, 'sell_fee': 0.0, 'cost': 0.0,
                    'cost_pct': None, 'profit': 0.0, 'net_profit': 0.0,
                    'net_ret_pct': None, 'hold_bars': None,
                    'status': '金叉过旧', 'bars_since_cross': bars_since,
                    '_b': None}
    # 成交日：cross=金叉日 b（含前视） / confirm=形态确认日 i1（可实盘）
    #         / shape=形态完成日 i1（不看金叉，可实盘）
    e0 = int(i1) if entry in ('confirm', 'shape') else b
    if sell_rule == 'shrink':                        # MACD柱连续 shrink 日缩短
        d = np.diff(hist) < 0                        # d[k]=True 表示第 k+1 天柱值缩短
        last_bad = np.maximum.accumulate(np.where(d, -1, np.arange(d.size)))
        run = np.zeros(hist.size, dtype=np.int64)    # run[i]=截至第 i 天连续缩短天数
        run[1:] = np.arange(d.size) - last_bad
        hit = np.where(run[e0 + 1:] >= int(shrink))[0]     # 第 shrink 个缩短日
        holding = hit.size == 0
        s = int(len(close) - 1) if holding else int(e0 + 1 + hit[0])
    elif sell_rule == 'lead':                        # 死叉前 lead 个交易日
        rel = np.where(dif[e0 + 1:] < dea[e0 + 1:])[0]
        if rel.size == 0:
            holding, s = True, int(len(close) - 1)
        else:
            holding = False
            s = max(e0 + 1 + int(rel[0]) - int(lead), e0)   # 不足 lead 天则退到买入日
    else:                                            # 条件触发当日收盘价
        if sell_rule == 'dif':
            lhs, rhs = dif, dea
        elif sell_rule == 'hist0':
            lhs, rhs = hist, np.zeros_like(hist)
        else:                                        # 'bar'：MACD柱 < DEA
            lhs, rhs = hist, dea
        rel = np.where(lhs[e0 + 1:] < rhs[e0 + 1:])[0]
        holding = rel.size == 0
        s = int(len(close) - 1) if holding else int(e0 + 1 + rel[0])
    bp, sp = float(close[e0]), float(close[s])
    sh = int(budget // (bp * 100)) * 100 if bp > 0 else 0      # 整手
    buy_amt, sell_amt = sh * bp, sh * sp
    if fees is None:
        bfee = sfee = 0.0
    else:
        bfee, sfee = float(fees[0](buy_amt)), float(fees[1](sell_amt))
    cost = bfee + sfee
    gross = sell_amt - buy_amt
    net = gross - cost                           # 净收益 = 毛收益 - 双边费用
    return {
        'buy_date': pd.Timestamp(dates[e0]).date(),
        'buy_price': round(bp, 3),
        'sell_date': pd.Timestamp(dates[s]).date(),
        'sell_price': round(sp, 3),
        'trade_ret_pct': round((sp / bp - 1) * 100, 2) if bp else None,
        'shares': sh,
        'invest': round(buy_amt, 2),
        'buy_fee': round(bfee, 2),
        'sell_fee': round(sfee, 2),
        'cost': round(cost, 2),
        'cost_pct': round(cost / buy_amt * 100, 3) if buy_amt else None,
        'profit': round(gross, 2),
        'net_profit': round(net, 2),
        'net_ret_pct': round(net / (buy_amt + bfee) * 100, 2) if buy_amt else None,
        'hold_bars': int(s - e0),
        'status': '持有中' if holding else '已了结',
        'bars_since_cross': bars_since,
        '_b': e0,
    }


def _scan(daily, tpl_dif_z, tpl_dea_z, L, mp, args, keep_meta=False,
          tpl_curv=None):
    """在给定股票池上做滑动模板匹配，返回命中 DataFrame（已排序+去重）。

    keep_meta=True 时保留内部列 `_i0/_i1`（窗口起止索引）与 `_b`（买入索引），
    供参数扫描等下游复用；默认丢弃。

    买入口径由 ``args.entry``（'cross' / 'confirm' / 'shape'）与
    ``args.fresh_days`` 决定，见 ``_trade_calc``；confirm + fresh 下
    「金叉过旧」的窗口在此过滤。

    ``tpl_curv`` = (DIF、DEA 的二阶差分模板, 已 z-score)，配合
    ``args.shape_curv``（0~1）把「抛物线弯曲程度」按权重并入 score：
    ``score = (1-w) * 水平相关性 + w * 曲率相关性``。
    """
    rows = []
    fees = _fee_model(args)
    entry = getattr(args, 'entry', 'cross')
    fresh = getattr(args, 'fresh_days', None)
    if fresh is not None and int(fresh) < 0:
        fresh = None
    w_c = float(getattr(args, 'shape_curv', 0.0) or 0.0)
    if tpl_curv is None:
        w_c = 0.0
    n_stale = 0
    for code, g in daily.groupby('stock_code', sort=False):
        if args.exclude_self and code == args.code:
            continue
        g = g.sort_values('trade_date')
        name = g['stock_name'].iloc[-1]
        close = g['close_price'].to_numpy('float64')
        dates = pd.to_datetime(g['trade_date']).to_numpy()
        dif, dea, hist = calc_macd(close, *mp)
        if dif.size < L:
            continue
        cd = sliding_corr(dif, tpl_dif_z)
        ce = sliding_corr(dea, tpl_dea_z)
        if cd.size == 0:
            continue
        score = (cd + ce) / 2.0
        curv = None
        if w_c > 0:
            c2d = sliding_corr(np.diff(dif, 2), tpl_curv[0])
            c2e = sliding_corr(np.diff(dea, 2), tpl_curv[1])
            if c2d.size == score.size:
                curv = (c2d + c2e) / 2.0
                score = (1.0 - w_c) * score + w_c * curv
        hits = np.where(score >= args.threshold)[0]
        if args.step > 1:
            hits = hits[::args.step]
        for k in hits:
            e = k + L - 1
            row = {
                '_i0': int(k), '_i1': int(e),
                'stock_code': code, 'stock_name': name,
                'start': pd.Timestamp(dates[k]).date(),
                'end': pd.Timestamp(dates[e]).date(),
                'score': round(float(score[k]), 4),
                'corr_dif': round(float(cd[k]), 4),
                'corr_dea': round(float(ce[k]), 4),
                'bars': L,
                'win_ret_pct': round(float(close[e] / close[k] - 1) * 100, 2),
                'dif_min': round(float(dif[k:e + 1].min()), 4),
                'dif_max': round(float(dif[k:e + 1].max()), 4),
            }
            if curv is not None:
                row['corr_curv'] = round(float(curv[k]), 4)
            row.update(_trade_calc(close, dates, dif, dea, hist, int(k), int(e),
                                   args.budget, sell_rule=args.sell_rule,
                                   lead=args.lead, shrink=args.shrink_days,
                                   fees=fees, entry=entry, fresh_days=fresh))
            if row.get('status') == '金叉过旧':
                n_stale += 1
                continue
            rows.append(row)
    if n_stale:
        print(f'[过滤] 金叉过旧（距确认日 > {fresh} 根）：{n_stale} 个窗口')
    args._n_stale = int(getattr(args, '_n_stale', 0)) + n_stale
    df = pd.DataFrame(rows)
    if len(df):
        df = df.sort_values(['score', 'start'],
                            ascending=[False, False]).reset_index(drop=True)
        if args.overlap > 0:
            n0 = len(df)
            df = _dedup(df, args.overlap)
            print(f'[去重] 重叠窗口非极大值抑制：{n0} -> {len(df)} 条')
            df = df.sort_values(['score', 'start'],
                                ascending=[False, False]).reset_index(drop=True)
    if args.latest and len(df):
        df = (df.sort_values(['stock_code', 'start'])
                .groupby('stock_code', as_index=False).tail(1)
                .sort_values('score', ascending=False).reset_index(drop=True)
        )
    if keep_meta:
        return df
    return df.drop(columns=[c for c in ('_i0', '_i1', '_b') if c in df.columns])


def _stats_row(df, label):
    """把一组匹配结果里「已了结」的交易汇总成一行统计（毛/净并列）。"""
    d = df[df['status'] == '已了结']
    if not len(d):
        return None
    inv = float(d['invest'].sum())
    win = d.loc[d['net_profit'] > 0, 'net_profit']
    los = d.loc[d['net_profit'] < 0, 'net_profit']
    return {
        '分组': label,
        '笔数': int(len(d)),
        '投入本金元': int(round(inv)),
        '毛胜率%': round(float((d['trade_ret_pct'] > 0).mean() * 100), 2),
        '净胜率%': round(float((d['net_profit'] > 0).mean() * 100), 2),
        '平均毛涨幅%': round(float(d['trade_ret_pct'].mean()), 2),
        '平均净涨幅%': round(float(d['net_ret_pct'].mean()), 2),
        '中位净涨幅%': round(float(d['net_ret_pct'].median()), 2),
        '毛收益元': int(round(float(d['profit'].sum()))),
        '交易成本元': int(round(float(d['cost'].sum()))),
        '净收益元': int(round(float(d['net_profit'].sum()))),
        '净收益率%': round(float(d['net_profit'].sum() / inv * 100), 2) if inv else None,
        '平均持有天': round(float(d['hold_bars'].mean()), 1),
        '盈亏比': (round(float(win.mean() / abs(los.mean())), 2)
                   if len(win) and len(los) else None),
        '最大单笔净盈利%': round(float(d['net_ret_pct'].max()), 2),
        '最大单笔净亏损%': round(float(d['net_ret_pct'].min()), 2),
        '持有中笔数': int((df['status'] == '持有中').sum()),
        '无金叉笔数': int((df['status'] == '无金叉').sum()),
    }


def _stats_table(comb):
    """三层回测统计：全部 / 按买入年份 / 按匹配池。"""
    rows = [_stats_row(comb, '全部')]
    for y, g in comb.groupby(pd.to_datetime(comb['buy_date']).dt.year):
        rows.append(_stats_row(g, f'{int(y)}年'))
    for p, g in comb.groupby('pool'):
        rows.append(_stats_row(g, f'池:{p}'))
    return pd.DataFrame([r for r in rows if r])


def main(argv=None):
    ap = argparse.ArgumentParser(prog='template_scan',
                                 description='以某段 DIF/DEA 为模板全市场形状匹配')
    ap.add_argument('--code', default='601898', help='模板股票代码')
    ap.add_argument('--start', default='2025-11-11', help='模板起始日')
    ap.add_argument('--end', default='2026-03-16', help='模板结束日')
    ap.add_argument('--universe', default=None,
                    help='匹配池 hs300 / coal / all，可逗号分隔多池'
                         '（如 hs300,coal；默认取配置 universe.default）')
    ap.add_argument('--threshold', type=float, default=0.97,
                    help='形状相似度 score 阈值（默认0.97；0.98更严，0.99极严）')
    ap.add_argument('--overlap', type=float, default=0.5,
                    help='同一只股票窗口重叠超过该比例即去重，仅留最高分（0=不去重）')
    ap.add_argument('--budget', type=float, default=100000.0,
                    help='单次买入金额上限（元），按100股整手，默认10万')
    ap.add_argument('--sell-rule', default='shrink',
                    choices=['shrink', 'lead', 'bar', 'dif', 'hist0'],
                    help='卖出条件：shrink=MACD柱连续N日缩短(默认) / '
                         'lead=死叉(DIF<DEA)前N个交易日 / bar=MACD柱<DEA / '
                         'dif=DIF<DEA / hist0=MACD柱<0')
    ap.add_argument('--lead', type=int, default=4,
                    help='--sell-rule=lead 时提前的交易日数（默认4，即死叉前4天）')
    ap.add_argument('--shrink-days', type=int, default=6,
                    help='--sell-rule=shrink 时要求的连续缩短日数（默认6，全市场最优档）')
    ap.add_argument('--entry', default='cross', choices=['cross', 'confirm'],
                    help='买入口径：cross=匹配窗口内金叉日收盘（旧口径，含前视偏差，'
                         '仅用于复现历史结论）/ confirm=形态确认日收盘（可实盘，默认卖出'
                         '计时也从确认日起算）')
    ap.add_argument('--fresh-days', type=int, default=5,
                    help='--entry=confirm 时要求窗口内最后一次金叉距确认日 <= N 根'
                         '（默认5，与 scan_buy_history 一致；-1=不限制）')
    ap.add_argument('--commission', type=float, default=0.00025,
                    help='佣金率（双边，默认万2.5=0.00025）')
    ap.add_argument('--stamp', type=float, default=0.0005,
                    help='印花税率（仅卖出，默认0.05%%=0.0005）')
    ap.add_argument('--transfer', type=float, default=0.00001,
                    help='过户费率（双边，默认0.001%%=0.00001）')
    ap.add_argument('--min-commission', type=float, default=5.0,
                    help='单笔最低佣金（元，默认5）')
    ap.add_argument('--cost', type=float, default=None,
                    help='统一双边合计成本（如0.002），给出后覆盖上述明细费率，'
                         '口径同 ABM 的 backtest.cost')
    ap.add_argument('--step', type=int, default=1, help='滑动步长（默认1）')
    ap.add_argument('--latest', action='store_true',
                    help='每只股票只保留最近一个匹配窗口')
    ap.add_argument('--exclude-self', action='store_true',
                    help='排除模板股票自身')
    ap.add_argument('--limit', type=int, default=None, help='只处理前 N 只（冒烟）')
    ap.add_argument('--show', type=int, default=40, help='控制台打印前 N 条')
    ap.add_argument('--out', default=None)
    args = ap.parse_args(argv)

    # ── 1. 提取模板 ─────────────────────────────────────────────────────
    t_close, t_dates, t_dif, t_dea, t_name, mp = _series(args.code)
    m = (t_dates >= np.datetime64(args.start)) & (t_dates <= np.datetime64(args.end))
    if int(m.sum()) < 8:
        raise SystemExit(f'模板区间 {args.start}~{args.end} 内交易日不足（{int(m.sum())}）')
    tpl_dif_z = zscore(t_dif[m])
    tpl_dea_z = zscore(t_dea[m])
    L = int(m.sum())
    print('=' * 78)
    print(f'  模板匹配：{args.code} {t_name}  {args.start} ~ {args.end}')
    print('=' * 78)
    print(f'  模板长度 L={L} 个交易日   MACD={mp}')
    print(f'  模板 DIF 范围 {float(t_dif[m].min()):.3f} ~ {float(t_dif[m].max()):.3f}   '
          f'DEA {float(t_dea[m].min()):.3f} ~ {float(t_dea[m].max()):.3f}')
    print(f'  成本口径   : {_fee_desc(args)}')
    if args.entry == 'confirm':
        fresh_desc = ('不限' if args.fresh_days < 0
                      else f'金叉距确认日 <= {args.fresh_days} 根')
        print(f'  买入口径   : 形态确认日收盘（可实盘）  {fresh_desc}')
    else:
        print('  买入口径   : 窗口内金叉日收盘（旧口径，含前视偏差，实盘不可执行）')

    # ── 2. 匹配池（支持逗号分隔多池，如 hs300,coal,all）─────────────────
    cfg = S.load_config()
    default = cfg.get('universe', {}).get('default', 'hs300')
    pools = [p.strip() for p in (args.universe or default).split(',') if p.strip()]

    S.ensure_dirs()
    out = args.out or os.path.join(S.OUTPUT_DIR, f'template_{args.code}.xlsx')
    as_excel = out.lower().endswith(('.xlsx', '.xls'))
    sheets, collected = {}, []

    for pool in pools:
        codes, label = _resolve_pool(pool)
        daily = D.load_daily(codes=codes)
        if args.limit:
            keep = sorted(daily['stock_code'].unique())[:args.limit]
            daily = daily[daily['stock_code'].isin(set(keep))]
            print(f'[限制] 只处理前 {len(keep)} 只')
        print(f'\n--- 匹配池「{label}」{daily["stock_code"].nunique()} 只 / '
              f'{len(daily):,} 行  (step={args.step}, '
              f'threshold={args.threshold}) ---', flush=True)
        df = _scan(daily, tpl_dif_z, tpl_dea_z, L, mp, args)
        n_hit = df['stock_code'].nunique() if len(df) else 0
        print(f'[结果] {label}: {len(df)} 个匹配窗口 / {n_hit} 只股票')
        if not len(df):
            print('（无结果，可降低 --threshold）')
            continue
        if args.show > 0:
            print('\n' + df.head(args.show).to_string(index=False))
            if len(df) > args.show:
                print(f'  ... 其余 {len(df) - args.show} 条见输出文件')
        best = (df.sort_values(['stock_code', 'score'])
                  .groupby('stock_code', as_index=False).tail(1)
                  .sort_values('score', ascending=False).reset_index(drop=True))
        if as_excel:
            sheets[f'{label}_明细'] = df
            sheets[f'{label}_每股最佳'] = best
        collected.append(df.assign(pool=label))

    if not collected:
        print('\n（全部匹配池均无结果）')
        return 0

    # ── 3. 输出 ─────────────────────────────────────────────────────────
    comb = pd.concat(collected, ignore_index=True)
    comb = (comb.sort_values('score', ascending=False)
                .groupby(['stock_code', 'start', 'end'], as_index=False,
                         sort=False)
                .agg(stock_name=('stock_name', 'first'),
                     score=('score', 'first'),
                     corr_dif=('corr_dif', 'first'),
                     corr_dea=('corr_dea', 'first'),
                     bars=('bars', 'first'),
                     win_ret_pct=('win_ret_pct', 'first'),
                     dif_min=('dif_min', 'first'),
                     dif_max=('dif_max', 'first'),
                     buy_date=('buy_date', 'first'),
                     buy_price=('buy_price', 'first'),
                     sell_date=('sell_date', 'first'),
                     sell_price=('sell_price', 'first'),
                     trade_ret_pct=('trade_ret_pct', 'first'),
                     shares=('shares', 'first'),
                     invest=('invest', 'first'),
                     buy_fee=('buy_fee', 'first'),
                     sell_fee=('sell_fee', 'first'),
                     cost=('cost', 'first'),
                     cost_pct=('cost_pct', 'first'),
                     profit=('profit', 'first'),
                     net_profit=('net_profit', 'first'),
                     net_ret_pct=('net_ret_pct', 'first'),
                     hold_bars=('hold_bars', 'first'),
                     status=('status', 'first'),
                     pool=('pool', lambda s: ','.join(dict.fromkeys(s)))))
    comb = comb[['stock_code', 'stock_name', 'start', 'end', 'score', 'corr_dif',
                 'corr_dea', 'bars', 'win_ret_pct', 'dif_min', 'dif_max',
                 'buy_date', 'buy_price', 'sell_date', 'sell_price',
                 'trade_ret_pct', 'shares', 'invest', 'buy_fee', 'sell_fee',
                 'cost', 'cost_pct', 'profit', 'net_profit', 'net_ret_pct',
                 'hold_bars', 'status', 'pool']]
    if as_excel:
        stats = _stats_table(comb)
        _write_excel(out, {'回测统计': stats, '全部汇总': comb, **sheets})
        print(f'\n[OK] 已保存: {out}')
        print(f'     工作表: {["回测统计", "全部汇总"] + list(sheets)}')
        print(f'     全部汇总: {len(comb)} 个匹配窗口 / '
              f'{comb["stock_code"].nunique()} 只股票')
        if len(stats):
            print('\n--- 回测统计（「净」= 已扣双边交易成本）---')
            print(stats[['分组', '笔数', '净胜率%', '平均净涨幅%', '中位净涨幅%',
                         '净收益元', '交易成本元', '净收益率%', '平均持有天',
                         '盈亏比', '持有中笔数']].to_string(index=False))
        done = comb[comb['status'] == '已了结']
        if len(done):
            gross = done['profit'].sum()
            print(f'     [毛额] 已了结 {len(done)} 笔  '
                  f'胜率 {(done["trade_ret_pct"] > 0).mean() * 100:.1f}%  '
                  f'平均涨幅 {done["trade_ret_pct"].mean():.2f}%  '
                  f'合计收益 {gross:,.0f} 元')
            print(f'     [成本] 合计 {done["cost"].sum():,.0f} 元'
                  f'（均 {done["cost"].mean():,.0f} 元/笔，占本金 '
                  f'{done["cost_pct"].mean():.3f}%）')
            print(f'     [净额] 净收益 {done["net_profit"].sum():,.0f} 元  '
                  f'平均净涨幅 {done["net_ret_pct"].mean():.2f}%  '
                  f'净胜率 {(done["net_profit"] > 0).mean() * 100:.1f}%  '
                  f'（成本/毛收益 {done["cost"].sum() / (abs(gross) or 1.0) * 100:.1f}%）')
    else:
        comb.to_csv(out, index=False, encoding='utf-8-sig')
        print(f'\n[OK] 已保存: {out}   （{len(comb)} 个窗口 / '
              f'{comb["stock_code"].nunique()} 只）')
    return 0


if __name__ == '__main__':
    sys.exit(main())
