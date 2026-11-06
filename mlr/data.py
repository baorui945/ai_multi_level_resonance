# -*- coding: utf-8 -*-
"""
==============================================================================
mlr.data —— 日线加载（复用 ai_backtest_model 数据源）+ 周/月线重采样
==============================================================================

* `load_daily`  ：从 ABM 的后复权日线表拉全市场（或指定股票）OHLC，parquet 缓存
* `resample`    ：把日线按「周(W-FRI) / 月(ME)」聚合为周线 / 月线；`period_date`
                  取该周期内最后一个真实交易日，`period_end` 保留名义周期末
                  （周五 / 自然月末），保证「周期K线在该日收盘时才成立」。
* `build_timeframes`：生成三周期长表，并给周/月行打 `is_complete`
                  （`period_end <= 数据末日`）。`resonance.build_resonance` 在
                  `data.period_mode='closed'` 时丢弃未完成周期，使实盘扫描与
                  回测使用同一状态。
"""
import os
from datetime import datetime

import numpy as np
import pandas as pd
import pymysql

from . import settings as S

PX_COLS = ('open_price', 'high_price', 'low_price', 'close_price', 'volume', 'amount')

# 月线历史缓存：日线缓存只从 2015-09 起，重采样只有 ~134 根月线，
# MACD(30,89,9) 的 EMA89 需要 178 根才收敛。ABM 的 backward_monthly
# 从 2000 年起有全市场月线，补到月线序列前面即可让 30/89/9 在 2014-11 前收敛。
MONTHLY_CACHE = os.path.join(S.CACHE_DIR, 'monthly_px.parquet')
TABLE_MONTHLY = S.ABM.TABLE_BACKWARD_MONTHLY

# 缓存命中阈值：指定 codes 时，池内覆盖率 >= 该比例即直接用缓存切片
# （个别新股 / 退市股不在缓存时跳过即可 —— 若为它们回退全量 DB 拉取，
#  会丢掉缓存里已经同步到的最新交易日）
CACHE_MIN_COVER = 0.5


# ═════════════════════════════════════════════════════════════════════════
# 日线加载（ABM 后复权表 + parquet 缓存）
# ═════════════════════════════════════════════════════════════════════════
def load_daily(codes=None, start=None, refresh=False, chunk=400, verbose=True):
    """加载后复权日线。

    Args:
        codes:   指定股票代码列表；None = 全市场。
        start:   起始日（默认 settings 的 data.start）。
        refresh: True 强制重拉并覆盖缓存。
        chunk:   分批 IN 查询的股票数（codes 非空时生效）。
    Returns:
        长表 DataFrame：stock_code, stock_name, trade_date, open_price,
        high_price, low_price, close_price, volume, amount
    """
    start = start or S.load_config()['data']['start']
    S.ensure_dirs()

    if os.path.isfile(S.PX_CACHE) and not refresh:
        px = pd.read_parquet(S.PX_CACHE)
        have = set(px['stock_code'].unique())
        if codes is None:
            if verbose:
                print(f'[日线] 命中缓存 {S.PX_CACHE}  '
                      f'{len(px):,} 行 / {len(have)} 只')
            return px
        codes = sorted(set(codes))
        hit = [c for c in codes if c in have]
        if hit and len(hit) >= CACHE_MIN_COVER * len(codes):
            if verbose:
                miss = len(codes) - len(hit)
                note = (f'（池内 {len(codes)} 只：命中 {len(hit)}，'
                        f'缺数据跳过 {miss}）' if miss else '')
                print(f'[日线] 命中缓存 {S.PX_CACHE}  '
                      f'{len(px):,} 行 / {len(have)} 只{note}')
            return px[px['stock_code'].isin(set(hit))]

    table = S.TABLE_DAILY
    cn = pymysql.connect(**S.ABM.get_db_config(),
                         cursorclass=pymysql.cursors.DictCursor)
    cur = cn.cursor()
    sel = ('SELECT stock_code, stock_name, trade_date, open_price, high_price, '
           'low_price, close_price, volume, amount FROM ')
    frames, t0 = [], datetime.now()
    if codes is None:
        cur.execute(sel + f'{table} WHERE trade_date >= %s', (start,))
        frames.append(pd.DataFrame(cur.fetchall()))
    else:
        codes = sorted(set(codes))
        q = sel + f'{table} WHERE trade_date >= %s AND stock_code IN ({{}})'
        nblk = (len(codes) - 1) // chunk + 1
        for i in range(0, len(codes), chunk):
            ch = codes[i:i + chunk]
            cur.execute(q.format(','.join(['%s'] * len(ch))), [start] + ch)
            f = pd.DataFrame(cur.fetchall())
            if not f.empty:
                frames.append(f)
            if verbose:
                print(f'  [日线] 块 {i // chunk + 1}/{nblk}  '
                      f'累计 {sum(len(x) for x in frames):,}', flush=True)
    cn.close()

    if not frames:
        raise RuntimeError(f'未从 {table} 拉到任何日线数据（start={start}）')

    px = pd.concat(frames, ignore_index=True)
    px['stock_code'] = px['stock_code'].astype(str).str.zfill(6)
    px['trade_date'] = pd.to_datetime(px['trade_date'])
    for c in PX_COLS:
        px[c] = pd.to_numeric(px[c], errors='coerce')
    px = px.dropna(subset=['close_price'])
    px = px.sort_values(['stock_code', 'trade_date']).reset_index(drop=True)

    # 缓存只在全市场拉取时落盘（子集拉取会截断缓存）
    if codes is None:
        px.to_parquet(S.PX_CACHE, index=False)
        print(f'[日线] 已缓存 {S.PX_CACHE}  {len(px):,} 行 / '
              f'{px["stock_code"].nunique()} 只  '
              f'用时 {(datetime.now() - t0).seconds}s')
    return px


