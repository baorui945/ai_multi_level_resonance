# -*- coding: utf-8 -*-
"""
==============================================================================
squeeze_scan.py —— 「长期均线粘合 + 放量突破」盘后扫描
==============================================================================

用法::

    python squeeze_scan.py --universe all --recent 10
    python squeeze_scan.py --universe all --date 2026-10-19 --max-stick 0.08
    python squeeze_scan.py --case 600188 --case-date 2026-01-13

输出：``output/squeeze/squeeze_scan_<YYYYMMDD>.xlsx``
      （形态明细 / 按日汇总 / 口径说明）
"""
import argparse
import os
import time

import numpy as np
import pandas as pd

from mlr import data as D
from mlr import universe as U
from squeeze import rule as R
from squeeze import settings as SS


def _parse_args():
    p = argparse.ArgumentParser(description='SQUEEZE 均线粘合→放量突破扫描')
    p.add_argument('--universe', default='all', choices=['all', 'hs300', 'coal'])
    p.add_argument('--codes', default='', help='额外指定代码，逗号分隔')
    p.add_argument('--date', default='', help='观察日 YYYY-MM-DD（默认缓存最新日）')
    p.add_argument('--start', default='', help='信号起始日（默认取 --recent 个交易日）')
    p.add_argument('--recent', type=int, default=10, help='最近 N 个交易日')
    p.add_argument('--max-stick', type=float, default=None, help='粘合度上限')
    p.add_argument('--min-pct', type=float, default=None, help='当日涨幅下限')
    p.add_argument('--no-vol', action='store_true', help='不要求放量')
    p.add_argument('--macd', action='store_true', help='要求 DIF>DEA 且在零轴附近')
    p.add_argument('--flat120', action='store_true', help='要求 MA120 走平')
    p.add_argument('--stack', action='store_true', help='要求 MA7>MA21>MA120')
    p.add_argument('--max-range', type=float, default=None,
                   help='近 60 日振幅上限（如 0.30）')
    p.add_argument('--watch', action='store_true',
                   help='观察池模式：列出“已粘合、尚未突破”的股票')
    p.add_argument('--watch-band', type=float, default=None,
                   help='观察池：收盘距均线束的上下限（如 0.03）')
    p.add_argument('--watch-min-days', type=int, default=None,
                   help='观察池：粘合至少持续 N 个交易日')
    p.add_argument('--min-price', type=float, default=None,
                   help='收盘价下限（元）')
    p.add_argument('--min-amount', type=float, default=None,
                   help='当日成交额下限（亿元）')
    p.add_argument('--exclude-st', action='store_true',
                   help='剔除 ST / *ST / 退市整理')
    p.add_argument('--exclude-limit', action='store_true',
                   help='剔除当日已涨停（次日难买）')
    p.add_argument('--min-grade', default='', choices=['', 'A', 'B'],
                   help='形态完整度下限（A / B）')
    p.add_argument('--literal', action='store_true',
                   help='关闭形态硬条件，回到“三条均线粘合+放量大涨”原式')
    p.add_argument('--min-run', type=int, default=None,
                   help='粘合天数下限（默认 10；0=不限制）')
    p.add_argument('--max-box-h', type=float, default=None,
                   help='突破前箱体高度上限（如 0.30；0=不限制）')
    p.add_argument('--no-box', action='store_true',
                   help='不要求收盘站上箱体上沿')
    p.add_argument('--box-tol', type=float, default=None,
                   help='允许收盘低于箱体上沿的比例（默认 0.02）')
    p.add_argument('--ma120-min', type=float, default=None,
                   help='MA120 5 日变化下限（默认 -0.005）')
    p.add_argument('--no-ma120-floor', action='store_true',
                   help='取消“MA120 不走坏”条件')
    p.add_argument('--no-shape', action='store_true',
                   help='关闭“蓄势质量”六条（v0.1.3 口径）')
    p.add_argument('--shape-range20', type=float, default=None,
                   help='突破前 20 日振幅上限（默认 0.12；0=不限）')
    p.add_argument('--shape-spike5', type=float, default=None,
                   help='120 日内最大 5 日涨幅上限（默认 0.12；0=不限）')
    p.add_argument('--shape-off-low', type=float, default=None,
                   help='收盘距 120 日低点上限（默认 0.20）')
    p.add_argument('--shape-ma120-over', type=float, default=None,
                   help='MA120 高于 MA21 的容差（默认 0.01）')
    p.add_argument('--clean', action='store_true',
                   help='实用预设：剔ST/剔涨停/价格>=2元/成交额>=0.5亿')
    p.add_argument('--top', type=int, default=40, help='控制台打印前 N 条')
    p.add_argument('--out', default='', help='输出 xlsx 路径')
    p.add_argument('--no-style', action='store_true', help='不套用表头样式')
    p.add_argument('--case', default='', help='复核单只股票（配合 --case-date）')
    p.add_argument('--case-date', default='', help='复核日 YYYY-MM-DD')
    p.add_argument('--case-span', type=int, default=15,
                   help='复核日前后展示的交易日数')
    return p.parse_args()


