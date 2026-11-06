# -*- coding: utf-8 -*-
"""
==============================================================================
launch_scan.py —— 「平台整理 → 放量上抛」日线扫描
==============================================================================

形态（T 日收盘即可全部得出，无未来函数）：

    平台    近 box_win 日（不含 T）振幅 (maxH-minL)/minL <= box_max_h
    突破    C_T >= 平台高点 × (1 - tol) 且当日涨幅 >= min_pct 且量比 >= min_vol
    趋势    MA120 20 日变化 >= ma120_min、60 日变化 >= ma120_60min
            且 C_T >= MA120 × ma120_ref
    位置    C_T >= 250 日最高价 × pos250_min（不在深坑里）
    MACD    DIF > DEA 且 DIF/C >= -macd_neg，且 cross_win 日内出现过金叉

信号日 = 平台被放量突破的那一天；T+1 开盘可执行。

用法::

    python launch_scan.py --recent 10
    python launch_scan.py --date 2026-10-19 --min-pct 0.03
    python launch_scan.py --watch            # 平台中、尚未突破
    python launch_scan.py --case 000400 --case-date 2024-04-11
"""
import argparse
import json
import os
import time

import numpy as np
import pandas as pd

from mlr import data as D
from mlr import settings as _MLR
from mlr import universe as U

ROOT_DIR = _MLR.ROOT_DIR
CONFIG_PATH = os.path.join(ROOT_DIR, 'config', 'launch.json')
OUTPUT_DIR = os.path.join(_MLR.OUTPUT_DIR, 'launch')
PX_CACHE = _MLR.PX_CACHE

DEF = {
    'data': {'min_bars': 260},
    'ma': {'short': 7, 'mid': 21, 'long': 120},
    'macd': {'fast': 12, 'slow': 26, 'signal': 9},
    'rule': {
        'box_win': 20,
        'box_max_h': 0.12,
        'box_tol': 0.005,
        'min_pct': 0.02,
        'min_pct3': None,
        'min_vol': 1.5,
        'max_breakout': 0.04,    # 收盘高出平台高点的上限（追高过滤）
        'ma60_min': None,
        'ma120_min': -0.01,
        'ma120_60min': None,
        'ma120_ref': 0.97,
        'pos250_min': 0.60,
        'macd_neg': 0.05,
        'require_cross': True,
        'cross_win': 20,
        'confirm_days': 0,
        'cooldown_days': 20,
    },
    'pick': {'min_price': 2.0, 'min_amount': 0.5,
             'exclude_st': True, 'exclude_limit': True},
    'exit': {'horizons': [5, 10, 20, 40], 'main_horizon': 20, 'cost': 0.002},
}


