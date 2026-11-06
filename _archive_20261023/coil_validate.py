# -*- coding: utf-8 -*-
"""
==============================================================================
coil_validate.py —— COIL 策略的统计验证（事件研究，不依赖旧回测引擎）
==============================================================================

用法::

    # 主验证：全市场 2017 至今，事件研究 + 三组基准 + 分位单调 + 样本外
    python coil_validate.py --universe all --start 2017-01-01

    # 追加参数网格（win × max_cv 档 × vol_mult），耗时约 3~5 分钟
    python coil_validate.py --universe hs300 --grid

    # 可选：与旧 resonance.entry 对照（惰性 import，仅验证期使用）
    python coil_validate.py --compare-resonance

输出：``output/coil/coil_validate_<YYYYMMDD>.xlsx``

判定门槛（不过关就如实记录，不硬调参）：
    1) 主持有期（默认 40 日）逐笔净 alpha 的 t ≥ 2；
    2) coil_score 五分组未来 40 日 alpha 单调；
    3) 参数网格 ≥3 档 alpha 同号；
    4) 样本外（split 之后）alpha 不翻负。
"""
import argparse
import copy
import os
import time

import numpy as np
import pandas as pd

from coil import settings as CS
from coil import detect as DT
from coil import evaluate as EV
from coil import features as F
import coil_scan as SC
from mlr import data as D
from mlr import universe as U


def _args():
    p = argparse.ArgumentParser(description='COIL 策略统计验证')
    p.add_argument('--universe', default='all', choices=['all', 'hs300', 'coal'])
    p.add_argument('--start', default='')
    p.add_argument('--end', default='')
    p.add_argument('--win', type=int, default=0)
    p.add_argument('--horizons', default='5,10,20,40,60')
    p.add_argument('--split', default='', help='样本外切分日（默认读配置）')
    p.add_argument('--grid', action='store_true', help='跑 win×cv×vol 参数网格')
    p.add_argument('--no-baseline', action='store_true', help='跳过基准对照')
    p.add_argument('--compare-resonance', action='store_true',
                   help='追加旧 resonance.entry 对照（仅验证期，惰性 import）')
    p.add_argument('--out', default='')
    return p.parse_args()


def _load(a):
    cfg = CS.load_config()
    if a.win:
        cfg['coil']['win'] = int(a.win)
    if a.universe == 'hs300':
        codes, upd = U.get_hs300_codes()
        print(f'[池] 沪深300 {len(codes)} 只（快照 {upd}，存在幸存者偏差）')
    elif a.universe == 'coal':
        codes, _ = U.get_coal_codes()
    else:
        codes = None
    px = pd.read_parquet(CS.PX_CACHE) if codes is None else \
        D.load_daily(codes=codes, verbose=False)
    px = px[['stock_code', 'stock_name', 'trade_date', 'open_price',
             'high_price', 'low_price', 'close_price', 'volume']].copy()
    px['trade_date'] = pd.to_datetime(px['trade_date'])
    px = px.sort_values(['stock_code', 'trade_date'], ignore_index=True)
    asof = pd.Timestamp(a.end) if a.end else pd.Timestamp(px['trade_date'].max())
    start = pd.Timestamp(a.start) if a.start else \
        pd.Timestamp(cfg['backtest']['date_start'])
    split = pd.Timestamp(a.split) if a.split else \
        pd.Timestamp(cfg['backtest']['split'])
    return cfg, px, start, asof, split


def _scan_signals(px, cfg, start, end):
    df, _ = SC.scan(px, cfg, start, end, all_state=False)
    if not len(df):
        return pd.DataFrame(columns=['code', 'date', 'close', 'score'])
    out = pd.DataFrame({'code': df['代码'].astype(str).str.zfill(6),
                        'date': pd.to_datetime(df['突破日']),
                        'close': df['收盘'].astype(float),
                        'score': df['coil_score'].astype(float),
                        'coil_strength': df['蓄势强度'].astype(float),
                        'coil_run': df['蓄势根数'].astype(int),
                        'vol_mult': df['量比'].astype(float),
                        'cv': df['cv'].astype(float),
                        'box_w': df['box_w'].astype(float),
                        'ma_gap': df['ma_gap'].astype(float),
                        'macd_band': df['macd_band'].astype(float)})
    return out


