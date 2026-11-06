# -*- coding: utf-8 -*-
"""
==============================================================================
coil.evaluate —— 事件研究与统计检验（独立实现，只读缓存，不跑旧回测引擎）
==============================================================================

口径约定（与 doc/优化记录.md 的前视偏差教训对齐）：

* 信号在 t 日收盘后确认，**可执行入场价 = t+1 日开盘**；同时给出
  close→close 的口径用于与「旧记录」对比，两套数都报，不看单一套。
* alpha = 个股区间收益 − 沪深300 同期收益 − 成本（默认双边 0.002）。
* t 值 = mean(alpha) / (std(alpha, ddof=1) / sqrt(n))，不做任何平滑。
* 一字板剔除：t+1 开盘相对 t 收盘涨幅 ≥ 9.5%（主板）/19.5%（创业、科创）
  记为不可成交，open_open 口径下剔除。
* 基准三组：同日随机抽样 / 仅价格突破（关闭蓄势与 MACD 条件）/
  旧 resonance.entry（可选，惰性 import，仅用于对照）。
"""
import os

import numpy as np
import pandas as pd

from . import settings as CS


# ═════════════════════════════════════════════════════════════════════════
# 价格矩阵与指数
# ═════════════════════════════════════════════════════════════════════════
def build_matrices(px):
    """把长表转成日期×代码矩阵（float32，全市场约 2×36MB）。"""
    pxc = px.pivot(index='trade_date', columns='stock_code',
                   values='close_price').sort_index()
    pxo = px.pivot(index='trade_date', columns='stock_code',
                   values='open_price').sort_index().reindex(
                       index=pxc.index, columns=pxc.columns)
    dates = pd.DatetimeIndex(pxc.index)
    return {'close': pxc.to_numpy('float32'),
            'open': pxo.to_numpy('float32'),
            'dates': dates, 'codes': np.asarray(pxc.columns),
            'date_pos': {d: i for i, d in enumerate(dates)},
            'code_pos': {c: i for i, c in enumerate(pxc.columns)}}


def load_index():
    """沪深300 收盘序列（与 mlr.settings.IDX_PKL 同一文件）。"""
    if not CS.IDX_PKL or not os.path.isfile(CS.IDX_PKL):
        return None
    d, c = pd.read_pickle(CS.IDX_PKL)
    idx = pd.Series(np.asarray(c, dtype='float64'),
                    index=pd.to_datetime(pd.Series(d).astype(str),
                                         format='%Y%m%d'))
    return idx[~idx.index.duplicated(keep='last')].sort_index()


def _bench_forward(index, mat, h):
    """基准 h 日收益，按个股矩阵的日期轴对齐（缺失日 ffill）。"""
    if index is None:
        return np.zeros(len(mat['dates']))
    s = index.reindex(mat['dates']).ffill()
    a = s.to_numpy('float64')
    out = np.full(a.size, np.nan)
    if h < a.size:
        out[:-h] = a[h:] / a[:-h] - 1.0
    return out



# ═════════════════════════════════════════════════════════════════════════
# 事件研究
# ═════════════════════════════════════════════════════════════════════════
def _limit_pct(codes):
    c = np.asarray(codes).astype(str)
    return np.where(np.char.startswith(c, '30') | np.char.startswith(c, '68'),
                    0.195, 0.095)


