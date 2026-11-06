# -*- coding: utf-8 -*-
"""
==============================================================================
ma7_scan_dw.py —— 日线 × 周线共振扫描（冻结版 2026-11-03）
==============================================================================
规则（doc/日线周线共振方案_冻结版.md）：

  日线  段：MA7 的 5 日斜率 > 0，连续 2 天确认起止，允许 1 天回踩
        C ：段内首次收盘 > MA7 起，连续 3 根收盘 > MA7 → 第 3 根确认
        B ：前一日跌破、当日重新站上，连续 3 根 → 第 3 根确认
        方案3：每段 C 一次 + 每次 B 都给信号（--single-signal 回到每段一个）
  周线  W-FRI 重采样；MA7w=7 周 SMA；s5w 5 周窗口
        区间 = 段内首次收盘 > MA7w 的那一周 ~ 连续 3 周收盘 < MA7w（第 3 周为结束确认周）
  共振  日线确认日 ∈ 周线区间 → 开仓；开仓 = 确认日下一交易日开盘
  平仓  连续 3 日收盘 < 日线 MA7 → 次日开盘（--exit-days 可改）
  单仓  --position-mode ignore（默认，与 600188_opt3_exit3.csv 一致）/ add

用法::

    python ma7_scan_dw.py --universe hs300
    python ma7_scan_dw.py --codes 600188 --date 2026-11-02 --recent 10
    python ma7_scan_dw.py --universe hs300 --single-signal    # 每段只取最早一个信号
    python ma7_scan_dw.py --universe hs300 --strict-week      # 周线段严格因果（截断到当日）
"""
import argparse
import json
import os
import sys
import time
import unicodedata

import numpy as np
import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _ROOT)
sys.path.insert(0, _HERE)
try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:                                            # noqa: BLE001
    pass

from ma7daily import settings as S, upthrow as UP, weekly as W, dw   # noqa: E402
from mlr import data as D, universe as U                             # noqa: E402

CONFIG_NAME = 'ma7_dw.json'
RULE_VERSION = 'frozen-2026-11-03'

COLS = ['bucket', 'code', 'name', 'signal_date', 'date', 'close', 'ma7', 's5', 'day',
        'mode', 'confirm', 'entry', 'wk_in', 'wk_start', 'wk_end', 'wk_week',
        'wk_close', 'wk_ma7', 'wk_s5', 'wk_pos', 'pos', 'action', 'note']


# ═════════════════════════════════════════════════════════════════════════
# 小工具
# ═════════════════════════════════════════════════════════════════════════
def _w(x):
    """显示宽度（CJK 记 2 列）。"""
    return sum(2 if unicodedata.east_asian_width(ch) in ('W', 'F') else 1 for ch in str(x))


def _pad(s, width):
    s = str(s)
    return s + ' ' * max(0, width - _w(s))


def _table(headers, rows):
    """等宽中文表格。"""
    if not rows:
        return '  （无）'
    widths = [max(_w(h), *(_w(r[i]) for r in rows)) for i, h in enumerate(headers)]
    out = ['  ' + '  '.join(_pad(h, widths[i]) for i, h in enumerate(headers))]
    out.append('  ' + '  '.join('-' * widths[i] for i in range(len(headers))))
    for r in rows:
        out.append('  ' + '  '.join(_pad(v, widths[i]) for i, v in enumerate(r)))
    return '\n'.join(out)


def _d(x):
    if x is None or (isinstance(x, float) and not np.isfinite(x)):
        return ''
    try:
        ts = pd.Timestamp(x)
        return '' if pd.isna(ts) else ts.strftime('%Y-%m-%d')
    except Exception:                                        # noqa: BLE001
        return str(x)


def _pool(args, cfg):
    if args.codes:
        return [c.strip().zfill(6) for c in args.codes.split(',') if c.strip()], ''
    universe = args.universe or cfg['scan'].get('universe', 'hs300')
    if universe == 'hs300':
        return U.get_hs300_codes()
    if universe == 'coal':
        return U.get_coal_codes()
    if universe == 'all':
        return None, ''
    raise SystemExit(f'未知 --universe {universe!r}（hs300 / coal / all）')


