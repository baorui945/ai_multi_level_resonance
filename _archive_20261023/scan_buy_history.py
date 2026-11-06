# -*- coding: utf-8 -*-
"""
==============================================================================
scan_buy_history.py —— Route B 买点 · 历史区间回放扫描（默认扫 2026 全年）
==============================================================================
把 `scan_buy.py` 的**单日**买点判定（P2 宽容档）扩展成**区间回放**：
对区间内每个交易日 t，逐个判定股票在该日是否为买点，最终给出一份
「个股 × 买点日」的明细列表。

买点定义（与 scan_buy.py 完全一致，逐字复用其口径）
---------------------------------------------------
对每只股票、每个交易日 t：
  1. 窗口 W = [t-L+1, t]（L = 模板长度）；
  2. score = (corr(DIF_W, 模板DIF_z) + corr(DEA_W, 模板DEA_z)) / 2 >= --threshold；
  3. W 内**最后一次金叉**位于 b，且 t - b <= --fresh-days 且 b 落在窗口内；
  三者同时成立 → t 日为买点，建仓价 = 当日收盘价。

实现要点（为何快）
------------------
`scan_buy.scan_buys()` 每判一天都要对每只股票重算一遍 MACD 与滑动相关；
本脚本对每只股票**只算一次**全序列的 DIF/DEA/柱、`sliding_corr` 与金叉位置，
再按下标取值判定任意日期，结果与逐日调用 `scan_buys()` 等价（见 --check）。

数据来源
--------
与 scan_buy.py 相同：`backward_tdx_daily`（后复权）→ 本地 `data/_cache/daily_px.parquet`；
默认先做上游 `backward_daily` 增量同步（`--no-sync` 可跳过，`--refresh` 全量重拉）。

用法::

    python scan_buy_history.py                              # 全市场，2026-01-01 至今
    python scan_buy_history.py --from 2025-01-01           # 指定回放起点
    python scan_buy_history.py --universe hs300 --fresh-days 3
    python scan_buy_history.py --threshold 0.90            # 0.98 极严，跨年回放常 0 命中
    python scan_buy_history.py --from 2015-01-01 --threshold 0.90 --fresh-days 0
                                                            # 金叉当日提示：信号日=金叉日，可实盘
                                                            # （与 backtest_shrink --fresh-days 0
                                                            #   --overlap 0 同口径，见 check_scan_bt.py）
    python scan_buy_history.py --check 2026-10-19           # 与 scan_buys() 逐票对拍
    python scan_buy_history.py --out output\\scanDaily\\hist_2026.xlsx

    模板口径三分支（重要）::

    # 1) 整段模板 + 金叉当日达标（可实盘，信号日=金叉日）
    python scan_buy_history.py --fresh-days 0 --threshold 0.90

    # 2) 只取「金叉前段」做形状匹配（无前视，金叉当日出手）
    #    模板起点 → 区间内最后一个金叉日；窗口终点=个股金叉日
    python scan_buy_history.py --template-part pre --threshold 0.995

    # 3) 复现回测 cross 口径（整段 0.98 + NMS 0.5 + 信号日回填金叉日）
    #    注意：窗口要金叉后 14~32 根才达标，属事后口径，不可实盘
    python scan_buy_history.py --threshold 0.98 --fresh-days 999 \\
        --dedup 0.5 --alert-day cross

产物：默认 `output/scanDaily/buy_alert_hist_<起>_<止>_s<阈值>.xlsx`
worksheets：`买点明细`（逐日命中一行）/ `信号列表`（一次金叉=一个买点）/
`个股汇总`（每只股票一行）/ `口径说明`。
"""
import argparse
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mlr import data as D, settings as S                        # noqa: E402
from mlr.macd import macd as calc_macd, cross_flags             # noqa: E402
from scan_buy import OUT_COLS, build_template, _series_from    # noqa: E402
from template_scan import (_resolve_pool, sliding_corr, zscore,  # noqa: E402
                           _dedup)

HIST_COLS = ['data_day'] + OUT_COLS