def event_study(mat, index, signals, horizons=(5, 10, 20, 40, 60),
                cost=0.002):
    """给每条信号补上 h 日收益 / alpha / 可成交标记。

    Returns:
        detail DataFrame：原信号列 + 每档 ``r{h}``、``a{h}``、``r{h}_oo``、
        ``a{h}_oo``，以及 ``untradable``。
    """
    if not len(signals):
        return pd.DataFrame()
    sig = signals.copy()
    sig['date'] = pd.to_datetime(sig['date'])
    rows = np.array([mat['date_pos'].get(d, -1) for d in sig['date']])
    cols = np.array([mat['code_pos'].get(str(c).zfill(6), -1)
                     for c in sig['code']])
    keep = (rows >= 0) & (cols >= 0)
    sig, rows, cols = sig.loc[keep].reset_index(drop=True), rows[keep], cols[keep]
    if not len(sig):
        return sig
    close, opn = mat['close'], mat['open']
    n = close.shape[0]
    valid = rows + 1 + max(horizons) < n
    sig = sig.loc[valid].reset_index(drop=True)
    rows, cols = rows[valid], cols[valid]

    # 一字板 / 大幅高开：t+1 开盘即接近涨停 → 视为不可成交
    nxt = opn[rows + 1, cols]
    prev = close[rows, cols]
    with np.errstate(invalid='ignore', divide='ignore'):
        gap = nxt / prev - 1.0
    sig['untradable'] = gap >= (_limit_pct(sig['code']) - 0.005)

    for h in horizons:
        # A. 收盘→收盘（与旧记录同口径，便于横向对比）
        r = close[rows + h, cols] / prev - 1.0
        b = _bench_forward(index, mat, h)[rows]
        sig[f'r{h}'] = r
        sig[f'a{h}'] = r - b - cost
        # B. 次日开盘→h 日后开盘（可执行口径）
        e = opn[rows + 1, cols]
        x = opn[rows + 1 + h, cols]
        ro = x / e - 1.0
        sig[f'r{h}_oo'] = ro
        sig[f'a{h}_oo'] = ro - b - cost
    return sig


def _tstat(x):
    x = np.asarray(x, dtype='float64')
    x = x[np.isfinite(x)]
    if x.size < 3:
        return np.nan, np.nan
    sd = x.std(ddof=1)
    return (float(x.mean() / (sd / np.sqrt(x.size))) if sd > 0 else np.nan,
            float(x.mean()))


def summarize(detail, horizons, label='全部', oo=True):
    """按 horizon 汇总：n / 均值 / t / 胜率 / 中位数（已扣成本）。"""
    cols = ['分组', '持有期', 'n', '均值alpha', 't值', '胜率%', '中位alpha']
    if not len(detail):
        return pd.DataFrame(columns=cols)
    rows = []
    for h in horizons:
        key = f'a{h}_oo' if oo and f'a{h}_oo' in detail else f'a{h}'
        x = detail[key].to_numpy('float64') if len(detail) else np.array([])
        if oo and 'untradable' in detail and len(detail):
            x = x[~detail['untradable'].to_numpy(bool)]
        t, m = _tstat(x)
        xv = x[np.isfinite(x)]
        rows.append({'分组': label, '持有期': h, 'n': int(xv.size),
                     '均值alpha': round(m * 100, 2) if np.isfinite(m) else np.nan,
                     't值': round(t, 2) if np.isfinite(t) else np.nan,
                     '胜率%': round(float((xv > 0).mean() * 100), 1)
                     if xv.size else np.nan,
                     '中位alpha': round(float(np.median(xv) * 100), 2)
                     if xv.size else np.nan})
    return pd.DataFrame(rows)


def quintile_table(detail, horizons, key='coil_score', q=5, oo=True):
    """按评分/蓄势强度分组的 alpha 单调性检验。"""
    if not len(detail) or key not in detail:
        return pd.DataFrame()
    d = detail.copy()
    try:
        d['组'] = pd.qcut(d[key].rank(method='first'), q, labels=False) + 1
    except ValueError:
        return pd.DataFrame()
    out = []
    for g, gg in d.groupby('组'):
        s = summarize(gg, horizons, label=f'{key}=Q{int(g)}', oo=oo)
        s.insert(0, '分组变量', key)
        out.append(s)
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame()



