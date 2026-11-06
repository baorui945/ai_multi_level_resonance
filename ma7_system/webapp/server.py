# -*- coding: utf-8 -*-
"""
==============================================================================
webapp/server.py —— MA7 上抛扫描 Web UI 后端（stdlib，无第三方框架）
==============================================================================
接口：
    GET  /                      页面
    GET  /static/*              静态资源
    GET  /api/meta              数据日期、市值快照、可选扫描范围
    GET  /api/scan?mode=&scope= 扫描结果（up=上抛过程中，watch=上抛观察名单）
    GET  /api/kline?code=&mark= 个股近 1 年 K 线 + MA7 + MACD(30,90,9) + 段起点
"""
import json
import os
import sys
import threading
import time
import traceback
import webbrowser
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

HERE = os.path.dirname(os.path.abspath(__file__))
SYS_DIR = os.path.dirname(HERE)
ROOT = os.path.dirname(SYS_DIR)
sys.path.insert(0, ROOT)
sys.path.insert(0, SYS_DIR)
try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:                                            # noqa: BLE001
    pass

import numpy as np
import pandas as pd

from ma7daily import upthrow as UP, resample as R, resonance as RES, cycle as CYC   # noqa: E402
from mlr import data as D, universe as U, settings as MLR_S           # noqa: E402

DAY_CFG = UP.load_config(os.path.join(SYS_DIR, 'config', 'ma7_close3.json'))
WEEK_CFG = UP.load_config(os.path.join(SYS_DIR, 'config', 'ma7_week.json'))
WEEK_RULE = (WEEK_CFG.get('resample') or {}).get('rule', 'W-FRI')

SCOPES = [
    ('hs300', '沪深300'),
    ('mv1000', '市值 ≥ 1000 亿'),
    ('mv500_1000', '市值 500–1000 亿'),
    ('mv200_500', '市值 200–500 亿'),
    ('mv100_200', '市值 100–200 亿'),
    ('mv_below100', '市值 < 100 亿'),
]
SCOPE_LABEL = dict(SCOPES)

_lock = threading.Lock()
_PX = None            # 全市场日线（后复权）
_MV = {}              # code -> 总市值（元）
_MV_SNAP = ''
_ASOF = None
_FC = {}              # scope -> (daily_frame, weekly_frame)
_SC = {}              # (mode, scope) -> result

# ── 磁盘缓存：同一「数据日期 + 市值快照 + 模式 + 范围」只算一次，重启直接读 ──
CACHE_DIR = os.path.join(HERE, '.cache')


def _cache_file(mode, scope):
    tag = '%s_%s_%s_%s' % (mode, scope,
                           _ASOF.strftime('%Y%m%d') if _ASOF is not None else 'na',
                           _MV_SNAP.replace('-', '') or 'nomv')
    return os.path.join(CACHE_DIR, tag + '.json')


def _cache_get(mode, scope):
    try:
        fp = _cache_file(mode, scope)
        if os.path.isfile(fp):
            with open(fp, encoding='utf-8') as f:
                obj = json.load(f)
            obj['cached'] = True
            return obj
    except Exception as e:                                   # noqa: BLE001
        print('[cache] 读取失败:', e)
    return None


def _cache_put(mode, scope, data):
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        fp = _cache_file(mode, scope)
        tmp = fp + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, fp)
    except Exception as e:                                   # noqa: BLE001
        print('[cache] 写入失败:', e)


# ═════════════════════════════════════════════════════════════════════════
# 数据装载
# ═════════════════════════════════════════════════════════════════════════
def _load_px():
    px = pd.read_parquet(MLR_S.PX_CACHE)
    px = px[['stock_code', 'stock_name', 'trade_date', 'open_price',
             'high_price', 'low_price', 'close_price', 'volume', 'amount']].copy()
    px['stock_code'] = px['stock_code'].astype(str).str.zfill(6)
    px['trade_date'] = pd.to_datetime(px['trade_date'])
    for c in ('open_price', 'high_price', 'low_price', 'close_price'):
        px[c] = pd.to_numeric(px[c], errors='coerce').astype('float64')
    for c in ('volume', 'amount'):
        px[c] = pd.to_numeric(px[c], errors='coerce').astype('float32')
    px['stock_name'] = px['stock_name'].fillna('')
    return px.sort_values(['stock_code', 'trade_date']).reset_index(drop=True)


