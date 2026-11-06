# -*- coding: utf-8 -*-
"""
==============================================================================
pattern_prob.py —— 「前半形态成立 → 后半走势出现」的**条件概率**统计
==============================================================================
template_scan.py 的做法是：把模板**整段**（前半绿柱下跌 + 后半红柱上抛）的
DIF/DEA 形状**一起**拿去全市场匹配（见 template_scan.py:328-333），命中即意味着
「后半段的上涨形状也已经被匹配到了」，因此它**无法**回答下面这个问题：

    如果只要求**前半段（绿柱下跌）**形态成立，
    那么**后半段（上抛上涨）**出现的概率有多大？

本脚本把模板沿「分界点」切成两段：

    [0, S-1]  前半段 pre   —— 绿柱下跌 / 筑底段（形状模板）
    [S, L-1]  后半段 post  —— 红柱上抛段（只用于观察，不参与匹配）

然后：

    ① 只用**前半段模板** pre 做全市场滑动匹配（DIF/DEA 形状 Pearson 相似度
       score = (corr_dif + corr_dea) / 2 ≥ --threshold）；
    ② 对每个命中点（窗口末日 = 形态完成日 e），把紧随其后的 L2 = L - S 个
       交易日当作**后半观察窗** [e+1, e+L2]，统计：

           条件概率  P(后半上涨 | 前半形态成立) = ?

       具体包括三类「后半出现」的判定：
         * 收益口径  —— 后续 L2 天终值涨幅 > 0 / >5% / >10% / >15% / >20%
         * 形态口径  —— 后续窗口的 DIF/DEA 形状与模板后半的 score ≥ threshold
                        （即「后半段的形状真的又出现了」）
         * 事件口径  —— 后续窗口内是否出现金叉（DIF 上穿 DEA，即策略买点）
          * 期间口径  —— 后续 L2 天内**最高涨幅** > 5% / 10% / 15% / 20%
                        （「上抛上涨到底有没有出现过」的最直接口径）


    ③ **基准对照**：全市场**任意时点**持有同样 L2 个交易日的收益分布。
       没有基准的「上涨率 55%」是没有意义的（随机也能到 50%），因此本脚本
       同时给出「命中 − 基准 = 超额」，用于判断这个形态到底有没有预测力。

分界点 --split（默认 auto）—— 决定「匹配段」到哪根为止：
    auto   模板内**最后一次金叉**（DIF 上穿 DEA）—— 即策略的买入点，前半含
           「红柱衰竭 + 绿柱下跌」完整下跌过程。601898 默认模板下 S=60。
    green  模板内**最后一个柱<0 的交易日的次日**（绿柱段真正结束那天）
    mid    模板中点 L//2
    end    整段匹配：模板**全部**都用来做形状匹配（S = L），适用于
           「模板曲线本身已含金叉、要看之后再涨不涨」的问题，例如
           --start 2025-11-11 --end 2026-02-09 --split end
    N      直接给出整数（模板内相对索引，从 0 起）

「上抛参照段」--ref-start（参照形状取自哪里）：
    默认  --split end 时 = --end 之后的**真实走势**（即模板自己的下一段）；
          否则 = 模板内分界点之后的后半段。
    长度  = --horizon（--split end 时默认 22 个交易日）

用法::

    python pattern_prob.py                                  # 默认模板 601898 / 沪深300
    python pattern_prob.py --universe all                   # 全市场（更充分）
    python pattern_prob.py --universe hs300,coal,all        # 多池合并统计
    python pattern_prob.py --split green                    # 只认「纯绿柱段」为前半
    python pattern_prob.py --pre-start 9                    # 前半模板从 j=9 起（剔除红柱段）
    python pattern_prob.py --threshold 0.95                 # 放宽形态匹配
    python pattern_prob.py --horizon 22                     # 观察窗 / 参照段长度
    python pattern_prob.py --pcts 5,10,20,30                # 自定义涨幅档位

    # 「601898 2025-11-11~2026-02-09 这条 DIF/DEA 曲线再现后，之后会上抛吗？」
    python pattern_prob.py --end 2026-02-09 --split end --horizon 22 --universe all

输出 output/pattern_prob_<code>.xlsx，多工作表：
    「概率总表」    命中组 / 基准组 / 超额（一行一层）
    「分池统计」    按匹配池拆分
    「分年度统计」  按命中点「形态完成日」的年份拆分
    「分档统计」    按形状相似度 / 匹配段涨跌幅分档（看「越像/跌得越深是否越准」）
    「命中明细」    每个命中窗口一行（含 score / 匹配段涨幅 / 上抛相似度 / 各金叉标记）
    「基准样本」    全市场基准收益样本（最多 2000 行抽样）
    「口径说明」    模板切分点、上抛参照段、长度、阈值等复现信息

数据源：backward_tdx_daily（后复权），见 mlr/settings.py TABLE_DAILY。
匹配与归一化函数直接复用 template_scan.py，保证与主策略口径一致。
"""
import argparse
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import template_scan as TS
from mlr import data as D, settings as S
from mlr.macd import macd as calc_macd

PCT_BINS = (5.0, 10.0, 15.0, 20.0)       # 默认涨幅档位，--pcts 可覆盖


