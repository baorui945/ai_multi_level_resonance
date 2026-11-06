# -*- coding: utf-8 -*-
"""
==============================================================================
pattern_case.py —— 「当前时点横截面」事件研究：同款形态个股数 → 后续上抛个股数
==============================================================================

pattern_prob.py 做的是**历史滑动扫描**（把形状模板拿到全历史里滚动匹配，回答
「历史上出现这种形状后，之后 N 天的上抛率是多少」）。

本脚本做的是**单一时点的横截面统计**，回答一个更直接的问题：

    现在（模板截止日）全市场有多少只股票的 DIF/DEA 形态
    和 <模板股票> 的「前段」几乎一样？
    这些股票在「后段」这段**真实同期**里，有多少真的走成了
    和 <模板股票> 几乎一样的「后段上抛」？
    比率 = 后者 / 前者。

两个窗口都是**固定日历区间**（不是每只股票自己的相对窗口）：

    前段（条件） pre  = --pre  2025-11-11:2026-02-05
    后段（结果） post = --post 2026-02-06:2026-03-31

每只股票在每个窗口上算一次形状相似度（与 pattern_prob / template_scan 口径一致）：
    score = (corr(DIF, 模板DIF) + corr(DEA, 模板DEA)) / 2   （先各自 z-score）

判「上抛再现」用同一套阈值 --threshold（默认 0.97），并同时给出**价格口径**
（后段区间涨幅 / 期间最高涨幅）以便交叉验证——形状像 ≠ 赚钱。

用法::

    python pattern_case.py                                   # 601898 默认两段
    python pattern_case.py --universe all                    # 全市场（推荐）
    python pattern_case.py --threshold 0.95                  # 放宽形态匹配
    python pattern_case.py --post 2026-02-06:2026-03-31      # 换后段区间
    python pattern_case.py --min-cover 0.9                   # 允许区间内少量停牌
    python pattern_case.py --exclude-self                    # 排除模板股票自身

输出 output/pattern_case_<code>.xlsx，多工作表：
    「汇总」        计数与比率（核心答案）
    「对照统计」    命中组 / 全市场 / 超额（形状口径 + 价格口径）
    「核对清单」    前段相似度 ≥ --detail-min 的个股逐只明细（含「次优」），
                    可据此人工检查「到底有多少只满足要求」
    「阈值分档」    前段阈值取 0.995~0.80 各值时 N1 / N2 / 比率的敏感性
    「曲线明细」    核对清单个股前段逐日 DIF/DEA/收盘 + z_DIF/z_DEA（可画图比对）
    「模板曲线」    模板股票自身前段/后段逐日 DIF/DEA/收盘 + 金叉标记
    「形态命中明细」前段 score ≥ 阈值的个股逐只明细（含后段相似度、后段涨幅）
    「命中且上抛明细」上表 ∩ 后段 score ≥ 阈值（即 N2，逐只）
    「全部个股」    两段数据齐全的全部个股（便于复核基准）
    「口径说明」    区间、长度、阈值、剔除规则等复现信息

数据源：backward_tdx_daily（后复权），见 mlr/settings.py TABLE_DAILY。
归一化 / 股票池 / Excel 写出函数直接复用 template_scan.py，保证口径一致。
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

PCT_BINS = (5.0, 10.0, 15.0, 20.0)       # 价格口径档位，--pcts 可覆盖


def _range(s):
    """解析 'YYYY-MM-DD:YYYY-MM-DD' → (起, 止) 两个 pd.Timestamp。"""
    if ':' not in s:
        raise argparse.ArgumentTypeError(f'区间格式应为 A:B，收到 {s!r}')
    a, b = (x.strip() for x in s.split(':', 1))
    a, b = pd.Timestamp(a), pd.Timestamp(b)
    if a > b:
        raise argparse.ArgumentTypeError(f'区间起止颠倒: {s!r}')
    return a, b


def _corr(u, w):
    """Pearson 相关系数；任一序列方差为 0（形状退化成常数）时返回 None。"""
    u = TS.zscore(u)
    w = TS.zscore(w)
    du = float(np.sqrt(np.mean(u * u)))
    dw = float(np.sqrt(np.mean(w * w)))
    if du < 1e-12 or dw < 1e-12:
        return None
    return float(np.mean(u * w) / (du * dw))


def _eval_stock(g, cal_pre, cal_post, tpl, mp, min_cover, detail_min=2.0):
    """对单只股票在**两个固定日历窗口**上算形状相似度与后段价格统计。

    cal_pre / cal_post : pd.Timestamp 列表（模板股票在这两段里的交易日）
    detail_min         : 前段相似度 ≥ 该值时，额外保留前段逐日 DIF/DEA/收盘
                         （供「曲线明细」sheet 人工核对形状，默认只在更严时才留）
    返回 (dict, None)；数据不满足要求时返回 (None, 剔除原因字符串)。
    """
    g = g.sort_values('trade_date')
    name = g['stock_name'].iloc[-1]
    close = g['close_price'].to_numpy('float64')
    dts = pd.to_datetime(g['trade_date']).to_numpy()
    dif, dea, _ = calc_macd(close, *mp)
    pos = {pd.Timestamp(d): i for i, d in enumerate(dts)}

    out = {'stock_code': g['stock_code'].iloc[-1], 'stock_name': name}
    n_bar = {}
    for tag, cal, td, te in (('pre', cal_pre, tpl['pre_d'], tpl['pre_e']),
                             ('post', cal_post, tpl['post_d'], tpl['post_e'])):
        idx = np.array([pos.get(d, -1) for d in cal], dtype=int)
        ok = idx >= 0
        if ok.sum() < min_cover * len(cal):      # 覆盖率不足（停牌 / 上市晚）
            return None, f'{tag}段覆盖不足'
        v = np.where(ok)[0]                      # 日历上的有效位置
        a, b = dif[idx[v]], dea[idx[v]]
        if not (np.isfinite(a).all() and np.isfinite(b).all()):
            return None, f'{tag}段MACD预热'      # 次新股，指标还没出来
        r1, r2 = _corr(a, td[v]), _corr(b, te[v])
        if r1 is None or r2 is None:
            return None, f'{tag}段形状退化'
        out[f'score_{tag}'] = round((r1 + r2) / 2.0, 4)
        out[f'corr_dif_{tag}'] = round(r1, 4)
        out[f'corr_dea_{tag}'] = round(r2, 4)
        out[f'cover_{tag}'] = round(float(ok.mean()), 4)
        out[f'_i_{tag}'] = idx[v]                # 该股自身索引（算收益用）
        out[f'_v_{tag}'] = v                     # 日历上的有效位置
        n_bar[tag] = int(v.size)

    # ── 价格口径（后段区间：首根收盘 → 末根收盘；期间最高/最低）──
    for tag in ('pre', 'post'):
        ii = out[f'_i_{tag}']
        p0 = float(close[ii[0]])
        if p0 <= 0:
            return None, f'{tag}段起点价异常'
        out[f'{tag}_ret_pct'] = round(float(close[ii[-1]] / p0 - 1) * 100, 2)
        out[f'{tag}_max_pct'] = round(float(close[ii].max() / p0 - 1) * 100, 2)
        out[f'{tag}_min_pct'] = round(float(close[ii].min() / p0 - 1) * 100, 2)
        out[f'n_{tag}'] = n_bar[tag]

    # ── 诊断：前段末根是否正好是金叉日（DIF 上穿 DEA）──
    ip = out['_i_pre']
    if ip.size >= 2:
        out['pre_gold_last'] = bool(
            dif[ip[-1]] > dea[ip[-1]] and dif[ip[-2]] <= dea[ip[-2]])
        out['pre_gold_any'] = bool(
            ((dif[ip[1:]] > dea[ip[1:]]) & (dif[ip[:-1]] <= dea[ip[:-1]])).any())
    else:
        out['pre_gold_last'] = None
        out['pre_gold_any'] = None
    # ── 可选：保留前段逐日曲线（供人工核对形状）──
    if out['score_pre'] >= detail_min:
        cal = np.asarray(cal_pre)
        ii, v = out['_i_pre'], out['_v_pre']
        out['pre_dates'] = [str(x)[:10] for x in cal[v]]
        out['pre_dif'] = [round(float(x), 6) for x in dif[ii]]
        out['pre_dea'] = [round(float(x), 6) for x in dea[ii]]
        out['pre_close'] = [round(float(x), 4) for x in close[ii]]
    out.pop('_i_pre')
    out.pop('_i_post')
    out.pop('_v_pre')
    out.pop('_v_post')
    return out, None


def _mark(df, label, threshold, bins):
    """一组样本的「形状口径 + 价格口径」统计（命中组 / 全市场通用）。"""
    if df is None or not len(df):
        return None
    row = {'分组': label, '样本数': int(len(df)),
           '后段上抛再现率%': round(
               float((df['score_post'] >= threshold).mean()) * 100, 2),
           '后段平均相似度': round(float(df['score_post'].mean()), 4),
           '后段上涨率%': round(float((df['post_ret_pct'] > 0).mean()) * 100, 2)}
    for b in bins:
        row[f'后段涨>{float(b):.0f}%率%'] = round(
            float((df['post_ret_pct'] > b).mean()) * 100, 2)
        row[f'后段期间涨>{float(b):.0f}%率%'] = round(
            float((df['post_max_pct'] > b).mean()) * 100, 2)
    row['后段平均收益%'] = round(float(df['post_ret_pct'].mean()), 2)
    row['后段中位收益%'] = round(float(df['post_ret_pct'].median()), 2)
    return row


def _excess(hit, base):
    """命中组 − 全市场 的超额（仅两行共有的数值列做差）。"""
    if hit is None or base is None:
        return None
    out = {'分组': '超额(命中-全市场)'}
    for k, v in hit.items():
        if k in ('分组', '样本数'):
            continue
        b = base.get(k)
        if isinstance(v, (int, float)) and isinstance(b, (int, float)):
            out[k] = round(float(v) - float(b), 2)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog='pattern_case',
        description='当前时点横截面统计：同款 DIF/DEA 形态个股数 → '
                    '后段真实同期走出上抛的个股数与比率')
    ap.add_argument('--code', default='601898', help='模板股票代码（默认601898）')
    ap.add_argument('--pre', type=_range, default=_range('2025-11-11:2026-02-05'),
                    help='前段（条件）日历区间 A:B（默认2025-11-11:2026-02-05）')
    ap.add_argument('--post', type=_range, default=_range('2026-02-06:2026-03-31'),
                    help='后段（结果）日历区间 A:B（默认2026-02-06:2026-03-31）')
    ap.add_argument('--threshold', type=float, default=0.97,
                    help='形状相似度阈值（前段命中 & 后段上抛再现共用，默认0.97）')
    ap.add_argument('--universe', default='all',
                    help='股票池 hs300 / coal / all，可逗号分隔（默认all）')
    ap.add_argument('--min-cover', type=float, default=1.0,
                    help='区间内日线覆盖率下限（默认1.0=两段均需全覆盖，停牌股剔除；'
                         '设 0.9 可放宽）')
    ap.add_argument('--pcts', default='5,10,15,20',
                    help='价格口径涨幅档位（%%），逗号分隔（默认 5,10,15,20）')
    ap.add_argument('--exclude-self', action='store_true',
                    help='排除模板股票自身（其前/后段相似度必为 1.0，会高估命中率）')
    ap.add_argument('--limit', type=int, default=None,
                    help='每池只处理前 N 只（冒烟）')
    ap.add_argument('--show', type=int, default=0,
                    help='控制台打印前段命中明细前 N 条（按前段相似度降序）')
    ap.add_argument('--detail-min', type=float, default=0.90,
                    help='「次优候选」与逐日曲线明细的相似度下限（默认0.90），'
                         '便于人工核对阈值松紧')
    ap.add_argument('--out', default=None, help='Excel 输出路径')
    args = ap.parse_args(argv)
    if not 0.0 < args.min_cover <= 1.0:
        raise SystemExit(f'--min-cover {args.min_cover} 非法（须在 (0,1]）')
    bins = tuple(sorted(float(x) for x in args.pcts.split(',') if x.strip()))
    pools = [p.strip() for p in args.universe.split(',') if p.strip()]

    # ── 1. 模板：两段固定日历 + 各自的 DIF/DEA 形状模板 ─────────────────
    t_close, t_date, t_dif, t_dea, t_name, mp = TS._series(args.code)
    pre0, pre1 = args.pre
    post0, post1 = args.post
    ki = [i for i, x in enumerate(t_date)
          if pre0 <= pd.Timestamp(x) <= pre1]
    kj = [i for i, x in enumerate(t_date)
          if post0 <= pd.Timestamp(x) <= post1]
    if not ki or not kj:
        raise SystemExit(f'模板股票 {args.code} 在 '
                         f'{"--pre" if not ki else "--post"} 区间内没有交易日')
    cal_pre = [pd.Timestamp(t_date[i]) for i in ki]
    cal_post = [pd.Timestamp(t_date[i]) for i in kj]
    tpl = {'pre_d': TS.zscore(t_dif[ki]), 'pre_e': TS.zscore(t_dea[ki]),
           'post_d': TS.zscore(t_dif[kj]), 'post_e': TS.zscore(t_dea[kj])}

    print('=' * 78)
    print(f'  模板股票  {args.code} {t_name}   MACD={mp}')
    print(f'  前段 pre  {pre0.date()} ~ {pre1.date()}  共 {len(cal_pre)} 根 '
          f'（{cal_pre[0].date()} ~ {cal_pre[-1].date()}）')
    print(f'  后段 post {post0.date()} ~ {post1.date()}  共 {len(cal_post)} 根 '
          f'（{cal_post[0].date()} ~ {cal_post[-1].date()}）')
    print(f'  形状阈值 score >= {args.threshold}    '
          f'覆盖率下限 {args.min_cover}    价格档位 {bins}')
    print('=' * 78, flush=True)

    # ── 2. 全市场逐只横截面评估 ──────────────────────────────────────────
    parts, skips = [], {}
    for pool in pools:
        codes, label = TS._resolve_pool(pool)
        daily = D.load_daily(codes=codes)
        if args.limit:
            keep = sorted(daily['stock_code'].unique())[:args.limit]
            daily = daily[daily['stock_code'].isin(set(keep))]
            print(f'[限制] 只处理前 {len(keep)} 只')
        print(f'\n--- 匹配池「{label}」{daily["stock_code"].nunique()} 只 / '
              f'{len(daily):,} 行 ---', flush=True)
        rows, n_skip = [], {}
        for code, g in daily.groupby('stock_code', sort=False):
            if args.exclude_self and code == args.code:
                n_skip['模板自身'] = n_skip.get('模板自身', 0) + 1
                continue
            r, why = _eval_stock(g, cal_pre, cal_post, tpl, mp, args.min_cover,
                                 args.detail_min)
            if r is None:
                n_skip[why] = n_skip.get(why, 0) + 1
                continue
            r['pool'] = label
            rows.append(r)
        df = pd.DataFrame(rows)
        for k, v in n_skip.items():
            skips[k] = skips.get(k, 0) + v
        print(f'[有效] {label}: {len(df)} 只'
              + ('   剔除: ' + '，'.join(f'{k} {v} 只'
                                       for k, v in sorted(n_skip.items()))
                 if n_skip else ''), flush=True)
        if len(df):
            parts.append(df)

    all_df = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
    if not len(all_df):
        print('\n（无有效样本：请检查区间、--min-cover 或数据覆盖）')
        return 1

    # ── 3. 核心计数：前段命中 N1 → 其中后段上抛 N2 ───────────────────────
    hit = all_df[all_df['score_pre'] >= args.threshold].copy()
    hit = hit.sort_values('score_pre', ascending=False).reset_index(drop=True)
    n_all, n1 = int(len(all_df)), int(len(hit))
    n2 = int((hit['score_post'] >= args.threshold).sum())
    nb = int((all_df['score_post'] >= args.threshold).sum())
    rate_hit = n2 / n1 * 100 if n1 else None
    rate_all = nb / n_all * 100
    t1 = _mark(hit, '前段命中组', args.threshold, bins)
    t2 = _mark(all_df, '全市场', args.threshold, bins)
    cmp_tbl = pd.DataFrame([r for r in (t1, t2, _excess(t1, t2)) if r])

    # ── 4. 汇总表 ────────────────────────────────────────────────────────
    summary = pd.DataFrame([
        ('模板股票', f'{args.code} {t_name}'),
        ('前段 pre 区间（条件）', f'{pre0.date()} ~ {pre1.date()}  '
                              f'{len(cal_pre)} 根 ({cal_pre[0].date()}~'
                              f'{cal_pre[-1].date()})'),
        ('后段 post 区间（结果）', f'{post0.date()} ~ {post1.date()}  '
                               f'{len(cal_post)} 根 ({cal_post[0].date()}~'
                               f'{cal_post[-1].date()})'),
        ('股票池', ','.join(pools)),
        ('形状相似度阈值', args.threshold),
        ('有效样本（两段数据齐全的个股）', n_all),
        ('剔除合计', sum(skips.values())),
        ('剔除明细', '；'.join(f'{k}={v}' for k, v in sorted(skips.items()))),
        ('① 前段同款形态个股数 N1', n1),
        ('① 占有效样本比例 %', round(n1 / n_all * 100, 2)),
        ('② 其中后段走出上抛个股数 N2', n2),
        ('② 比率 N2 / N1 %  ← 核心答案',
         None if rate_hit is None else round(rate_hit, 2)),
        ('③ 全市场后段上抛个股数（基准）', nb),
        ('③ 基准比率 %', round(rate_all, 2)),
        ('超额（② − ③）pp',
         None if rate_hit is None else round(rate_hit - rate_all, 2)),
        ('前段命中且前段末根=金叉日 的个股数',
         int(hit['pre_gold_last'].fillna(False).sum()) if n1 else 0),
        ('前段命中组 后段平均收益%',
         round(float(hit['post_ret_pct'].mean()), 2) if n1 else None),
        ('全市场 后段平均收益%', round(float(all_df['post_ret_pct'].mean()), 2)),
        ('前段命中组 后段期间最高涨幅均值%',
         round(float(hit['post_max_pct'].mean()), 2) if n1 else None),
        ('全市场 后段期间最高涨幅均值%',
         round(float(all_df['post_max_pct'].mean()), 2)),
    ], columns=['项', '值'])

    # ── 5. 明细 / 口径说明 / 写出 ────────────────────────────────────────
    cols = ['stock_code', 'stock_name', 'pool', 'score_pre', 'corr_dif_pre',
            'corr_dea_pre', 'pre_ret_pct', 'pre_max_pct', 'pre_gold_last',
            'pre_gold_any', 'score_post', 'corr_dif_post', 'corr_dea_post',
            'post_ret_pct', 'post_max_pct', 'post_min_pct']
    cn = {'stock_code': '股票代码', 'stock_name': '股票名称', 'pool': '池',
          'score_pre': '前段相似度', 'corr_dif_pre': 'corr_dif_前',
          'corr_dea_pre': 'corr_dea_前', 'pre_ret_pct': '前段涨幅%',
          'pre_max_pct': '前段最高%', 'pre_gold_last': '前段末根金叉',
          'pre_gold_any': '前段曾金叉', 'score_post': '后段相似度',
          'corr_dif_post': 'corr_dif_后', 'corr_dea_post': 'corr_dea_后',
          'post_ret_pct': '后段涨幅%', 'post_max_pct': '后段最高%',
          'post_min_pct': '后段最低%'}
    det_hit = hit[[c for c in cols if c in hit.columns]].rename(columns=cn)
    det_all = (all_df[[c for c in cols if c in all_df.columns]]
               .rename(columns=cn)
               .sort_values('前段相似度', ascending=False))
    det_n2 = det_hit[det_hit['后段相似度'] >= args.threshold] if n1 else det_hit.iloc[:0]

    # ── 5b. 人工核对用：核对清单 / 阈值分档 / 逐日曲线 / 模板曲线 ────────
    cur = (all_df[all_df['score_pre'] >= args.detail_min]
           .sort_values('score_pre', ascending=False).reset_index(drop=True))
    bcols = ['stock_code', 'stock_name', 'score_pre', 'corr_dif_pre',
             'corr_dea_pre', 'pre_ret_pct', 'pre_max_pct', 'pre_min_pct',
             'pre_gold_last', 'pre_gold_any', 'n_pre', 'cover_pre',
             'score_post', 'corr_dif_post', 'corr_dea_post', 'post_ret_pct',
             'post_max_pct', 'post_min_pct', 'n_post', 'cover_post']
    cn2 = dict(cn)
    cn2.update({'n_pre': '前段根数', 'cover_pre': '前段覆盖率',
                'n_post': '后段根数', 'cover_post': '后段覆盖率',
                'pre_min_pct': '前段最低%'})
    chk = cur[[c for c in bcols if c in cur.columns]].rename(columns=cn2).copy()
    if len(chk):
        chk.insert(2, '是否满足要求',
                   (cur['score_pre'] >= args.threshold).map(
                       {True: '是(命中)', False: '次优(未达阈值)'}))
        chk.insert(3, '前段起点', [d[0] for d in cur['pre_dates']])
        chk.insert(4, '前段终点', [d[-1] for d in cur['pre_dates']])
        chk.insert(5, '前段末根DIF', [round(a[-1], 4) for a in cur['pre_dif']])
        chk.insert(6, '前段末根DEA', [round(a[-1], 4) for a in cur['pre_dea']])
        chk.insert(7, '前段末根DIF-DEA', [round(a[-1] - b[-1], 4)
                                      for a, b in zip(cur['pre_dif'],
                                                      cur['pre_dea'])])
        chk.insert(8, '前段末根收盘', [a[-1] for a in cur['pre_close']])

    # 阈值敏感性：前段阈值取不同值时 N1 / N2 / 比率（后段判定阈值不变）
    thr_rows = []
    for th in (0.995, 0.99, 0.985, 0.98, 0.975, 0.97, 0.965, 0.96, 0.95,
               0.93, 0.90, 0.85, 0.80):
        h = all_df[all_df['score_pre'] >= th]
        n = int(len(h))
        m = int((h['score_post'] >= args.threshold).sum())
        thr_rows.append({
            '前段相似度阈值': th,
            '全市场基准%': round(rate_all, 2),
            'N1 前段同款个股数': n,
            'N1 占全市场%': round(n / n_all * 100, 2),
            'N2 后段上抛个股数': m,
            '比率 N2/N1 %': round(m / n * 100, 2) if n else None,
            '超额 vs 基准 pp': round(m / n * 100 - rate_all, 2) if n else None,
            '后段平均收益%': round(float(h['post_ret_pct'].mean()), 2) if n else None,
        })
    thr_tbl = pd.DataFrame(thr_rows)

    # 逐日曲线明细（含 z-score 归一化列，可直接在 Excel 里画图对比模板）
    rows_curve = []
    for _, r in cur.iterrows():
        zd = TS.zscore(np.asarray(r['pre_dif'], dtype=float))
        ze = TS.zscore(np.asarray(r['pre_dea'], dtype=float))
        for i, d in enumerate(r['pre_dates']):
            rows_curve.append({
                '股票代码': r['stock_code'], '股票名称': r['stock_name'],
                '日期': d, 'DIF': r['pre_dif'][i], 'DEA': r['pre_dea'][i],
                'DIF-DEA': round(float(r['pre_dif'][i]) - float(r['pre_dea'][i]), 6),
                '收盘': r['pre_close'][i], 'z_DIF': round(float(zd[i]), 4),
                'z_DEA': round(float(ze[i]), 4),
                '前段相似度': r['score_pre'],
                '是否模板': '★模板' if r['stock_code'] == args.code else ''})
    curve_tbl = pd.DataFrame(rows_curve)

    # 模板股票自身两段曲线（含 z-score 与金叉标记）
    trows = []
    for tag, il in (('前段(条件)', ki), ('后段(结果)', kj)):
        dd, ee = TS.zscore(t_dif[il]), TS.zscore(t_dea[il])
        for j, i in enumerate(il):
            gold = (j > 0 and t_dif[il[j - 1]] <= t_dea[il[j - 1]]
                    and t_dif[i] > t_dea[i])
            trows.append({
                '段': tag, '日期': str(pd.Timestamp(t_date[i]))[:10],
                'DIF': round(float(t_dif[i]), 6), 'DEA': round(float(t_dea[i]), 6),
                'DIF-DEA': round(float(t_dif[i] - t_dea[i]), 6),
                '收盘': round(float(t_close[i]), 4),
                'z_DIF': round(float(dd[j]), 4), 'z_DEA': round(float(ee[j]), 4),
                '是否金叉日': '★金叉' if gold else ''})
    tpl_tbl = pd.DataFrame(trows)

    meta = pd.DataFrame([
        ('脚本', 'pattern_case.py —— 当前时点横截面事件研究'),
        ('与 pattern_prob.py 的区别',
         'pattern_prob 是历史滑动扫描（同一形态在历史上出现 N 次，统计其后 L2 天）；'
         '本脚本是单一时点横截面（同一日历区间内，全市场逐只算一次）'),
        ('前段（条件）', f'{pre0.date()} ~ {pre1.date()}，{len(cal_pre)} 根，'
                     '以模板股票自己的交易日为日历'),
        ('后段（结果）', f'{post0.date()} ~ {post1.date()}，{len(cal_post)} 根，'
                     '同一日历'),
        ('形状相似度', 'corr(DIF, 模板DIF) 与 corr(DEA, 模板DEA) 的平均；'
                   '两者先各自 z-score（只留形状，消除价格与指标尺度）'),
        ('「上抛再现」判定', f'后段相似度 >= {args.threshold}'),
        ('覆盖率规则', f'股票需在日历区间内有 >= {args.min_cover:.0%} 的交易日，'
                   '否则整只剔除（停牌 / 上市晚），剔除数量见「汇总」'),
        ('价格口径', '前/后段涨幅 = 区间首根收盘 → 末根收盘（后复权）；'
                  '最高/最低 = 区间内极值，均以首根收盘为基准'),
        ('基准', '全市场所有有效个股在**同一后段区间**走出该上抛形状的比例'
               '（不要求前段相似）'),
        ('重要提醒', '形状相似 ≠ 赚钱；本脚本同时给出价格口径，'
                  '请用「对照统计」表交叉验证'),
        ('核对清单', f'前段相似度 >= {args.detail_min} 的全部个股（含未达 '
                  f'{args.threshold} 的「次优」），按相似度降序；'
                  '推荐先看这里人工判断阈值是否合适'),
        ('阈值分档', '前段阈值取 0.995~0.80 各值时 N1 / N2 / 比率的变化；'
                  '后段判定阈值固定为本轮 --threshold，'
                  '全市场基准% 因此各档相同'),
        ('曲线明细', '核对清单中每只股票前段 61 根的 DIF / DEA / 收盘 逐日数值，'
                  '并附 z_DIF / z_DEA（与模板同尺度）；'
                  '在 Excel 里对 z_DIF / z_DEA 画折线即可肉眼比对形状'),
        ('模板曲线', f'{args.code} 自身前段(条件)/后段(结果)的逐日 DIF/DEA/收盘 '
                  '与金叉标记'),
    ], columns=['项', '值'])

    out = args.out or os.path.join('output', f'pattern_case_{args.code}.xlsx')
    sheets = {'汇总': summary, '对照统计': cmp_tbl, '核对清单': chk,
              '阈值分档': thr_tbl, '曲线明细': curve_tbl, '模板曲线': tpl_tbl,
              '形态命中明细': det_hit, '命中且上抛明细': det_n2,
              '全部个股': det_all, '口径说明': meta}
    TS._write_excel(out, sheets)
    print(f'\n[OK] 已保存: {os.path.abspath(out)}')
    print(f'     工作表: {list(sheets)}')

    # ── 6. 控制台结论 ────────────────────────────────────────────────────
    print('\n' + '=' * 78)
    print('  横截面结论：同款形态 → 后段上抛')
    print('=' * 78)
    print(f'  有效样本（{",".join(pools)}，两段数据齐全）: {n_all} 只')
    print(f'  ① 前段（{pre0.date()}~{pre1.date()}）同款形态个股数 '
          f'N1 = {n1} 只（占 {round(n1 / n_all * 100, 2)}%）')
    print(f'  ② 其中后段（{post0.date()}~{post1.date()}）走出上抛 '
          f'N2 = {n2} 只'
          + (f'  →  比率 = {round(rate_hit, 2)}%' if rate_hit is not None
             else ''))
    print(f'  ③ 全市场后段上抛 {nb} / {n_all} = {round(rate_all, 2)}%（基准）')
    if rate_hit is not None:
        print(f'     超额 = {round(rate_hit - rate_all, 2)}pp')
    print('\n--- 对照统计（形状口径 + 价格口径）---')
    print(cmp_tbl.to_string(index=False))
    if len(det_n2):
        print(f'\n--- ② N2 明细：前段同款 且 后段走出上抛（{len(det_n2)} 只）---')
        print(det_n2[['股票代码', '股票名称', '前段相似度', '前段涨幅%',
                      '后段相似度', '后段涨幅%', '后段最高%']]
              .to_string(index=False))
    if args.show > 0:
        print(f'\n--- 前段命中明细（前 {args.show} 条，按前段相似度降序）---')
        print(det_hit.head(args.show).to_string(index=False))
    return 0


if __name__ == '__main__':
    sys.exit(main())