def _deep_merge(base, over):
    out = dict(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config(path=None):
    p = path or CONFIG_PATH
    user = {}
    if os.path.isfile(p):
        with open(p, 'r', encoding='utf-8') as f:
            user = json.load(f)
    return _deep_merge(DEF, user)


# ── 特征 ─────────────────────────────────────────────────────────────────
def _roll(a, n, how='mean'):
    return pd.Series(a).rolling(int(n), min_periods=int(n)).agg(how) \
        .to_numpy('float64')


def _sh(a, n=1):
    return pd.Series(a).shift(int(n)).to_numpy('float64')


def _div(a, b):
    a = np.asarray(a, dtype='float64')
    b = np.asarray(b, dtype='float64')
    out = np.full(a.size, np.nan)
    ok = np.isfinite(a) & np.isfinite(b) & (b != 0)
    out[ok] = a[ok] / b[ok]
    return out


def _ema(a, n):
    return pd.Series(a).ewm(span=int(n), adjust=False).mean().to_numpy('float64')


def features(c, h, l, v, p):
    r = p['rule']
    ma_s = _roll(c, p['ma']['short'])
    ma_m = _roll(c, p['ma']['mid'])
    ma_l = _roll(c, p['ma']['long'])

    prev = _sh(c, 1)
    pct = _div(c - prev, prev)
    pct3 = _div(c - _sh(c, 3), _sh(c, 3))
    ma60 = _roll(c, 60)
    ma60_chg = _div(ma60 - _sh(ma60, 20), _sh(ma60, 20))

    vma5p = _sh(_roll(v, 5), 1)
    vol_mult = _div(v, vma5p)
    vma20p = _sh(_roll(v, 20), 1)
    vol20 = _div(v, vma20p)

    dif = _ema(c, p['macd']['fast']) - _ema(c, p['macd']['slow'])
    dea = _ema(dif, p['macd']['signal'])
    dif_n = _div(dif, c)

    bw = int(r['box_win'])
    box_hi = _sh(_roll(h, bw, 'max'), 1)
    box_lo = _sh(_roll(l, bw, 'min'), 1)
    # 平台上沿 = 近 box_win 日最高收盘价（用收盘而非最高价，避免插针抬高平台）
    box_top = _sh(_roll(c, bw, 'max'), 1)
    box_h = _div(box_hi - box_lo, box_lo)
    box_pos = _div(c - box_lo, box_hi - box_lo)

    ma120_chg = _div(ma_l - _sh(ma_l, 20), _sh(ma_l, 20))
    ma120_60 = _div(ma_l - _sh(ma_l, 60), _sh(ma_l, 60))
    h250 = _roll(h, 250, 'max')
    pos250 = _div(c, h250)

    cross = (dif > dea) & (_sh(dif, 1) <= _sh(dea, 1))
    cross_recent = pd.Series(cross.astype('float64')).rolling(
        int(r['cross_win']), min_periods=1).max().to_numpy() > 0

    ma7_chg = _div(ma_s - _sh(ma_s, 5), _sh(ma_s, 5))
    ma21_chg = _div(ma_m - _sh(ma_m, 5), _sh(ma_m, 5))
    dif_chg = dif - _sh(dif, 5)
    dea_chg = dea - _sh(dea, 5)

    # MA7 上穿 MA21 之后的天数（0=今天是金叉日）
    ok = np.isfinite(ma_s) & np.isfinite(ma_m) & (ma_s > ma_m)
    idx = np.arange(c.size)
    last_bad = np.maximum.accumulate(np.where(ok, -1, idx))
    cross_age = (idx - last_bad).astype('float64')
    cross_age[~ok] = 0.0

    return {'ma_s': ma_s, 'ma_m': ma_m, 'ma_l': ma_l, 'box_hi': box_hi,
            'box_lo': box_lo, 'box_h': box_h, 'box_pos': box_pos,
            'box_top': box_top,
            'ma7_chg': ma7_chg, 'ma21_chg': ma21_chg,
            'dif_chg': dif_chg, 'dea_chg': dea_chg, 'cross_age': cross_age,
            'pct': pct, 'pct3': pct3, 'vol_mult': vol_mult, 'vol20': vol20,
            'ma60_chg': ma60_chg,
            'dif': dif, 'dea': dea, 'dif_n': dif_n, 'ma120_chg': ma120_chg,
            'ma120_60': ma120_60, 'pos250': pos250, 'cross': cross,
            'cross_recent': cross_recent,
            'v': np.asarray(v, dtype='float64')}


def mask(f, c, p):
    r = p['rule']
    m = np.isfinite(f['box_h']) & np.isfinite(f['pct3'])
    m &= f['box_h'] <= float(r['box_max_h'])
    m &= c >= f['box_top'] * (1.0 - float(r['box_tol']))
    if r.get('max_breakout') is not None:
        m &= np.isfinite(f['box_top']) & (f['box_top'] > 0) & \
            (c <= f['box_top'] * (1.0 + float(r['max_breakout'])))
    if float(r.get('min_pct') or 0) > 0:
        m &= f['pct'] >= float(r['min_pct'])
    if r.get('min_pct3') is not None:
        m &= f['pct3'] >= float(r['min_pct3'])
    m &= np.isfinite(f['vol_mult']) & (f['vol_mult'] >= float(r['min_vol']))
    if r.get('ma60_min') is not None:
        m &= np.isfinite(f['ma60_chg']) & \
            (f['ma60_chg'] >= float(r['ma60_min']))
    m &= np.isfinite(f['ma120_chg']) & (f['ma120_chg'] >= float(r['ma120_min']))
    m &= c >= f['ma_l'] * float(r['ma120_ref'])
    return m


def para_mask(f, c, r):
    """抛物线/上抛（v0.4）：MA7、MA21、DIF、DEA 同时向上，收盘在均线之上。

    用户口径（2021-08-03 陡峭版、2024-02-28 平缓版都算）：
      * MA7 与 MA21 的 5 日变化都 >= 0（均线上抛）
      * DIF > DEA，且 DIF、DEA 的 5 日变化都 >= 0（MACD 上抛）
      * 收盘 > MA21（允许在 MA7 之下，2024-02-28 就是）
      * MA7 上穿 MA21 后的 20 日内（抓启动，不抓趋势中段）
    """
    m = np.isfinite(f['ma7_chg']) & np.isfinite(f['ma21_chg'])
    m &= (f['ma7_chg'] >= float(r.get('para_ma7_min', 0.0)))
    m &= (f['ma21_chg'] >= float(r.get('para_ma21_min', 0.0)))
    m &= (f['dif'] > f['dea'])
    m &= (f['dif_chg'] >= float(r.get('para_dif_min', 0.0)))
    m &= (f['dea_chg'] >= float(r.get('para_dea_min', 0.0)))
    m &= c > f['ma_m']
    if r.get('para_cross_max') is not None:
        m &= f['cross_age'] <= int(r['para_cross_max'])
        m &= f['cross_age'] >= 1
    return m


def full_mask(f, c, p):
    r = p['rule']
    if r.get('use_para', False):
        return para_mask(f, c, r)
    m = mask(f, c, p)
    m &= np.isfinite(f['pos250']) & (f['pos250'] >= float(r['pos250_min']))
    if r.get('ma120_60min') is not None:
        m &= np.isfinite(f['ma120_60']) & \
            (f['ma120_60'] >= float(r['ma120_60min']))
    m &= (f['dif'] > f['dea']) & (f['dif_n'] >= -float(r['macd_neg']))
    if r.get('require_cross', True):
        m &= f['cross_recent']
    return m


def score_at(f, i, p):
    """0~100：平台越窄、突破越干脆、量能越大、离前高越近，分越高。"""
    r = p['rule']
    s_box = 1.0 - min(f['box_h'][i] / float(r['box_max_h']), 1.0)
    s_pos = np.clip(f['box_pos'][i], 0, 1)
    s_vol = np.clip((f['vol_mult'][i] - float(r['min_vol'])) /
                    max(3.0 - float(r['min_vol']), 1e-9), 0, 1)
    s_high = np.clip((f['pos250'][i] - 0.6) / 0.4, 0, 1)
    return 100.0 * (0.35 * s_box + 0.20 * s_pos + 0.30 * s_vol
                    + 0.15 * s_high)


# ── 数据与选股过滤 ───────────────────────────────────────────────────────
def _limit_pct(code):
    code = str(code)
    if code[:1] in ('4', '8'):
        return 0.295
    return 0.195 if code[:2] in ('30', '68') else 0.095


def _is_st(name):
    s = str(name)
    return ('ST' in s.upper()) or ('退' in s)


def _raw_price(arr, i):
    v = float(arr['volume'][i])
    a = float(arr['amount'][i])
    if np.isfinite(a) and np.isfinite(v) and v > 0 and a > 0:
        return a / v
    return float(arr['close'][i])


def _pick_ok(code, name, close, pct, amount, pick):
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


def load_px(universe='all', codes=''):
    if universe == 'hs300':
        codes_, upd = U.get_hs300_codes()
        print(f'[池] 沪深300 {len(codes_)} 只（快照 {upd}）')
    elif universe == 'coal':
        codes_, upd = U.get_coal_codes()
        print(f'[池] 动力煤 {len(codes_)} 只（快照 {upd}）')
    else:
        codes_ = None
        print('[池] 全市场（parquet 缓存）')
    if codes:
        extra = [x.strip().zfill(6) for x in codes.split(',') if x.strip()]
        codes_ = sorted(set((codes_ or []) + extra)) if codes_ else extra
    px = pd.read_parquet(PX_CACHE) if codes_ is None else \
        D.load_daily(codes=codes_, verbose=False)
    if 'amount' not in px.columns:
        px['amount'] = px['close_price'] * px['volume']
    px = px[['stock_code', 'stock_name', 'trade_date', 'open_price',
             'high_price', 'low_price', 'close_price', 'volume',
             'amount']].copy()
    px['stock_code'] = px['stock_code'].astype(str).str.zfill(6)
    px['trade_date'] = pd.to_datetime(px['trade_date'])
    return px.sort_values(['stock_code', 'trade_date'], ignore_index=True)


def _arr(g):
    return {'open': g['open_price'].to_numpy('float64'),
            'high': g['high_price'].to_numpy('float64'),
            'low': g['low_price'].to_numpy('float64'),
            'close': g['close_price'].to_numpy('float64'),
            'volume': g['volume'].to_numpy('float64'),
            'amount': g['amount'].to_numpy('float64')}


def _record(code, name, ts, arr, f, i, sc, j=None):
    box_hi = float(f['box_top'][i])
    amt = float(arr['amount'][i])
    pct = float(f['pct'][i])
    k = i if j is None else j
    rec = {'代码': code, '名称': name,
           '信号日': pd.Timestamp(ts[k]),
           '突破日': pd.Timestamp(ts[i]),
           '收盘': round(float(arr['close'][i]), 3),
           '现价': round(_raw_price(arr, i), 2),
           '涨幅%': round(pct * 100, 2),
           '3日累计%': round(float(f['pct3'][i]) * 100, 2)
           if np.isfinite(f['pct3'][i]) else np.nan,
           '量比': round(float(f['vol_mult'][i]), 2),
            '平台振幅%': round(float(f['box_h'][i]) * 100, 2),
            '平台高点': round(box_hi, 3),
            '突破幅度%': round((float(arr['close'][i]) / box_hi - 1) * 100, 2)
            if box_hi > 0 else np.nan,
            '平台位置%': round(float(f['box_pos'][i]) * 100, 1),
            'MA7': round(float(f['ma_s'][i]), 3),
            'MA21': round(float(f['ma_m'][i]), 3),
            'MA120': round(float(f['ma_l'][i]), 3),
            'MA120斜率%': round(float(f['ma120_chg'][i]) * 100, 2)
            if np.isfinite(f['ma120_chg'][i]) else np.nan,
            'MA120_60日%': round(float(f['ma120_60'][i]) * 100, 2)
            if np.isfinite(f['ma120_60'][i]) else np.nan,
            '距250高%': round((float(f['pos250'][i]) - 1) * 100, 1)
            if np.isfinite(f['pos250'][i]) else np.nan,
            'DIF': round(float(f['dif'][i]), 3),
            'DEA': round(float(f['dea'][i]), 3),
            'DIF/C%': round(float(f['dif_n'][i]) * 100, 2)
            if np.isfinite(f['dif_n'][i]) else np.nan,
            '成交额(亿)': round(amt / 1e8, 2) if np.isfinite(amt) else np.nan,
            '是否涨停': 'Y' if pct >= _limit_pct(code) - 0.005 else '',
            'ST': 'Y' if _is_st(name) else '',
            'score': round(float(sc), 1),
            '信号价(次日开盘)': round(float(arr['open'][k + 1]), 3)
            if k + 1 < arr['open'].size else np.nan}
    return rec


def scan(px, cfg, start, end, pick=None, verbose=True):
    r = cfg['rule']
    p = dict(cfg)
    pick = pick or {}
    cd = int(r.get('cooldown_days', 0))
    lo = np.datetime64(pd.Timestamp(start))
    hi = np.datetime64(pd.Timestamp(end))
    rows, t0 = [], time.time()
    for k, (code, g) in enumerate(px.groupby('stock_code', sort=False), 1):
        if len(g) < int(cfg['data']['min_bars']):
            continue
        arr = _arr(g)
        f = features(arr['close'], arr['high'], arr['low'], arr['volume'], cfg)
        ts = g['trade_date'].to_numpy()
        m = (ts >= lo) & (ts <= hi) & full_mask(f, arr['close'], cfg)
        last, name = -10 ** 9, g['stock_name'].iloc[-1]
        confirm = int(r.get('confirm_days') or 0)
        for i in np.flatnonzero(m):
            if i - last < cd:
                continue
            j = None
            if confirm:                      # 突破后 N 日站稳才确认
                j = int(i) + confirm
                if j >= len(arr['close']):
                    continue
                if not np.all(arr['close'][i + 1:j + 1] >= arr['close'][i]):
                    continue
            if not _pick_ok(code, name, _raw_price(arr, i), f['pct'][int(i)],
                            arr['amount'][int(i)], pick):
                continue
            last = int(i) if j is None else int(j)
            rows.append(_record(code, name, ts, arr, f,
                                int(i), score_at(f, int(i), cfg), j=j))
        if verbose and k % 1500 == 0:
            print(f'  ... {k} 只，{time.time() - t0:.0f}s')
    df = pd.DataFrame(rows)
    if len(df):
        df = df.sort_values(['信号日', 'score'], ascending=[False, False]) \
            .reset_index(drop=True)
    return df


def watch(px, cfg, day, pick=None):
    """平台中、尚未突破的票（提前埋伏）。"""
    r = cfg['rule']
    p = dict(cfg)
    pick = pick or {}
    cross_col = f'金叉{int(r.get("cross_win", 5))}日内'
    day64 = np.datetime64(pd.Timestamp(day))
    rows = []
    for code, g in px.groupby('stock_code', sort=False):
        if len(g) < int(cfg['data']['min_bars']):
            continue
        ts = g['trade_date'].to_numpy()
        if ts[-1] != day64:
            continue
        arr = _arr(g)
        f = features(arr['close'], arr['high'], arr['low'], arr['volume'], cfg)
        i = len(g) - 1
        if not (np.isfinite(f['box_h'][i]) and f['box_h'][i] <= float(r['box_max_h'])):
            continue
        c = float(arr['close'][i])
        if c >= f['box_top'][i] * (1 - float(r['box_tol'])):
            continue
        if c < f['ma_l'][i] * float(r['ma120_ref']):
            continue
        if not (np.isfinite(f['ma120_chg'][i]) and
                f['ma120_chg'][i] >= float(r['ma120_min'])):
            continue
        if r.get('ma120_60min') is not None and \
                not (np.isfinite(f['ma120_60'][i]) and
                     f['ma120_60'][i] >= float(r['ma120_60min'])):
            continue
        if not (np.isfinite(f['pos250'][i]) and
                f['pos250'][i] >= float(r['pos250_min'])):
            continue
        if not _pick_ok(code, g['stock_name'].iloc[-1], _raw_price(arr, i),
                        f['pct'][i], arr['amount'][i], pick):
            continue
        rows.append({'代码': code, '名称': g['stock_name'].iloc[-1],
                     '日期': pd.Timestamp(ts[i]),
                     '现价': round(_raw_price(arr, i), 2),
                     '平台振幅%': round(float(f['box_h'][i]) * 100, 2),
                     '平台位置%': round(float(f['box_pos'][i]) * 100, 1),
                     '距平台高点%': round((c / f['box_top'][i] - 1) * 100, 2),
                     '距250高%': round((float(f['pos250'][i]) - 1) * 100, 1),
                     'MA120斜率%': round(float(f['ma120_chg'][i]) * 100, 2),
                     'DIF/C%': round(float(f['dif_n'][i]) * 100, 2),
                     cross_col: 'Y' if f['cross_recent'][i] else '',
                     '成交额(亿)': round(float(arr['amount'][i]) / 1e8, 2),
                     'ST': 'Y' if _is_st(g['stock_name'].iloc[-1]) else ''})
    df = pd.DataFrame(rows)
    if len(df):
        df = df.sort_values([cross_col, '平台振幅%'],
                            ascending=[False, True]).reset_index(drop=True)
    return df


def _notes(cfg, a, asof):
    r = cfg['rule']
    pk = cfg.get('pick') or {}
    items = [
        ('策略', 'LAUNCH：上涨途中平台整理 → 放量突破平台上沿（“上抛”）'),
        ('模式', '观察池（平台中、未突破）' if a.watch else '突破日清单'),
        ('观察日', str(pd.Timestamp(asof).date())),
        ('平台', f'近 {int(r["box_win"])} 日（不含当日）振幅 '
                 f'(maxH-minL)/minL <= {r["box_max_h"] * 100:.0f}%'),
        ('突破', (f'收盘 >= 平台高点×(1-{r["box_tol"] * 100:.1f}%) 且 '
                  f'涨幅 >= {r["min_pct"] * 100:.0f}% 且 '
                  f'量比 >= {r["min_vol"]:.1f}（/前 5 日均量）'
                  + (f'，突破后连续 {int(r["confirm_days"])} 日收盘不低于突破日'
                     if r.get('confirm_days') else ''))
                 if not a.watch else
                 f'收盘 < 平台高点×(1-{r["box_tol"] * 100:.1f}%)（尚未突破）'),
        ('趋势', f'MA120 20 日变化 >= {r["ma120_min"] * 100:.1f}%；'
                 + (f'60 日变化 >= {float(r["ma120_60min"]) * 100:.0f}%；'
                    if r.get('ma120_60min') is not None else '')
                 + (f'MA60 20 日 >= {float(r["ma60_min"]) * 100:.0f}%；'
                    if r.get('ma60_min') is not None else '')
                 + f'收盘 >= MA120×{r["ma120_ref"]:.2f}；'
                   f'收盘 >= 250 日最高×{r["pos250_min"]:.2f}'),
        ('MACD', f'DIF>DEA 且 DIF/C >= -{r["macd_neg"] * 100:.0f}%'
                 + (f'，且 {int(r["cross_win"])} 日内出现金叉'
                    if r.get('require_cross', True) else '')),
        ('选股过滤', f'价格>={float(pk.get("min_price") or 0):.2f} 元；'
                     f'成交额>={float(pk.get("min_amount") or 0):.2f} 亿；'
                     f'剔ST={"开" if pk.get("exclude_st") else "关"}；'
                     f'剔涨停={"开" if pk.get("exclude_limit") else "关"}'),
        ('排序', '信号日降序、score 降序'),
        ('数据口径', '后复权日线；比例不受复权影响；`现价` 为成交额/成交量'),
        ('未来函数控制', 'T 日收盘确认，只用 T 日及以前数据；不是收益预测'),
    ]
    return pd.DataFrame(items, columns=['项目', '说明'])


def _post_format(out, detail_sheet='形态明细'):
    """代码列强制文本（保留 000400 这类前导零）+ 全表自动筛选。"""
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


def _write(out, sheets, cfg, a, end):
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
        print(f'[警告] {out} 被占用，改写 {alt}')
        return alt


def _parse_args():
    p = argparse.ArgumentParser(description='LAUNCH 平台→上抛 日线扫描')
    p.add_argument('--universe', default='all', choices=['all', 'hs300', 'coal'])
    p.add_argument('--codes', default='')
    p.add_argument('--date', default='')
    p.add_argument('--start', default='')
    p.add_argument('--recent', type=int, default=10)
    p.add_argument('--box-win', type=int, default=None)
    p.add_argument('--box-max-h', type=float, default=None)
    p.add_argument('--min-pct', type=float, default=None)
    p.add_argument('--min-pct3', type=float, default=None,
                   help='突破日近 3 日累计涨幅下限（默认 0.04）')
    p.add_argument('--ma60-min', type=float, default=None,
                   help='MA60 20 日变化下限（默认 0）')
    p.add_argument('--confirm', type=int, default=None,
                   help='突破后连续 N 日收盘不低于突破日才确认（默认 3，0=关闭）')
    p.add_argument('--min-vol', type=float, default=None)
    p.add_argument('--pos250-min', type=float, default=None)
    p.add_argument('--ma120-60min', type=float, default=None,
                   help='MA120 60 日涨幅下限（默认 0.03）')
    p.add_argument('--no-cross', action='store_true')
    p.add_argument('--cross-win', type=int, default=None,
                   help='金叉须发生在最近 N 日内（默认 20）')
    p.add_argument('--watch', action='store_true')
    p.add_argument('--min-price', type=float, default=None)
    p.add_argument('--min-amount', type=float, default=None)
    p.add_argument('--exclude-st', action='store_true')
    p.add_argument('--exclude-limit', action='store_true')
    p.add_argument('--top', type=int, default=40)
    p.add_argument('--out', default='')
    p.add_argument('--case', default='')
    p.add_argument('--case-date', default='')
    p.add_argument('--case-span', type=int, default=12)
    return p.parse_args()


def main():
    a = _parse_args()
    cfg = load_config()
    r = cfg['rule']
    if a.box_win is not None:
        r['box_win'] = int(a.box_win)
    if a.box_max_h is not None:
        r['box_max_h'] = float(a.box_max_h)
    if a.min_pct is not None:
        r['min_pct'] = float(a.min_pct)
    if a.min_pct3 is not None:
        r['min_pct3'] = float(a.min_pct3)
    if a.ma60_min is not None:
        r['ma60_min'] = float(a.ma60_min)
    if a.confirm is not None:
        r['confirm_days'] = int(a.confirm)
    if a.min_vol is not None:
        r['min_vol'] = float(a.min_vol)
    if a.pos250_min is not None:
        r['pos250_min'] = float(a.pos250_min)
    if a.ma120_60min is not None:
        r['ma120_60min'] = float(a.ma120_60min)
    if a.no_cross:
        r['require_cross'] = False
    if a.cross_win is not None:
        r['cross_win'] = int(a.cross_win)
    pk = cfg['pick']
    if a.min_price is not None:
        pk['min_price'] = float(a.min_price)
    if a.min_amount is not None:
        pk['min_amount'] = float(a.min_amount)
    if a.exclude_st:
        pk['exclude_st'] = True
    if a.exclude_limit:
        pk['exclude_limit'] = True

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    px = load_px(a.universe, a.codes)

    if a.case:
        _case(px, cfg, a.case, a.case_date or str(px['trade_date'].max().date()),
              a.case_span)
        return

    asof = pd.Timestamp(a.date) if a.date else pd.Timestamp(px['trade_date'].max())
    dates = np.sort(px.loc[px['trade_date'] <= asof, 'trade_date'].unique())
    end = pd.Timestamp(dates[-1])
    start = pd.Timestamp(a.start) if a.start else \
        pd.Timestamp(dates[max(0, len(dates) - int(a.recent))])

    if a.watch:
        df = watch(px, cfg, end, pick=pk)
        print(f'[观察池] {end.date()} → {len(df)} 只')
        out = a.out or os.path.join(
            OUTPUT_DIR, f'launch_watch_{end:%Y%m%d}.xlsx')
        out = _write(out, [('观察池', df if len(df) else pd.DataFrame(columns=['代码']))],
                     cfg, a, end)
        _post_format(out, detail_sheet='观察池')
        print(f'[输出] {out}')
        if len(df) and a.top:
            print(df.head(int(a.top)).to_string(index=False))
        return

    print(f'[扫描] {start.date()} ~ {end.date()} | 平台<='
          f'{r["box_max_h"] * 100:.0f}% 涨幅>={r["min_pct"] * 100:.0f}% '
          f'量比>={r["min_vol"]:.1f} '
          f'MA120_60日>={float(r["ma120_60min"] or 0) * 100:.0f}%')
    df = scan(px, cfg, start, end, pick=pk)
    print(f'[结果] {len(df)} 条')
    out = a.out or os.path.join(OUTPUT_DIR, f'launch_scan_{end:%Y%m%d}.xlsx')
    out = _write(out, [('形态明细', df if len(df) else pd.DataFrame(columns=['代码']))],
                 cfg, a, end)
    _post_format(out)
    print(f'[输出] {out}')
    if len(df) and a.top:
        cols = ['代码', '名称', '信号日', '现价', '涨幅%', '量比', '平台振幅%',
                '突破幅度%', '距250高%', 'MA120斜率%', 'MA120_60日%', 'DIF/C%',
                '成交额(亿)', 'score']
        print(df[cols].head(int(a.top)).to_string(index=False))


def _case(px, cfg, code, day, span):
    g = px[px['stock_code'] == code.zfill(6)].sort_values('trade_date')
    if not len(g):
        print(f'[复核] {code} 不在缓存')
        return
    arr = _arr(g)
    f = features(arr['close'], arr['high'], arr['low'], arr['volume'], cfg)
    ts = g['trade_date'].to_numpy()
    pos = np.flatnonzero(ts == np.datetime64(pd.Timestamp(day)))
    if not len(pos):
        print(f'[复核] {code} 在 {day} 无交易')
        return
    i = int(pos[0])
    print(f'--- {code} {g["stock_name"].iloc[-1]} {day} （后复权）---')
    print(f'  平台振幅={f["box_h"][i] * 100:.2f}%  平台高点={f["box_top"][i]:.2f}  '
          f'突破幅度={(arr["close"][i] / f["box_top"][i] - 1) * 100:.2f}%')
    print(f'  涨幅={f["pct"][i] * 100:.2f}%  量比={f["vol_mult"][i]:.2f}  '
          f'平台位置={f["box_pos"][i] * 100:.0f}%  距250高={(f["pos250"][i] - 1) * 100:.1f}%')
    print(f'  MA120斜率={f["ma120_chg"][i] * 100:.2f}%  '
          f'MA120_60日={f["ma120_60"][i] * 100:.2f}%  '
          f'DIF={f["dif"][i]:.3f}  DEA={f["dea"][i]:.3f}  '
          f'DIF/C={f["dif_n"][i] * 100:.2f}%  '
          f'近期金叉={"Y" if f["cross_recent"][i] else "N"}')
    print(f'  信号判定={"通过" if full_mask(f, arr["close"], cfg)[i] else "不通过"}')
    a, b = max(0, i - span), min(len(ts), i + span + 1)
    tab = pd.DataFrame({
        '日期': pd.to_datetime(ts[a:b]).date,
        '收盘': arr['close'][a:b].round(2),
        '涨幅%': (f['pct'][a:b] * 100).round(2),
        '平台振幅%': (f['box_h'][a:b] * 100).round(2),
        '平台位置%': (f['box_pos'][a:b] * 100).round(0),
        '量比': f['vol_mult'][a:b].round(2),
        'DIF': f['dif'][a:b].round(2), 'DEA': f['dea'][a:b].round(2),
        '信号': np.where(full_mask(f, arr['close'], cfg)[a:b], 'Y', ''),
    })
    print(tab.to_string(index=False))


if __name__ == '__main__':
    main()