def _split_index(hist, dif, dea, mode):
    """确定模板前/后半的分界索引 S（模板内相对索引，0 起）。

    S 的含义：前半 = [0, S-1]，后半 = [S, L-1]，S ∈ [1, L-1]。
    """
    n = hist.size
    if mode in ('end', 'full', 'all'):       # 整段匹配：匹配段 = 整个模板
        return n
    if mode == 'mid':
        return n // 2
    if mode == 'green':                      # 最后一个绿柱日的次日
        neg = np.where(hist < 0)[0]
        return int(neg[-1]) + 1 if neg.size else n // 2
    if mode == 'auto':                       # 最后一次金叉（策略买点）
        xs = np.where((dif[1:] > dea[1:]) & (dif[:-1] <= dea[:-1]))[0] + 1
        return int(xs[-1]) if xs.size else n // 2
    try:                                     # 直接给整数
        return max(1, min(int(mode), n - 1))
    except (TypeError, ValueError):
        raise SystemExit(f'未知 --split 取值: {mode}'
                         '（auto / green / mid / 整数）')


def _prep_template(t_dif, t_dea, t_hist, S, pre0=0, post_dif=None, post_dea=None):
    """构造「匹配段 / 上抛参照段」两段模板（DIF、DEA 各自 z-score 归一化）。

    z-score 只保留**形状**（消除价格尺度差异），与 template_scan.py 口径一致。

    匹配段  = [pre0, S-1]（长度 L1）—— 拿去全市场滑动匹配的形状模板
              pre0 默认 0；设为 >0 可剔除模板开头的红柱衰竭段。
              特殊地 S = L 时匹配段 = 整个模板（--split end）。
    上抛参照段（post_dif/post_dea，长度 L2）—— 「上抛曲线」的形状基准，
              用于统计命中之后是否走出同款上抛。None 时退化为模板自身 [S, L-1]。
    """
    L = int(t_hist.size)
    if post_dif is None:
        post_dif, post_dea = t_dif[S:L], t_dea[S:L]
    post_dif = np.asarray(post_dif, dtype='float64')
    post_dea = np.asarray(post_dea, dtype='float64')
    return {
        'S': int(S), 'L': L, 'L1': int(S - pre0),
        'L2': int(post_dif.size), 'pre0': int(pre0),
        'dif_z': TS.zscore(t_dif[pre0:S]),
        'dea_z': TS.zscore(t_dea[pre0:S]),
        'post_dif_z': TS.zscore(post_dif),
        'post_dea_z': TS.zscore(post_dea),
    }


def _scan_pre(daily, tpl, mp, args):
    """只用「匹配段模板」做全市场滑动匹配，返回含后续观察窗统计的命中明细。

    对每个命中窗口 [k, e]（e = k + L1 - 1，即「形态完成日」）：
        观察窗   = [e+1, e+L2]，起点价 = close[e]（形态完成日收盘价）。
    另给「金叉后」锚点口径（观察窗内**首个金叉日** g）：
        g_*      = 以 g 为起点价、窗口 [g+1, g+G] 的收益与上抛相似度。
    """
    L1, L2 = tpl['L1'], tpl['L2']
    if L2 < 1:
        raise SystemExit(f'模板后半为空（S={tpl["S"]}），请调小 --split')
    G = int(args.gold_horizon) if args.gold_horizon else L2
    rows = []
    for code, g in daily.groupby('stock_code', sort=False):
        if args.exclude_self and code == args.code:
            continue
        g = g.sort_values('trade_date')
        name = g['stock_name'].iloc[-1]
        close = g['close_price'].to_numpy('float64')
        dates = pd.to_datetime(g['trade_date']).to_numpy()
        dif, dea, _ = calc_macd(close, *mp)
        n = close.size
        if n < L1 + L2 + 1:                       # 连一个完整观察窗都放不下
            continue
        cd = TS.sliding_corr(dif, tpl['dif_z'])   # 匹配段形状相似度
        ce = TS.sliding_corr(dea, tpl['dea_z'])
        if cd.size == 0:
            continue
        score = (cd + ce) / 2.0
        hits = np.where(score >= args.threshold)[0]
        if not hits.size:
            continue
        sc2 = _pair_score(dif, dea, tpl, L2)      # 上抛形状相似度（起点索引）
        scg = sc2 if G == L2 else _pair_score(dif, dea, tpl, G)
        for k in hits:
            k = int(k)
            e = k + L1 - 1                        # 形态完成日（匹配段末日）
            if e + L2 > n - 1:                    # 后续观察窗不足 L2 天
                continue
            base = float(close[e])
            if base <= 0:
                continue
            fw = close[e + 1:e + L2 + 1]
            ret = float(close[e + L2] / base - 1) * 100
            d2, e2 = dif[e + 1:e + L2 + 1], dea[e + 1:e + L2 + 1]
            gold = bool(((d2[1:] > e2[1:]) & (d2[:-1] <= e2[:-1])).any())
            dp, ep = dif[k:e + 1], dea[k:e + 1]        # 匹配段内的金叉（诊断用）
            pre_gold = (bool(((dp[1:] > ep[1:]) &
                              (dp[:-1] <= ep[:-1])).any())
                        if dp.size > 1 else False)
            # ── 「金叉后」锚点口径：观察窗内首个金叉日 g ──
            gdate = ggap = gr = gmx = gmn = gsc = None
            cr = np.where((d2[1:] > e2[1:]) & (d2[:-1] <= e2[:-1]))[0]
            if cr.size:
                gi = e + 1 + int(cr[0])
                if gi + G <= n - 1 and close[gi] > 0:
                    bg = float(close[gi])
                    gw = close[gi + 1:gi + G + 1]
                    gdate = pd.Timestamp(dates[gi]).date()
                    ggap = int(gi - e)
                    gr = round(float(close[gi + G] / bg - 1) * 100, 2)
                    gmx = round(float(gw.max() / bg - 1) * 100, 2)
                    gmn = round(float(gw.min() / bg - 1) * 100, 2)
                    if scg.size > gi + 1:
                        gsc = round(float(scg[gi + 1]), 4)
            fsc = (round(float(sc2[e + 1]), 4)
                   if sc2.size > e + 1 else None)
            rows.append({
                '_i0': k, '_i1': e,
                'stock_code': code, 'stock_name': name,
                'start': pd.Timestamp(dates[k]).date(),
                'end': pd.Timestamp(dates[e]).date(),
                'score': round(float(score[k]), 4),
                'corr_dif': round(float(cd[k]), 4),
                'corr_dea': round(float(ce[k]), 4),
                'pre_ret_pct': round(float(close[e] / close[k] - 1) * 100, 2),
                'fwd_start': pd.Timestamp(dates[e + 1]).date(),
                'fwd_end': pd.Timestamp(dates[e + L2]).date(),
                'fwd_ret_pct': round(ret, 2),
                'fwd_max_pct': round(float(fw.max() / base - 1) * 100, 2),
                'fwd_min_pct': round(float(fw.min() / base - 1) * 100, 2),
                'fwd_score': fsc,
                'fwd_gold': gold,
                'pre_gold': pre_gold,
                'gdate': gdate, 'ggap': ggap, 'g_ret_pct': gr,
                'g_max_pct': gmx, 'g_min_pct': gmn, 'g_score': gsc,
                'up': bool(ret > 0),
            })
    df = pd.DataFrame(rows)
    if len(df):
        df = df.sort_values(['score', 'start'],
                            ascending=[False, False]).reset_index(drop=True)
        if args.overlap > 0:                      # 同一只股票的重叠命中只留最高分
            n0 = len(df)
            df = TS._dedup(df, args.overlap)
            df = df.sort_values(['score', 'start'],
                                ascending=[False, False]).reset_index(drop=True)
            print(f'  [去重] 重叠窗口非极大值抑制：{n0} -> {len(df)} 条')
    return df


