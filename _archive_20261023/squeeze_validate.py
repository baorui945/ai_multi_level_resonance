# -*- coding: utf-8 -*-
"""
==============================================================================
squeeze_validate.py —— 「均线粘合 + 放量突破」的统计验证
==============================================================================

检验内容：

1. 八个变体的主持有期净 alpha 与按日聚合 t（阈值敏感性）；
2. literal 口径在 5/10/20/40 日上的表现；
3. 粘合度五分组：**越粘越好**这条假设是否成立；
4. score 五分组、样本内外、逐年；
5. 基准：同日「纯放量大涨但完全不看均线粘合」的信号；
6. 形态硬条件（选股器默认口径）vs 原式：样本内外 + 逐年，判断它是
   alpha 增强还是仅图形过滤（结论见 doc/squeeze_策略记录.md §4.5）。

口径全部复用 `coil.evaluate`（t+1 开盘、一字板剔除、沪深300 同期基准，
双边 0.2% 成本），只读工具函数，不读取 coil 的任何策略参数。

用法::

    python squeeze_validate.py --universe all
    python squeeze_validate.py --universe all --start 2017-01-01 --end 2026-10-19
"""
import argparse
import os

import numpy as np
import pandas as pd

import squeeze_scan as SCN
from coil import evaluate as EV
from squeeze import settings as SS

VARIANTS = {
    # 前 9 个变体保持“原式口径”（use_form=False），只动阈值开关，
    # 与 v0.1.0 的表格可比；最后两档才是选股器默认的形态硬条件。
    'literal 5%': {'max_stick': 0.05, 'use_form': False},
    'tight 3%': {'max_stick': 0.03, 'use_form': False},
    'tight 2%': {'max_stick': 0.02, 'use_form': False},
    'loose 8%': {'max_stick': 0.08, 'use_form': False},
    '+多头排列': {'max_stick': 0.05, 'require_stack': True, 'use_form': False},
    '+零轴': {'max_stick': 0.05, 'use_macd': True, 'use_form': False},
    '+MA120走平': {'max_stick': 0.05, 'use_ma120_flat': True,
                   'use_form': False},
    '严格组合': {'max_stick': 0.05, 'require_stack': True, 'use_macd': True,
                 'use_ma120_flat': True, 'max_range': 0.30,
                 'use_form': False},
    '无粘合(基准)': {'max_stick': None, 'use_form': False},
    '形态四条件(v0.1.3)': {'max_stick': 0.05, 'use_form': True,
                         'use_shape': False},
    '形态+蓄势(v0.1.4)': {'max_stick': 0.05, 'use_form': True},
}


def _parse_args():
    p = argparse.ArgumentParser(description='SQUEEZE 粘合突破统计验证')
    p.add_argument('--universe', default='all', choices=['all', 'hs300', 'coal'])
    p.add_argument('--start', default='', help='默认读配置 backtest.date_start')
    p.add_argument('--end', default='', help='默认缓存最新日')
    p.add_argument('--split', default='', help='样本内外切分日')
    p.add_argument('--out', default='')
    p.add_argument('--no-style', action='store_true', help='不套用表头样式')
    return p.parse_args()


def _load(a):
    ns = argparse.Namespace(universe=a.universe, codes='')
    return SCN._load_px(ns)


def _prep(sig):
    """统一成 event_study 需要的列名。"""
    d = sig.rename(columns={'代码': 'code', '信号日': 'date', '收盘': 'close'})
    cols = ['code', 'date', 'close', 'score', '变体'] + \
        [c for c in d.columns if c.startswith('_')]
    return d[cols].copy()


def _t(x):
    x = np.asarray(x, dtype='float64')
    x = x[np.isfinite(x)]
    if x.size < 3:
        return np.nan
    sd = x.std(ddof=1)
    return float(x.mean() / (sd / np.sqrt(x.size))) if sd > 0 else np.nan


def _cluster_t(det, h):
    """按交易日聚合的 t：同日几十条信号高度相关，逐笔 t 会严重高估。"""
    d = det
    if 'untradable' in d:
        d = d[~d['untradable'].to_numpy(bool)]
    key = f'a{h}_oo' if f'a{h}_oo' in d else f'a{h}'
    g = d.groupby(pd.to_datetime(d['date']))[key].mean()
    g = g[np.isfinite(g)]
    return (round(_t(g.to_numpy()), 2) if len(g) else np.nan), int(len(g))


def _main_row(det, h, name):
    s = EV.summarize(det, [h], label=name)
    if not len(s):
        return None
    r = s.iloc[0].to_dict()
    r['按日聚合t'], r['交易日数'] = _cluster_t(det, h)
    return r