def _apply_overrides(cfg, a):
    r = cfg['rule']
    if a.max_stick is not None:
        r['max_stick'] = float(a.max_stick)
    if a.min_pct is not None:
        r['min_pct'] = float(a.min_pct)
    if a.no_vol:
        r['use_vol'] = False
    if a.macd:
        r['use_macd'] = True
    if a.flat120:
        r['use_ma120_flat'] = True
    if a.stack:
        r['require_stack'] = True
    if a.max_range is not None:
        r['max_range'] = float(a.max_range)
    pk = cfg.setdefault('pick', {})
    if a.clean:
        pk.update({'exclude_st': True, 'exclude_limit': True,
                   'min_price': 2.0, 'min_amount': 0.5})
    if a.min_price is not None:
        pk['min_price'] = float(a.min_price)
    if a.min_amount is not None:
        pk['min_amount'] = float(a.min_amount)
    if a.exclude_st:
        pk['exclude_st'] = True
    if a.exclude_limit:
        pk['exclude_limit'] = True
    if a.min_grade:
        pk['min_grade'] = a.min_grade
    if a.watch_band is not None:
        pk['watch_band'] = float(a.watch_band)
    if a.watch_min_days is not None:
        pk['watch_min_days'] = int(a.watch_min_days)
    if a.literal:
        r['use_form'] = False
    if a.min_run is not None:
        r['min_run'] = int(a.min_run)
    if a.max_box_h is not None:
        r['max_box_h'] = float(a.max_box_h) or None
    if a.no_box:
        r['need_box'] = False
    if a.box_tol is not None:
        r['box_tol'] = float(a.box_tol)
    if a.ma120_min is not None:
        r['ma120_min_chg'] = float(a.ma120_min)
    if a.no_ma120_floor:
        r['ma120_min_chg'] = None
    if a.no_shape:
        r['use_shape'] = False
    if a.shape_range20 is not None:
        r['shape_range20'] = float(a.shape_range20) or None
    if a.shape_spike5 is not None:
        r['shape_spike5'] = float(a.shape_spike5) or None
    if a.shape_off_low is not None:
        r['shape_off_low'] = float(a.shape_off_low)
    if a.shape_ma120_over is not None:
        r['shape_ma120_over'] = float(a.shape_ma120_over)
    return cfg


def _load_px(a):
    if a.universe == 'hs300':
        codes, upd = U.get_hs300_codes()
        print(f'[池] 沪深300 {len(codes)} 只（快照 {upd}，历史回看有幸存者偏差）')
    elif a.universe == 'coal':
        codes, upd = U.get_coal_codes()
        print(f'[池] 动力煤 {len(codes)} 只（快照 {upd}）')
    else:
        codes = None
        print('[池] 全市场（直接读 parquet 缓存）')
    if a.codes:
        extra = [x.strip().zfill(6) for x in a.codes.split(',') if x.strip()]
        codes = sorted(set((codes or []) + extra)) if codes else extra
    px = pd.read_parquet(SS.PX_CACHE) if codes is None else \
        D.load_daily(codes=codes, verbose=False)
    if 'amount' not in px.columns:                 # 兼容只有量、没有额的源
        px['amount'] = px['close_price'] * px['volume']
    px = px[['stock_code', 'stock_name', 'trade_date', 'open_price',
             'high_price', 'low_price', 'close_price', 'volume',
             'amount']].copy()
    px['stock_code'] = px['stock_code'].astype(str).str.zfill(6)
    px['trade_date'] = pd.to_datetime(px['trade_date'])
    return px.sort_values(['stock_code', 'trade_date'], ignore_index=True)


# ── 选股辅助：涨停判定 / ST / 可执行性过滤 ────────────────────────────────
def _limit_pct(code):
    code = str(code)
    if code[:1] in ('4', '8'):          # 北交所
        return 0.295
    return 0.195 if code[:2] in ('30', '68') else 0.095


def _is_st(name):
    s = str(name)
    return ('ST' in s.upper()) or ('退' in s)


def _raw_price(arr, i):
    """真实成交均价（元）：成交额/成交量。后复权下的 close 不能用于价格门槛。"""
    v = float(arr['volume'][i])
    a = float(arr['amount'][i])
    if np.isfinite(a) and np.isfinite(v) and v > 0 and a > 0:
        return a / v
    return float(arr['close'][i])


def _pick_ok(code, name, close, pct, amount, pick):
    """选股可执行性过滤。pick 为空 dict/None 时不过滤。"""
    if not pick:
        return True
    if pick.get('exclude_st') and _is_st(name):
        return False
    if pick.get('exclude_limit') and np.isfinite(pct) and \
            pct >= _limit_pct(code) - 0.005:
        return False
    if pick.get('min_price') and close < float(pick['min_price']):
        return False
    if pick.get('min_amount') and \
            (not np.isfinite(amount) or amount < float(pick['min_amount']) * 1e8):
        return False
    return True


def _arrange(f, i):
    if f['ma_s'][i] > f['ma_m'][i] > f['ma_l'][i]:
        return '多头'
    if f['ma_s'][i] < f['ma_m'][i] < f['ma_l'][i]:
        return '空头'
    return '纠缠'


def _ma120_trend(f, i):
    v = f['ma120_chg'][i]
    if not np.isfinite(v):
        return ''
    if v > 0.005:
        return '上行'
    if v < -0.005:
        return '下行'
    return '走平'