# ═════════════════════════════════════════════════════════════════════════
# 基准构造
# ═════════════════════════════════════════════════════════════════════════
def random_signals(mat, n, start, end, seed=42, min_future=61):
    """同日随机抽样基准：随机 (日期, 股票)，要求未来有足够数据。"""
    rng = np.random.default_rng(int(seed))
    dates = mat['dates']
    pos = np.flatnonzero((dates >= pd.Timestamp(start))
                         & (dates <= pd.Timestamp(end)))
    pos = pos[pos < len(dates) - min_future]
    if not len(pos):
        return pd.DataFrame(columns=['code', 'date', 'close'])
    close = mat['close']
    picked = []
    for _ in range(int(n) * 20):
        if len(picked) >= n:
            break
        i = int(rng.choice(pos))
        j = int(rng.integers(0, close.shape[1]))
        if np.isfinite(close[i, j]) and np.isfinite(close[i + min_future, j]):
            picked.append((str(mat['codes'][j]).zfill(6), dates[i],
                           float(close[i, j])))
    return pd.DataFrame(picked, columns=['code', 'date', 'close'])


def matched_price_only(base, pool, seed=42, tol_days=5):
    """同日匹配基准：每条 COIL 信号，在价格突破池里同/邻近交易日随机取 1 条。

    这样两组的 n、日期分布、市场环境一致，t 值才可比（全体价格突破有 5 万+ 条）。
    """
    if not len(base) or not len(pool):
        return pd.DataFrame(columns=['code', 'date', 'close'])
    rng = np.random.default_rng(int(seed))
    p = pool.copy()
    p['date'] = pd.to_datetime(p['date'])
    by_date = {d: g for d, g in p.groupby('date')}
    all_dates = np.array(sorted(by_date))
    picked = []
    for d in pd.to_datetime(base['date']):
        cand = by_date.get(d)
        if cand is None:
            pos = np.searchsorted(all_dates, np.datetime64(d))
            near = [all_dates[k] for k in (pos - 1, pos, pos + 1)
                    if 0 <= k < all_dates.size]
            near = [x for x in near if abs((x - d).days) <= tol_days]
            if not near:
                continue
            cand = pd.concat([by_date[x] for x in near])
        picked.append(cand.iloc[int(rng.integers(0, len(cand)))])
    return pd.DataFrame(picked, columns=['code', 'date', 'close']).reset_index(
        drop=True)


def price_only_signals(px, cfg, start, end):
    """基准：只保留「价格突破 + 量能 + 均线排列」，关闭蓄势与 MACD 条件。"""
    from . import detect as DT
    from . import features as F
    mp = (cfg['macd']['fast'], cfg['macd']['slow'], cfg['macd']['signal'])
    mv = (cfg['ma']['short'], cfg['ma']['mid'], cfg['ma']['long'])
    rows = []
    for code, g in px.groupby('stock_code', sort=False):
        c = g['close_price'].to_numpy('float64')
        if c.size < cfg['data']['min_bars']:
            continue
        dif, dea, hist = F.macd(c, *mp)
        ms, mm, ml = F.moving_averages(c, mv)
        st = DT.state(c, g['volume'].to_numpy('float64'), cfg, mp, mv,
                      dif=dif, dea=dea, hist=hist, ma_s=ms, ma_m=mm, ma_l=ml,
                      use_coil=False, use_macd=False)
        dates = pd.to_datetime(g['trade_date']).to_numpy()
        m = (dates >= np.datetime64(pd.Timestamp(start))) \
            & (dates <= np.datetime64(pd.Timestamp(end))) & st['entry']
        for i in np.flatnonzero(m):
            rows.append({'code': code, 'date': pd.Timestamp(dates[i]),
                         'close': float(c[i]), 'score': float(st['score'][i])})
    return pd.DataFrame(rows, columns=['code', 'date', 'close', 'score'])