def grid_search(px, cfg, start, end, mat, index, horizons):
    """参数网格：win × max_cv 缩放档 × vol_mult，逐档做事件研究。"""
    wins = [90, 120, 160]
    cv_scales = [0.85, 1.0, 1.15]
    vol_mults = [1.2, 1.5, 2.0]
    main_h = int(cfg['exit']['main_horizon'])
    cost = float(cfg['exit']['cost'])
    mp = (cfg['macd']['fast'], cfg['macd']['slow'], cfg['macd']['signal'])
    mv = (cfg['ma']['short'], cfg['ma']['mid'], cfg['ma']['long'])
    rows = []
    for win in wins:
        p = copy.deepcopy(cfg)
        p['coil']['win'] = win
        combos = [(s, v) for s in cv_scales for v in vol_mults]
        bags = {k: [] for k in combos}
        t0 = time.time()
        for code, g in px.groupby('stock_code', sort=False):
            c = g['close_price'].to_numpy('float64')
            if c.size < max(int(p['data']['min_bars']), win + 120):
                continue
            dif, dea, hist = F.macd(c, *mp)
            ms, mm, ml = F.moving_averages(c, mv)
            f = F.rolling_coil(c, dif, dea, hist, ms, mm, ml,
                               volume=g['volume'].to_numpy('float64'),
                               win=win, box_q=p['breakout']['box_q'])
            dates = pd.to_datetime(g['trade_date']).to_numpy()
            in_range = (dates >= np.datetime64(pd.Timestamp(start))) \
                & (dates <= np.datetime64(pd.Timestamp(end)))
            base_cv = float(cfg['coil']['max_cv'])
            for s, v in combos:
                p['coil']['max_cv'] = base_cv * s
                p['breakout']['vol_mult'] = v
                st = DT.state_from_features(f, c, dif, dea, hist, ms, mm, ml, p)
                for i in np.flatnonzero(st['entry'] & in_range):
                    bags[(s, v)].append((code, pd.Timestamp(dates[i]),
                                         float(c[i]), float(st['score'][i])))
        for (s, v), bag in bags.items():
            d = pd.DataFrame(bag, columns=['code', 'date', 'close', 'score'])
            det = EV.event_study(mat, index, d, [main_h], cost)
            st = EV.summarize(det, [main_h], oo=True)
            rec = {'win': win, 'cv档': f'×{s:.2f}',
                   'vol_mult': v, 'n': int(st['n'].iloc[0]) if len(st) else 0,
                   '均值alpha%': float(st['均值alpha'].iloc[0]) if len(st) else np.nan,
                   't值': float(st['t值'].iloc[0]) if len(st) else np.nan,
                   '胜率%': float(st['胜率%'].iloc[0]) if len(st) else np.nan}
            rows.append(rec)
        print(f'  [网格] win={win} 完成，用时 {time.time() - t0:.0f}s')
    return pd.DataFrame(rows)


def resonance_signals(px, start, end):
    """旧 resonance 对照（仅验证期；惰性 import，coil 运行期不依赖）。"""
    from mlr import data as D2
    from mlr import resonance as R
    from mlr import settings as S2
    tfs = D2.build_timeframes(px, S2.load_config())
    panel = R.build_resonance(tfs, S2.load_config())
    sig = R.signals(panel, start=start, mode='fresh_daily')
    sig = sig[sig['signal_date'] <= pd.Timestamp(end)]
    return pd.DataFrame({'code': sig['stock_code'].astype(str).str.zfill(6),
                         'date': pd.to_datetime(sig['signal_date']),
                         'close': sig['signal_close'].astype(float)})


