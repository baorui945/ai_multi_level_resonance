# -*- coding: utf-8 -*-
"""
==============================================================================
coil_scan.py —— 横盘蓄势 → 放量突破：盘后扫描（独立策略，不写旧策略产物）
==============================================================================

用法::

    # 最近 10 个交易日的突破信号（全市场）
    python coil_scan.py --universe all

    # 指定日期的全部蓄势中股票（未突破）
    python coil_scan.py --universe all --date 2026-10-19 --all-state

    # 只看历史区间，并放宽容忍度做敏感性观察
    python coil_scan.py --universe hs300 --start 2024-01-01 --max-cv 0.12 --vol-mult 1.2

输出：``output/coil/coil_scan_<YYYYMMDD>.xlsx``
      sheets：形态明细 / 每股最近 / 口径说明
"""
import argparse
import os
import time

import numpy as np
import pandas as pd

from coil import settings as CS
from coil import detect as DT
from coil import features as F
from mlr import data as D
from mlr import universe as U


def _parse_args():
    p = argparse.ArgumentParser(description='COIL 横盘蓄势→放量突破扫描')
    p.add_argument('--universe', default='all', choices=['all', 'hs300', 'coal'],
                   help='股票池：全市场缓存 / 沪深300 / 动力煤板块')
    p.add_argument('--codes', default='', help='额外指定代码，逗号分隔')
    p.add_argument('--date', default='', help='观察日 YYYY-MM-DD（默认缓存最新日）')
    p.add_argument('--start', default='', help='信号起始日（默认取 --recent 个交易日）')
    p.add_argument('--recent', type=int, default=10, help='最近 N 个交易日')
    p.add_argument('--win', type=int, default=0, help='覆盖蓄势窗口（默认读配置）')
    p.add_argument('--max-cv', type=float, default=None)
    p.add_argument('--max-box-w', type=float, default=None)
    p.add_argument('--max-ma-gap', type=float, default=None)
    p.add_argument('--max-macd-band', type=float, default=None)
    p.add_argument('--min-coil-bars', type=int, default=None)
    p.add_argument('--min-coil-strength', type=float, default=None)
    p.add_argument('--vol-mult', type=float, default=None)
    p.add_argument('--eps', type=float, default=None)
    p.add_argument('--fresh-days', type=int, default=None)
    p.add_argument('--min-score', type=float, default=0.0)
    p.add_argument('--all-state', action='store_true',
                   help='输出观察日全部「蓄势中」股票（不必已突破）')
    p.add_argument('--out', default='', help='输出 xlsx 路径')
    p.add_argument('--no-style', action='store_true', help='不套用表头样式')
    return p.parse_args()


def _apply_overrides(cfg, a):
    c, b = cfg['coil'], cfg['breakout']
    if a.win:
        c['win'] = int(a.win)
    for key, val in (('max_cv', a.max_cv), ('max_box_w', a.max_box_w),
                     ('max_ma_gap', a.max_ma_gap),
                     ('max_macd_band', a.max_macd_band),
                     ('min_coil_strength', a.min_coil_strength)):
        if val is not None:
            c[key] = float(val)
    if a.min_coil_bars is not None:
        c['min_coil_bars'] = int(a.min_coil_bars)
    for key, val in (('vol_mult', a.vol_mult), ('eps', a.eps)):
        if val is not None:
            b[key] = float(val)
    if a.fresh_days is not None:
        b['fresh_days'] = int(a.fresh_days)
    return cfg


def _load_px(a):
    if a.universe == 'hs300':
        codes, upd = U.get_hs300_codes()
        print(f'[池] 沪深300 {len(codes)} 只（成分股快照 {upd}，历史回看有幸存者偏差）')
    elif a.universe == 'coal':
        codes, upd = U.get_coal_codes()
        print(f'[池] 动力煤 {len(codes)} 只（快照 {upd}）')
    else:
        codes = None
        print('[池] 全市场（直接读 parquet 缓存）')
    if a.codes:
        extra = [x.strip().zfill(6) for x in a.codes.split(',') if x.strip()]
        codes = sorted(set((codes or []) + extra)) if codes else extra
    px = pd.read_parquet(CS.PX_CACHE) if codes is None else \
        D.load_daily(codes=codes, verbose=False)
    px = px[['stock_code', 'stock_name', 'trade_date', 'open_price',
             'high_price', 'low_price', 'close_price', 'volume']].copy()
    px['trade_date'] = pd.to_datetime(px['trade_date'])
    return px.sort_values(['stock_code', 'trade_date'], ignore_index=True)



# ═════════════════════════════════════════════════════════════════════════
# 扫描
# ═════════════════════════════════════════════════════════════════════════
_DETAIL_COLS = ['cv', 'box_w', 'beta', 'curv', 'vol_compress', 'ma_gap',
                'ma120_slope', 'macd_band', 'hist_band', 'n_cross']


