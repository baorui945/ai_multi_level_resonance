# -*- coding: utf-8 -*-
"""临时对比：四种买入口径的组合级回测（同一事件集 / 卖出规则 / K）。

A. cross        : 金叉日 b 收盘买入（旧口径，含前视）
B. confirm_nms  : 形态确认日 e 收盘买入，e-b<=fresh；重叠窗口 NMS 留最高分（当前默认）
C. confirm_first: 同一金叉取「首个新鲜日」e（与 scan_buy_history 一致）
D. split        : C 的首日收盘买 50% + 次日开盘买 50%（一字板买不进则放弃）
卖出：shrink（MACD 柱连续 6 日缩短）从首个买点起算，收盘卖出。
资金：100 万 / K=5 槽位，base=当日权益/K，买 0.026% / 卖 0.076%。
"""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mlr import data as D
from mlr.macd import cross_flags, macd as calc_macd
from template_scan import _dedup, _resolve_pool, _series, sliding_corr, zscore

TH = float(sys.argv[1]) if len(sys.argv) > 1 else 0.90
FRESH, K = 5, 5
CAP, BUY_R, SELL_R = 1_000_000.0, 0.00026, 0.00076
SHRINK, LIMIT_UP = 6, 0.097

_, td, tdif, tdea, tname, mp = _series('601898')
m = (td >= np.datetime64('2025-11-11')) & (td <= np.datetime64('2026-03-16'))
L = int(m.sum())
tpl_d, tpl_e = zscore(tdif[m]), zscore(tdea[m])

codes, _ = _resolve_pool('all')
daily = D.load_daily(codes=codes).sort_values(['stock_code', 'trade_date'])
calendar = np.array(sorted(pd.to_datetime(daily['trade_date'].unique())))
cal_pos = {d: i for i, d in enumerate(calendar)}
T = len(calendar)
print(f'交易日 {T} 天：{calendar[0].date()} ~ {calendar[-1].date()}')

wins, wins_all, raw = [], [], {}
for code, g in daily.groupby('stock_code', sort=False):
    close = g['close_price'].to_numpy('float64')
    openp = g['open_price'].to_numpy('float64')
    dates = pd.to_datetime(g['trade_date']).to_numpy()
    if close.size < L + 2:
        continue
    dif, dea, hist = calc_macd(close, *mp)
    score = (sliding_corr(dif, tpl_d) + sliding_corr(dea, tpl_e)) / 2.0
    xs = np.where(cross_flags(dif, dea) == 1)[0]
    if xs.size == 0:
        continue
    d = np.diff(hist) < 0
    last_bad = np.maximum.accumulate(np.where(d, -1, np.arange(d.size)))
    run = np.zeros(hist.size, dtype=np.int64)
    run[1:] = np.arange(d.size) - last_bad
    for b in xs:
        if b < 120 or close[b] / close[b - 1] - 1 >= LIMIT_UP:
            continue                       # 次新股/一字板追不进，剔除
        wins_all.append({'stock_code': code, 'k': int(b), 'e': int(b),
                         'b': int(b), 'bars': 0, 'score': 1.0,
                         'dates': dates, 'close': close, 'open': openp,
                         'run': run})
    raw[code] = (dates, close, openp)

    idx = np.where(score >= TH)[0]
    idx = idx[idx + L - 1 < close.size]
    if idx.size == 0:
        continue
    e_all = idx + L - 1
    pos = np.searchsorted(xs, e_all, side='right') - 1
    ok = pos >= 0
    b_all = np.where(ok, xs[np.clip(pos, 0, xs.size - 1)], -1)
    ok &= b_all >= idx
    for k, e, b, s in zip(idx[ok], e_all[ok], b_all[ok], score[idx[ok]]):
        wins.append({'stock_code': code, 'k': int(k), 'e': int(e),
                     'b': int(b), 'bars': int(e - b), 'score': float(s),
                     'dates': dates, 'close': close, 'open': openp,
                     'run': run})
    if len(wins) > 0:
        raw[code] = (dates, close, openp)

print(f'原始窗口 {len(wins)} 个；全金叉 {len(wins_all)} 个')
_cache = {}


def aligned(code):
    if code not in _cache:
        dates, close, openp = raw[code]
        gi = np.array([cal_pos[d] for d in dates])
        cg = np.full(T, np.nan)
        og = np.full(T, np.nan)
        cg[gi] = close
        og[gi] = openp
        _cache[code] = (pd.Series(cg).ffill().to_numpy(), og)
    return _cache[code]



def pick(mode):
    if mode == 'all_cross':
        w = wins_all
    elif mode == 'cross':
        w = wins
    else:
        w = [x for x in wins if x['bars'] <= FRESH]
    if not w:
        return []
    df = pd.DataFrame([{'stock_code': x['stock_code'], '_i0': x['k'],
                        '_i1': x['e'], 'score': x['score'], '_b': x['b'],
                        '_i': i} for i, x in enumerate(w)])
    if mode in ('confirm_first', 'cross_same', 'all_cross'):
        df = (df.sort_values(['_b', '_i1'])
                .drop_duplicates(['stock_code', '_b'], keep='first'))
    else:
        df = _dedup(df, 0.5)          # 与 template_scan._scan 相同的 NMS
    if not len(df):
        return []
    out = []
    for i in df['_i']:
        x = w[int(i)]
        entry0 = (x['b'] if mode in ('cross', 'cross_same', 'cross_same_late',
                                     'all_cross') else x['e'])
        exit0 = x['e'] if mode == 'cross_same_late' else entry0
        hit = np.where(x['run'][exit0 + 1:] >= SHRINK)[0]
        s = int(len(x['close']) - 1) if hit.size == 0 else int(exit0 + 1 + hit[0])
        out.append({**x, 'entry': int(entry0), 'exit': s,
                    'holding': hit.size == 0})
    return out


