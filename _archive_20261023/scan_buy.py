# -*- coding: utf-8 -*-
"""
==============================================================================
scan_buy.py —— Route B 买点提示（模板形状匹配 + 金叉新鲜度）· 最新日线扫描
==============================================================================
复用 `template_scan` 的模板匹配口径（DIF/DEA 曲线 z-score 后滑动互相关），
在**最新交易日**上判定是否出现 Route B 的「买点」，命中则在控制台提示并落盘。

买点定义（P2 宽容档，默认）
---------------------------
对每只股票取市场最新交易日 t 的 K 线：
  1. 窗口终点 = t，窗口 W = [t-L+1, t]（L = 模板长度）；
  2. 形状相似度 score = (corr(DIF_W, 模板DIF_z) + corr(DEA_W, 模板DEA_z)) / 2
     且 score >= --threshold；
  3. 窗口 W 内**最后一次金叉**位于 b，满足 t - b <= --fresh-days（默认3）；
  4. 三者同时成立 → 今日买点，建仓价 = 当日收盘价（与 _trade_calc(rule='last') 一致）。

  --fresh-days 0 即退化为严格档：窗口终点 = 最新日 且 **当日就是金叉**。

数据来源与增量同步
------------------
* 扫描真源：`backward_tdx_daily`（后复权），经 `mlr.settings.TABLE_DAILY` 对接，
  本地镜像缓存 `data/_cache/daily_px.parquet`。
* 每次扫描前先做**上游增量同步**：查上游表 `backward_daily`（backward_tdx_daily
  的数据来源）的最新交易日：
    - 若它 **新于** 本地缓存最新日 → 增量拉取 `trade_date > 缓存最新日` 的行并入
      缓存（同一 (stock_code, trade_date) 以真源表 backward_tdx_daily 优先，
      真源表尚未跟上的行回退取自 backward_daily）；
    - 若它 **不新于** 缓存最新日 → **不更新**，直接用现有缓存。
* `--no-sync` 跳过该判断；`--refresh` 忽略增量、全量重拉。

用法::

    python scan_buy.py                                  # 沪深300，最新日，fresh=3
    python scan_buy.py --universe all --fresh-days 5    # 全市场，金叉<=5根，Excel落 output/scanDaily
    python scan_buy.py --universe all                   # 全市场（只读缓存）
    python scan_buy.py --fresh-days 0 --threshold 0.98  # 严格档(当日金叉)
    python scan_buy.py --code 601898 --start 2025-11-11 --end 2026-03-16
    python scan_buy.py --date 2026-10-16                # 回放指定交易日
    python scan_buy.py --out output\\buy_alert.csv      # 指定输出（.csv 合并单表 / .xlsx 分表）

「近似观察池」条数可调（`--near`，默认10，0=关闭）。

产物：默认 `output/scanDaily/buy_alert_<YYYYMMDD>.xlsx`（`--out-dir` 可改），
worksheets：`买点命中`（is_hit=True）/ `近似观察池`（未达阈值 Top N）/`口径说明`。
`--out xxx.csv` 则输出合并单表（含 is_hit 列）。
"""
import argparse
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mlr import data as D, settings as S                        # noqa: E402
from mlr.macd import macd as calc_macd, cross_flags             # noqa: E402
from template_scan import _resolve_pool, sliding_corr, zscore   # noqa: E402


# 提示与落盘用的列顺序（不含内部列 is_hit）
OUT_COLS = ['stock_code', 'stock_name', 'close', 'score', 'corr_dif', 'corr_dea',
            'cross_date', 'cross_close', 'bars_since_cross', 'dif', 'dea', 'hist',
            'shrink_run', 'win_ret_pct']


def _ordered(df):
    """落盘列顺序：多池时 pool 列在前，其余按 OUT_COLS。"""
    cols = (['pool'] if 'pool' in df.columns else []) + OUT_COLS
    return df.reindex(columns=cols)


# ═════════════════════════════════════════════════════════════════════════
# 模板提取（复用 template_scan 口径）
# ═════════════════════════════════════════════════════════════════════════
def _series_from(daily, code):
    """从已加载日线里取单只股票 (close, dates, dif, dea, name, macd_p)；无则 None。"""
    cfg = S.load_config()
    mp = (cfg['macd']['fast'], cfg['macd']['slow'], cfg['macd']['signal'])
    g = daily[daily['stock_code'] == code].sort_values('trade_date')
    if g.empty:
        return None
    close = g['close_price'].to_numpy('float64')
    dates = pd.to_datetime(g['trade_date']).to_numpy()
    dif, dea, _ = calc_macd(close, *mp)
    return close, dates, dif, dea, g['stock_name'].iloc[-1], mp