def _load_mv():
    global _MV, _MV_SNAP
    try:
        import pymysql
        cn = pymysql.connect(**MLR_S.ABM.get_db_config(),
                             cursorclass=pymysql.cursors.DictCursor)
        with cn.cursor() as cur:
            cur.execute('SELECT MAX(trade_date) AS d FROM factor_daily_basic')
            snap = cur.fetchone()['d']
            cur.execute('SELECT stock_code, total_mv FROM factor_daily_basic '
                        'WHERE trade_date = %s', (snap,))
            rows = cur.fetchall()
        cn.close()
        _MV = {str(r['stock_code']).zfill(6): float(r['total_mv'] or 0)
               for r in rows}
        _MV_SNAP = str(snap)
    except Exception as e:                                   # noqa: BLE001
        _MV, _MV_SNAP = {}, ''
        print('[警告] 市值数据不可用：', e)


def boot():
    global _PX, _ASOF
    t0 = time.time()
    _PX = _load_px()
    _ASOF = pd.Timestamp(_PX['trade_date'].max())
    _load_mv()
    print(f'[webapp] 日线 {len(_PX):,} 行 / {_PX.stock_code.nunique()} 只 | '
          f'最新 {_ASOF.date()} | 市值快照 {_MV_SNAP or "-"} | {time.time()-t0:.1f}s')


# ═════════════════════════════════════════════════════════════════════════
# 扫描范围
# ═════════════════════════════════════════════════════════════════════════
def _mv_ok(scope, mv):
    if not mv or mv <= 0:
        return False
    y = mv / 1e8
    if scope == 'mv1000':
        return y >= 1000
    if scope == 'mv500_1000':
        return 500 <= y < 1000
    if scope == 'mv200_500':
        return 200 <= y < 500
    if scope == 'mv100_200':
        return 100 <= y < 200
    if scope == 'mv_below100':
        return y < 100
    return False


def scope_codes(scope):
    if scope == 'hs300':
        return set(U.get_hs300_codes()[0])
    return {c for c, mv in _MV.items() if _mv_ok(scope, mv)}


def frames(scope):
    """返回 (日线 detect_frame, 周线 detect_frame)，按 scope 缓存。"""
    if scope in _FC:
        return _FC[scope]
    codes = scope_codes(scope)
    hist = _PX[_PX['stock_code'].isin(codes)].copy()
    # 计算前把价格还原成 float64（缓存里是 float32，MA7 边界会被舍入影响）
    for c in ('open_price', 'high_price', 'low_price', 'close_price'):
        hist[c] = hist[c].astype('float64')
    df = UP.detect_frame(hist, DAY_CFG)
    df['_pos'] = df.groupby('stock_code').cumcount()
    wk = R.to_weekly(hist, WEEK_RULE)
    wk = wk[wk['period_end'] <= _ASOF].reset_index(drop=True)
    wf = UP.detect_frame(wk, WEEK_CFG)
    wf['_pos'] = wf.groupby('stock_code').cumcount()
    _FC[scope] = (df, wf)
    return _FC[scope]


_WC = {}          # scope -> 周线原始 bars


def weekly_bars(scope):
    """只做重采样、不做信号判定，供周期性筛选使用（比 frames() 快很多）。"""
    if scope in _WC:
        return _WC[scope]
    codes = scope_codes(scope)
    hist = _PX[_PX['stock_code'].isin(codes)].copy()
    for c in ('open_price', 'high_price', 'low_price', 'close_price'):
        hist[c] = hist[c].astype('float64')
    wk = R.to_weekly(hist, WEEK_RULE)
    wk = wk[wk['period_end'] <= _ASOF].reset_index(drop=True)
    _WC[scope] = wk
    return wk