# ═════════════════════════════════════════════════════════════════════════
# 卖出规则对照：固定持有 vs MACD 柱连缩 N 日
# ═════════════════════════════════════════════════════════════════════════
def shrink_returns(px, signals, cfg):
    """对每条信号模拟「hist 连续缩短 N 日卖出」，返回收益与持有天数。"""
    from . import features as F
    ex = cfg['exit']
    n_shrink = int(ex['shrink_days'])
    max_hold = int(ex['max_hold'])
    cost = float(ex['cost'])
    mp = (cfg['macd']['fast'], cfg['macd']['slow'], cfg['macd']['signal'])
    out = []
    for code, g in signals.groupby('code', sort=False):
        sub = px[px['stock_code'] == str(code)].sort_values('trade_date')
        if not len(sub):
            continue
        c = sub['close_price'].to_numpy('float64')
        o = sub['open_price'].to_numpy('float64')
        hist = F.macd(c, *mp)[2]
        dpos = {d: i for i, d in enumerate(pd.to_datetime(sub['trade_date']))}
        limit = 0.195 if str(code).startswith(('30', '68')) else 0.095
        for _, r in g.iterrows():
            i = dpos.get(pd.Timestamp(r['date']))
            if i is None or i + 2 >= len(c):
                continue
            e = i + 1
            if not np.isfinite(o[e]) or o[e] <= 0:
                continue
            if o[e] / c[i] - 1.0 >= limit - 0.005:      # 一字板买不进
                continue
            run, exit_i, reason = 0, min(e + max_hold, len(c) - 1), 'max_hold'
            for j in range(e + 1, min(e + max_hold, len(c) - 1) + 1):
                run = run + 1 if (np.isfinite(hist[j])
                                  and np.isfinite(hist[j - 1])
                                  and hist[j] < hist[j - 1]) else 0
                if run >= n_shrink:
                    exit_i, reason = j, 'shrink'
                    break
            out.append({'code': str(code), 'date': pd.Timestamp(r['date']),
                        'ret': float(c[exit_i] / o[e] - 1.0 - cost),
                        'hold_days': int(exit_i - e), 'reason': reason})
    return pd.DataFrame(out)


def ma_exit_returns(px, signals, cfg, period=None, max_hold=None):
    """趋势退出：t+1 开盘买入；任一日收盘跌破 MA(period)，当日收盘卖出。

    与固定持有对照，检验「右偏收益能否被趋势规则拿住」。
    """
    from . import features as F
    ex = cfg['exit']
    period = int(period or ex.get('ma_exit_period', 21))
    max_hold = int(max_hold or ex['max_hold'])
    cost = float(ex['cost'])
    out = []
    for code, g in signals.groupby('code', sort=False):
        sub = px[px['stock_code'] == str(code)].sort_values('trade_date')
        if not len(sub):
            continue
        c = sub['close_price'].to_numpy('float64')
        o = sub['open_price'].to_numpy('float64')
        ma = F.moving_averages(c, (period,))[0]
        dpos = {d: i for i, d in enumerate(pd.to_datetime(sub['trade_date']))}
        limit = 0.195 if str(code).startswith(('30', '68')) else 0.095
        for _, r in g.iterrows():
            i = dpos.get(pd.Timestamp(r['date']))
            if i is None or i + 2 >= len(c):
                continue
            e = i + 1
            if not np.isfinite(o[e]) or o[e] <= 0:
                continue
            if o[e] / c[i] - 1.0 >= limit - 0.005:
                continue
            exit_i, reason = min(e + max_hold, len(c) - 1), 'max_hold'
            for j in range(e, min(e + max_hold, len(c) - 1) + 1):
                if np.isfinite(ma[j]) and c[j] < ma[j]:
                    exit_i, reason = j, f'close<MA{period}'
                    break
            out.append({'code': str(code), 'date': pd.Timestamp(r['date']),
                        'ret': float(c[exit_i] / o[e] - 1.0 - cost),
                        'hold_days': int(exit_i - e), 'reason': reason})
    return pd.DataFrame(out)


def by_year(detail, horizon=40, oo=True):
    """按信号年份看稳定性（样本外检验的基础表）。"""
    if not len(detail):
        return pd.DataFrame()
    d = detail.copy()
    d['年份'] = pd.to_datetime(d['date']).dt.year
    out = [summarize(g, [horizon], label=str(y), oo=oo).assign(年份=y)
           for y, g in d.groupby('年份')]
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame()