# ═════════════════════════════════════════════════════════════════════════
# 主流程
# ═════════════════════════════════════════════════════════════════════════
def _verdict(summary, qtab, grid, oos, main_h):
    """按四项门槛给出自动判定（不过关就写不过关）。"""
    s = summary[summary['持有期'] == main_h]
    t_main = float(s['t值'].iloc[0]) if len(s) and np.isfinite(s['t值'].iloc[0]) else np.nan
    a_main = float(s['均值alpha'].iloc[0]) if len(s) else np.nan
    mono = '未检验'
    if len(qtab):
        q = qtab[qtab['持有期'] == main_h].sort_values('分组')
        v = q['均值alpha'].to_numpy('float64')
        if np.isfinite(v).sum() >= 3:
            mono = '单调' if np.all(np.diff(v) >= 0) or np.all(np.diff(v) <= 0) \
                else '非单调'
    same_sign = '未检验'
    if len(grid):
        g = grid.dropna(subset=['均值alpha%'])
        pos = int((g['均值alpha%'] > 0).sum())
        same_sign = f'正{pos}/{len(g)}'
    o = oos[oos['分组'].str.startswith('样本外')]
    oos_ok = '未检验'
    if len(o):
        v = o[o['持有期'] == main_h]['均值alpha']
        if len(v):
            oos_ok = '未翻负' if float(v.iloc[0]) > 0 else '翻负'
    rows = [
        ('主持有期', f'{main_h} 日', ''),
        ('逐笔净 alpha 均值', f'{a_main:.2f}%', ''),
        ('逐笔 t 值 ≥ 2', f'{t_main:.2f}',
         '达标' if np.isfinite(t_main) and t_main >= 2 else '未达标'),
        ('五分组 alpha 单调', mono, '达标' if mono == '单调' else '未达标'),
        ('参数档同号', same_sign, '达标' if same_sign.startswith('正') and
         int(same_sign.split('/')[0][1:]) >= 3 else '未达标'),
        ('样本外不翻负', oos_ok, '达标' if oos_ok == '未翻负' else '未达标'),
    ]
    return pd.DataFrame(rows, columns=['检验项', '结果', '判定'])


def _notes(cfg, a, n_sig, n_detail):
    return pd.DataFrame({'项目': ['脚本', '样本', '区间', '信号数', '可评估数',
                                 '主口径', '对照口径', '成本', '参数来源',
                                 '门槛', '遗留'],
                         '说明': [
        'coil_validate.py（独立事件研究，不跑 mlr.backtest）',
        a.universe, f'{a.start} ~ {a.end}',
        str(n_sig), str(n_detail),
        't+1 开盘买入，持有 h 日后开盘卖出；一字板剔除',
        't 日收盘买入（与旧记录同口径，仅对照）',
        f'{cfg["exit"]["cost"]:.4f}（双边合计）',
        '默认阈值按沪深300随机120只、2015-2026 横截面 P60 标定；参数网格给出敏感性',
        't≥2 / 五分组单调 / ≥3 档同号 / 样本外不翻负',
        '默认阈值含全样本分位标定（轻度 in-sample）；样本外按时间切分但未重估阈值；'
        '未计冲击成本与停牌',
    ]})