# ═════════════════════════════════════════════════════════════════════════
# 月线历史（backward_monthly，用于月线 MACD 暖机）
# ═════════════════════════════════════════════════════════════════════════
def load_monthly_history(codes=None, start=None, refresh=False, verbose=True):
    """加载 ABM 后复权月线（`backward_monthly`，2000 起），供月线指标暖机。

    日线缓存从 2015-09 起，重采样只能得到 ~134 根月线；MACD(30,89,9) 的
    EMA89 需要 178 根才收敛（y0=x0，残差 <2.3%）。把该表 2015-09 之前的
    月线拼到序列前面，30/89/9 就能在 2014-11 前收敛，回测从 2020 起可用。

    Args:
        codes: 指定股票；None = 全市场（会写缓存）。
        start: 起始月（默认 2000-01-01）。
        refresh: 强制重拉。
    Returns:
        长表：stock_code, stock_name, trade_date, OHLC/volume/amount
    """
    start = pd.Timestamp(start or '2000-01-01')
    S.ensure_dirs()
    if os.path.isfile(MONTHLY_CACHE) and not refresh:
        px = pd.read_parquet(MONTHLY_CACHE)
        have = set(px['stock_code'].unique())
        if codes is None:
            if verbose:
                print(f'[月线] 命中缓存 {MONTHLY_CACHE}  {len(px):,} 行 / '
                      f'{len(have)} 只')
            return px[px['trade_date'] >= start]
        codes = sorted({str(c).zfill(6) for c in codes})
        hit = [c for c in codes if c in have]
        if hit:
            if verbose:
                print(f'[月线] 命中缓存 {MONTHLY_CACHE}  {len(px):,} 行 / '
                      f'{len(have)} 只（池内 {len(codes)}：命中 {len(hit)}）')
            return px[(px['stock_code'].isin(hit))
                      & (px['trade_date'] >= start)]

    table = TABLE_MONTHLY
    cn = pymysql.connect(**S.ABM.get_db_config(),
                         cursorclass=pymysql.cursors.DictCursor)
    cur = cn.cursor()
    sel = ('SELECT stock_code, stock_name, trade_date, open_price, high_price, '
           'low_price, close_price, volume, amount FROM ')
    frames = []
    if codes is None:
        cur.execute(sel + f'`{table}` WHERE trade_date >= %s',
                    (start.strftime('%Y-%m-%d'),))
        frames.append(pd.DataFrame(cur.fetchall()))
    else:
        codes = sorted({str(c).zfill(6) for c in codes})
        q = (sel + f'`{table}` WHERE trade_date >= %s '
             f'AND stock_code IN ({{}})')
        for i in range(0, len(codes), 400):
            ch = codes[i:i + 400]
            cur.execute(q.format(','.join(['%s'] * len(ch))),
                        [start.strftime('%Y-%m-%d')] + ch)
            f = pd.DataFrame(cur.fetchall())
            if len(f):
                frames.append(f)
    cn.close()

    if not frames:
        raise RuntimeError(f'未从 {table} 拉到任何月线数据（start={start.date()}）')
    px = pd.concat(frames, ignore_index=True)
    px['stock_code'] = px['stock_code'].astype(str).str.zfill(6)
    px['trade_date'] = pd.to_datetime(px['trade_date'])
    for c in PX_COLS:
        px[c] = pd.to_numeric(px[c], errors='coerce')
    px = (px.dropna(subset=['close_price'])
            .sort_values(['stock_code', 'trade_date'])
            .reset_index(drop=True))
    if codes is None:
        px.to_parquet(MONTHLY_CACHE, index=False)
        if verbose:
            print(f'[月线] 已缓存 {MONTHLY_CACHE}  {len(px):,} 行 / '
                  f'{px["stock_code"].nunique()} 只')
    return px