def _variant_table(df, mat, index, H, main_h, cost):
    rows, dets = [], {}
    for v, gg in df.groupby('变体', sort=False):
        det = EV.event_study(mat, index, _prep(gg), H, cost)
        dets[v] = det
        r = _main_row(det, main_h, v)
        if not r:
            continue
        rows.append({'变体': v, '信号数': int(len(det)),
                     '交易日数': r['交易日数'], 'n': r['n'],
                     '均值alpha': r['均值alpha'], '逐笔t': r['t值'],
                     '按日聚合t': r['按日聚合t'],
                     '胜率%': r['胜率%'], '中位alpha': r['中位alpha']})
    return pd.DataFrame(rows), dets


def _horizon_table(det, H):
    s = EV.summarize(det, H, label='literal 5%')
    s['按日聚合t'] = [_cluster_t(det, h)[0] for h in H]
    s['交易日数'] = [_cluster_t(det, h)[1] for h in H]
    return s


def _quint(det, key, h, label):
    if key not in det or len(det) < 250:
        return pd.DataFrame()
    q = EV.quintile_table(det, [h], key=key, q=5)
    if not len(q):
        return pd.DataFrame()
    q = q[q['持有期'] == h].copy()
    q['分组变量'] = label
    return q[['分组变量', '分组', 'n', '均值alpha', 't值', '胜率%', '中位alpha']]


def _year_table(det, h):
    d = det.copy()
    d = d[~d['untradable'].to_numpy(bool)] if 'untradable' in d else d
    d['年'] = pd.to_datetime(d['date']).dt.year
    key = f'a{h}_oo' if f'a{h}_oo' in d else f'a{h}'
    rows = []
    for y, g in d.groupby('年'):
        x = g[key].to_numpy('float64')
        x = x[np.isfinite(x)]
        if not x.size:
            continue
        rows.append({'年': int(y), 'n': int(x.size),
                     '均值alpha': round(float(x.mean()) * 100, 2),
                     '中位alpha': round(float(np.median(x)) * 100, 2),
                     '胜率%': round(float((x > 0).mean() * 100), 1)})
    return pd.DataFrame(rows)


def _io_table(det, split, h):
    d = det.copy()
    key = f'a{h}_oo' if f'a{h}_oo' in d else f'a{h}'
    if 'untradable' in d:
        d = d[~d['untradable'].to_numpy(bool)]
    dt = pd.to_datetime(d['date'])
    rows = []
    for name, m in (('样本内', dt < split), ('样本外', dt >= split)):
        x = d.loc[m.to_numpy(), key].to_numpy('float64')
        x = x[np.isfinite(x)]
        if not x.size:
            continue
        rows.append({'区间': name, 'n': int(x.size),
                     '均值alpha': round(float(x.mean()) * 100, 2),
                     '中位alpha': round(float(np.median(x)) * 100, 2),
                     '胜率%': round(float((x > 0).mean() * 100), 1)})
    return pd.DataFrame(rows)


def _io_quint(det, split, h):
    """头/尾 20% 在样本内外的 alpha 差（粘合度越小越好 → 头部=最低 20%）。"""
    d = det.copy()
    if 'untradable' in d:
        d = d[~d['untradable'].to_numpy(bool)]
    d = d[np.isfinite(d['_stick'].to_numpy('float64'))]
    if len(d) < 100:
        return pd.DataFrame()
    d['_g'] = pd.qcut(d['_stick'].rank(method='first'), 5, labels=False)
    key = f'a{h}_oo' if f'a{h}_oo' in d else f'a{h}'
    rows = []
    for name, m in (('样本内', pd.to_datetime(d['date']) < split),
                    ('样本外', pd.to_datetime(d['date']) >= split)):
        g = d[m.to_numpy()]
        top = g.loc[g['_g'] == 0, key].to_numpy('float64')
        bot = g.loc[g['_g'] == 4, key].to_numpy('float64')
        top, bot = top[np.isfinite(top)], bot[np.isfinite(bot)]
        if top.size < 20 or bot.size < 20:
            continue
        rows.append({'区间': name, 'n头部': int(top.size),
                     '头部(最粘)均值%': round(float(top.mean()) * 100, 2),
                     '尾部(最松)均值%': round(float(bot.mean()) * 100, 2),
                     '头-尾(pp)': round(float(top.mean() - bot.mean()) * 100, 2)})
    return pd.DataFrame(rows)