def _mv_yi(code):
    mv = _MV.get(code, 0)
    return round(mv / 1e8, 1) if mv else None


def _d(x):
    if x is None or pd.isna(x):
        return ''
    return pd.Timestamp(x).strftime('%Y-%m-%d')


# ═════════════════════════════════════════════════════════════════════════
# 扫描
# ═════════════════════════════════════════════════════════════════════════
def _rows_up(df, asof, today_sig, last_sig):
    d = df[df['trade_date'] == asof].copy()
    act = d[(d['seg_id'] > 0) & (d['seg_start_date'] <= asof)].copy()
    if not len(act):
        return []
    tmp = (df[['stock_code', 'trade_date', '_pos']]
           .rename(columns={'trade_date': 'seg_start_date', '_pos': 'pos0'}))
    act = act.merge(tmp, on=['stock_code', 'seg_start_date'], how='left')
    act['days'] = (act['_pos'] - act['pos0'] + 1).fillna(0).astype(int)
    out = []
    for r in act.itertuples(index=False):
        out.append(dict(
            code=r.stock_code, name=r.stock_name, mv=_mv_yi(r.stock_code),
            seg_start=_d(r.seg_start_date), days=int(r.days),
            close=round(float(r.close_price), 2), ma7=round(float(r.ma7), 2),
            dev=round((float(r.close_price) / float(r.ma7) - 1) * 100, 2),
            s5=round(float(r.s5), 3),
            sig=_d(last_sig.get(r.stock_code)),
            today=bool(r.stock_code in today_sig),
        ))
    out.sort(key=lambda x: (-x['days'], x['code']))
    return out


def _rows_watch(df, asof, c2, c1):
    out = []
    for frame, day in ((c2, 2), (c1, 1)):
        for r in frame.itertuples(index=False):
            out.append(dict(
                code=r.stock_code, name=r.stock_name, mv=_mv_yi(r.stock_code),
                start=_d(r.seg_start_date), day=int(day), mode=r.mode or 'C',
                close=round(float(r.close_price), 2), ma7=round(float(r.ma7), 2),
                dev=round((float(r.close_price) / float(r.ma7) - 1) * 100, 2),
                s5=round(float(r.s5), 3),
            ))
    out.sort(key=lambda x: (x['day'], x['code']), reverse=True)
    return out


def _scan_cycle(scope):
    """周线周期性筛选：ZigZag + MACD(30,90,9) + MA21。"""
    wk = weekly_bars(scope)
    names = (_PX.drop_duplicates('stock_code')
             .set_index('stock_code')['stock_name'].to_dict())
    rows = []
    for code, g in wk.groupby('stock_code', sort=False):
        st = CYC.stats(g)
        if not st:
            continue
        st.update(code=code, name=names.get(code, ''), mv=_mv_yi(code))
        rows.append(st)
    rows.sort(key=lambda r: (-r['score'], r['code']))
    strong = [r for r in rows if r['score'] >= 76 and r['r2'] >= 0.55]
    mid = [r for r in rows if 70 <= r['score'] < 76 and r['r2'] >= 0.50]
    res = dict(
        mode='cycle', scope=scope, scope_label=SCOPE_LABEL.get(scope, scope),
        asof=_d(pd.Timestamp(wk['trade_date'].max())), mv_snapshot=_MV_SNAP,
        unit='周',
        counts=dict(total=len(rows), up=len(strong), watch=len(mid), today=0,
                    early=sum(1 for r in strong if r.get('early'))),
        up=strong, watch=mid,
    )
    _SC[('cycle', scope)] = res
    return res


