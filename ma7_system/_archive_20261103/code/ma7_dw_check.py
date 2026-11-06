# -*- coding: utf-8 -*-
"""
==============================================================================
ma7_dw_check.py —— 日线 × 周线共振回归对拍（v2 冻结口径 + 方案 3）
==============================================================================
用法（在 ma7_system 目录下）::

    python ma7_dw_check.py

校验项：
  1. v2 口径（默认，doc/日线扫描方案_v2.md）：600188 33 笔 =
     output/600188_dw_resonance_entries.csv（确认日/开仓日/周线区间逐笔一致）
  2. v2 口径 600188 2026 三笔：04-28 +2.2% / 06-04 −7.5% / 09-02 −5.6%
  3. 方案 3（--multi-signal）：600188 46 笔 = output/600188_opt3_exit3.csv（逐笔）
  4. 方案 3 600188 2026 四笔（冻结版 §6，含 02-09 +18.6%）
  5. 600188 2026 周线区间 = 冻结版 §6 三段
  6. 000400 周线区间 25 个（含进行中）
  7. v2 信号数：000400 2026=5 / 全历史 86；600188 2026=7 / 全历史 81

任何一项不符 → 退出码 1。
"""
import os
import sys

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
from mlr import data as D                                             # noqa: E402

CFG_PATH = os.path.join(S.BASE_DIR, 'config', 'ma7_dw.json')
OUT = S.OUTPUT_DIR
FAILS = []


def ok(cond, label, detail=''):
    print(('  [PASS] ' if cond else '  [FAIL] ') + label + (('  ' + detail) if detail else ''))
    if not cond:
        FAILS.append(label)


def _d(s):
    return pd.to_datetime(s).dt.strftime('%Y-%m-%d')


def load(code, multi=False):
    daily = D.load_daily(codes=[code], verbose=False).sort_values('trade_date').reset_index(drop=True)
    cfg = UP.load_config(CFG_PATH)
    cfg['signal']['multi_reclaim'] = bool(multi)
    wcfg = W.load_config(cfg)
    up = UP.detect_frame(daily, cfg)
    wf = W.detect_frame(daily, wcfg)
    return daily, cfg, up, wf


def check_v2():
    print('\n[1] v2 口径：600188 33 笔 vs 600188_dw_resonance_entries.csv')
    daily, cfg, up, wf = load('600188', multi=False)
    tr = dw.simulate(up, wf, cfg, one_position=True)
    ref = pd.read_csv(os.path.join(OUT, '600188_dw_resonance_entries.csv'))
    ref.columns = ['signal_date', 'confirm', 'entry', 'wk_start', 'wk_end']
    ok(len(tr) == len(ref), '笔数一致', f'{len(tr)} vs {len(ref)}')
    if len(tr) == len(ref):
        ok(list(_d(tr['confirm'])) == list(_d(ref['confirm'])), '确认日逐笔一致')
        ok(list(_d(tr['entry'])) == list(_d(ref['entry'])), '开仓日逐笔一致')
        ok(list(_d(tr['wk_start'])) == list(_d(ref['wk_start'])), '周线区间起点一致')

    print('\n[2] v2 口径：600188 2026 三笔（04-28 / 06-04 / 09-02）')
    t26 = tr[pd.to_datetime(tr['confirm']).dt.year == 2026]
    ok(len(t26) == 3, '2026 成交笔数 = 3', f'{len(t26)}')
    if len(t26) == 3:
        ok(list(_d(t26['entry'])) == ['2026-04-28', '2026-06-04', '2026-09-02'],
           '开仓日', str(list(_d(t26['entry']))))
        ok(max(abs(t26['net'].values * 100 - [2.2, -7.5, -5.6])) <= 0.15,
           '净收益 +2.2/−7.5/−5.6', str([round(x * 100, 1) for x in t26['net'].values]))
    return tr