def _extend_monthly(m_res, ext, asof):
    """把 `backward_monthly` 的旧月份拼到日线重采样的月线前面。

    两个表都是后复权，但基准日不同（比值近似常数、随分红缓慢漂移）。
    用重叠期（2015-09 之后）的比值中位数把旧月份缩放到日线口径，保证
    拼接处连续；旧月份只用于 EMA 暖机，不参与 2020 之后的信号。
    """
    if ext is None or not len(ext) or not len(m_res):
        return m_res
    ext = ext.copy()
    ext['month'] = ext['trade_date'].dt.to_period('M')
    ext_by = {c: g for c, g in ext.groupby('stock_code', sort=False)}
    out = []
    for code, g in m_res.groupby('stock_code', sort=False):
        g = g.copy()
        g['month'] = g['trade_date'].dt.to_period('M')
        e = ext_by.get(code)
        if e is None:
            out.append(g.drop(columns=['month']))
            continue
        first = g['month'].min()
        old = e[e['month'] < first]
        if old.empty:
            out.append(g.drop(columns=['month']))
            continue
        ov = e.merge(g[['month', 'close_price']], on='month',
                     suffixes=('', '_res'))
        ov = ov[np.isfinite(ov['close_price'])
                & np.isfinite(ov['close_price_res'])].sort_values('month')
        # 用**拼接处前 3 个月**的比值（而非全期中位数）缩放旧月份：两表后复权
        # 基准不同，比值随分红缓慢漂移；全期中位数会在拼接处留下台阶。
        head = ov.head(3)
        ratio = (float((head['close_price_res'] / head['close_price']).median())
                 if len(head) else np.nan)
        if not np.isfinite(ratio) or ratio <= 0:
            out.append(g.drop(columns=['month']))
            continue
        old = old.copy()
        for c in ('open_price', 'high_price', 'low_price', 'close_price'):
            old[c] = old[c] * ratio
        old['period_end'] = (old['month'].dt.to_timestamp(how='end')
                             .dt.normalize())
        old = old[['stock_code', 'stock_name', 'trade_date', 'period_end',
                   'open_price', 'high_price', 'low_price', 'close_price',
                   'volume', 'amount']]
        out.append(pd.concat([old, g.drop(columns=['month'])],
                             ignore_index=True))
    return pd.concat(out, ignore_index=True)


# ═════════════════════════════════════════════════════════════════════════
# 上游增量同步（backward_daily → 本地日线缓存）
# ═════════════════════════════════════════════════════════════════════════
# 上游真源表：日线后复权表的「上游」（旧表，覆盖更长历史且通常先更新到最新日）。
# 扫描用的真源表是 S.TABLE_DAILY（.env 的 TABLE_BACKWARD_DAILY，默认
# backward_tdx_daily），其数据来源于 backward_daily；故以 **backward_daily**
# 的最新交易日判断「是否有新数据」，有则增量补进本地缓存，没有则不动。
TABLE_UPSTREAM = 'backward_daily'

_SEL_COLS = ('SELECT stock_code, stock_name, trade_date, open_price, '
             'high_price, low_price, close_price, volume, amount FROM ')


def _max_trade_date(cur, table):
    """表的 MAX(trade_date)；空表返回 None。"""
    cur.execute(f'SELECT MAX(trade_date) AS d FROM `{table}`')
    r = cur.fetchone()
    return None if not r or r['d'] is None else pd.Timestamp(r['d'])