# ═════════════════════════════════════════════════════════════════════════
# 行组装
# ═════════════════════════════════════════════════════════════════════════
def _make_row(bucket, r, asof, st, entry, pos, action, note):
    in_wk = bool(st.get('in_interval'))
    return dict(
        bucket=bucket, code=r.stock_code, name=getattr(r, 'stock_name', ''),
        signal_date=_d(getattr(r, 'signal_date', None) if pd.notna(getattr(r, 'signal_date', pd.NaT))
                      else r.seg_start_date),
        date=_d(r.trade_date),
        close=round(float(r.close_price), 2), ma7=round(float(r.ma7), 2),
        s5=round(float(r.s5), 3), day=int(r.seg_day), mode=r.mode,
        confirm=_d(getattr(r, 'confirm_date', None)),
        entry=_d(entry),
        wk_in='是' if in_wk else '否',
        wk_start=_d(st.get('start')) if in_wk else '',
        wk_end=('进行中' if st.get('ongoing') else _d(st.get('end'))) if in_wk else '',
        wk_week=int(st.get('week_no', 0)) if in_wk else 0,
        wk_close=round(float(st['close']), 2) if st.get('ok') else None,
        wk_ma7=round(float(st['ma7']), 2) if st.get('ok') and pd.notna(st.get('ma7')) else None,
        wk_s5=round(float(st['s5']), 3) if st.get('ok') and pd.notna(st.get('s5')) else None,
        wk_pos='上' if st.get('above') else '下',
        pos=('持仓(' + _d(pos['entry']) + ')') if pos else '空仓',
        action=action, note=note,
    )


def _enrich(df, bucket, asof, wby, upby, cfg, nxt, pos_mode, trades_of):
    rows = []
    for r in df.itertuples(index=False):
        st = W.state_of(wby.get(r.stock_code), asof) if r.stock_code in wby \
            else dict(W.EMPTY_STATE)
        entry, pos, action, note = None, None, '', ''
        if bucket == 'confirmed':
            entry = nxt.get(r.stock_code)
            if r.stock_code in upby:
                pos = dw.open_position(trades_of(r.stock_code), asof)
            if not st['in_interval']:
                action, note = '不开仓', '周线区间外'
            elif pos is not None and pos_mode == 'ignore':
                action, note = '忽略', '持仓中，不叠加'
            elif pos is not None and pos_mode == 'add':
                action, note = '开仓', '加仓（已有仓位）'
            elif entry is None:
                action, note = '开仓', '次一交易日开盘（数据末端）'
            else:
                action, note = '开仓', '次一交易日开盘'
        else:
            action = '观察'
            note = '共振，等待确认' if st['in_interval'] else '区间外'
        rows.append(_make_row(bucket, r, asof, st, entry, pos, action, note))
    return rows