def _pair_score(dif, dea, tpl, L2):
    """DIF/DEA 与「上抛参照段」前 L2 根的滑动形状相似度。

    返回数组按**窗口起点**索引：sc[j] 对应窗口 [j, j+L2-1]。
    """
    cd = TS.sliding_corr(dif, tpl['post_dif_z'][:L2])
    ce = TS.sliding_corr(dea, tpl['post_dea_z'][:L2])
    k = min(cd.size, ce.size)
    return (cd[:k] + ce[:k]) / 2.0 if k else np.array([])


def _baseline(daily, L2, tpl=None, mp=None):
    """全市场基准：**任意时点**持有 L2 个交易日的收益分布（与命中组同 horizon）。

    口径与命中组严格对齐：
        起点   = 第 i 个交易日收盘价
        观察窗 = (i, i+L2] 即 [i+1, i+L2]
        ret_pct = close[i+L2] / close[i] - 1
        max_pct = max(close[i+1 .. i+L2]) / close[i] - 1
        min_pct = min(close[i+1 .. i+L2]) / close[i] - 1
    传入 tpl + mp 时额外给出 fwd_score —— 「任意时点的上抛再现率」基准，
    用于判断「上抛再现率 21%」到底是高还是低。
    返回列：stock_code, date, ret_pct, max_pct, min_pct[, fwd_score]（均为 %）。
    """
    parts = []
    want_fwd = tpl is not None and mp is not None
    for code, g in daily.groupby('stock_code', sort=False):
        g = g.sort_values('trade_date')
        close = g['close_price'].to_numpy('float64')
        dts = pd.to_datetime(g['trade_date']).to_numpy()
        n = close.size
        if n <= L2 + 1:
            continue
        s = pd.Series(close)
        rev = s[::-1]
        # rmax[i] = max(close[i .. i+L2-1])，故 shift(-1) 后取到 (i, i+L2]
        rmax = rev.rolling(L2).max()[::-1]
        rmin = rev.rolling(L2).min()[::-1]
        ret = (s.shift(-L2) / s - 1) * 100
        mx = (rmax.shift(-1) / s - 1) * 100
        mn = (rmin.shift(-1) / s - 1) * 100
        ok = (ret.notna() & mx.notna() & mn.notna()).to_numpy()
        if not ok.any():
            continue
        cols = {
            'stock_code': code,
            'date': dts[ok],
            'ret_pct': ret.to_numpy()[ok],
            'max_pct': mx.to_numpy()[ok],
            'min_pct': mn.to_numpy()[ok],
        }
        if want_fwd:                      # 任意时点的「上抛再现」相似度
            dif, dea, _ = calc_macd(close, *mp)
            sc = _pair_score(dif, dea, tpl, L2)   # sc[j] = 窗口 [j, j+L2-1]
            fsc = np.full(n, np.nan)
            if sc.size > 1:               # 行内起点 i 的观察窗是 [i+1, i+L2]
                fsc[:sc.size - 1] = sc[1:]
            cols['fwd_score'] = fsc[ok]
        parts.append(pd.DataFrame(cols))
    if not parts:
        return pd.DataFrame(
            columns=['stock_code', 'date', 'ret_pct', 'max_pct', 'min_pct'])
    return pd.concat(parts, ignore_index=True)