def _fetch_since(cur, table, since):
    """拉取 trade_date > since 的全市场行（长表，可能为空）。"""
    cur.execute(_SEL_COLS + f'`{table}` WHERE trade_date > %s',
                (since.strftime('%Y-%m-%d'),))
    return pd.DataFrame(cur.fetchall())


def _norm_inc(f):
    """增量行规范化：代码补零、日期转 Timestamp、数值列转 float、丢空收盘。"""
    if not len(f):
        return f
    f['stock_code'] = f['stock_code'].astype(str).str.zfill(6)
    f['trade_date'] = pd.to_datetime(f['trade_date'])
    for c in PX_COLS:
        f[c] = pd.to_numeric(f[c], errors='coerce')
    return f.dropna(subset=['close_price'])


def sync_latest_daily(upstream=None, verbose=True):
    """检查上游最新交易日，有新增则增量并入本地日线缓存（否则不动）。

    判定口径（用户要求）：上游表 `backward_daily` 的最新交易日。
    * 上游最新日 <= 缓存最新日 → **不更新**（updated=False）；
    * 上游最新日 > 缓存最新日 → 增量拉取 `trade_date > 缓存最新日` 的行并入
      缓存；同一 (stock_code, trade_date) 以真源表 `S.TABLE_DAILY`
      （backward_tdx_daily）优先，真源表尚未跟上的行回退取自上游表。

    Returns:
        dict: updated / upstream_max / cache_max / new_max / added / reason
    """
    upstream = upstream or TABLE_UPSTREAM
    out = {'updated': False, 'upstream_max': None, 'cache_max': None,
           'new_max': None, 'added': 0, 'reason': ''}
    if not os.path.isfile(S.PX_CACHE):
        out['reason'] = '本地缓存不存在，交由 load_daily 全量拉取'
        if verbose:
            print(f'[数据同步] {out["reason"]}')
        return out

    px = pd.read_parquet(S.PX_CACHE)
    d_cache = pd.Timestamp(pd.to_datetime(px['trade_date']).max())
    out['cache_max'] = d_cache

    cn = pymysql.connect(**S.ABM.get_db_config(),
                         cursorclass=pymysql.cursors.DictCursor)
    try:
        cur = cn.cursor()
        d_up = _max_trade_date(cur, upstream)
        out['upstream_max'] = d_up
        if d_up is None:
            out['reason'] = '上游表无数据'
            if verbose:
                print(f'[数据同步] 上游 {upstream} 无数据，不更新')
            return out
        if d_up <= d_cache:
            out['reason'] = '上游无新数据'
            if verbose:
                print(f'[数据同步] 上游 {upstream} 最新日 {d_up.date()} | '
                      f'本地缓存 {d_cache.date()} → 无新数据，不更新')
            return out
        if verbose:
            print(f'[数据同步] 上游 {upstream} 最新日 {d_up.date()} | '
                  f'本地缓存 {d_cache.date()} → 检测到新数据，增量拉取 '
                  f'> {d_cache.date()} ...')
        inc_new = _norm_inc(_fetch_since(cur, S.TABLE_DAILY, d_cache))
        inc_up = _norm_inc(_fetch_since(cur, upstream, d_cache))
    finally:
        cn.close()

    frames = [f for f in (inc_up, inc_new) if len(f)]
    if not frames:
        out['reason'] = '增量区间无行'
        if verbose:
            print('[数据同步] 增量区间无行，不更新')
        return out

    merged = pd.concat([px, *frames], ignore_index=True)
    merged['trade_date'] = pd.to_datetime(merged['trade_date'])
    merged = (merged.drop_duplicates(subset=['stock_code', 'trade_date'],
                                     keep='last')      # 真源表优先
                    .sort_values(['stock_code', 'trade_date'])
                    .reset_index(drop=True))
    merged = merged[list(px.columns)]
    merged.to_parquet(S.PX_CACHE, index=False)
    out.update(updated=True, added=int(len(merged) - len(px)),
               new_max=merged['trade_date'].max(), reason='已增量更新')
    if verbose:
        print(f'[数据同步] 新增 {out["added"]:,} 行 → 缓存最新日 '
              f'{pd.Timestamp(out["new_max"]).date()}  （{S.PX_CACHE}）')
    return out