def _notes(cfg, H, main_h):
    r = cfg['rule']
    return pd.DataFrame({'项目': [
        'alpha 口径', '成本', '入场价', '一字板', 't 值口径', '粘合度',
        '收益基准', '变体口径', '形态变体', '注意'],
        '说明': [
        '个股区间收益 − 沪深300 同期收益 − 成本；默认 T+1 开盘 → h 日后开盘',
        f'{cfg["exit"]["cost"] * 100:.1f}%（双边合计）',
        '信号日 T 收盘后确认，T+1 开盘可执行',
        'T+1 开盘相对 T 收盘涨幅 ≥ 9.5%/19.5% 视为买不进，统计时剔除',
        '「逐笔t」因同日多条信号相关而高估；判定用「按日聚合t」',
        f'(max−min)/min of MA7/21/120，越小越粘；默认门槛 '
        f'{r["max_stick"] * 100:.1f}%',
        '沪深300 同期收益（index pkl），不是个股等权',
        '前 9 档 use_form=False，只动阈值开关，与 v0.1.0 表格可比',
        '后两档开启 form_mask：v0.1.3 四条件（粘合≥10 日、60 日箱体≤30%、'
        '收盘≥箱体上沿−2%、MA120 五日≥−0.5%）；v0.1.4 再加 shape_mask'
        '（20/40 日振幅≤12/15%、120 日无 5 日急拉>12%/急杀<−10%、'
        '离 120 日低≤20%、MA120≤MA21+1%）',
        '持有期重叠使按日聚合 t 仍偏乐观；结论方向看符号与量级'],
    })


def _verdict(tab, lit, hz, iq, main_h):
    """把几条硬指标摊平成「检验项 / 结果 / 判定」。"""
    def _row(name, val, ok):
        return {'检验项': name, '结果': val, '判定': '达标' if ok else '未达标'}

    r = tab[tab['变体'] == 'literal 5%']
    t_lit = float(r['按日聚合t'].iloc[0]) if len(r) else np.nan
    m_lit = float(r['均值alpha'].iloc[0]) if len(r) else np.nan
    base = tab[tab['变体'] == '无粘合(基准)']
    m_base = float(base['均值alpha'].iloc[0]) if len(base) else np.nan
    n_pos = int((tab['均值alpha'] > 0).sum())
    spread = np.nan
    if len(iq):
        spread = float(iq['头-尾(pp)'].mean())
    i5 = iq[iq['区间'] == '样本外'] if len(iq) else pd.DataFrame()
    oos = float(i5['头部(最粘)均值%'].iloc[0]) if len(i5) else np.nan
    return pd.DataFrame([
        _row(f'literal 主持有期({main_h}d) 按日聚合 t ≥ 2',
             f't={t_lit:.2f}', np.isfinite(t_lit) and t_lit >= 2),
        _row('literal 净 alpha > 0', f'{m_lit:+.2f}%',
             np.isfinite(m_lit) and m_lit > 0),
        _row('比「不看粘合的纯放量大涨」更好',
             f'{m_lit:+.2f}% vs {m_base:+.2f}%',
             np.isfinite(m_lit) and np.isfinite(m_base) and m_lit > m_base),
        _row('越粘越好（Q1最粘 − Q5最松 > 0）',
             f'{spread:+.2f}pp' if np.isfinite(spread) else 'NA',
             np.isfinite(spread) and spread > 0),
        _row('变体同号（≥60% 为正）', f'{n_pos}/{len(tab)}',
             len(tab) and n_pos / len(tab) >= 0.6),
        _row('样本外头部不翻负', f'{oos:+.2f}%' if np.isfinite(oos) else 'NA',
             np.isfinite(oos) and oos > 0),
    ])