def build_template(daily, code, start, end, refresh=False):
    """构造模板 z 序列；返回 (tpl_dif_z, tpl_dea_z, L, mp, name)。"""
    ser = _series_from(daily, code)
    if ser is None:
        ser = _series_from(D.load_daily(codes=[code], refresh=refresh), code)
    if ser is None:
        raise SystemExit(f'数据中找不到模板股票 {code}')
    _close, t_dates, t_dif, t_dea, t_name, mp = ser
    m = (t_dates >= np.datetime64(start)) & (t_dates <= np.datetime64(end))
    if int(m.sum()) < 8:
        raise SystemExit(
            f'模板区间 {start}~{end} 内交易日不足（{int(m.sum())}）')
    return zscore(t_dif[m]), zscore(t_dea[m]), int(m.sum()), mp, t_name


# ═════════════════════════════════════════════════════════════════════════
# 买点判定（P2：窗口终点 = 目标日 t，最后一次金叉距今 <= fresh_days）
# ═════════════════════════════════════════════════════════════════════════
def scan_buys(daily, tpl_dif_z, tpl_dea_z, L, mp, target_day,
              threshold=0.98, fresh_days=3):
    """在 daily 上逐票判定 target_day 是否为 Route B 买点。

    Args:
        daily:      长表日线（stock_code/trade_date/...）。
        tpl_dif_z:  模板 DIF z-score 序列（长度 L）。
        tpl_dea_z:  模板 DEA z-score 序列（长度 L）。
        L:          模板长度。
        mp:         MACD 参数 (fast, slow, signal)。
        target_day: 目标交易日（pd.Timestamp）；窗口终点。
        threshold:  score 阈值。
        fresh_days: 最后一次金叉距今（根数）上限；0 = 当日金叉。

    Returns:
        (df_all, stats) —— df_all 为「窗口内最后一次金叉且金叉新鲜」的全部股票，
        含 `is_hit` 列（score >= threshold 记 True）；stats 为跳过计数。
    """
    rows = []
    n_no_bar = n_short = n_no_cross = 0
    for code, g in daily.groupby('stock_code', sort=False):
        g = g.sort_values('trade_date')
        dates = pd.to_datetime(g['trade_date']).to_numpy()
        # 目标日必须在该股数据中（停牌 → 当日无 K 线则跳过）
        loc = np.where(dates == np.datetime64(target_day))[0]
        if loc.size == 0:
            n_no_bar += 1
            continue
        i_t = int(loc[-1])
        k = i_t - L + 1                       # 窗口起点索引
        if k < 0 or i_t + 1 < L:
            n_short += 1
            continue
        close = g['close_price'].to_numpy('float64')
        name = g['stock_name'].iloc[-1]
        dif, dea, hist = calc_macd(close, *mp)
        if dif.size < L or not np.isfinite(dif[i_t]) or not np.isfinite(dea[i_t]):
            n_short += 1
            continue
        # 窗口 [k, i_t] 与模板的形状相似度
        cd = sliding_corr(dif, tpl_dif_z)     # 长度 n-L+1；索引 k 对应窗口 [k, k+L-1]
        ce = sliding_corr(dea, tpl_dea_z)
        if cd.size == 0 or k >= cd.size:
            n_short += 1
            continue
        corr_dif, corr_dea = float(cd[k]), float(ce[k])
        score = (corr_dif + corr_dea) / 2.0

        # 窗口内最后一次金叉（DIF 上穿 DEA）
        x = cross_flags(dif[:i_t + 1], dea[:i_t + 1])
        xs = np.where(x == 1)[0]
        if xs.size == 0:
            n_no_cross += 1
            continue
        b = int(xs[-1])
        bars_since = i_t - int(b)             # 距最后一次金叉根数
        if b < k:                             # 金叉须落在匹配窗口内
            n_no_cross += 1
            continue
        if bars_since > int(fresh_days):      # 金叉不够新鲜 → 不入选
            continue

        # 当前「MACD柱连续缩短天数」（持仓状态参考，与卖出规则同口径）
        d = np.diff(hist) < 0
        last_bad = np.maximum.accumulate(
            np.where(d, -1, np.arange(d.size)))
        run = np.zeros(hist.size, dtype=np.int64)
        run[1:] = np.arange(d.size) - last_bad
        shrink_run = int(run[i_t])

        rows.append({
            'stock_code': code, 'stock_name': name,
            'data_day': pd.Timestamp(target_day).date(),
            'close': round(float(close[i_t]), 3),
            'score': round(score, 4),
            'corr_dif': round(corr_dif, 4),
            'corr_dea': round(corr_dea, 4),
            'is_hit': bool(score >= threshold),
            'cross_date': pd.Timestamp(dates[b]).date(),
            'cross_close': round(float(close[b]), 3),
            'bars_since_cross': bars_since,
            'dif': round(float(dif[i_t]), 4),
            'dea': round(float(dea[i_t]), 4),
            'hist': round(float(hist[i_t]), 4),
            'shrink_run': shrink_run,
            'win_ret_pct': round(
                float(close[i_t] / close[k] - 1) * 100, 2) if close[k] else None,
        })
    df = pd.DataFrame(rows)
    if len(df):
        df = df.sort_values(['is_hit', 'bars_since_cross', 'score'],
                            ascending=[False, True, False]).reset_index(drop=True)
    stats = {'no_bar': n_no_bar, 'short': n_short, 'no_cross': n_no_cross}
    return df, stats