def build_template_pre(daily, code, start, end, which='last'):
    """只取模板的「金叉前段」：起点 → 区间内最后一个金叉日。

    这样扫描窗口的终点就是个股自己的金叉日（配 `fresh_days=0`），
    比较的是「下跌段形状」，不包含金叉后的上抛段（无前视）。

    Returns:
        (tpl_dif_z, tpl_dea_z, L1, mp, name, cross_date)
    """
    ser = _series_from(daily, code)
    if ser is None:                       # --limit 时模板股可能不在样本里
        ser = _series_from(D.load_daily(codes=[code]), code)
    if ser is None:
        raise SystemExit(f'数据中找不到模板股票 {code}')
    _close, t_dates, t_dif, t_dea, t_name, mp = ser
    idx = np.where((t_dates >= np.datetime64(start)) &
                   (t_dates <= np.datetime64(end)))[0]
    if idx.size < 8:
        raise SystemExit(f'模板区间 {start}~{end} 内交易日不足（{idx.size}）')
    xs = np.where(cross_flags(t_dif, t_dea) == 1)[0]
    xs = xs[(xs >= idx[0]) & (xs <= idx[-1])]
    if xs.size == 0:
        raise SystemExit(f'模板区间 {start}~{end} 内无金叉，无法取前段')
    b = int(xs[-1] if which == 'last' else xs[0])
    L1 = b - int(idx[0]) + 1
    if L1 < 8:
        raise SystemExit(f'模板前段过短（金叉距起点 {L1} 根）')
    return (zscore(t_dif[idx[0]:b + 1]), zscore(t_dea[idx[0]:b + 1]),
            L1, mp, t_name, pd.Timestamp(t_dates[b]).date())


# ═════════════════════════════════════════════════════════════════════════
# 区间回放判定（口径与 scan_buy.scan_buys 一致）
# ═════════════════════════════════════════════════════════════════════════
def scan_range(daily, tpl_dif_z, tpl_dea_z, L, mp, d0, d1,
               threshold=0.98, fresh_days=5, dedup=None):
    """在 [d0, d1] 逐日回放买点判定。

    Args:
        daily:       长表日线（stock_code/trade_date/stock_name/close_price）。
        tpl_dif_z:   模板 DIF z-score 序列（长度 L）。
        tpl_dea_z:   模板 DEA z-score 序列（长度 L）。
        mp:          MACD 参数 (fast, slow, signal)。
        d0, d1:      回放起止日（含端点）。
        threshold:   score 阈值。
        fresh_days:  最后一次金叉距今天数（根）上限；0 = 当日金叉。
        dedup:       None/0 = 不去重；>0 = 按该重叠比例做 NMS（与回测同款，
                     同股高度重叠窗口只留最高分）。

    Returns:
        (df_hits, stats) —— df_hits 每行 = 某只股票某日的买点；
        stats 为跳过统计（short / no_cross）。
    """
    d0 = np.datetime64(pd.Timestamp(d0))
    d1 = np.datetime64(pd.Timestamp(d1))
    L, fresh_days = int(L), int(fresh_days)
    rows = []
    n_short = n_no_cross = 0

    for code, g in daily.groupby('stock_code', sort=False):
        g = g.sort_values('trade_date')
        close = g['close_price'].to_numpy('float64')
        dates = pd.to_datetime(g['trade_date']).to_numpy()
        name = g['stock_name'].iloc[-1]
        if close.size < L + 1:
            n_short += 1
            continue

        dif, dea, hist = calc_macd(close, *mp)
        cd = sliding_corr(dif, tpl_dif_z)        # 下标 k ↔ 窗口 [k, k+L-1]
        ce = sliding_corr(dea, tpl_dea_z)
        if cd.size == 0:
            n_short += 1
            continue

        idx = np.where((dates >= d0) & (dates <= d1))[0]
        if idx.size == 0:
            continue
        k = idx - L + 1
        ok = (k >= 0) & (k < cd.size)
        idx, k = idx[ok], k[ok]
        if idx.size == 0:
            n_short += 1
            continue

        score = (cd[k] + ce[k]) / 2.0
        xs = np.where(cross_flags(dif, dea) == 1)[0]
        if xs.size == 0:
            n_no_cross += 1
            continue
        pos = np.searchsorted(xs, idx, side='right') - 1
        has = pos >= 0
        b = np.where(has, xs[np.clip(pos, 0, xs.size - 1)], -1)
        fresh = has & (b >= k) & ((idx - b) <= fresh_days)
        keep = fresh & (score >= threshold)
        if not keep.any():
            continue

        # 「MACD 柱连续缩短根数」（与 scan_buys 同口径）
        d = np.diff(hist) < 0
        last_bad = np.maximum.accumulate(np.where(d, -1, np.arange(d.size)))
        run = np.zeros(hist.size, dtype=np.int64)
        run[1:] = np.arange(d.size) - last_bad

        for j in np.where(keep)[0]:
            i, bb, kk = int(idx[j]), int(b[j]), int(k[j])
            rows.append({
                'data_day': pd.Timestamp(dates[i]).date(),
                'stock_code': code, 'stock_name': name,
                'close': round(float(close[i]), 3),
                'score': round(float(score[j]), 4),
                'corr_dif': round(float(cd[kk]), 4),
                'corr_dea': round(float(ce[kk]), 4),
                'cross_date': pd.Timestamp(dates[bb]).date(),
                'cross_close': round(float(close[bb]), 3),
                'bars_since_cross': i - bb,
                'dif': round(float(dif[i]), 4),
                'dea': round(float(dea[i]), 4),
                'hist': round(float(hist[i]), 4),
                'shrink_run': int(run[i]),
                'win_ret_pct': round(float(close[i] / close[kk] - 1) * 100, 2)
                if close[kk] else None,
                # 内部列：窗口在个股序列里的起止下标（NMS 用，落盘时丢弃）
                '_i0': kk, '_i1': i,
            })

    if rows and dedup and float(dedup) > 0:
        rows = _dedup(pd.DataFrame(rows), float(dedup)).to_dict('records')

    df = pd.DataFrame(rows, columns=HIST_COLS)
    if len(df):
        df = (df.sort_values(['data_day', 'score'],
                             ascending=[True, False]).reset_index(drop=True))
    stats = {'short': n_short, 'no_cross': n_no_cross}
    return df, stats