# ═════════════════════════════════════════════════════════════════════════
# 周 / 月线重采样
# ═════════════════════════════════════════════════════════════════════════
def resample(daily, rule):
    """把长表日线重采样为周线 / 月线。

    Args:
        daily: `load_daily` 返回的长表（按 stock_code, trade_date 升序）。
        rule:  pandas 偏移串，如 'W-FRI' / 'ME'。
    Returns:
        长表：stock_code, stock_name, period_date（该周期最后交易日）,
        open_price, high_price, low_price, close_price, volume, amount
    """
    daily = daily.sort_values(['stock_code', 'trade_date'])
    out = []
    for code, g in daily.groupby('stock_code', sort=False):
        g = g.set_index('trade_date')
        r = g.resample(rule)
        agg = pd.DataFrame({
            'open_price': r['open_price'].first(),
            'high_price': r['high_price'].max(),
            'low_price': r['low_price'].min(),
            'close_price': r['close_price'].last(),
            'volume': r['volume'].sum(min_count=1),
            'amount': r['amount'].sum(min_count=1),
        }).dropna(subset=['close_price'])
        if agg.empty:
            continue
        # 周期标记 = 该周期内**最后一个真实交易日**（而非自然周/月末，避开长假错配）
        last_day = g.index.to_series().groupby(pd.Grouper(freq=rule)).last()
        agg['period_date'] = last_day.reindex(agg.index).to_numpy()
        # 名义周期末（周五 / 自然月末）：用于判定「该周期是否已收盘」。
        # 不能用 period_date 判（个股停牌会让 period_date 早于周期末）。
        agg['period_end'] = pd.DatetimeIndex(agg.index).to_numpy()
        agg['stock_code'] = code
        agg['stock_name'] = g['stock_name'].iloc[-1]
        out.append(agg.reset_index(drop=True))
    if not out:
        return pd.DataFrame(columns=['stock_code', 'stock_name', 'period_date',
                                     'period_end'])
    res = pd.concat(out, ignore_index=True)
    return res[['stock_code', 'stock_name', 'period_date', 'period_end',
                'open_price', 'high_price', 'low_price', 'close_price',
                'volume', 'amount']]


def build_timeframes(daily, cfg=None):
    """一次性生成 daily / weekly / monthly 三张长表（列名统一为 trade_date）。

    周/月表额外带两列：
    * `period_end`  —— 名义周期末（周五 / 自然月末）；
    * `is_complete` —— 相对**数据末日**的静态快照（period_end <= 数据末日），
      仅用于诊断；`period_mode='closed'` 的实际对齐键是 `period_end`，
      由 `resonance.build_resonance` 逐日求值（period_end <= 当日 才可用），
      保证 scan 与 backtest 使用同一状态。
    """
    cfg = cfg or S.load_config()
    asof = pd.Timestamp(daily['trade_date'].max()).normalize()
    d = daily.copy()
    w = resample(daily, cfg['data']['weekly_rule']).rename(
        columns={'period_date': 'trade_date'})
    m = resample(daily, cfg['data']['monthly_rule']).rename(
        columns={'period_date': 'trade_date'})
    # 月线补历史：日线从 2015-09 起，重采样只有 ~134 根；用 backward_monthly
    # 补到 2000 起，MACD(30,89,9) 才能在 2014-11 前收敛（否则整段 NaN）。
    if cfg['data'].get('monthly_history', False):
        try:
            ext = load_monthly_history(
                daily['stock_code'].unique(),
                start=cfg['data'].get('monthly_history_start', '2000-01-01'))
            m = _extend_monthly(m, ext, asof)
        except Exception as e:                              # noqa: BLE001
            print(f'[月线] 扩展历史失败（{e}），回落到日线重采样；'
                  f'MACD 30/89/9 会因暖机不足长期不可用')
    for tf in (w, m):
        tf['is_complete'] = (pd.to_datetime(tf['period_end'])
                             <= asof).to_numpy(bool)
    # 统一时间精度：日线缓存是 datetime64[ms]，月线拼接后可能是 [us]/[ns]，
    # pandas 3.0 的 merge_asof 要求左右键 dtype 完全一致。
    for tf in (d, w, m):
        for c in ('trade_date', 'period_end'):
            if c in tf.columns:
                tf[c] = pd.to_datetime(tf[c]).astype('datetime64[ms]')
    return {'daily': d, 'weekly': w, 'monthly': m}