# ── 单条记录 ─────────────────────────────────────────────────────────────
def _record(code, name, ts, arr, f, i, sc, internal=False, run=0.0, grade=''):
    c = float(arr['close'][i])
    ma_max = float(f['ma_max'][i])
    box = float(f['box'][i]) if np.isfinite(f['box'][i]) else np.nan
    amt = float(arr['amount'][i]) if np.isfinite(arr['amount'][i]) else np.nan
    pct = float(f['pct'][i])
    rec = {'代码': code, '名称': name, '信号日': ts,
           '收盘': round(c, 3),
           '现价': round(_raw_price(arr, i), 2),
           '涨幅%': round(pct * 100, 2),
           '粘合度%': round(float(f['stick'][i]) * 100, 2),
           '粘合天数': int(run),
           '量比': round(float(f['vol_mult'][i]), 2)
           if np.isfinite(f['vol_mult'][i]) else np.nan,
           'MA7': round(float(f['ma_s'][i]), 3),
           'MA21': round(float(f['ma_m'][i]), 3),
           'MA120': round(float(f['ma_l'][i]), 3),
           '均线排列': _arrange(f, i),
           'MA120趋势': _ma120_trend(f, i),
           'MA120五日%': round(float(f['ma120_chg'][i]) * 100, 2)
           if np.isfinite(f['ma120_chg'][i]) else np.nan,
           '突破幅度%': round((c / ma_max - 1.0) * 100, 2)
           if np.isfinite(ma_max) and ma_max > 0 else np.nan,
           '箱体上沿': round(box, 3) if np.isfinite(box) else np.nan,
           '距箱体%': round((c / box - 1.0) * 100, 2)
           if np.isfinite(box) and box > 0 else np.nan,
           '箱体高度%': round(float(f['box_h'][i]) * 100, 2)
           if np.isfinite(f['box_h'][i]) else np.nan,
           '20日振幅%': round(float(f['rng20'][i]) * 100, 1)
           if np.isfinite(f['rng20'][i]) else np.nan,
           '120日最大5日涨%': round(float(f['spike5'][i]) * 100, 1)
           if np.isfinite(f['spike5'][i]) else np.nan,
           '离120日低%': round(float(f['off_low'][i]) * 100, 1)
           if np.isfinite(f['off_low'][i]) else np.nan,
           'MA120-MA21%': round(float(f['ma120_over'][i]) * 100, 2)
           if np.isfinite(f['ma120_over'][i]) else np.nan,
           'DIF': round(float(f['dif'][i]), 3),
           'DEA': round(float(f['dea'][i]), 3),
           '成交额(亿)': round(amt / 1e8, 2) if np.isfinite(amt) else np.nan,
           '是否涨停': ('一字' if pct >= _limit_pct(code) - 0.005
                        and arr['high'][i] == arr['low'][i]
                        else ('Y' if pct >= _limit_pct(code) - 0.005 else '')),
           'ST': 'Y' if _is_st(name) else '',
           '形态等级': grade,
           'score': round(float(sc), 1),
           '信号价(次日开盘)': round(float(arr['open'][i + 1]), 3)
           if i + 1 < arr['open'].size else np.nan}
    if internal:                       # 验证用，不写 xlsx
        for k, key in (('_stick', 'stick'), ('_pct', 'pct'),
                       ('_vol', 'vol_mult'), ('_dif_n', 'dif_n'),
                       ('_rng', 'rng'), ('_ma120_chg', 'ma120_chg')):
            v = float(f[key][i])
            rec[k] = v if np.isfinite(v) else np.nan
    return rec


def scan(px, cfg, start, end, rule=None, internal=False, verbose=True,
         pick=None):
    """逐股判定，返回 [start, end] 内的信号表（含冷却期）。

    ``pick`` 为选股过滤（None = 不过滤）：min_price / min_amount（亿元）/
    exclude_st / exclude_limit / min_grade。统计验证传 None，保持样本纯净。
    """
    r = dict(cfg['rule'])
    if rule:
        r.update(rule)
    p = dict(cfg)
    p['rule'] = r
    gcfg = cfg.get('grade') or {}
    pick = pick or {}
    cd = int(r.get('cooldown_days', 0))
    thr = float(r.get('max_stick') or 0.05)
    min_grade = str(pick.get('min_grade') or '')
    grank = {'A': 0, 'B': 1, 'C': 2}
    min_bars = int(cfg['data']['min_bars'])
    lo = np.datetime64(pd.Timestamp(start))
    hi = np.datetime64(pd.Timestamp(end))
    rows, t0 = [], time.time()
    for k, (code, g) in enumerate(px.groupby('stock_code', sort=False), 1):
        c = g['close_price'].to_numpy('float64')
        if c.size < min_bars:
            continue
        arr = {'open': g['open_price'].to_numpy('float64'),
               'high': g['high_price'].to_numpy('float64'),
               'low': g['low_price'].to_numpy('float64'),
               'close': c,
               'volume': g['volume'].to_numpy('float64'),
               'amount': g['amount'].to_numpy('float64')}
        st = R.compute(c, arr['high'], arr['low'], arr['volume'], p)
        f = st['features']
        f['run'] = R.stick_run(f['stick'], thr)
        ts = pd.to_datetime(g['trade_date']).to_numpy()
        m = (ts >= lo) & (ts <= hi) & st['signal'] & \
            R.form_mask(f, c, r)
        last, name = -10 ** 9, g['stock_name'].iloc[-1]
        for i in np.flatnonzero(m):
            if i - last < cd:
                continue
            grade = R.grade_at(f, int(i), c, gcfg)
            if min_grade and grank.get(grade, 2) > grank.get(min_grade, 2):
                continue
            if not _pick_ok(code, name, _raw_price(arr, i), f['pct'][i],
                            arr['amount'][i], pick):
                continue
            last = int(i)              # 被过滤的信号不占用冷却期
            rows.append(_record(code, name, pd.Timestamp(ts[i]), arr,
                                f, int(i), st['score'][i],
                                internal=internal, run=f['run'][i],
                                grade=grade))
        if verbose and k % 1000 == 0:
            print(f'  ... {k} 只，已用 {time.time() - t0:.0f}s')
    df = pd.DataFrame(rows)
    if len(df):
        df['_g'] = df['形态等级'].map(grank).fillna(2)
        df = df.sort_values(['信号日', '_g', 'score'],
                            ascending=[False, True, False]) \
            .drop(columns='_g').reset_index(drop=True)
    return df