def check_multi():
    print('\n[3] 方案 3：600188 46 笔 vs 600188_opt3_exit3.csv')
    daily, cfg, up, wf = load('600188', multi=True)
    tr = dw.simulate(up, wf, cfg, one_position=True)
    ref = pd.read_csv(os.path.join(OUT, '600188_opt3_exit3.csv'))
    ref.columns = ['no', 'signal_date', 'confirm', 'mode', 'entry', 'epx', 'exit', 'xpx',
                   'net', 'hold', 'state', 'ws', 'we']
    ok(len(tr) == len(ref), '笔数一致', f'{len(tr)} vs {len(ref)}')
    if len(tr) == len(ref):
        ok(list(_d(tr['entry'])) == list(pd.to_datetime(ref['entry']).dt.strftime('%Y-%m-%d')),
           '开仓日逐笔一致')
        ok(list(_d(tr['exit'])) == list(pd.to_datetime(ref['exit']).dt.strftime('%Y-%m-%d')),
           '平仓日逐笔一致')
        dev = tr['net'].values * 100 - ref['net'].values
        ok(abs(dev).max() <= 0.15, '净收益逐笔一致（<=0.15pp）', f'最大偏差 {abs(dev).max():.3f}pp')

    print('\n[4] 方案 3：600188 2026 四笔（冻结版 §6）')
    t26 = tr[pd.to_datetime(tr['confirm']).dt.year == 2026]
    ok(len(t26) == 4, '2026 成交笔数 = 4', f'{len(t26)}')
    if len(t26) == 4:
        ok(list(_d(t26['entry'])) == ['2026-02-09', '2026-04-28', '2026-06-04', '2026-09-02'],
           '开仓日 02-09/04-28/06-04/09-02', str(list(_d(t26['entry']))))
        ok(max(abs(t26['net'].values * 100 - [18.6, 2.2, -7.5, -5.6])) <= 0.15,
           '净收益 +18.6/+2.2/−7.5/−5.6', str([round(x * 100, 1) for x in t26['net'].values]))
    return tr


def check_weekly():
    print('\n[5] 600188 2026 周线区间（冻结版 §6）')
    daily, cfg, up, wf = load('600188', multi=False)
    spans = []
    for iid, g in wf[wf['iv_id'] > 0].groupby('iv_id'):
        s = pd.Timestamp(g['iv_start_date'].iloc[0])
        e = g['iv_end_date'].iloc[0]
        e = '进行中' if pd.isna(e) else pd.Timestamp(e).strftime('%Y-%m-%d')
        if s.strftime('%Y-%m-%d').startswith('2026'):
            spans.append((s.strftime('%Y-%m-%d'), e))
    want = [('2026-02-06', '2026-04-17'), ('2026-04-24', '2026-07-03'),
            ('2026-08-21', '2026-09-24')]
    ok(spans == want, '2026 三段区间', str(spans))

    print('\n[6] 000400 周线区间 vs 000400_weekly_full_segments.csv')
    _d0, _c, _u, wf4 = load('000400', multi=False)
    ref = pd.read_csv(os.path.join(OUT, '000400_weekly_full_segments.csv'))
    ref.columns = ['start', 'end', 'last_above', 'n_weeks', 'ongoing']
    ref_pairs = [(pd.Timestamp(r.start).strftime('%Y-%m-%d'),
                  '进行中' if pd.isna(r.end) else pd.Timestamp(r.end).strftime('%Y-%m-%d'))
                 for r in ref.itertuples()]
    mine = []
    for iid, g in wf4[wf4['iv_id'] > 0].groupby('iv_id'):
        s = pd.Timestamp(g['iv_start_date'].iloc[0]).strftime('%Y-%m-%d')
        e = g['iv_end_date'].iloc[0]
        mine.append((s, '进行中' if pd.isna(e) else pd.Timestamp(e).strftime('%Y-%m-%d')))
    ok(len(mine) == len(ref_pairs), '区间数一致', f'{len(mine)} vs {len(ref_pairs)}')
    ok(mine == ref_pairs, '起止日全部一致（含进行中）', f'{sum(1 for x in mine if x in ref_pairs)}/{len(mine)}')


def check_counts():
    print('\n[7] v2 信号数（doc/日线扫描方案_v2.md §7）')
    for code, want26, want_all in (('000400', 5, 86), ('600188', 7, 81)):
        daily, cfg, up, wf = load(code, multi=False)
        s = up[up['signal']]
        n26 = int((pd.to_datetime(s['trade_date']).dt.year == 2026).sum())
        ok(n26 == want26 and len(s) == want_all, f'{code} 2026={want26} / 全历史={want_all}',
           f'实际 {n26} / {len(s)}')


def main():
    print('=' * 74)
    print('  日线 × 周线共振回归对拍（v2 冻结口径 + 方案 3）')
    print(f'  配置: {CFG_PATH}')
    print('=' * 74)
    check_v2()
    check_multi()
    check_weekly()
    check_counts()
    print('\n' + '=' * 74)
    if FAILS:
        print(f'  结果: FAIL（{len(FAILS)} 项）')
        for f in FAILS:
            print('    - ' + f)
        return 1
    print('  结果: ALL PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