def _scan_start(scope):
    """周线启动信号：刚上穿 MA21 + MACD 柱翻红（强周期的排前面）。"""
    wk = weekly_bars(scope)
    names = (_PX.drop_duplicates('stock_code')
             .set_index('stock_code')['stock_name'].to_dict())
    rows = []
    for code, g in wk.groupby('stock_code', sort=False):
        st = CYC.stats(g)
        if not st or not (st['early'] or st['pre_start']):
            continue
        st.update(code=code, name=names.get(code, ''), mv=_mv_yi(code))
        rows.append(st)
    rows.sort(key=lambda r: (-r['score'], r['code']))
    is_strong = lambda r: r['score'] >= 76 and r['r2'] >= 0.55
    strong = [r for r in rows if is_strong(r)]
    # 其余触发信号但周期分偏低的，只保留 score≥60 的，避免刷屏
    other = [r for r in rows if not is_strong(r) and r['score'] >= 60]
    res = dict(
        mode='start', scope=scope, scope_label=SCOPE_LABEL.get(scope, scope),
        asof=_d(pd.Timestamp(wk['trade_date'].max())), mv_snapshot=_MV_SNAP,
        unit='周',
        counts=dict(total=len(rows), up=len(strong), watch=len(other), today=0,
                    early=sum(1 for r in rows if r['early'])),
        up=strong, watch=other,
    )
    _SC[('start', scope)] = res
    return res


def scan(mode, scope):
    key = (mode, scope)
    if key in _SC:
        return _SC[key]
    with _lock:
        if key in _SC:
            return _SC[key]
        cached = _cache_get(mode, scope)
        if cached is not None:
            _SC[key] = cached
            return cached
        res = _scan_compute(mode, scope)
        _cache_put(mode, scope, res)
        _SC[key] = res
        return res


def _scan_compute(mode, scope):
    if mode == 'cycle':
        return _scan_cycle(scope)
    if mode == 'start':
        return _scan_start(scope)
    df, wf = frames(scope)
    asof = _ASOF if mode != 'weekly' else pd.Timestamp(wf['trade_date'].max())
    sig = df[df['signal']]
    last_sig = (sig.groupby('stock_code')['confirm_date'].max().to_dict()
                if len(sig) else {})
    today_sig = (set(sig.loc[pd.to_datetime(sig['confirm_date']) == _ASOF,
                            'stock_code'])
                 if len(sig) else set())

    if mode == 'daily':
        up = _rows_up(df, _ASOF, today_sig, last_sig)
        conf, c2, c1 = UP.scan_day(df, _ASOF)
        watch = _rows_watch(df, _ASOF, c2, c1)
        unit = '天'
    elif mode == 'weekly':
        sigw = wf[wf['signal']]
        last_sigw = (sigw.groupby('stock_code')['confirm_date'].max().to_dict()
                     if len(sigw) else {})
        today_sigw = (set(sigw.loc[pd.to_datetime(sigw['confirm_date']) == asof,
                                   'stock_code'])
                      if len(sigw) else set())
        up = _rows_up(wf, asof, today_sigw, last_sigw)
        conf, c2, c1 = UP.scan_day(wf, asof)
        watch = _rows_watch(wf, asof, c2, c1)
        unit = '周'
    else:  # resonance
        segs = RES.weekly_segments(wf)
        seg_by = ({c: g for c, g in segs.groupby('stock_code', sort=False)}
                  if len(segs) else {})
        up0 = _rows_up(df, _ASOF, today_sig, last_sig)
        conf, c2, c1 = UP.scan_day(df, _ASOF)
        watch0 = _rows_watch(df, _ASOF, c2, c1)
        up = []
        for row in up0:
            s = RES.segment_at(seg_by.get(row['code']), _ASOF)
            if s is None:
                continue
            up.append(dict(row, wk_start=_d(s.start),
                           wk_end=('进行中' if s.ongoing else _d(s.end))))
        watch = []
        for row in watch0:
            s = RES.segment_at(seg_by.get(row['code']), _ASOF)
            if s is None:
                continue
            watch.append(dict(row, wk_start=_d(s.start),
                              wk_end=('进行中' if s.ongoing else _d(s.end))))
        unit = '天'

    n_conf = sum(1 for r in up if r['today'])
    return dict(
        mode=mode, scope=scope, scope_label=SCOPE_LABEL.get(scope, scope),
        asof=_d(asof), mv_snapshot=_MV_SNAP, unit=unit,
        counts=dict(total=int(df['stock_code'].nunique()),
                    up=len(up), watch=len(watch), today=n_conf),
        up=up, watch=watch,
    )