def watch(px, cfg, day, pick=None, verbose=True):
    """观察池：截至 day 已长期粘合、但尚未放量突破的股票。

    筛选条件（全部只用 T 日及以前数据）：

        stick <= max_stick          处于粘合状态
        粘合天数 >= watch_min_days   已持续 N 个交易日（区分“刚贴合”）
        |C/MA束 - 1| <= watch_band   收盘贴近均线束（默认 ±3%）
        pct < min_pct               当日没有大涨（否则已进突破清单）

    返回按「粘合天数降序、粘合度升序」排序的表；纯粹是形态筛选，
    不含任何收益预测。
    """
    pick = pick or {}
    r = cfg['rule']
    thr = float(r.get('max_stick') or 0.05)
    band = float(pick.get('watch_band', 0.03))
    min_days = int(pick.get('watch_min_days', 5))
    p = dict(cfg)
    day64 = np.datetime64(pd.Timestamp(day))
    rows, t0 = [], time.time()
    for k, (code, g) in enumerate(px.groupby('stock_code', sort=False), 1):
        c = g['close_price'].to_numpy('float64')
        if c.size < int(cfg['data']['min_bars']):
            continue
        ts = pd.to_datetime(g['trade_date']).to_numpy()
        if ts[-1] != day64:            # 当日停牌 / 未上市
            continue
        i = c.size - 1
        arr = {'open': g['open_price'].to_numpy('float64'),
               'high': g['high_price'].to_numpy('float64'),
               'low': g['low_price'].to_numpy('float64'),
               'close': c,
               'volume': g['volume'].to_numpy('float64'),
               'amount': g['amount'].to_numpy('float64')}
        st = R.compute(c, arr['high'], arr['low'], arr['volume'], p)
        f = st['features']
        f['run'] = R.stick_run(f['stick'], thr)
        if not (np.isfinite(f['stick'][i]) and f['stick'][i] <= thr):
            continue
        if f['run'][i] < min_days:
            continue
        # 观察池：箱体不宽 / MA120 不走坏；粘合天数由 watch_min_days 控制
        rf = dict(r)
        rf['min_run'] = 0
        if not R.form_ok(f, i, c, rf, breakout=False):
            continue
        ma_max = f['ma_max'][i]
        if not (np.isfinite(ma_max) and ma_max > 0):
            continue
        dist = c[i] / ma_max - 1.0
        if abs(dist) > band:
            continue
        if f['pct'][i] >= float(r['min_pct']):
            continue
        name = g['stock_name'].iloc[-1]
        if not _pick_ok(code, name, _raw_price(arr, i), f['pct'][i],
                        arr['amount'][i], pick):
            continue
        box = f['box'][i]
        amt = arr['amount'][i]
        rows.append({
            '代码': code, '名称': name, '日期': pd.Timestamp(ts[i]),
            '收盘': round(float(c[i]), 3),
            '现价': round(_raw_price(arr, i), 2),
            '粘合度%': round(float(f['stick'][i]) * 100, 2),
            '粘合天数': int(f['run'][i]),
            '距均线束%': round(dist * 100, 2),
            '涨幅%': round(float(f['pct'][i]) * 100, 2),
            '量比': round(float(f['vol_mult'][i]), 2)
            if np.isfinite(f['vol_mult'][i]) else np.nan,
            'MA7': round(float(f['ma_s'][i]), 3),
            'MA21': round(float(f['ma_m'][i]), 3),
            'MA120': round(float(f['ma_l'][i]), 3),
            '均线排列': _arrange(f, i),
            'MA120趋势': _ma120_trend(f, i),
            'MA120五日%': round(float(f['ma120_chg'][i]) * 100, 2)
            if np.isfinite(f['ma120_chg'][i]) else np.nan,
            '箱体上沿': round(float(box), 3) if np.isfinite(box) else np.nan,
            '距箱体%': round((c[i] / box - 1.0) * 100, 2)
            if np.isfinite(box) and box > 0 else np.nan,
            '箱体高度%': round(float(f['box_h'][i]) * 100, 2)
            if np.isfinite(f['box_h'][i]) else np.nan,
            '20日振幅%': round(float(f['rng20'][i]) * 100, 1)
            if np.isfinite(f['rng20'][i]) else np.nan,
            '离120日低%': round(float(f['off_low'][i]) * 100, 1)
            if np.isfinite(f['off_low'][i]) else np.nan,
            'MA120-MA21%': round(float(f['ma120_over'][i]) * 100, 2)
            if np.isfinite(f['ma120_over'][i]) else np.nan,
            'DIF': round(float(f['dif'][i]), 3),
            'DEA': round(float(f['dea'][i]), 3),
            '成交额(亿)': round(float(amt) / 1e8, 2)
            if np.isfinite(amt) else np.nan,
            'ST': 'Y' if _is_st(name) else '',
        })
        if verbose and k % 1000 == 0:
            print(f'  ... {k} 只，已用 {time.time() - t0:.0f}s')
    df = pd.DataFrame(rows)
    if len(df):
        df = df.sort_values(['粘合天数', '粘合度%'],
                            ascending=[False, True]).reset_index(drop=True)
    return df