def _record(code, name, dates, c, o, i, st, run, all_state):
    f = st['features']
    rec = {
        '代码': code, '名称': name,
        '蓄势起点': pd.Timestamp(dates[max(0, i - int(run))]),
        '蓄势根数': int(run),
        '突破日': pd.NaT if all_state else pd.Timestamp(dates[i]),
        '收盘': round(float(c[i]), 3),
        '量比': round(float(f['vol_mult'][i]), 2)
        if np.isfinite(f['vol_mult'][i]) else np.nan,
        'coil_score': round(float(st['score'][i]), 1),
        '蓄势强度': round(float(st['coil_strength'][i]), 3),
        '箱体上沿': round(float(f['box_high'][i]), 3)
        if np.isfinite(f['box_high'][i]) else np.nan,
        '突破幅度': round(float(f['dist_high'][i]), 4)
        if np.isfinite(f['dist_high'][i]) else np.nan,
        '信号价(次日开盘)': round(float(o[i + 1]), 3) if i + 1 < o.size else np.nan,
    }
    for col in _DETAIL_COLS:
        val = f[col][i]
        rec[col] = round(float(val), 5) if np.isfinite(val) else np.nan
    return rec


def scan(px, cfg, start, end, all_state=False, min_score=0.0):
    """逐股判定，返回 (信号表, 观察日蓄势表)。"""
    mp = (cfg['macd']['fast'], cfg['macd']['slow'], cfg['macd']['signal'])
    mv = (cfg['ma']['short'], cfg['ma']['mid'], cfg['ma']['long'])
    min_bars = int(cfg['data']['min_bars'])
    rows, state_rows = [], []
    t0 = time.time()
    for k, (code, g) in enumerate(px.groupby('stock_code', sort=False), 1):
        c = g['close_price'].to_numpy('float64')
        if c.size < min_bars:
            continue
        v = g['volume'].to_numpy('float64')
        o = g['open_price'].to_numpy('float64')
        dif, dea, hist = F.macd(c, *mp)
        ma_s, ma_m, ma_l = F.moving_averages(c, mv)
        st = DT.state(c, v, cfg, mp, mv, dif=dif, dea=dea, hist=hist,
                      ma_s=ma_s, ma_m=ma_m, ma_l=ma_l)
        dates = pd.to_datetime(g['trade_date']).to_numpy()
        name = g['stock_name'].iloc[-1]
        if all_state:
            m = (dates >= np.datetime64(end)) & (dates <= np.datetime64(end)) \
                & st['coil']
        else:
            m = (dates >= np.datetime64(start)) & (dates <= np.datetime64(end)) \
                & st['entry'] & (st['score'] >= float(min_score))
        for i in np.flatnonzero(m):
            rec = _record(code, name, dates, c, o, i, st,
                          st['coil_run'][i], all_state)
            rec.update({'MA7': round(float(ma_s[i]), 3),
                        'MA21': round(float(ma_m[i]), 3),
                        'MA120': round(float(ma_l[i]), 3),
                        'DIF': round(float(dif[i]), 4),
                        'DEA': round(float(dea[i]), 4),
                        'HIST': round(float(hist[i]), 4)})
            (state_rows if all_state else rows).append(rec)
        if k % 500 == 0:
            print(f'  ... {k} 只，已用 {time.time() - t0:.0f}s')
    if all_state:
        df = pd.DataFrame(state_rows)
        if len(df):
            df = df.sort_values(['蓄势根数', '蓄势强度'],
                                ascending=False).reset_index(drop=True)
        return df, df
    df = pd.DataFrame(rows)
    if len(df):
        df = df.sort_values(['突破日', 'coil_score'],
                            ascending=[False, False]).reset_index(drop=True)
    return df, pd.DataFrame(state_rows)



def _per_stock(df):
    """每股最近一次信号（便于盘后快速浏览）。"""
    if not len(df):
        return df
    return (df.sort_values('突破日').groupby('代码', as_index=False).tail(1)
              .sort_values('coil_score', ascending=False).reset_index(drop=True))