# ═════════════════════════════════════════════════════════════════════════
# 汇总与落盘
# ═════════════════════════════════════════════════════════════════════════
def merge_events(df, alert_day='confirm'):
    """把逐日明细折叠为「一次金叉 = 一个买点」的信号列表。

    同一 (stock_code, cross_date) 的连续新鲜日只保留**首个**命中日，
    并记录该事件内的峰值 score 与持续交易日数。

    Args:
        alert_day: 'confirm' = 信号日/建仓价取首个命中窗口日（当日收盘，可实盘）；
                   'cross'   = 信号日/建仓价回填到金叉日（与回测 cross 口径一致，
                               等价于事后按金叉日买入，含前视）。
    """
    cols = ['stock_code', 'stock_name', '信号日', '建仓价', 'score', '峰值score',
            '金叉日', '金叉日收盘', '持续交易日', '确认日']
    if not len(df):
        return pd.DataFrame(columns=cols)
    d = (df.sort_values(['data_day', 'score'], ascending=[True, False])
           .assign(_key=lambda x: x['stock_code'] + '|' + x['cross_date'].astype(str)))
    first = d.drop_duplicates('_key', keep='first').set_index('_key')
    by_cross = str(alert_day).lower() == 'cross'
    ev = pd.DataFrame({
        'stock_code': first['stock_code'],
        'stock_name': first['stock_name'],
        '信号日': first['cross_date'] if by_cross else first['data_day'],
        '建仓价': first['cross_close'] if by_cross else first['close'],
        'score': first['score'],
        '峰值score': d.groupby('_key')['score'].max(),
        '金叉日': first['cross_date'],
        '金叉日收盘': first['cross_close'],
        '持续交易日': d.groupby('_key')['data_day'].nunique(),
        '确认日': first['data_day'],
    }).reset_index(drop=True)
    return (ev.reindex(columns=cols)
              .sort_values(['信号日', 'score'], ascending=[True, False])
              .reset_index(drop=True))


def stock_summary(ev):
    """个股维度汇总：买点次数 / 首末信号日 / 最近建仓价与 score / 全部信号日。"""
    cols = ['stock_code', 'stock_name', '买点次数', '首次买点日', '最近买点日',
            '最近买点价', '最近score', '历史最高score', '买点日期']
    if not len(ev):
        return pd.DataFrame(columns=cols)
    base = ev.sort_values('信号日')
    s = (base.groupby('stock_code')
             .agg(stock_name=('stock_name', 'last'),
                  买点次数=('信号日', 'size'),
                  首次买点日=('信号日', 'min'),
                  最近买点日=('信号日', 'max'),
                  最近买点价=('建仓价', 'last'),
                  最近score=('score', 'last'),
                  历史最高score=('峰值score', 'max'))
             .reset_index())
    days = (base.groupby('stock_code')['信号日']
                .apply(lambda x: '、'.join(str(v) for v in sorted(x))))
    s['买点日期'] = s['stock_code'].map(days)
    return (s.reindex(columns=cols)
             .sort_values(['买点次数', '最近买点日'], ascending=[False, False])
             .reset_index(drop=True))