# ═════════════════════════════════════════════════════════════════════════
# 主流程
# ═════════════════════════════════════════════════════════════════════════
def main(argv=None):
    ap = argparse.ArgumentParser(prog='ma7_scan_dw', description='日线 × 周线共振扫描（冻结版）')
    ap.add_argument('--date', default=None, help='交易日（默认最新）')
    ap.add_argument('--codes', default=None, help='逗号分隔指定股票，如 600188')
    ap.add_argument('--universe', default=None, choices=['hs300', 'coal', 'all'])
    ap.add_argument('--recent', type=int, default=None, help='附最近 N 个交易日确认（默认配置 5）')
    ap.add_argument('--refresh', action='store_true', help='强制重拉日线')
    ap.add_argument('--no-sync', action='store_true', help='跳过上游增量同步')
    ap.add_argument('--out', default=None, help='CSV 输出路径')
    ap.add_argument('--config', default=None, help='参数文件（默认 config/ma7_dw.json）')
    ap.add_argument('--multi-signal', action='store_true',
                    help='方案 3：每段 C 一次 + 每次 B 都给信号（默认关闭，走 v2 口径）')
    ap.add_argument('--single-signal', action='store_true',
                    help='强制 v2 口径（C 主 + B 兜底，每段最早一个；默认就是它）')
    ap.add_argument('--position-mode', default=None, choices=['ignore', 'add'],
                    help='持仓占用：ignore=持仓中忽略（默认）/ add=不过滤')
    ap.add_argument('--strict-week', action='store_true',
                    help='周线严格因果：只用当日已收盘周（段需下一周确认）')
    ap.add_argument('--exit-days', type=int, default=None,
                    help='离场：连续 N 日收盘 < MA7（默认 3）')
    args = ap.parse_args(argv)

    cfg_path = args.config or os.path.join(S.BASE_DIR, 'config', CONFIG_NAME)
    cfg = UP.load_config(cfg_path)
    if args.multi_signal:
        cfg['signal']['multi_reclaim'] = True
    if args.single_signal:
        cfg['signal']['multi_reclaim'] = False
    res_cfg = cfg.setdefault('resonance', {})
    pos_mode = args.position_mode or res_cfg.get('position_mode', 'ignore')
    res_cfg['position_mode'] = pos_mode
    if args.exit_days:
        res_cfg['exit_below_days'] = int(args.exit_days)
    wcfg = W.load_config(cfg)
    recent_n = args.recent if args.recent is not None else int(cfg['scan'].get('recent', 5))
    multi = bool(cfg['signal'].get('multi_reclaim', False))

    codes, upd = _pool(args, cfg)
    print('=' * 84)
    print('  日线 × 周线共振扫描（冻结版 2026-11-03）')
    print(f"  日线段: MA7 {cfg['ma7_window']} / 斜率 {cfg['segment']['slope_window']} 日 / "
          f"连续 {cfg['segment']['start_confirm']} 天确认 / 允许 {cfg['segment']['end_confirm'] - 1} 天回踩")
    print(f"  日线信号: {'方案3（每段 C 一次 + 每次 B）' if multi else 'v2 冻结口径（C 主 + B 兜底，每段最早一个）'}"
          f" / 连续 {cfg['signal']['close_above_days']} 根收盘 > MA7")
    print(f"  周线    : {wcfg['rule']} / MA7w {wcfg['window']} 周 / 区间=首次站上~连续 "
          f"{wcfg['break_weeks']} 周跌破")
    print(f"  执行    : 确认日次一交易日开盘 / 离场=连续 {res_cfg.get('exit_below_days', 3)} 日跌破次日开盘 / "
          f"成本 {res_cfg.get('cost', 0.002) * 100:.1f}% / 持仓 {pos_mode}")
    print("  日期    : 信号日 D1（首次站上）→ 确认日 D3（第 3 根收盘）→ 开仓日 D4（次日开盘）")
    print('=' * 84)

    if codes is None:
        print('  股票池: 全市场')
    else:
        print(f"  股票池: {len(codes)} 只" + (f' (update_date={upd})' if upd else ''))

    if not args.no_sync:
        try:
            D.sync_latest_daily()
        except Exception as e:                               # noqa: BLE001
            print(f'  [警告] 上游同步失败，使用本地缓存：{e}')

    daily = D.load_daily(codes=codes, refresh=args.refresh)
    if not len(daily):
        raise SystemExit('无日线数据')
    daily['trade_date'] = pd.to_datetime(daily['trade_date'])
    daily = daily.sort_values(['stock_code', 'trade_date']).reset_index(drop=True)

    cal = pd.DatetimeIndex(sorted(daily['trade_date'].unique()))
    asof = pd.Timestamp(args.date) if args.date else pd.Timestamp(cal[-1])
    if asof not in cal:
        raise SystemExit(f'{asof.date()} 不是数据内交易日；最新为 {cal[-1].date()}')

    hist = daily[daily['trade_date'] <= asof].reset_index(drop=True)
    up = UP.detect_frame(hist, cfg)
    wf = W.detect_frame(hist if args.strict_week else daily, wcfg)

    nxt = (daily.loc[daily['trade_date'] > asof, ['stock_code', 'trade_date']]
           .groupby('stock_code')['trade_date'].min().to_dict())

    conf, cand2, cand1 = UP.scan_day(up, asof)
    act = UP.active(up, asof)

    wby = {c: g for c, g in wf.groupby('stock_code', sort=False)}
    upby = {c: g for c, g in up.groupby('stock_code', sort=False)}
    cache = {}

    def trades_of(code):
        if code not in cache:
            cache[code] = dw.simulate(upby[code], wby.get(code), cfg, one_position=True)
        return cache[code]

    rows = []
    for df_, b in ((conf, 'confirmed'), (cand2, 'candidate2'), (cand1, 'candidate1')):
        if len(df_):
            rows += _enrich(df_, b, asof, wby, upby, cfg, nxt, pos_mode, trades_of)
    out = pd.DataFrame(rows, columns=COLS) if rows else pd.DataFrame(columns=COLS)

    conf_out = out[out['bucket'] == 'confirmed']
    n_in = int((conf_out['wk_in'] == '是').sum()) if len(conf_out) else 0
    n_open = int((conf_out['action'] == '开仓').sum()) if len(conf_out) else 0
    print(f"\n  交易日: {asof.date()} | {len(upby)} 只 | 段内 {len(act)} | 今日确认 {len(conf)} | "
          f"其中共振 {n_in} | 可开仓 {n_open} | 候选2/3 {len(cand2)} | 候选1/3 {len(cand1)}")

    # ---------- 今日确认 ----------
    print(f'\n  【今日确认 3/3】（确认日={asof.date()}，开仓=次一交易日开盘）:')
    c = out[out.bucket == 'confirmed']
    if len(c):
        c = c.sort_values(['wk_in', 'action'], ascending=[False, True])
        print(_table(['代码', '名称', '信号日', '模式', '开仓', '周线区间', '周', '周线', '持仓', '动作'],
                     [[r.code, r.name, r.signal_date, r.mode, r.entry or '待定',
                       (r.wk_start + '~' + r.wk_end) if r.wk_in == '是' else '—',
                       r.wk_week if r.wk_in == '是' else '',
                       r.wk_pos, r.pos, r.action] for r in c.itertuples()]))
    else:
        print('  （无）')

    for b, title in (('candidate2', '候选 2/3（已站上 2 天，明日确认）'),
                     ('candidate1', '候选 1/3（今日刚站上）')):
        d_ = out[out.bucket == b]
        print(f'\n  【{title}】:')
        if len(d_):
            d_ = d_.sort_values(['wk_in'], ascending=False)
            print(_table(['代码', '名称', '信号日', '日', '模式', '周线', '说明'],
                         [[r.code, r.name, r.signal_date, r.day, r.mode, r.wk_pos,
                           '共振' if r.wk_in == '是' else '区间外'] for r in d_.itertuples()]))
        else:
            print('  （无）')

    # ---------- 最近 N 个交易日确认 ----------
    if recent_n and recent_n > 0:
        lo = cal[max(0, len(cal[cal <= asof]) - recent_n)]
        rec = up[up['signal'] & (up['trade_date'] >= lo) & (up['trade_date'] <= asof)].copy()
        print(f'\n  【最近 {recent_n} 个交易日确认】（{lo.date()} ~ {asof.date()}，共 {len(rec)} 条）:')
        if len(rec):
            rrows = []
            for r in rec.sort_values(['trade_date', 'stock_code']).itertuples(index=False):
                st = W.state_of(wby.get(r.stock_code), r.confirm_date) if r.stock_code in wby \
                    else dict(W.EMPTY_STATE)
                pl = ''
                if r.stock_code in upby:
                    tr = trades_of(r.stock_code)
                    hit = tr[tr['confirm'] == pd.Timestamp(r.confirm_date)]
                    if len(hit):
                        t = hit.iloc[0]
                        pl = ('持有中' if t['status'] == 'open'
                              else f"{t['net'] * 100:+.1f}% ({int(t['hold'])}日)")
                    elif pd.isna(r.entry_date):
                        pl = '待开仓'
                    elif st['in_interval']:
                        pl = '跳过(持仓中)'
                    else:
                        pl = '—'
                rrows.append([r.stock_code, getattr(r, 'stock_name', ''), _d(r.trade_date),
                              r.mode, '是' if st['in_interval'] else '否',
                              _d(r.entry_date) or _d(nxt.get(r.stock_code)), pl])
            print(_table(['代码', '名称', '确认日', '模式', '共振', '开仓日', '结果'],
                         rrows))
        else:
            print('  （无）')

    # ---------- 输出 ----------
    S.ensure_dirs()
    out_path = args.out or os.path.join(
        S.OUTPUT_DIR, f"ma7_dw_scan_{asof.strftime('%Y%m%d')}.csv")
    out.to_csv(out_path, index=False, encoding='utf-8-sig')

    meta = {
        'rule': 'ma7_dw',
        'rule_version': RULE_VERSION,
        'config': cfg,
        'weekly': wcfg,
        'last_day': str(asof.date()),
        'data_range': [str(cal[0].date()), str(cal[-1].date())],
        'universe_update_date': upd,
        'n_codes': int(daily['stock_code'].nunique()),
        'flags': {'multi_reclaim': multi, 'position_mode': pos_mode,
                  'strict_week': bool(args.strict_week),
                  'exit_below_days': int(res_cfg.get('exit_below_days', 3))},
        'counts': {'active': int(len(act)), 'confirmed': int(len(conf)),
                   'confirmed_in_wk': int(n_in if len(out) else 0),
                   'actionable': n_open,
                   'candidate2': int(len(cand2)), 'candidate1': int(len(cand1))},
        'generated_at': time.strftime('%Y-%m-%d %H:%M:%S'),
    }
    meta_path = os.path.splitext(out_path)[0] + '.meta.json'
    with open(meta_path, 'w', encoding='utf-8') as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    print(f'\n[OK] CSV : {out_path}')
    print(f'[OK] Meta: {meta_path}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