def scan_multi(px, cfg, start, end, variants, internal=True, verbose=True):
    """一次遍历、多套阈值：特征只算一遍，用于统计验证的对照实验。

    Args:
        variants: {名称: rule 覆盖 dict}；None 或 {} 表示用配置原值。
    Returns:
        纵向拼接的信号表，多一列 ``变体``。
    """
    min_bars = int(cfg['data']['min_bars'])
    lo = np.datetime64(pd.Timestamp(start))
    hi = np.datetime64(pd.Timestamp(end))
    rows, t0 = [], time.time()
    for k, (code, g) in enumerate(px.groupby('stock_code', sort=False), 1):
        c = g['close_price'].to_numpy('float64')
        if c.size < min_bars:
            continue
        arr = {'open': g['open_price'].to_numpy('float64'),
               'high': g['high_price'].to_numpy('float64'),
               'low': g['low_price'].to_numpy('float64'),
               'close': c,
               'volume': g['volume'].to_numpy('float64'),
               'amount': g['amount'].to_numpy('float64')}
        f = R.features(c, arr['high'], arr['low'], arr['volume'], cfg)
        f['v'] = arr['volume']
        f['run'] = R.stick_run(f['stick'],
                               float(cfg['rule'].get('max_stick') or 0.05))
        ts = pd.to_datetime(g['trade_date']).to_numpy()
        name = g['stock_name'].iloc[-1]
        for vname, ov in variants.items():
            r = dict(cfg['rule'])
            r.update(ov or {})
            pv = dict(cfg)
            pv['rule'] = r
            # 粘合天数按该变体自己的 max_stick 计，避免 3% 变体沿用 5% 的天数
            f['run'] = R.stick_run(f['stick'],
                                   float(r.get('max_stick') or 0.05))
            sig = R.mask(f, c, r)
            m = (ts >= lo) & (ts <= hi) & sig
            if r.get('use_form', True):     # 变体可显式关闭形态硬条件
                m &= R.form_mask(f, c, r)
            last = -10 ** 9
            cd = int(r.get('cooldown_days', 0))
            for i in np.flatnonzero(m):
                if i - last < cd:
                    continue
                last = int(i)
                rec = _record(code, name, pd.Timestamp(ts[i]), arr, f,
                              int(i), R.score_at(f, int(i), pv),
                              internal=internal, run=f['run'][i])
                rec['变体'] = vname
                rows.append(rec)
        if verbose and k % 1000 == 0:
            print(f'  ... {k} 只，已用 {time.time() - t0:.0f}s')
    return pd.DataFrame(rows)


# ── 参考形态复核 ─────────────────────────────────────────────────────────
def _case(px, cfg, code, day, span):
    g = px[px['stock_code'] == code.zfill(6)].sort_values('trade_date')
    if not len(g):
        print(f'[复核] {code} 不在缓存中')
        return
    arr = {k: g[v].to_numpy('float64') for k, v in (
        ('open', 'open_price'), ('high', 'high_price'),
        ('low', 'low_price'), ('close', 'close_price'), ('volume', 'volume'))}
    st = R.compute(arr['close'], arr['high'], arr['low'], arr['volume'], cfg)
    ts = pd.to_datetime(g['trade_date']).to_numpy()
    pos = np.flatnonzero(ts == np.datetime64(pd.Timestamp(day)))
    if not len(pos):
        print(f'[复核] {code} 在 {day} 无交易')
        return
    i = int(pos[0])
    f = st['features']
    print(f'--- {code} {g["stock_name"].iloc[-1]} {pd.Timestamp(day).date()} '
          f'（后复权）---')
    print(f'  MA7={f["ma_s"][i]:.2f}  MA21={f["ma_m"][i]:.2f}  '
          f'MA120={f["ma_l"][i]:.2f}  粘合度={f["stick"][i] * 100:.2f}%')
    print(f'  DIF={f["dif"][i]:.3f}  DEA={f["dea"][i]:.3f}  '
          f'DIF/C={f["dif_n"][i] * 100:.2f}%  '
          f'MA120五日={f["ma120_chg"][i] * 100:.2f}%')
    print(f'  涨幅={f["pct"][i] * 100:.2f}%  量比={f["vol_mult"][i]:.2f}')
    fm = R.form_mask(f, arr['close'], cfg['rule'])
    print(f'  粘合天数={f["run"][i]:.0f}  箱体高度={f["box_h"][i] * 100:.2f}%  '
          f'箱体上沿={f["box"][i]:.2f}  '
          f'距箱体={(arr["close"][i] / f["box"][i] - 1) * 100:.2f}%')
    print(f'  20日振幅={f["rng20"][i] * 100:.1f}%  '
          f'120日最大5日涨={f["spike5"][i] * 100:.1f}%  '
          f'120日最大5日跌={f["crash5"][i] * 100:.1f}%  '
          f'离120日低={f["off_low"][i] * 100:.1f}%  '
          f'MA120-MA21={f["ma120_over"][i] * 100:.2f}%')
    sh = R.shape_mask(f, arr['close'], cfg['rule'])
    print(f'  蓄势质量={"通过" if sh[i] else "不通过"}')
    print(f'  形态硬条件={"通过" if fm[i] else "不通过"}')
    a, b = max(0, i - span), min(len(ts), i + span + 1)
    tab = pd.DataFrame({
        '日期': pd.to_datetime(ts[a:b]).date,
        '收盘': arr['close'][a:b].round(2),
        '涨幅%': (f['pct'][a:b] * 100).round(2),
        '粘合%': (f['stick'][a:b] * 100).round(2),
        '粘合天数': f['run'][a:b].astype(int),
        '箱体高度%': (f['box_h'][a:b] * 100).round(2),
        '量比': f['vol_mult'][a:b].round(2),
        'MA7': f['ma_s'][a:b].round(2), 'MA21': f['ma_m'][a:b].round(2),
        'MA120': f['ma_l'][a:b].round(2),
        'DIF': f['dif'][a:b].round(2), 'DEA': f['dea'][a:b].round(2),
        '信号': np.where(st['signal'][a:b], 'Y', ''),
        '形态': np.where(fm[a:b], 'Y', ''),
    })
    print(tab.to_string(index=False))
    m = np.flatnonzero(st['signal'] & fm & (np.arange(len(ts)) > i))
    if len(m):
        j = int(m[0])
        print(f'  -> 之后首个信号：{pd.Timestamp(ts[j]).date()}  '
              f'涨幅 {f["pct"][j] * 100:.2f}%  粘合 {f["stick"][j] * 100:.2f}%  '
              f'量比 {f["vol_mult"][j]:.2f}  score={st["score"][j]:.1f}')
    else:
        print('  -> 之后无信号（阈值过严或已错过）')