def build(mode):
    ev = []
    for x in pick(mode):
        if x['holding']:
            continue
        dates = x['dates']
        if x['exit'] <= x['entry']:
            continue
        legs = [(int(cal_pos[dates[x['entry']]]), 'close', 1.0)]
        if mode == 'split':
            j = x['entry'] + 1
            if j >= len(dates) - 1:
                continue
            if x['open'][j] / x['close'][j - 1] - 1 >= LIMIT_UP:
                continue
            legs = [(int(cal_pos[dates[x['entry']]]), 'close', 0.5),
                    (int(cal_pos[dates[j]]), 'open', 0.5)]
        ev.append({'code': x['stock_code'], 'entry': legs[0][0],
                   'exit': int(cal_pos[dates[x['exit']]]), 'legs': legs,
                   'px_entry': x['close'][x['entry']],
                   'px_cross': x['close'][x['b']],
                   'bars': int(x['bars']),
                   'hold': int(x['exit'] - x['entry'])})
    return ev


def sim(ev):
    ent, add, ex = {}, {}, {}
    for v in ev:
        ent.setdefault(v['entry'], []).append(v)
        ex.setdefault(v['exit'], []).append(v['code'])
        for p, _f, frac in v['legs'][1:]:
            add.setdefault(p, []).append((v['code'], frac))
    cash, pos, nav, expo, pnl = CAP, {}, [], [], []
    det = []
    for t in range(T):
        for code in ex.get(t, []):
            if code not in pos:
                continue
            sh, inv = pos.pop(code)[:2]
            px = float(aligned(code)[0][t])
            if px <= 0:
                pos[code] = (sh, inv)
                continue
            cash += sh * px * (1 - SELL_R)
            pnl.append(sh * px * (1 - SELL_R) / inv - 1)
            det.append((code, t, sh * px * (1 - SELL_R) / inv - 1, inv))
        equity = cash + sum(
            sh * float(aligned(c)[0][t]) for c, (sh, _i, _b) in pos.items())
        for v in ent.get(t, []):
            if len(pos) >= K or v['code'] in pos:
                continue
            base = min(equity / K, cash)
            if base <= 1:
                continue
            amt = base * v['legs'][0][2]
            px = float(aligned(v['code'])[0][t])
            if not np.isfinite(px) or px <= 0:
                continue
            pos[v['code']] = [amt * (1 - BUY_R) / px, amt, base]
            cash -= amt
        for code, frac in add.get(t, []):
            if code not in pos:
                continue
            amt = min(pos[code][2] * frac, cash)
            px = float(aligned(code)[1][t])
            if amt <= 1 or not np.isfinite(px) or px <= 0:
                continue
            pos[code][0] += amt * (1 - BUY_R) / px
            pos[code][1] += amt
            cash -= amt
        mtm = sum(sh * float(aligned(c)[0][t]) for c, (sh, _i, _b) in pos.items())
        nav.append(cash + mtm)
        expo.append(mtm / (cash + mtm) if cash + mtm > 0 else 0.0)
    nav = np.array(nav)
    total = nav[-1] / CAP - 1
    yrs = (calendar[-1] - calendar[0]).days / 365.25
    ann = (1 + total) ** (1 / yrs) - 1
    dd = float((nav / np.maximum.accumulate(nav) - 1).min())
    pnl = np.array(pnl)
    return {'n': len(pnl), 'total': total, 'ann': ann, 'dd': dd,
            'expo': float(np.mean(expo)), 'win': float((pnl > 0).mean()),
            'med': float(np.median(pnl)) if pnl.size else np.nan,
            'avg_hold': float(np.mean([v['hold'] for v in ev])),
            'det': det}


rows = []
for mode in ['all_cross', 'cross', 'cross_same', 'cross_same_late',
             'confirm_nms', 'confirm_first', 'split']:
    ev = build(mode)
    if not ev:
        print(f'[{mode}] 无信号（0 仓位），跳过')
        continue
    r = sim(ev)
    prem = np.mean([v['px_entry'] / v['px_cross'] - 1 for v in ev]) * 100
    r.update({'口径': mode, '仓位数': len(ev), '溢价%': prem,
              '成交': r['n'],
              '单笔均值': float(np.mean([x[2] for x in r['det']])),
              '金叉距确认中位': float(np.median([v['bars'] for v in ev]))})
    rows.append(r)
    print(f"[{mode}] 事件 {len(ev)} / 成交 {r['n']}  年化 {r['ann'] * 100:+.2f}%  "
          f"总收益 {r['total'] * 100:+.2f}%  回撤 {r['dd'] * 100:.2f}%  "
          f"暴露 {r['expo'] * 100:.1f}%  胜率 {r['win'] * 100:.1f}%  "
          f"单笔中位 {r['med'] * 100:+.2f}%  单笔均值 {r['单笔均值'] * 100:+.2f}%  "
          f"金叉距确认中位 {r['金叉距确认中位']:.0f} 根", flush=True)

print('\n' + pd.DataFrame(rows)[
    ['口径', '仓位数', '成交', 'ann', 'total', 'dd', 'expo', 'win', 'med',
     '单笔均值', 'avg_hold', '金叉距确认中位', '溢价%']].round(4).to_string(index=False))