def _write(path, detail, events, summary, meta):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with pd.ExcelWriter(path, engine='openpyxl') as xw:
        detail.to_excel(xw, sheet_name='买点明细', index=False)
        events.to_excel(xw, sheet_name='信号列表', index=False)
        summary.to_excel(xw, sheet_name='个股汇总', index=False)
        if meta:
            pd.DataFrame(list(meta.items()),
                         columns=['项目', '值']).to_excel(
                xw, sheet_name='口径说明', index=False)
    try:
        from mlr.report import _style_openpyxl
        _style_openpyxl(path)
    except Exception:                                # noqa: BLE001
        pass
    return path


def _compare_with_single_day(daily, tpl_dif_z, tpl_dea_z, L, mp, day,
                             threshold, fresh_days):
    """对拍：`scan_range` 单日结果 vs `scan_buys` 单日结果（逐票 code/score）。"""
    from scan_buy import scan_buys
    t = pd.Timestamp(day)
    a, _ = scan_buys(daily, tpl_dif_z, tpl_dea_z, L, mp, t,
                     threshold=threshold, fresh_days=fresh_days)
    a = a[a['is_hit']].sort_values('stock_code')
    b, _ = scan_range(daily, tpl_dif_z, tpl_dea_z, L, mp, t, t,
                      threshold=threshold, fresh_days=fresh_days)
    b = b.sort_values('stock_code')
    sa, sb = list(a['stock_code']), list(b['stock_code'])
    same_set = sa == sb
    diff = 0.0
    if same_set and len(sa):
        diff = float(np.abs(a['score'].to_numpy() - b['score'].to_numpy()).max())
    ok = same_set and diff <= 1e-9
    print(f'  [对拍] {t.date()}  scan_buys={len(sa)} 只 / scan_range={len(sb)} 只  '
          f'{"一致" if same_set else "不一致"}  max|Δscore|={diff:.2e}  '
          f'→ {"PASS" if ok else "FAIL"}')
    if not same_set:
        print(f'         仅 scan_buys: {sorted(set(sa) - set(sb))}')
        print(f'         仅 scan_range: {sorted(set(sb) - set(sa))}')
    return ok



# ═════════════════════════════════════════════════════════════════════════
# CLI
# ═════════════════════════════════════════════════════════════════════════
def build_parser():
    ap = argparse.ArgumentParser(
        prog='scan_buy_history',
        description='Route B 买点 · 历史区间回放扫描（默认扫 2026 全年）')
    ap.add_argument('--code', default='601898', help='模板股票代码（默认601898）')
    ap.add_argument('--start', default='2025-11-11', help='模板起始日')
    ap.add_argument('--end', default='2026-03-16', help='模板结束日')
    ap.add_argument('--template-part', choices=['full', 'pre'], default='full',
                    help='模板取整段（full，默认）或只取「金叉前段」（pre）；'
                         'pre 配合 --fresh-days 0 = 金叉当日比前段形状，无前视')
    ap.add_argument('--pre-cross', choices=['last', 'first'], default='last',
                    help='pre 模式用区间内最后一个（默认）还是第一个金叉切分')
    ap.add_argument('--universe', default=None,
                    help='股票池 hs300 / coal / all，可逗号分隔（默认取配置 universe.default）')
    ap.add_argument('--threshold', type=float, default=0.98,
                    help='形状相似度 score 阈值（默认0.98）')
    ap.add_argument('--fresh-days', type=int, default=5,
                    help='窗口内最后一次金叉距今（根数）上限（默认5；0=当日金叉；'
                         '999=不限，对齐回测 cross 口径）')
    ap.add_argument('--dedup', type=float, default=0.0,
                    help='同股重叠窗口 NMS 阈值（0=不去重；0.5=与回测默认一致）')
    ap.add_argument('--alert-day', choices=['confirm', 'cross'], default='confirm',
                    help='信号日/建仓价取哪天：confirm=首个命中窗口日（可实盘，默认）；'
                         'cross=回填金叉日（与回测 cross 买入口径一致）')
    ap.add_argument('--from', dest='date_from', default='2026-01-01',
                    help='回放起点（默认2026-01-01）')
    ap.add_argument('--to', dest='date_to', default=None,
                    help='回放终点（默认数据最新交易日）')
    ap.add_argument('--limit', type=int, default=None, help='每池只处理前 N 只（冒烟）')
    ap.add_argument('--no-sync', action='store_true',
                    help='跳过「上游 backward_daily 最新日」判断与增量同步')
    ap.add_argument('--refresh', action='store_true', help='全量重拉日线缓存')
    ap.add_argument('--check', default=None,
                    help='对拍：指定日期，与 scan_buys() 单日结果逐票比较')
    ap.add_argument('--out', default=None,
                    help='输出路径（.xlsx）；默认 <out-dir>/buy_alert_hist_<起>_<止>.xlsx')
    ap.add_argument('--out-dir', default=os.path.join(S.OUTPUT_DIR, 'scanDaily'),
                    help='默认输出目录（默认 output/scanDaily）')
    return ap