def _notes(cfg, a, asof):
    r = cfg['rule']
    pk = cfg.get('pick') or {}
    g = cfg.get('grade') or {}
    mode = '观察池（已粘合、未突破）' if a.watch else '突破日信号（选股清单）'
    grade_txt = (
        f"A：粘合<={float(g.get('a_max_stick', 0.03)) * 100:.0f}% 且 "
        f"粘合天数>={int(g.get('a_min_run', 5))} 且 "
        f"量比>={float(g.get('a_min_vol', 2.0)):.1f}"
        f"{' 且站上箱体上沿' if g.get('a_need_box') else ''}；"
        f"B：粘合<={float(g.get('b_max_stick', 0.05)) * 100:.0f}% 且 "
        f"粘合天数>={int(g.get('b_min_run', 3))} 且 "
        f"量比>={float(g.get('b_min_vol', 1.3)):.1f}；其余为 C")
    def _flag(v):
        return '开' if v else '关'
    filt = (f"价格>={float(pk.get('min_price') or 0):.2f} 元；"
            f"成交额>={float(pk.get('min_amount') or 0):.2f} 亿；"
            f"剔ST={_flag(pk.get('exclude_st'))}；"
            f"剔涨停={_flag(pk.get('exclude_limit'))}；"
            f"等级>={pk.get('min_grade') or '不限'}")
    if r.get('use_form', True):
        run_min = (int(pk.get('watch_min_days', 10)) if a.watch
                   else int(r.get('min_run') or 0))
        form_txt = (
            f"粘合天数>={run_min}；"
            f"箱体高度(近{int(r.get('form_win', 60))}日，不含当日)"
            f"<={float(r.get('max_box_h') if r.get('max_box_h') is not None else 1) * 100:.0f}%；"
            + (f"收盘>=箱体上沿*(1-{float(r.get('box_tol') or 0) * 100:.0f}%)；"
               if (r.get('need_box') and not a.watch) else '')
            + (f"MA120五日>={float(r['ma120_min_chg']) * 100:.2f}%"
               if r.get('ma120_min_chg') is not None else 'MA120不限'))
        if a.watch:
            form_txt += '；观察池不要求站上箱体（此时股价仍在箱内）'
        if r.get('use_shape', True):
            shape_txt = (
                f"20日振幅<={float(r['shape_range20'] or 1) * 100:.0f}%；"
                f"40日振幅<={float(r['shape_range40'] or 1) * 100:.0f}%；"
                f"120日最大5日涨<={float(r['shape_spike5'] or 1) * 100:.0f}%；"
                f"最大5日跌>={float(r['shape_crash5'] if r.get('shape_crash5') is not None else -1) * 100:.0f}%；"
                f"离120日低<={float(r['shape_off_low'] if r.get('shape_off_low') is not None else 1) * 100:.0f}%；"
                f"MA120<=MA21*(1+{float(r['shape_ma120_over'] if r.get('shape_ma120_over') is not None else 1) * 100:.0f}%)")
        else:
            shape_txt = '已关闭（--no-shape：退回 v0.1.3 四条件，会放进“跌多了碰巧收敛”的票）'
    else:
        form_txt = '已关闭（--literal：回到原式，图形上可能只是反弹而非箱体突破）'
        shape_txt = '已关闭'
    items = [
        ('策略', 'SQUEEZE：长期均线粘合（筹码沉淀）→ 当日放量大涨、突破全部均线'),
        ('模式', mode),
        ('观察日', str(pd.Timestamp(asof).date())),
        ('股票池', {'all': '全市场缓存（含退市股，无幸存者偏差）',
                    'hs300': '沪深300（当前快照，历史回看有幸存者偏差）',
                    'coal': '动力煤板块（当前快照）'}[a.universe]),
        ('粘合度', f'(max-min)/min of MA7/21/120 <= {r["max_stick"] * 100:.1f}%'),
    ]
    if a.watch:
        items += [
            ('观察池条件', f'粘合 <= {r["max_stick"] * 100:.1f}% 且 '
                           f'粘合天数 >= {int(pk.get("watch_min_days", 5))} 且 '
                           f'|收盘/均线束-1| <= {float(pk.get("watch_band", 0.03)) * 100:.1f}% 且 '
                           f'当日涨幅 < {r["min_pct"] * 100:.1f}%（箱体高度/MA120 条件同下）'),
        ]
    else:
        items += [
            ('突破', f'C > max(三条均线) 且涨幅 >= {r["min_pct"] * 100:.1f}%'),
            ('放量', 'V_t > MA(V,5)（含当日，通达信口径）'
                     if r['use_vol'] else '已关闭'),
            ('零轴', f'DIF>DEA 且 DIF/C >= -{r["macd_zero_band"] * 100:.1f}%（归一化）'
                     if r['use_macd'] else '已关闭（原公式 COND_E 未接入 XG，默认关）'),
            ('MA120 走平', f'|MA120_t/MA120_(t-5)-1| <= {r["ma120_flat"] * 100:.1f}%'
                          if r['use_ma120_flat'] else '已关闭'),
            ('多头排列', 'MA7 > MA21 > MA120' if r['require_stack'] else '已关闭'),
            ('振幅', f'近 {r["range_win"]} 日 (maxH-minL)/minL <= {r["max_range"]}'
                     if r['max_range'] is not None else '已关闭'),
        ]
    items += [('形态硬条件', form_txt), ('蓄势质量', shape_txt), ('选股过滤', filt)]
    if not a.watch:
        items += [
            ('形态等级', grade_txt),
            ('冷却期', f'{r["cooldown_days"]} 个交易日内同一股票只记一次'
                       '（被过滤的不占用）'),
        ]
    items += [
        ('排序', '粘合天数降序、粘合度升序' if a.watch
                 else '信号日降序，同日按形态等级、score 降序'),
        ('数据口径', '后复权日线 parquet 缓存；均线/MACD 用后复权价，比例不受复权影响'),
        ('未来函数控制', 'T 日收盘确认，只用到 T 日及以前数据；不含任何收益预测'),
        ('注意', '涨停/一字板次日可能买不进；等级/score 只是形态完整度与排序，'
                 '不是上涨概率'),
    ]
    return pd.DataFrame(items, columns=['项目', '说明'])