# ═════════════════════════════════════════════════════════════════════════
# 输出
# ═════════════════════════════════════════════════════════════════════════
def _write_out(path, hits, near=None, meta=None):
    """结果落盘。

    * `.xlsx` → 分表：``买点命中`` / ``近似观察池`` / ``口径说明``；
    * `.csv`  → 合并单表（保留 is_hit 列，True=真买点，False=近似候选）。
    """
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    hits = _ordered(hits if hits is not None
                    else pd.DataFrame(columns=OUT_COLS))
    near = _ordered(near if near is not None
                    else pd.DataFrame(columns=OUT_COLS))
    if path.lower().endswith(('.xlsx', '.xls')):
        with pd.ExcelWriter(path, engine='openpyxl') as xw:
            hits.to_excel(xw, sheet_name='买点命中', index=False)
            near.to_excel(xw, sheet_name='近似观察池', index=False)
            if meta:
                pd.DataFrame(list(meta.items()),
                             columns=['项目', '值']).to_excel(
                    xw, sheet_name='口径说明', index=False)
        try:
            from mlr.report import _style_openpyxl
            _style_openpyxl(path)
        except Exception:                            # noqa: BLE001
            pass
    else:
        both = pd.concat([x for x in (hits.assign(is_hit=True),
                                       near.assign(is_hit=False)) if len(x)],
                         ignore_index=True)
        if not len(both):
            both = pd.DataFrame(columns=OUT_COLS + ['is_hit'])
        both.to_csv(path, index=False, encoding='utf-8-sig')
    return path