def _baseline_gold(daily, G, tpl, mp):
    """「金叉后」基准：全市场**任意金叉日**起算 G 个交易日的收益与上抛相似度。

    口径与命中组「金叉后」层严格对齐：
        锚点 = 金叉日 g（close[g-1] <= close[g] 那一类的 DIF 上穿 DEA）
        收益 = close[g+G] / close[g] - 1，观察窗 = [g+1, g+G]
    回答的是「**金叉本身**有没有预测力」——即不要求任何形状前提时的上抛率。
    """
    parts = []
    for code, g in daily.groupby('stock_code', sort=False):
        g = g.sort_values('trade_date')
        close = g['close_price'].to_numpy('float64')
        dts = pd.to_datetime(g['trade_date']).to_numpy()
        n = close.size
        if n < G + 2:
            continue
        dif, dea, _ = calc_macd(close, *mp)
        cr = np.where((dif[1:] > dea[1:]) & (dif[:-1] <= dea[:-1]))[0] + 1
        cr = cr[cr + G <= n - 1]
        if not cr.size:
            continue
        cr = cr[close[cr] > 0]
        if not cr.size:
            continue
        base = close[cr]
        idx = cr[:, None] + np.arange(1, G + 1)
        win = close[idx]
        sc = _pair_score(dif, dea, tpl, G)        # sc[j] = 窗口 [j, j+G-1]
        fsc = np.full(n, np.nan)
        if sc.size > 1:
            fsc[:sc.size - 1] = sc[1:]            # 锚点 g 的观察窗是 [g+1, g+G]
        parts.append(pd.DataFrame({
            'stock_code': code,
            'date': dts[cr],
            'ret_pct': (close[cr + G] / base - 1) * 100,
            'max_pct': (win.max(axis=1) / base - 1) * 100,
            'min_pct': (win.min(axis=1) / base - 1) * 100,
            'fwd_score': fsc[cr],
        }))
    if not parts:
        return pd.DataFrame(columns=['stock_code', 'date', 'ret_pct',
                                     'max_pct', 'min_pct', 'fwd_score'])
    return pd.concat(parts, ignore_index=True)


def _gold_frame(hit):
    """从命中明细里抽出「金叉后」锚点层，并改成 _pair_rows 认识的行结构。"""
    if hit is None or not len(hit) or 'gdate' not in hit.columns:
        return None
    gdf = hit[hit['gdate'].notna()].copy()
    if not len(gdf):
        return None
    # 先丢掉「形态完成日之后」那套 fwd_*，只留金叉锚点那套（避免重名列）
    gdf = gdf.drop(columns=[c for c in
                            ('fwd_start', 'fwd_end', 'fwd_ret_pct',
                             'fwd_max_pct', 'fwd_min_pct', 'fwd_score')
                            if c in gdf.columns])
    gdf = gdf.rename(columns={'gdate': 'anchor', 'ggap': 'gap',
                              'g_ret_pct': 'fwd_ret_pct',
                              'g_max_pct': 'fwd_max_pct',
                              'g_min_pct': 'fwd_min_pct',
                              'g_score': 'fwd_score'})
    gdf['fwd_gold'] = True
    if 'fwd_score' in gdf.columns:
        gdf['fwd_score'] = pd.to_numeric(gdf['fwd_score'], errors='coerce')
    for c in ('fwd_ret_pct', 'fwd_max_pct', 'fwd_min_pct'):
        gdf[c] = pd.to_numeric(gdf[c], errors='coerce')
    return gdf.dropna(subset=['fwd_ret_pct'])


def _prob_row(ret, label, max_pct=None, min_pct=None, bins=PCT_BINS,
              fwd_score=None, fwd_gold=None, fwd_threshold=None,
              pre_gold=None):
    """把一组「后续收益样本」汇总成一行条件概率统计。

    ret      : 后续 L2 天终值收益率（%），用于「上涨率 / 涨>X%率 / 均值」等
    max_pct  : 观察窗内最高收益率（%），max_pct 为 None 时该组不输出这两列
    fwd_score: 观察窗与「上抛参照段」的形状相似度 → 「上抛再现率」
    fwd_gold : 观察窗内是否出现金叉 → 「后续金叉率」
    pre_gold : **匹配段内**是否已出现金叉 → 诊断（命中形状是否本身已含金叉）
    """
    ret = np.asarray(ret, dtype='float64')
    ret = ret[~np.isnan(ret)]
    if ret.size == 0:
        return None
    row = {'分组': label, '样本数': int(ret.size),
           '上涨率%': round(float((ret > 0).mean()) * 100, 2)}
    for b in bins:
        row[f'涨>{float(b):.0f}%率%'] = round(float((ret > b).mean()) * 100, 2)
    row['平均收益%'] = round(float(ret.mean()), 2)
    row['中位收益%'] = round(float(np.median(ret)), 2)
    row['收益标准差%'] = (round(float(ret.std(ddof=1)), 2)
                      if ret.size > 1 else None)
    if max_pct is not None:
        mx = np.asarray(max_pct, dtype='float64')
        mx = mx[~np.isnan(mx)]
        row['期间最高均值%'] = round(float(mx.mean()), 2) if mx.size else None
        row['期间最低均值%'] = round(float(np.nanmean(min_pct)), 2)
        # 「上抛上涨出现过」的直接口径：观察窗内最高涨幅超过档位
        for b in bins:
            row[f'期间涨>{float(b):.0f}%率%'] = (
                round(float((mx > b).mean()) * 100, 2) if mx.size else None)
    if fwd_score is not None:
        fs = pd.Series(fwd_score).dropna().to_numpy('float64')
        row['上抛再现率%'] = (round(float((fs >= fwd_threshold).mean()) * 100, 2)
                          if fs.size else None)
        row['上抛平均相似度'] = round(float(fs.mean()), 4) if fs.size else None
    if fwd_gold is not None:
        gd = pd.Series(fwd_gold).dropna().astype(bool).to_numpy()
        row['后续金叉率%'] = round(float(gd.mean()) * 100, 2) if gd.size else None
    if pre_gold is not None:
        pg = pd.Series(pre_gold).dropna().astype(bool).to_numpy()
        row['匹配段内金叉率%'] = (round(float(pg.mean()) * 100, 2)
                            if pg.size else None)
    return row