def _notes(cfg, a, asof):
    c, b = cfg['coil'], cfg['breakout']
    pool = {'all': '全市场缓存（含退市股，无幸存者偏差）',
            'hs300': '沪深300（当前快照，历史回看有幸存者偏差）',
            'coal': '动力煤板块（当前快照）'}[a.universe]
    return pd.DataFrame({'项目': [
        '策略', '观察日', '股票池', '蓄势窗口', '硬条件', '软条件',
        '蓄势根数下限', '蓄势强度下限', '箱体上沿', '突破缓冲', '量比下限',
        '金叉新鲜度', '红柱放大', '均线多头', '冷却期', '评分口径',
        '数据口径', '未来函数控制', '注意'],
        '说明': [
        '独立包 coil：长期横盘蓄势→放量突破，与旧共振/morph 零耦合',
        str(pd.Timestamp(asof).date()), pool,
        f'{c["win"]} 个交易日（只用 t-1 及以前）',
        f'cv≤{c["max_cv"]}、box_w≤{c["max_box_w"]}、'
        f'vol_compress≤{c["vol_compress"]}、ma_gap≤{c["max_ma_gap"]}、'
        f'macd_band≤{c["max_macd_band"]}',
        f'|beta|≤{c["max_abs_beta"]}、|curv|≤{c["max_abs_curv"]}、'
        f'|ma120_slope|≤{c["max_ma120_slope"]}、'
        f'hist_band≤{c["max_hist_band"]}、n_cross≤{c["max_cross_n"]}'
        '（进入蓄势强度，不做硬性一票否决）',
        f'{c["min_coil_bars"]} 根（每根都要求其前 {c["win"]} 日窗口达标；'
        '--all-state 会列出不足下限的「窗口刚达标」观察对象）',
        f'{c["min_coil_strength"]}',
        f'前 {c["win"]} 日收盘的 q{int(b["box_q"] * 100)}',
        f'close > 箱体上沿×(1+{b["eps"]})',
        f'{b["vol_mult"]}×（前20日均量）',
        f'{b["fresh_days"]} 个交易日内出现 MACD 金叉',
        '要求 hist>0 且 hist_t>hist_{t-1}' if b['require_hist_grow'] else '不要求',
        '要求 MA7>MA21>MA120' if b['require_ma_stack'] else '不要求',
        f'{b.get("cooldown_days", 0)} 根内同一股票只记一次',
        'score=100×(0.7×蓄势强度+0.3×(0.65×突破幅度+0.35×量能))',
        '后复权日线 parquet 缓存（backward_tdx_daily），成交量不除权',
        '全部蓄势特征 shift(1)：t 日只用 [t-win, t-1]；突破只用当日收盘与量',
        '「信号价(次日开盘)」列给出可执行参考价；一字板需人工剔除',
    ]})


def main():
    a = _parse_args()
    cfg = _apply_overrides(CS.load_config(), a)
    CS.ensure_dirs()
    px = _load_px(a)
    asof = pd.Timestamp(a.date) if a.date else pd.Timestamp(px['trade_date'].max())
    dates = np.sort(px.loc[px['trade_date'] <= asof, 'trade_date'].unique())
    if not len(dates):
        raise SystemExit(f'观察日 {asof.date()} 之前无数据')
    end = pd.Timestamp(dates[-1])
    start = pd.Timestamp(a.start) if a.start else \
        pd.Timestamp(dates[max(0, len(dates) - int(a.recent))])
    print(f'[扫描] {start.date()} ~ {end.date()} | win={cfg["coil"]["win"]} '
          f'| universe={a.universe} | all_state={a.all_state}')
    df, _ = scan(px, cfg, start, end, all_state=a.all_state,
                 min_score=a.min_score)
    if a.all_state:
        print(f'[结果] 观察日蓄势中 {len(df)} 只')
    else:
        n_code = df['代码'].nunique() if len(df) else 0
        print(f'[结果] 区间信号 {len(df)} 条，涉及 {n_code} 只')

    out = a.out or os.path.join(
        CS.OUTPUT_DIR, f'coil_scan_{end.strftime("%Y%m%d")}.xlsx')
    per = _per_stock(df) if not a.all_state else df
    with pd.ExcelWriter(out, engine='openpyxl') as xw:
        (df if len(df) else pd.DataFrame(columns=['代码'])).to_excel(
            xw, sheet_name='形态明细', index=False)
        per.to_excel(xw, sheet_name='每股最近', index=False)
        _notes(cfg, a, end).to_excel(xw, sheet_name='口径说明', index=False)
    if not a.no_style:
        try:
            from mlr.report import _style_openpyxl
            _style_openpyxl(out)
        except Exception:                                  # noqa: BLE001
            pass
    print(f'[输出] {out}')

    ref = cfg.get('reference', {})
    if ref.get('code') and ref.get('breakout_date'):
        _print_reference(px, cfg, ref)


def _print_reference(px, cfg, ref):
    code = str(ref['code']).zfill(6)
    day = pd.Timestamp(ref['breakout_date'])
    g = px[px['stock_code'] == code].sort_values('trade_date')
    if not len(g):
        print(f'[参考] {code} 不在缓存中')
        return
    st = DT.state(g['close_price'].to_numpy('float64'),
                  g['volume'].to_numpy('float64'), cfg,
                  (cfg['macd']['fast'], cfg['macd']['slow'], cfg['macd']['signal']),
                  (cfg['ma']['short'], cfg['ma']['mid'], cfg['ma']['long']))
    pos = np.flatnonzero(pd.to_datetime(g['trade_date']).to_numpy()
                         == np.datetime64(day))
    if not len(pos):
        print(f'[参考] {code} 在 {day.date()} 无交易')
        return
    i = int(pos[0])
    print(f'--- 参考 {code} {day.date()} 特征 ---')
    for k in _DETAIL_COLS + ['vol_mult', 'dist_high', 'box_high']:
        print(f'  {k:12s} {st["features"][k][i]:.6f}')
    print(f'  coil_strength {st["coil_strength"][i]:.3f} | '
          f'coil={bool(st["coil"][i])} breakout={bool(st["breakout"][i])} '
          f'entry={bool(st["entry"][i])}')


if __name__ == '__main__':
    main()