def build_parser():
    ap = argparse.ArgumentParser(
        prog='scan_buy',
        description='Route B 买点提示（模板形状匹配 + 金叉新鲜度）· 最新日线扫描')
    ap.add_argument('--code', default='601898', help='模板股票代码（默认601898）')
    ap.add_argument('--start', default='2025-11-11', help='模板起始日')
    ap.add_argument('--end', default='2026-03-16', help='模板结束日')
    ap.add_argument('--universe', default=None,
                    help='股票池 hs300 / coal / all，可逗号分隔（默认取配置 universe.default）')
    ap.add_argument('--threshold', type=float, default=0.98,
                    help='形状相似度 score 阈值（默认0.98）')
    ap.add_argument('--fresh-days', type=int, default=3,
                    help='窗口内最后一次金叉距今（根数）上限（默认3；0=当日金叉严格档）')
    ap.add_argument('--date', default=None, help='目标交易日（默认数据最新交易日）')
    ap.add_argument('--near', type=int, default=10,
                    help='无命中时展示「金叉新鲜但未达阈值」的近似观察池条数（默认10，0=关闭）')
    ap.add_argument('--limit', type=int, default=None, help='每池只处理前 N 只（冒烟）')
    ap.add_argument('--no-sync', action='store_true',
                    help='跳过「上游 backward_daily 最新日」判断与增量同步（不动缓存）')
    ap.add_argument('--refresh', action='store_true',
                    help='忽略增量判断，全量重拉日线缓存（默认关闭）')
    ap.add_argument('--out', default=None,
                    help='输出路径（.xlsx / .csv）；默认 <out-dir>/buy_alert_<日期>.xlsx')
    ap.add_argument('--out-dir', default=os.path.join(S.OUTPUT_DIR, 'scanDaily'),
                    help='默认输出目录（默认 output/scanDaily）')
    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)
    cfg = S.load_config()
    default = cfg.get('universe', {}).get('default', 'hs300')
    pools = [p.strip() for p in (args.universe or default).split(',') if p.strip()]

    print('=' * 78)
    print('  Route B 买点扫描 —— 模板形状匹配 + 金叉新鲜度（P2 宽容档）')
    print('=' * 78)

    S.ensure_dirs()

    # ── 数据同步：上游 backward_daily 有新数据才增量更新，否则不动 ────────
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

    all_hits, stats = [], {'no_bar': 0, 'short': 0, 'no_cross': 0}
    pool_labels = []
    t_label, t_name = None, None

    for pool in pools:
        codes, label = _resolve_pool(pool)
        daily = D.load_daily(codes=codes, refresh=args.refresh)
        if args.limit:
            keep = sorted(daily['stock_code'].unique())[:args.limit]
            daily = daily[daily['stock_code'].isin(set(keep))]
            print(f'[限制] 只处理前 {len(keep)} 只')

        tpl_dif_z, tpl_dea_z, L, mp, t_name = build_template(
            daily, args.code, args.start, args.end, refresh=args.refresh)

        target_day = (pd.Timestamp(args.date) if args.date
                      else pd.Timestamp(daily['trade_date'].max()))
        t_label = target_day.date()

        print(f'\n--- 匹配池「{label}」{daily["stock_code"].nunique()} 只 / '
              f'{len(daily):,} 行 ---')
        print(f'  模板 {args.code} {t_name} {args.start}~{args.end} (L={L})  '
              f'MACD={mp}')
        print(f'  目标交易日 {t_label}   阈值 {args.threshold}   '
              f'金叉新鲜度 <= {args.fresh_days} 根')
        df, st = scan_buys(daily, tpl_dif_z, tpl_dea_z, L, mp, target_day,
                           threshold=args.threshold, fresh_days=args.fresh_days)
        for k in stats:
            stats[k] += st[k]
        n_hit = int(df['is_hit'].sum()) if len(df) else 0
        print(f'  [结果] 命中 {n_hit} 只 / 金叉新鲜候选 {len(df)} 只'
              f'（跳过：当日无K线 {st["no_bar"]} / 数据不足 {st["short"]}'
              f' / 窗口内无金叉 {st["no_cross"]}）')
        pool_labels.append(label)
        if len(df):
            all_hits.append(df.assign(pool=label))

    # ── 提示 ─────────────────────────────────────────────────────────────
    print('\n' + '=' * 100)
    print(f'  Route B 买点提示   数据日: {t_label}   数据源: backward_tdx_daily'
          f'（{sync_note}）')
    print('=' * 100)
    stamp = pd.Timestamp(t_label).strftime('%Y%m%d')
    out = args.out or os.path.join(args.out_dir, f'buy_alert_{stamp}.xlsx')
    meta = {
        '数据日': str(t_label),
        '数据源': 'backward_tdx_daily（后复权）',
        '数据同步': sync_note,
        '股票池': '、'.join(dict.fromkeys(pool_labels)),
        '模板': f'{args.code} {t_name} {args.start}~{args.end}（L={L}）',
        'MACD参数': f'fast={mp[0]} / slow={mp[1]} / signal={mp[2]}',
        'score阈值': args.threshold,
        '金叉新鲜度上限（根）': args.fresh_days,
        '命中数': 0,
        '近似候选数': 0,
        '近似观察池写入条数': 0,
    }
    if not all_hits:
        print('  今日无买点命中，且无「金叉新鲜」的候选股票。')
        print(f'  跳过统计：当日无K线 {stats["no_bar"]} / 数据不足 '
              f'{stats["short"]} / 窗口内无金叉 {stats["no_cross"]}')
        _write_out(out, pd.DataFrame(columns=OUT_COLS),
                   pd.DataFrame(columns=OUT_COLS), meta)
        print(f'\n[OK] 已保存（空表）: {out}')
        return 0

    hits = (pd.concat(all_hits, ignore_index=True)
              .sort_values(['is_hit', 'bars_since_cross', 'score'],
                           ascending=[False, True, False]).reset_index(drop=True))
    cols = OUT_COLS
    hit = hits[hits['is_hit']]
    near = hits[~hits['is_hit']]
    near_top = (near.sort_values('score', ascending=False).head(args.near)
                if args.near > 0 else near.iloc[0:0])
    if len(hit):
        print(f'  今日买点命中 {len(hit)} 只（按「金叉新鲜度 → score」排序）：')
        print(hit[cols].to_string(index=False))
    else:
        print(f'  今日无买点命中（阈值 {args.threshold}，金叉新鲜度 '
              f'<={args.fresh_days} 根）。')
        print(f'  跳过统计：当日无K线 {stats["no_bar"]} / 数据不足 '
              f'{stats["short"]} / 窗口内无金叉 {stats["no_cross"]}')
    if len(near_top):
        print(f'\n  [近似观察池] 金叉新鲜但形状相似度未达阈值'
              f'（Top {len(near_top)}，按 score 降序）：')
        print(near_top[cols].to_string(index=False))

    meta.update({'命中数': len(hit), '近似候选数': len(near),
                 '近似观察池写入条数': len(near_top)})
    _write_out(out, hit, near_top, meta)
    print(f'\n[OK] 已保存: {out}   （命中 {len(hit)} 只 / 近似候选 {len(near)} 只，'
          f'写入 Top {len(near_top)}）')
    return 0


if __name__ == '__main__':
    sys.exit(main())