def _daily_summary(df):
    """按信号日汇总，方便在 Excel 里看“今天出了多少票、质量如何”。"""
    cols = ['信号日', '信号数', '平均涨幅%', '平均粘合度%', '平均量比',
            '最高score', '有次日开盘价']
    if not len(df):
        return pd.DataFrame(columns=cols)
    g = df.groupby('信号日', as_index=False).agg(
        信号数=('代码', 'size'),
        平均涨幅=('涨幅%', 'mean'),
        平均粘合=('粘合度%', 'mean'),
        平均量比=('量比', 'mean'),
        最高score=('score', 'max'),
        有次日开盘价=('信号价(次日开盘)', lambda s: int(s.notna().sum())),
    )
    g.columns = cols
    for c in ['平均涨幅%', '平均粘合度%', '平均量比']:
        g[c] = g[c].round(2)
    g['最高score'] = g['最高score'].round(1)
    return g.sort_values('信号日', ascending=False).reset_index(drop=True)


def _post_format(out, detail_sheet='形态明细'):
    """代码列强制文本（保留 000860 这类前导零）+ 全表自动筛选。"""
    try:
        from openpyxl import load_workbook
        wb = load_workbook(out)
        for ws in wb.worksheets:
            if ws.max_row >= 1 and ws.max_column >= 1:
                ws.auto_filter.ref = ws.dimensions
        if detail_sheet in wb.sheetnames:
            ws = wb[detail_sheet]
            for cell in ws['A'][1:]:
                cell.number_format = '@'
                if cell.value is not None:
                    cell.value = str(cell.value).zfill(6)
        wb.save(out)
    except Exception:                                      # noqa: BLE001
        pass


def _write_sheets(out, sheets, cfg, a, end):
    """写 xlsx；目标文件被 Excel 占用时自动改名，避免整次扫描白跑。"""
    def _build(path):
        with pd.ExcelWriter(path, engine='openpyxl') as xw:
            for name, data in sheets:
                data.to_excel(xw, sheet_name=name, index=False)
            _notes(cfg, a, end).to_excel(xw, sheet_name='口径说明', index=False)

    try:
        _build(out)
        return out
    except PermissionError:
        stem, ext = os.path.splitext(out)
        alt = f'{stem}_{time.strftime("%H%M%S")}{ext}'
        _build(alt)
        print(f'[警告] {out} 被占用（Excel 打开？），已改写到 {alt}')
        return alt