# ═════════════════════════════════════════════════════════════════════════
# K 线
# ═════════════════════════════════════════════════════════════════════════
def _ema(s, n):
    return s.ewm(span=n, adjust=False).mean()


def kline(code, mark='daily', days=None, full=False):
    """近 N 根 K 线：mark=daily → 日 K（250 根）；mark=weekly → 周 K（默认 60 根，full=True 取全部历史）。"""
    g = _PX[_PX['stock_code'] == code].sort_values('trade_date').copy()
    if not len(g):
        raise KeyError(code)
    for c in ('open_price', 'high_price', 'low_price', 'close_price'):
        g[c] = g[c].astype('float64')

    if mark == 'weekly':
        base = R.to_weekly(g, WEEK_RULE).reset_index(drop=True)
        # 周 K 只用已收盘周（盘中当周是半成品，不画）
        base = base[base['period_end'] <= _ASOF].reset_index(drop=True)
        n = len(base) if full else (days or 60)
        tf = 'weekly'
    else:
        base = g.reset_index(drop=True)
        n = days or 250
        tf = 'daily'

    close = base['close_price'].astype(float)
    ma7 = close.rolling(7).mean()
    ma21 = close.rolling(21).mean()
    dif = _ema(close, 30) - _ema(close, 90)
    dea = _ema(dif, 9)
    hist = (dif - dea) * 2.0

    win = base.tail(n)
    idx = win.index
    dates = win['trade_date'].dt.strftime('%Y-%m-%d').tolist()
    ohlc = [[round(float(o), 2), round(float(c), 2), round(float(l), 2), round(float(h), 2)]
            for o, c, l, h in zip(win['open_price'], win['close_price'],
                                  win['low_price'], win['high_price'])]
    vol = [int(v) if np.isfinite(v) else 0 for v in win['volume']]
    first, last = dates[0], dates[-1]

    marks, signals = [], []
    if mark == 'weekly':
        wf = UP.detect_frame(base, WEEK_CFG)
        for s in RES.weekly_segments(wf).itertuples(index=False):
            d0 = pd.Timestamp(s.start).strftime('%Y-%m-%d')
            if first <= d0 <= last:
                marks.append(dict(date=d0, label='周线起'))
        for d0 in wf.loc[wf['signal'], 'confirm_date']:
            s0 = pd.Timestamp(d0).strftime('%Y-%m-%d')
            if first <= s0 <= last:
                signals.append(s0)
    else:
        d1 = UP.detect_frame(g, DAY_CFG)
        for _, gg in d1[d1['seg_id'] > 0].groupby('seg_id'):
            d0 = pd.Timestamp(gg['trade_date'].min()).strftime('%Y-%m-%d')
            if first <= d0 <= last:
                marks.append(dict(date=d0, label='上抛起'))
        for d0 in d1.loc[d1['signal'], 'confirm_date']:
            s0 = pd.Timestamp(d0).strftime('%Y-%m-%d')
            if first <= s0 <= last:
                signals.append(s0)

    return dict(
        code=code, name=str(g['stock_name'].iloc[-1]), tf=tf, n_bars=len(win),
        dates=dates, ohlc=ohlc, vol=vol,
        ma7=[None if not np.isfinite(v) else round(float(v), 2) for v in ma7.loc[idx]],
        ma21=[None if not np.isfinite(v) else round(float(v), 2) for v in ma21.loc[idx]],
        dif=[round(float(v), 3) for v in dif.loc[idx]],
        dea=[round(float(v), 3) for v in dea.loc[idx]],
        hist=[round(float(v), 3) for v in hist.loc[idx]],
        marks=marks, signals=signals, mark_type=mark,
    )


# ═════════════════════════════════════════════════════════════════════════
# HTTP
# ═════════════════════════════════════════════════════════════════════════
MIME = {'.html': 'text/html; charset=utf-8', '.js': 'application/javascript; charset=utf-8',
        '.css': 'text/css; charset=utf-8', '.json': 'application/json; charset=utf-8',
        '.png': 'image/png', '.svg': 'image/svg+xml', '.ico': 'image/x-icon'}