def _excess_row(hit, base):
    """命中组 − 基准组 的超额（仅对两组共有的数值列做差）。"""
    if hit is None or base is None:
        return None
    out = {'分组': '超额(命中-基准)'}
    for k, v in hit.items():
        if k == '分组' or k == '样本数':
            continue
        b = base.get(k)
        if isinstance(v, (int, float)) and isinstance(b, (int, float)):
            out[k] = round(float(v) - float(b), 2)
    return out


def _bucket_rows(hit, base_df, col, edges, fmt, args, bins):
    """把命中样本按某一列（相似度 / 前半跌幅）分档，每档输出「命中/基准/超额」。

    用于回答「形态**越像**、或前半**跌得越深**，后半上涨的概率是否越高」。
    """
    if hit is None or not len(hit):
        return []
    v = pd.to_numeric(hit[col], errors='coerce')
    lab = pd.cut(v, bins=list(edges), right=False, include_lowest=True)
    rows = []
    for iv, g in hit.groupby(lab, observed=True, sort=True):
        rows += _pair_rows(g, base_df, fmt.format(iv), args, bins)
    return rows


def _pair_rows(hit_df, base_df, label, args, bins):
    """给出一组「命中 / 基准 / 超额」三行（样本为空则跳过对应行）。"""
    h = b = None
    if hit_df is not None and len(hit_df):
        kw = {}
        if 'pre_gold' in hit_df.columns:
            kw['pre_gold'] = hit_df['pre_gold']
        h = _prob_row(hit_df['fwd_ret_pct'], f'{label}·命中',
                      hit_df['fwd_max_pct'], hit_df['fwd_min_pct'], bins,
                      fwd_score=hit_df['fwd_score'],
                      fwd_gold=hit_df['fwd_gold'],
                      fwd_threshold=args.threshold, **kw)
    if base_df is not None and len(base_df):
        kbb = {}
        if 'fwd_score' in base_df.columns:      # 基准也给出「上抛再现率」
            kbb['fwd_score'] = base_df['fwd_score']
            kbb['fwd_threshold'] = args.threshold
        b = _prob_row(base_df['ret_pct'], f'{label}·基准',
                      base_df['max_pct'], base_df['min_pct'], bins, **kbb)
    return [r for r in (h, b, _excess_row(h, b)) if r]


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog='pattern_prob',
        description='「DIF/DEA 形状匹配 → 上抛曲线是否出现」的条件概率统计'
                    '（含全市场基准对照）')
    ap.add_argument('--code', default='601898', help='模板股票代码（默认601898）')
    ap.add_argument('--start', default='2025-11-11', help='模板起始日')
    ap.add_argument('--end', default='2026-03-16', help='模板结束日')
    ap.add_argument('--split', default='auto',
                    help='匹配段分界：auto=最后一次金叉(默认) / green=最后一个'
                         '绿柱日的次日 / mid=中点 / end=整段(模板全部用于匹配) / '
                         '整数=直接指定相对索引')
    ap.add_argument('--pre-start', type=int, default=0,
                    help='前半模板的起始相对索引（默认0；设为9可只用「纯绿柱段」'
                         '做前半模板，排除模板开头的红柱衰竭段）')
    ap.add_argument('--horizon', type=int, default=None,
                    help='观察窗/上抛参照段长度（默认=模板后半长度；'
                         '--split end 时默认 22）')
    ap.add_argument('--gold-horizon', type=int, default=None,
                    help='「金叉后」观察窗长度 G（默认=--horizon）')
    ap.add_argument('--ref-start', default=None,
                    help='「上抛参照段」起始日（默认：--split end 时取 --end 之后'
                         '首个交易日，否则取模板内分界点之后）')
    ap.add_argument('--threshold', type=float, default=0.97,
                    help='形状相似度阈值（匹配段命中 & 上抛再现判定共用，默认0.97）')
    ap.add_argument('--universe', default=None,
                    help='匹配池 hs300 / coal / all，可逗号分隔（默认取配置项）')
    ap.add_argument('--overlap', type=float, default=0.5,
                    help='同一只股票窗口重叠超过该比例即去重，仅留最高分（默认0.5）')
    ap.add_argument('--pcts', default='5,10,15,20',
                    help='涨幅档位（%%），逗号分隔，默认 5,10,15,20')
    ap.add_argument('--limit', type=int, default=None, help='每池只处理前 N 只（冒烟）')
    ap.add_argument('--exclude-self', action='store_true',
                    help='排除模板股票自身')
    ap.add_argument('--show', type=int, default=30, help='控制台打印命中明细前 N 条')
    ap.add_argument('--out', default=None)
    args = ap.parse_args(argv)

    bins = tuple(float(x) for x in str(args.pcts).split(',') if x.strip())
    if not bins:
        raise SystemExit('--pcts 至少要有一个档位')

    # ── 1. 提取模板并沿分界点切成前 / 后半 ───────────────────────────────
    t_close, t_dates, t_dif, t_dea, t_name, mp = TS._series(args.code)
    m = ((t_dates >= np.datetime64(args.start)) &
         (t_dates <= np.datetime64(args.end)))
    if int(m.sum()) < 12:
        raise SystemExit(f'模板区间 {args.start}~{args.end} 交易日不足'
                         f'（{int(m.sum())}）')
    tc, td, te = t_close[m], t_dif[m], t_dea[m]
    tdt = t_dates[m]
    th = 2.0 * (td - te)                       # MACD 柱
    L = int(td.size)
    SP = _split_index(th, td, te, args.split)  # 分界点（勿用 S，S 是 settings 别名）
    pre0 = int(args.pre_start)
    if not (0 <= pre0 <= SP - 3):
        raise SystemExit(f'--pre-start {pre0} 非法（须满足 0 <= pre-start <= '
                         f'分界点-3 = {SP - 3}）')

    # ── 「上抛参照段」：命中后拿它比对「上抛曲线到底有没有出现」───────────
    L2 = int(args.horizon) if args.horizon else (L - SP if SP < L else 22)
    if L2 < 1:
        raise SystemExit(f'--horizon {L2} 非法（须 >= 1）')
    end_i = int(np.searchsorted(t_dates, np.datetime64(args.end), 'right'))
    if args.ref_start:
        j0 = int(np.searchsorted(t_dates,
                                 np.datetime64(args.ref_start), 'left'))
        if j0 + L2 > t_dates.size:
            raise SystemExit(f'--ref-start {args.ref_start} 之后不足 {L2} 根')
        ref_dt, rd, re_ = (t_dates[j0:j0 + L2], t_dif[j0:j0 + L2],
                           t_dea[j0:j0 + L2])
        ref_desc = f'--ref-start {args.ref_start} 起 {L2} 根'
    elif SP >= L:                              # --split end：整段匹配
        if end_i + L2 > t_dates.size:
            raise SystemExit(f'--end {args.end} 之后不足 {L2} 根交易日，'
                             f'请调小 --horizon（最多 {t_dates.size - end_i}）')
        ref_dt, rd, re_ = (t_dates[end_i:end_i + L2], t_dif[end_i:end_i + L2],
                           t_dea[end_i:end_i + L2])
        ref_desc = f'模板结束日({args.end})之后的真实走势'
    elif SP + L2 <= L:                         # 参照段 = 模板自身的后半
        ref_dt, rd, re_ = tdt[SP:SP + L2], td[SP:SP + L2], te[SP:SP + L2]
        ref_desc = f'模板内 [{SP}, {SP + L2 - 1}]'
    else:                                      # 沿用模板最后 L2 根
        ref_dt, rd, re_ = tdt[L - L2:], td[L - L2:], te[L - L2:]
        ref_desc = f'模板内 [{L - L2}, {L - 1}]'
    tpl = _prep_template(td, te, th, SP, pre0, rd, re_)
    G = int(args.gold_horizon) if args.gold_horizon else L2
    if G < 1:
        raise SystemExit(f'--gold-horizon {G} 非法（须 >= 1）')

    print('=' * 78)
    print(f'  DIF/DEA 形状匹配 → 上抛曲线出现概率：{args.code} {t_name}  '
          f'{args.start} ~ {args.end}')
    print('=' * 78)
    print(f'  MACD={mp}   模板长度 L={tpl["L"]}   --split={args.split}   '
          f'--pre-start={pre0}')
    print(f'  匹配段(形状模板) = [{pre0}, {SP - 1}]  共 {tpl["L1"]} 交易日  '
          f'收盘 {tc[pre0]:.2f} -> {tc[SP - 1]:.2f}')
    print(f'  上抛参照段 = {ref_desc}  共 {L2} 交易日  '
          f'{pd.Timestamp(ref_dt[0]).date()} ~ '
          f'{pd.Timestamp(ref_dt[-1]).date()}')
    print(f'  观察窗 = 命中日之后紧随的 {L2} 个交易日')
    print(f'  金叉后口径：观察窗内首个金叉日起算 G={G} 个交易日')
    print(f'  形状阈值 score >= {args.threshold}   涨幅档位 {bins}')

    # ── 2. 逐池：只用前半模板匹配 + 全市场基准 ──────────────────────────
    cfg = S.load_config()
    default = cfg.get('universe', {}).get('default', 'hs300')
    pools = [p.strip() for p in (args.universe or default).split(',')
             if p.strip()]
    S.ensure_dirs()
    out = args.out or os.path.join(S.OUTPUT_DIR,
                                   f'pattern_prob_{args.code}.xlsx')

    hit_parts, base_by_pool, gbase_by_pool = [], {}, {}
    for pool in pools:
        codes, label = TS._resolve_pool(pool)
        daily = D.load_daily(codes=codes)
        if args.limit:
            keep = sorted(daily['stock_code'].unique())[:args.limit]
            daily = daily[daily['stock_code'].isin(set(keep))]
            print(f'[限制] 只处理前 {len(keep)} 只')
        print(f'\n--- 匹配池「{label}」{daily["stock_code"].nunique()} 只 / '
              f'{len(daily):,} 行 ---', flush=True)
        df = _scan_pre(daily, tpl, mp, args)
        bl = _baseline(daily, L2, tpl, mp)
        gl = _baseline_gold(daily, G, tpl, mp)
        base_by_pool[label] = bl
        gbase_by_pool[label] = gl
        n_hit = df['stock_code'].nunique() if len(df) else 0
        n_g = int(df['gdate'].notna().sum()) if len(df) else 0
        print(f'[命中] {label}: {len(df)} 个形状命中 / {n_hit} 只   '
              f'其中后续金叉 {n_g} 个   '
              f'基准样本 {len(bl):,} 条 / 金叉基准 {len(gl):,} 条', flush=True)
        if len(df):
            hit_parts.append(df.assign(pool=label))

    hit = (pd.concat(hit_parts, ignore_index=True) if hit_parts
           else pd.DataFrame(columns=[
               'stock_code', 'stock_name', 'start', 'end', 'score', 'corr_dif',
               'corr_dea', 'pre_ret_pct', 'fwd_start', 'fwd_end',
               'fwd_ret_pct', 'fwd_max_pct', 'fwd_min_pct', 'fwd_score',
               'fwd_gold', 'pre_gold', 'gdate', 'ggap', 'g_ret_pct',
               'g_max_pct', 'g_min_pct', 'g_score', 'up', 'pool',
               '_i0', '_i1']))
    base_all = (pd.concat(list(base_by_pool.values()), ignore_index=True)
                if base_by_pool else pd.DataFrame(
                    columns=['stock_code', 'date', 'ret_pct', 'max_pct',
                             'min_pct', 'fwd_score']))
    gbase_all = (pd.concat(list(gbase_by_pool.values()), ignore_index=True)
                 if gbase_by_pool else pd.DataFrame(
                     columns=['stock_code', 'date', 'ret_pct', 'max_pct',
                              'min_pct', 'fwd_score']))

    # ── 3. 概率总表（命中 / 基准 / 超额；再叠一层「金叉后」）──────────────
    total_rows = _pair_rows(hit, base_all, '全体', args, bins)
    g_hit = _gold_frame(hit)
    if g_hit is not None:
        total_rows += _pair_rows(g_hit, gbase_all, f'金叉后{G}日', args, bins)
    prob_tbl = pd.DataFrame(total_rows)

    # ── 4. 分池 / 分年度 ─────────────────────────────────────────────────
    pool_rows = []
    if len(hit):
        for p, g in hit.groupby('pool', sort=True):
            pool_rows += _pair_rows(g, base_by_pool.get(p), str(p), args, bins)
            gg = _gold_frame(g)
            if gg is not None:
                pool_rows += _pair_rows(gg, gbase_by_pool.get(p),
                                        f'{p}·金叉后{G}日', args, bins)
    pool_tbl = pd.DataFrame(pool_rows)

    yr_rows = []
    if len(hit):
        hy = pd.to_datetime(hit['end']).dt.year
        by = pd.to_datetime(base_all['date']).dt.year
        for y in sorted(hy.unique()):
            yr_rows += _pair_rows(hit[hy == y], base_all[by == y],
                                  f'{int(y)}年', args, bins)
    yr_tbl = pd.DataFrame(yr_rows)

    bk_rows = []
    if len(hit):
        se = ([args.threshold] +
              [x for x in (0.98, 0.99) if x > args.threshold] + [1.0001])
        bk_rows += _bucket_rows(hit, base_all, 'score', se,
                                '相似度{}', args, bins)
        bk_rows += _bucket_rows(hit, base_all, 'pre_ret_pct',
                                [-100.0, -20.0, -10.0, 0.0, 1e9],
                                '匹配段涨幅区间{}%', args, bins)
    bk_tbl = pd.DataFrame(bk_rows)

    # ── 5. 命中明细 / 基准样本 / 口径说明 ────────────────────────────────
    detail = hit.drop(columns=[c for c in ('_i0', '_i1', 'up')
                               if c in hit.columns]).copy()
    detail = detail.rename(columns={
        'stock_code': '股票代码', 'stock_name': '股票名称',
        'start': '匹配起始', 'end': '形态完成日', 'score': '形状相似度',
        'corr_dif': 'corr_dif', 'corr_dea': 'corr_dea',
        'pre_ret_pct': '匹配段涨幅%', 'fwd_start': '观察起始',
        'fwd_end': '观察结束', 'fwd_ret_pct': '观察窗涨幅%',
        'fwd_max_pct': '观察窗最高%', 'fwd_min_pct': '观察窗最低%',
        'fwd_score': '上抛相似度', 'fwd_gold': '观察窗金叉',
        'pre_gold': '匹配段内金叉', 'pool': '池',
        'gdate': '金叉日', 'ggap': '金叉距完成日',
        'g_ret_pct': f'金叉后{G}日涨幅%',
        'g_max_pct': '金叉后窗内最高%', 'g_min_pct': '金叉后窗内最低%',
        'g_score': '金叉后上抛相似度'})
    base_samp = (base_all.head(2000).rename(columns={
        'stock_code': '股票代码', 'date': '起点日', 'ret_pct': 'L2日收益%',
        'max_pct': '窗内最高%', 'min_pct': '窗内最低%',
        'fwd_score': '上抛相似度'})
        if len(base_all) else base_all)

    meta = pd.DataFrame([
        ('模板股票', f'{args.code} {t_name}'),
        ('模板区间', f'{args.start} ~ {args.end}'),
        ('模板长度 L', tpl['L']),
        ('--split 分界方式', str(args.split)),
        ('分界点 S（匹配段末日 + 1）', SP),
        ('匹配段起始索引 --pre-start', pre0),
        ('匹配段（形状模板）', f'[{pre0}, {SP - 1}]  共 {tpl["L1"]} 日'),
        ('上抛参照段', f'{ref_desc}  共 {L2} 日  '
                     f'{pd.Timestamp(ref_dt[0]).date()} ~ '
                     f'{pd.Timestamp(ref_dt[-1]).date()}'),
        ('观察窗长度 L2', L2),
        ('形状相似度阈值', args.threshold),
        ('去重比例 --overlap', args.overlap),
        ('涨幅档位 --pcts %', ','.join(f'{b:.0f}' for b in bins)),
        ('匹配池', ','.join(pools)),
        ('命中样本数', len(hit)),
        ('基准样本数', len(base_all)),
        ('起点价定义', '形态完成日（匹配段末日）收盘价，观察窗 = 之后 L2 个交易日'),
        ('上涨率口径', '观察窗终点收益 > 0 的比例 '
                     '（即 P(观察窗上涨 | 该 DIF/DEA 形状出现)）'),
        ('上抛再现率口径', f'观察窗 DIF/DEA 形状与「上抛参照段」score >= '
                        f'{args.threshold} 的比例 ← 本表最核心的比率'),
        ('匹配段内金叉率口径', '匹配段内（命中形状自身）已出现 DIF 上穿 DEA 的比例，'
                          '用于确认命中的形状是否确实「刚走过金叉」'),
        ('观察窗金叉率口径', '观察窗内再出现 DIF 上穿 DEA（策略买点）的比例'),
        ('金叉后口径', f'命中后，观察窗内**首个金叉日** g 起算 G={G} 个交易日：'
                     '起点价 = close[g]，窗口 = [g+1, g+G]'),
        ('金叉后·基准', '全市场**任意金叉日**起算 G 个交易日的收益与上抛相似度，'
                      '即「不加任何形状前提、只看金叉」的对照'),
        ('金叉后联合概率', 'P(曲线出现 ∧ 后续金叉 ∧ 金叉后上抛) = '
                        '金叉发生率 × 金叉后上抛率'),
        ('基准定义', '全市场**任意时点**起、持有 L2 个交易日的收益分布；'
                     '「上抛再现率」基准 = 任意时点起 L2 天窗口与上抛参照段的'
                     '形状相似度 ≥ 阈值的比例'),
        ('基准用途', '命中组 − 基准 = 超额；无基准的「上涨率 / 上抛再现率」'
                     '都没有意义'),
        ('已知局限1', '命中样本按同股窗口重叠做非极大值抑制去重，基准未去重，'
                     '故基准样本更「密」（相邻起点高度相关），'
                     '比较时应看**概率值**而非样本量'),
        ('已知局限2', '基准是等权「全时点」池化：长期上涨的股票贡献更多样本，'
                     '因此基准本身偏乐观，超额是**保守**估计'),
    ], columns=['项', '值'])

    sheets = {'概率总表': prob_tbl, '分池统计': pool_tbl,
              '分年度统计': yr_tbl, '分档统计': bk_tbl,
              '命中明细': detail, '基准样本': base_samp, '口径说明': meta}
    TS._write_excel(out, sheets)
    print(f'\n[OK] 已保存: {out}')
    print(f'     工作表: {list(sheets)}')

    # ── 6. 控制台结论 ───────────────────────────────────────────────────
    if not len(hit):
        print('\n（无命中：可降低 --threshold，或改用 --split mid 缩短前半模板）')
        return 0
    cols = (['分组', '样本数', '上涨率%'] +
            [f'涨>{b:.0f}%率%' for b in bins] +
            [f'期间涨>{b:.0f}%率%' for b in bins] +
            ['平均收益%', '中位收益%', '期间最高均值%', '期间最低均值%',
             '上抛再现率%', '上抛平均相似度', '后续金叉率%',
             '匹配段内金叉率%'])
    show = [c for c in cols if c in prob_tbl.columns]
    print('\n' + '=' * 78)
    print('  条件概率：P(上抛出现 | 该 DIF/DEA 形状出现)  vs  全市场基准')
    print('=' * 78)
    print(prob_tbl[show].to_string(index=False))

    # ── 6.1 「曲线出现 → 后面金叉 → 金叉后上抛」的联合概率 ───────────────
    if g_hit is not None and len(g_hit):
        n_hit = int(len(hit))
        rate_g = len(g_hit) / n_hit * 100
        rows = [r for r in total_rows if str(r.get('分组', '')).startswith(
            f'金叉后{G}日')]
        gh = next((r for r in rows if r['分组'].endswith('·命中')), None)
        gb = next((r for r in rows if r['分组'].endswith('·基准')), None)
        if gh is not None:
            print('\n' + '-' * 78)
            print(f'  【金叉后口径】命中 {n_hit:,} 个 → 之后 {L2} 日内出现金叉 '
                  f'{len(g_hit):,} 个（{rate_g:.2f}%）')
            print(f'  【金叉后·基准】全市场任意金叉点 {len(gbase_all):,} 个')
            pr = gh.get('上抛再现率%')
            pb = None if gb is None else gb.get('上抛再现率%')
            print(f'  P(金叉后 {G} 日内走出上抛曲线 | 曲线出现且后续金叉) = '
                  f'{pr}%' + ('' if pb is None else f'   基准(任意金叉后) = {pb}%'))
            if pr is not None and pb is not None:
                print(f'      超额 = {round(pr - pb, 2)}pp')
            if pr is not None:
                print(f'  联合概率 P(曲线出现 ∧ 后续金叉 ∧ 金叉后上抛) = '
                      f'{rate_g:.2f}% × {pr}% = '
                      f'{round(rate_g * pr / 100, 2)}%')
            print('-' * 78)
    if len(pool_tbl):
        print('\n--- 分池 ---')
        print(pool_tbl[[c for c in show if c in pool_tbl.columns]]
              .to_string(index=False))
    if len(yr_tbl):
        print('\n--- 分年度 ---')
        print(yr_tbl[[c for c in show if c in yr_tbl.columns]]
              .to_string(index=False))
    if len(bk_tbl):
        print('\n--- 分档（相似度 / 前半跌幅）---')
        print(bk_tbl[[c for c in show if c in bk_tbl.columns]]
              .to_string(index=False))
    if args.show > 0:
        print(f'\n--- 命中明细（前 {args.show} 条，按前半相似度降序）---')
        print(detail.head(args.show).to_string(index=False))
    return 0


if __name__ == '__main__':
    sys.exit(main())