def main():
    a = _parse_args()
    cfg = SS.load_config()
    SS.ensure_dirs()
    px = _load(a)
    end = pd.Timestamp(a.end) if a.end else pd.Timestamp(px['trade_date'].max())
    start = pd.Timestamp(a.start) if a.start else \
        pd.Timestamp(cfg['backtest']['date_start'])
    split = pd.Timestamp(a.split) if a.split else \
        pd.Timestamp(cfg['backtest']['split'])
    H = [int(x) for x in cfg['exit']['horizons']]
    main_h = int(cfg['exit']['main_horizon'])
    cost = float(cfg['exit']['cost'])
    print(f'[验证] {start.date()} ~ {end.date()} | 变体 {len(VARIANTS)} 个 | '
          f'持有期 {H} | 主期 {main_h}d | 成本 {cost * 100:.1f}%')

    df = SCN.scan_multi(px, cfg, start, end, VARIANTS)
    print(f'[信号] {len(df)} 条（全部变体合计）')
    if not len(df):
        raise SystemExit('无信号，检查阈值')
    mat = EV.build_matrices(px)
    index = EV.load_index()
    tab, dets = _variant_table(df, mat, index, H, main_h, cost)
    lit = dets.get('literal 5%', pd.DataFrame())
    hz = _horizon_table(lit, H)
    q_stick = _quint(lit, '_stick', main_h, '粘合度(升序=越粘)')
    q_score = _quint(lit, 'score', main_h, 'score')
    io = _io_table(lit, split, main_h)
    iq = _io_quint(lit, split, main_h)
    yr = _year_table(lit, main_h)
    vd = _verdict(tab, lit, hz, iq, main_h)

    # 逐变体的样本内外 / 逐年（形态变体是否只是牛年 beta，靠这两张表判断）
    io_v, yr_v = [], []
    for v, dd in dets.items():
        t = _io_table(dd, split, main_h)
        if len(t):
            t.insert(0, '变体', v)
            io_v.append(t)
        t2 = _year_table(dd, main_h)
        if len(t2):
            t2.insert(0, '变体', v)
            yr_v.append(t2)
    io_v = pd.concat(io_v, ignore_index=True) if io_v else pd.DataFrame()
    yr_v = pd.concat(yr_v, ignore_index=True) if yr_v else pd.DataFrame()

    print('\n=== 变体对照（主持有期）===')
    print(tab.to_string(index=False))
    print('\n=== literal 5% 持有期 ===')
    print(hz.to_string(index=False))
    if len(q_stick):
        print('\n=== 粘合度五分组（Q1=最粘）===')
        print(q_stick.to_string(index=False))
    if len(iq):
        print('\n=== 头/尾 20%（样本内外）===')
        print(iq.to_string(index=False))
    if len(io_v):
        pick_v = [v for v in ('literal 5%', '形态四条件(v0.1.3)',
                              '形态+蓄势(v0.1.4)')
                  if v in set(io_v['变体'])]
        print('\n=== 形态 vs 原式：样本内外 ===')
        print(io_v[io_v['变体'].isin(pick_v)].to_string(index=False))
        pv = yr_v[yr_v['变体'].isin(pick_v)].pivot(
            index='年', columns='变体', values='均值alpha')
        if len(pv):
            print('\n=== 形态 vs 原式：逐年均值alpha% ===')
            print(pv.to_string())
    print('\n=== 结论摘要 ===')
    print(vd.to_string(index=False))

    out = a.out or os.path.join(
        SS.OUTPUT_DIR, f'squeeze_validate_{end.strftime("%Y%m%d")}.xlsx')
    keep = ['code', 'date', 'close', 'score', '变体', '_stick', '_pct', '_vol',
            '_dif_n', '_rng', '_ma120_chg'] + \
        [c for c in lit.columns if c.startswith(('r', 'a')) and c[-1:].isdigit()]
    with pd.ExcelWriter(out, engine='openpyxl') as xw:
        vd.to_excel(xw, sheet_name='结论摘要', index=False)
        tab.to_excel(xw, sheet_name='变体对照', index=False)
        hz.to_excel(xw, sheet_name='持有期', index=False)
        (q_stick if len(q_stick) else pd.DataFrame(columns=['分组'])).to_excel(
            xw, sheet_name='粘合分位', index=False)
        (q_score if len(q_score) else pd.DataFrame(columns=['分组'])).to_excel(
            xw, sheet_name='评分数位', index=False)
        io.to_excel(xw, sheet_name='样本内外', index=False)
        iq.to_excel(xw, sheet_name='头尾20pct', index=False)
        yr.to_excel(xw, sheet_name='逐年', index=False)
        (io_v if len(io_v) else pd.DataFrame(columns=['变体'])).to_excel(
            xw, sheet_name='变体样本内外', index=False)
        (yr_v if len(yr_v) else pd.DataFrame(columns=['变体'])).to_excel(
            xw, sheet_name='变体逐年', index=False)
        _notes(cfg, H, main_h).to_excel(xw, sheet_name='口径说明', index=False)
        if len(lit):
            lit[[c for c in keep if c in lit.columns]].to_excel(
                xw, sheet_name='信号明细', index=False)
    if not a.no_style:
        try:
            from mlr.report import _style_openpyxl
            _style_openpyxl(out)
        except Exception:                                  # noqa: BLE001
            pass
    print(f'\n[输出] {out}')


if __name__ == '__main__':
    main()