class Handler(SimpleHTTPRequestHandler):
    server_version = 'MA7Upthrow/1.0'

    def log_message(self, fmt, *args):                       # noqa: A003
        pass

    def _json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False, allow_nan=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', MIME['.json'])
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def _file(self, path):
        if not os.path.isfile(path):
            self.send_error(404)
            return
        ext = os.path.splitext(path)[1].lower()
        with open(path, 'rb') as f:
            body = f.read()
        self.send_response(200)
        self.send_header('Content-Type', MIME.get(ext, 'application/octet-stream'))
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-cache')
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):                                        # noqa: N802
        u = urlparse(self.path)
        q = parse_qs(u.query)
        try:
            if u.path in ('/', '/index.html'):
                return self._file(os.path.join(HERE, 'static', 'index.html'))
            if u.path.startswith('/static/'):
                rel = u.path[len('/static/'):]
                safe = os.path.normpath(os.path.join(HERE, 'static', rel))
                if not safe.startswith(os.path.join(HERE, 'static')):
                    self.send_error(403)
                    return
                return self._file(safe)
            if u.path == '/api/meta':
                return self._json(dict(asof=_d(_ASOF), mv_snapshot=_MV_SNAP,
                                       scopes=[dict(k=k, label=v) for k, v in SCOPES],
                                       n_codes=int(_PX['stock_code'].nunique())))
            if u.path == '/api/reload':
                with _lock:
                    _FC.clear()
                    _WC.clear()
                    _SC.clear()
                    # 强制重算：清掉磁盘缓存
                    try:
                        for fn in os.listdir(CACHE_DIR):
                            if fn.endswith('.json'):
                                os.remove(os.path.join(CACHE_DIR, fn))
                    except FileNotFoundError:
                        pass
                    boot()
                return self._json(dict(ok=True, asof=_d(_ASOF),
                                       mv_snapshot=_MV_SNAP))
            if u.path == '/api/scan':
                mode = (q.get('mode', ['daily'])[0] or 'daily').lower()
                scope = (q.get('scope', ['hs300'])[0] or 'hs300').lower()
                if mode not in ('daily', 'weekly', 'res', 'cycle', 'start'):
                    return self._json(dict(error='bad mode'), 400)
                if scope not in SCOPE_LABEL:
                    return self._json(dict(error='bad scope'), 400)
                t0 = time.time()
                data = scan(mode, scope)
                data['elapsed'] = round(time.time() - t0, 2)
                return self._json(data)
            if u.path == '/api/kline':
                code = (q.get('code', [''])[0] or '').zfill(6)
                mark = (q.get('mark', ['daily'])[0] or 'daily').lower()
                full = (q.get('full', ['0'])[0] or '0') in ('1', 'true', 'yes')
                return self._json(kline(code, 'weekly' if mark == 'weekly' else 'daily',
                                        full=full))
            self.send_error(404)
        except KeyError:
            self._json(dict(error='not found'), 404)
        except Exception as e:                               # noqa: BLE001
            traceback.print_exc()
            self._json(dict(error=str(e)), 500)


def main():
    port = int(os.environ.get('MA7_WEB_PORT', '8770'))
    boot()
    # 预热门面范围，避免首屏等太久
    threading.Thread(target=lambda: scan('daily', 'hs300'), daemon=True).start()
    srv = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    url = f'http://127.0.0.1:{port}/'
    print(f'[webapp] {url}')

    # 由「服务自己」在端口真正就绪后打开浏览器，比在 bat 里 sleep 更可靠
    if os.environ.get('MA7_OPEN_BROWSER', '0') in ('1', 'true', 'yes'):
        def _open():
            try:
                webbrowser.open(url)
            except Exception as e:                           # noqa: BLE001
                print('[webapp] 打开浏览器失败:', e)
        threading.Timer(0.6, _open).start()

    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