def main():
    a = _args()
    cfg, px, start, end, split = _load(a)
    CS.ensure_dirs()
    if not a.start:
        a.start = str(start.date())
    if not a.end:
        a.end = str(end.date())
    horizons = [int(x) for x in a.horizons.split(',') if x.strip()]
    main_h = int(cfg['exit']['main_horizon'])
    cost = float(cfg['exit']['cost'])
    print(f'[区间] {start.date()} ~ {end.date()} | 样本外切分 {split.date()} '
          f'| win={cfg["coil"]["win"]}')

    sig = _scan_signals(px, cfg, start, end)
    print(f'[信号] {len(sig)} 条')
    mat = EV.build_matrices(px)
    index = EV.load_index()
    detail = EV.event_study(mat, index, sig, horizons, cost)
    print(f'[事件研究] 可评估 {len(detail)} 条')

    s_oo = EV.summarize(detail, horizons, '全部-次日开盘', oo=True)
    s_cc = EV.summarize(detail, horizons, '全部-收盘收盘', oo=False)
    summary = pd.concat([s_oo, s_cc], ignore_index=True)
    qtab = pd.concat([
        EV.quintile_table(detail, [main_h], 'score'),
        EV.quintile_table(detail, [main_h], 'coil_strength'),
        EV.quintile_table(detail, [main_h], 'coil_run'),
        EV.quintile_table(detail, [main_h], 'cv'),
        EV.quintile_table(detail, [main_h], 'box_w'),
        EV.quintile_table(detail, [main_h], 'ma_gap'),
        EV.quintile_table(detail, [main_h], 'macd_band'),
        EV.quintile_table(detail, [main_h], 'vol_mult')], ignore_index=True)
    year = EV.by_year(detail, main_h)

    tr = detail[detail['date'] < split]
    te = detail[detail['date'] >= split]
    oos = pd.concat([
        EV.summarize(tr, horizons, '样本内', oo=True),
        EV.summarize(te, horizons, '样本外', oo=True)], ignore_index=True)

    pool = EV.price_only_signals(px, cfg, start, end) if not a.no_baseline \
        else pd.DataFrame(columns=['code', 'date', 'close'])
    base = pd.concat([
        s_oo.assign(基准='COIL'),
        EV.summarize(EV.event_study(mat, index, EV.random_signals(
            mat, max(len(sig), 50), start, end, cfg['backtest']['seed']),
            horizons, cost), horizons, '随机抽样', oo=True) if not a.no_baseline
        else pd.DataFrame(),
        EV.summarize(EV.event_study(mat, index, EV.matched_price_only(
            sig, pool, cfg['backtest']['seed']), horizons, cost),
            horizons, '仅价格突破(同日匹配)', oo=True) if not a.no_baseline
        else pd.DataFrame(),
        EV.summarize(EV.event_study(mat, index, pool, horizons, cost),
                     horizons, f'仅价格突破(全体,n={len(pool)})', oo=True)
        if not a.no_baseline else pd.DataFrame(),
    ], ignore_index=True)

    if a.compare_resonance:
        print('[对照] 正在计算旧 resonance.entry ...')
        try:
            rs = resonance_signals(px, start, end)
            base = pd.concat([base, EV.summarize(
                EV.event_study(mat, index, rs, horizons, cost),
                horizons, '旧共振(fresh_daily)', oo=True)], ignore_index=True)
        except Exception as exc:                            # noqa: BLE001
            print(f'[对照] 旧共振计算失败，跳过：{exc}')

    grid = grid_search(px, cfg, start, end, mat, index, horizons) if a.grid \
        else pd.DataFrame()

    shrink = EV.shrink_returns(px, sig, cfg)
    ma_exit = EV.ma_exit_returns(px, sig, cfg)
    fixed = detail[[f'a{main_h}_oo']].rename(
        columns={f'a{main_h}_oo': 'ret'}).copy()
    exit_tab = pd.DataFrame([
        {'规则': f'固定持有 {main_h} 日', 'n': int(fixed['ret'].notna().sum()),
         '均值收益%': round(float(fixed['ret'].mean() * 100), 2),
         't值': round(float(EV._tstat(fixed['ret'])[0]), 2),
         '平均持有天数': main_h},
        {'规则': f'MACD柱连缩 {cfg["exit"]["shrink_days"]} 日',
         'n': len(shrink),
         '均值收益%': round(float(shrink['ret'].mean() * 100), 2)
         if len(shrink) else np.nan,
         't值': round(float(EV._tstat(shrink['ret'])[0]), 2)
         if len(shrink) else np.nan,
         '平均持有天数': round(float(shrink['hold_days'].mean()), 1)
         if len(shrink) else np.nan},
        {'规则': f'收盘跌破 MA{cfg["exit"].get("ma_exit_period", 21)}',
         'n': len(ma_exit),
         '均值收益%': round(float(ma_exit['ret'].mean() * 100), 2)
         if len(ma_exit) else np.nan,
         't值': round(float(EV._tstat(ma_exit['ret'])[0]), 2)
         if len(ma_exit) else np.nan,
         '平均持有天数': round(float(ma_exit['hold_days'].mean()), 1)
         if len(ma_exit) else np.nan},
    ])

    verdict = _verdict(s_oo, qtab, grid, oos, main_h)
    out = a.out or os.path.join(
        CS.OUTPUT_DIR, f'coil_validate_{end.strftime("%Y%m%d")}.xlsx')
    with pd.ExcelWriter(out, engine='openpyxl') as xw:
        verdict.to_excel(xw, sheet_name='结论摘要', index=False)
        summary.to_excel(xw, sheet_name='持有期汇总', index=False)
        qtab.to_excel(xw, sheet_name='分组单调', index=False)
        year.to_excel(xw, sheet_name='分年度', index=False)
        base.to_excel(xw, sheet_name='基准对照', index=False)
        (grid if len(grid) else pd.DataFrame(
            {'说明': ['未跑 --grid']})).to_excel(
            xw, sheet_name='参数网格', index=False)
        exit_tab.to_excel(xw, sheet_name='卖出规则', index=False)
        oos.to_excel(xw, sheet_name='样本外', index=False)
        detail.to_excel(xw, sheet_name='信号明细', index=False)
        _notes(cfg, a, len(sig), len(detail)).to_excel(
            xw, sheet_name='口径说明', index=False)
    try:
        from mlr.report import _style_openpyxl
        _style_openpyxl(out)
    except Exception:                                        # noqa: BLE001
        pass
    print(verdict.to_string(index=False))
    print(f'[输出] {out}')


if __name__ == '__main__':
    main()