def main():
    a = _parse_args()
    cfg = _apply_overrides(SS.load_config(), a)
    SS.ensure_dirs()
    px = _load_px(a)
    if a.case:
        day = a.case_date or str(px['trade_date'].max().date())
        _case(px, cfg, a.case, day, int(a.case_span))
        return
    asof = pd.Timestamp(a.date) if a.date else \
        pd.Timestamp(px['trade_date'].max())
    dates = np.sort(px.loc[px['trade_date'] <= asof, 'trade_date'].unique())
    if not len(dates):
        raise SystemExit(f'观察日 {asof.date()} 之前无数据')
    end = pd.Timestamp(dates[-1])
    start = pd.Timestamp(a.start) if a.start else \
        pd.Timestamp(dates[max(0, len(dates) - int(a.recent))])
    r = cfg['rule']
    pk = cfg.get('pick') or {}

    if a.watch:
        print(f'[观察池] {end.date()} | 粘合<={r["max_stick"] * 100:.1f}% '
              f'粘合天数>={int(pk.get("watch_min_days", 5))} '
              f'距均线束<=±{float(pk.get("watch_band", 0.03)) * 100:.1f}% '
              f'涨幅<{r["min_pct"] * 100:.1f}%')
        df = watch(px, cfg, end, pick=pk)
        print(f'[结果] 观察池 {len(df)} 只')
        out = a.out or os.path.join(
            SS.OUTPUT_DIR, f'squeeze_watch_{end.strftime("%Y%m%d")}.xlsx')
        out = _write_sheets(
            out, [('观察池', df if len(df) else
                   pd.DataFrame(columns=['代码']))], cfg, a, end)
        if not a.no_style:
            try:
                from mlr.report import _style_openpyxl
                _style_openpyxl(out)
            except Exception:                              # noqa: BLE001
                pass
        _post_format(out, detail_sheet='观察池')
        print(f'[输出] {out}（观察池 {len(df)} 行 / 口径说明）')
        if len(df) and a.top:
            cols = ['代码', '名称', '现价', '粘合度%', '粘合天数', '距均线束%',
                    '箱体高度%', '量比', '均线排列', 'MA120趋势', '成交额(亿)']
            print(df[cols].head(int(a.top)).to_string(index=False))
        return

    print(f'[扫描] {start.date()} ~ {end.date()} | '
          f'粘合<={r["max_stick"] * 100:.1f}% '
          f'涨幅>={r["min_pct"] * 100:.1f}% '
          f'vol={"on" if r["use_vol"] else "off"} '
          f'macd={"on" if r["use_macd"] else "off"} '
          f'stack={"on" if r["require_stack"] else "off"}')
    if r.get('use_form', True):
        print(f'[形态] 粘合天数>={int(r.get("min_run") or 0)} '
              f'箱体高度<={float(r.get("max_box_h") or 1) * 100:.0f}% '
              f'站上箱体={"on" if r.get("need_box") else "off"} '
              f'MA120五日>={float(r["ma120_min_chg"]) * 100:.2f}%'
              if r.get('ma120_min_chg') is not None else
              f'[形态] 粘合天数>={int(r.get("min_run") or 0)} '
              f'箱体高度<={float(r.get("max_box_h") or 1) * 100:.0f}% '
              f'站上箱体={"on" if r.get("need_box") else "off"} MA120不限')
    else:
        print('[形态] 已关闭（--literal 原式）')
    if r.get('use_form', True) and r.get('use_shape', True):
        print(f'[蓄势] 20日振幅<={float(r["shape_range20"] or 1) * 100:.0f}% '
              f'40日振幅<={float(r["shape_range40"] or 1) * 100:.0f}% '
              f'120日5日涨<={float(r["shape_spike5"] or 1) * 100:.0f}% '
              f'5日跌>={float(r["shape_crash5"] if r.get("shape_crash5") is not None else -1) * 100:.0f}% '
              f'离120低<={float(r["shape_off_low"] or 1) * 100:.0f}% '
              f'MA120<=MA21*(1+{float(r["shape_ma120_over"] if r.get("shape_ma120_over") is not None else 1) * 100:.0f}%)')
    elif r.get('use_form', True):
        print('[蓄势] 已关闭（--no-shape，退回 v0.1.3 四条件）')
    print(f'[过滤] 价格>={float(pk.get("min_price") or 0):.2f} '
          f'成交额>={float(pk.get("min_amount") or 0):.2f}亿 '
          f'剔ST={"on" if pk.get("exclude_st") else "off"} '
          f'剔涨停={"on" if pk.get("exclude_limit") else "off"} '
          f'等级>={pk.get("min_grade") or "不限"}')
    df = scan(px, cfg, start, end, pick=pk)
    n = df['代码'].nunique() if len(df) else 0
    print(f'[结果] {len(df)} 条信号，涉及 {n} 只')

    out = a.out or os.path.join(
        SS.OUTPUT_DIR, f'squeeze_scan_{end.strftime("%Y%m%d")}.xlsx')
    out = _write_sheets(
        out, [('形态明细', df if len(df) else pd.DataFrame(columns=['代码'])),
              ('按日汇总', _daily_summary(df))], cfg, a, end)
    if not a.no_style:
        try:
            from mlr.report import _style_openpyxl
            _style_openpyxl(out)
        except Exception:                                  # noqa: BLE001
            pass
    _post_format(out)
    print(f'[输出] {out}（形态明细 {len(df)} 行 / 按日汇总 / 口径说明）')

    if len(df) and a.top:
        cols = ['代码', '名称', '信号日', '现价', '涨幅%', '粘合度%',
                '粘合天数', '量比', '突破幅度%', '距箱体%', '箱体高度%',
                '均线排列', '成交额(亿)', '形态等级', 'score']
        print(df[cols].head(int(a.top)).to_string(index=False))


if __name__ == '__main__':
    main()