def main(argv=None):
    args = build_parser().parse_args(argv)
    cfg = S.load_config()
    default = cfg.get('universe', {}).get('default', 'hs300')
    pools = [p.strip() for p in (args.universe or default).split(',') if p.strip()]

    print('=' * 104)
    print('  Route B 买点 · 历史区间回放（模板形状匹配 + 金叉新鲜度）')
    print('=' * 104)

    S.ensure_dirs()

    # ── 数据同步（与 scan_buy.py 同一套判断）────────────────────────────
    if args.refresh:
        sync_note = '全量重拉'
    elif args.no_sync:
        sync_note = '未同步（--no-sync）'
    else:
        try:
            sy = D.sync_latest_daily(verbose=True)
            if sy['updated']:
                sync_note = f'已增量同步至 {pd.Timestamp(sy["new_max"]).date()}'
            else:
                cm = sy['cache_max']
                sync_note = (f'已是最新 {cm.date()}（{sy["reason"]}）'
                             if cm is not None else sy['reason'])
        except Exception as exc:                     # noqa: BLE001
            sync_note = f'同步失败，用现有缓存（{type(exc).__name__}）'
            print(f'[数据同步] 失败：{exc}（继续用现有缓存扫描）')

    frames, stats = [], {'short': 0, 'no_cross': 0}
    pool_labels, d0, d1, t_name, L, mp = [], None, None, None, None, None
    tpl_cross = None

    for pool in pools:
        codes, label = _resolve_pool(pool)
        daily = D.load_daily(codes=codes, refresh=args.refresh)
        if args.limit:
            keep = sorted(daily['stock_code'].unique())[:args.limit]
            daily = daily[daily['stock_code'].isin(set(keep))]
            print(f'[限制] 只处理前 {len(keep)} 只')

        tpl_cross = None
        if args.template_part == 'pre':
            if args.fresh_days != 0:
                print(f'[口径] pre 模式强制 金叉新鲜度=0（原 {args.fresh_days} 已忽略）')
            args.fresh_days = 0
            args.alert_day = 'confirm'
            (tpl_dif_z, tpl_dea_z, L, mp, t_name,
             tpl_cross) = build_template_pre(daily, args.code, args.start,
                                             args.end, args.pre_cross)
        else:
            tpl_dif_z, tpl_dea_z, L, mp, t_name = build_template(
                daily, args.code, args.start, args.end, refresh=args.refresh)
        d1 = (pd.Timestamp(args.date_to) if args.date_to
              else pd.Timestamp(daily['trade_date'].max()))
        d0 = pd.Timestamp(args.date_from)
        n_days = int(daily.loc[(daily['trade_date'] >= d0) &
                               (daily['trade_date'] <= d1), 'trade_date'].nunique())

        print(f'\n--- 匹配池「{label}」{daily["stock_code"].nunique()} 只 / '
              f'{len(daily):,} 行 ---')
        print(f'  模板 {args.code} {t_name} {args.start}~{args.end} (L={L})  '
              f'MACD={mp}')
        print(f'  回放区间 {d0.date()} ~ {d1.date()}（{n_days} 个交易日）  '
              f'阈值 {args.threshold}  金叉新鲜度 <= {args.fresh_days} 根')

        df, st = scan_range(daily, tpl_dif_z, tpl_dea_z, L, mp, d0, d1,
                            threshold=args.threshold, fresh_days=args.fresh_days,
                            dedup=args.dedup)
        for k in stats:
            stats[k] += st[k]
        n_stock = df['stock_code'].nunique() if len(df) else 0
        print(f'  [结果] 命中 {len(df)} 次 / 涉及 {n_stock} 只个股'
              f'（跳过：数据不足 {st["short"]} / 区间内无金叉 {st["no_cross"]}）')
        pool_labels.append(label)
        if len(df):
            frames.append(df.assign(pool=label))

        if args.check:
            _compare_with_single_day(daily, tpl_dif_z, tpl_dea_z, L, mp,
                                     args.check, args.threshold, args.fresh_days)


    hits = pd.concat(frames, ignore_index=True) if frames else \
        pd.DataFrame(columns=HIST_COLS)
    if 'pool' in hits.columns:
        hits = (hits.drop(columns=['pool']) if hits['pool'].nunique() <= 1
                else hits[['pool'] + HIST_COLS])

    events = merge_events(hits, alert_day=args.alert_day)
    summary = stock_summary(events)
    n_days_hit = int(hits['data_day'].nunique()) if len(hits) else 0

    print('\n' + '=' * 104)
    print(f'  Route B 买点列表   区间 {d0.date()} ~ {d1.date()}   '
          f'阈值 {args.threshold}   金叉新鲜度 <= {args.fresh_days} 根   '
          f'NMS {args.dedup:g}   提示日 {args.alert_day}')
    print('=' * 104)
    if not len(hits):
        print('  区间内无买点命中。')
        print(f'  跳过统计：数据不足 {stats["short"]} / '
              f'区间内无金叉 {stats["no_cross"]}')
    else:
        print(f'  合计逐日命中 {len(hits)} 次 / {hits["stock_code"].nunique()} 只个股 / '
              f'{n_days_hit} 个交易日'
              f'；折叠为「一次金叉=一个买点」后 {len(events)} 个信号')
        print('\n  [个股汇总] 按「买点次数 → 最近买点日」降序：')
        print(summary.to_string(index=False))
        print('\n  [买点明细] 一次金叉=一个买点：'
              + ('信号日=金叉日，建仓价=金叉日收盘（回测 cross 口径）'
                 if args.alert_day == 'cross'
                 else '信号日=首个命中日，建仓价=当日收盘'))
        print(events.to_string(index=False))

    meta = {
        '回放区间': f'{d0.date()} ~ {d1.date()}',
        '股票池': '、'.join(dict.fromkeys(pool_labels)),
        '模板': (f'{args.code} {t_name} {args.start}~{args.end}（L={L}）'
                 + (f'｜只取金叉前段：起点~{tpl_cross}' if tpl_cross else '')),
        'MACD参数': f'fast={mp[0]} / slow={mp[1]} / signal={mp[2]}',
        'score阈值': args.threshold,
        '金叉新鲜度上限（根）': args.fresh_days,
        'NMS重叠阈值': args.dedup,
        '提示日口径': ('cross = 信号日/建仓价回填金叉日（与回测 cross 一致，含前视）'
                   if args.alert_day == 'cross'
                   else 'confirm = 首个命中窗口日收盘（可实盘）'),
        '逐日命中次数': len(hits),
        '折叠后买点数（一次金叉=一个）': len(events),
        '涉及个股数': int(hits['stock_code'].nunique()) if len(hits) else 0,
        '有命中的交易日数': n_days_hit,
        '数据同步': sync_note,
        '数据源': 'backward_tdx_daily（后复权）',
        '说明': '买点 = 窗口内最后一次金叉且新鲜、score≥阈值的窗口终点；'
                '「信号列表」把同一金叉的连续命中折叠为一次买点；'
                '--alert-day cross 时信号日/建仓价回填金叉日（对齐回测 cross 买点）',
    }
    stamp = f'{d0.strftime("%Y%m%d")}_{d1.strftime("%Y%m%d")}'
    out = args.out or os.path.join(
        args.out_dir, f'buy_alert_hist_{stamp}_s{args.threshold:.2f}.xlsx')
    detail_cols = (['pool'] if 'pool' in hits.columns else []) + HIST_COLS
    _write(out, hits.reindex(columns=detail_cols), events, summary, meta)
    print(f'\n[OK] 已保存: {out}   （逐日命中 {len(hits)} 次 / '
          f'折叠后 {len(events)} 个买点 / {len(summary)} 只个股）')
    return 0



if __name__ == '__main__':
    sys.exit(main())
