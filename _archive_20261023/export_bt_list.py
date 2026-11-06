# -*- coding: utf-8 -*-
"""导出「回测口径（0.98 + cross + NMS）」的年度个股清单。

输入两份产物：

* `--scan`：`scan_buy_history.py --threshold 0.98 --fresh-days 999 --dedup 0.5
  --alert-day cross` 的「信号列表」工作表（信号日 = 金叉日 = 回测买入日）；
* `--bt`：`backtest_shrink.py --entry-mode cross` 的「逐笔明细」工作表。

输出一张 Excel：候选清单已按金叉日排序，并标注哪些被回测真正成交（资金槽位
接住的），带上确认日 / 滞后交易日 / 确认日较买价，便于区分「事后口径」与
「可实盘口径」。

用法::

    python export_bt_list.py --from 2026-01-01 --to 2026-12-31
    python export_bt_list.py --scan output\\scan_098_cross_dedup.xlsx --out output\\list.xlsx
"""
import argparse
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mlr import data as D                                        # noqa: E402

COLS = ['代码', '名称', '金叉日(=买入日)', '买入价', '窗口score', '确认日',
        '滞后交易日', '确认日收盘', '确认日较买价%', '回测成交', '卖出日',
        '净收益%', '备注']


def _lag_bars(cal, code, d0, d1):
    """该股自身交易日序列上，d0(不含) → d1(含) 的根数。"""
    ds = cal.get(code)
    if ds is None:
        return None
    return int(((ds > np.datetime64(d0)) & (ds <= np.datetime64(d1))).sum())


def main(argv=None):
    ap = argparse.ArgumentParser(prog='export_bt_list',
                                 description='导出回测口径（0.98+cross+NMS）的个股清单')
    ap.add_argument('--scan', default=os.path.join('output', 'scan_098_cross_dedup.xlsx'),
                    help='扫描产物（信号列表；信号日=金叉日）')
    ap.add_argument('--bt', default=os.path.join('output', 'backtest_shrink_all_K5.xlsx'),
                    help='回测产物（逐笔明细）')
    ap.add_argument('--from', dest='d0', default='2026-01-01', help='清单起点')
    ap.add_argument('--to', dest='d1', default='2026-12-31', help='清单终点')
    ap.add_argument('--scan-info',
                    default=os.path.join('output', 'scan_098_cross_all.xlsx'),
                    help='不去重的全量扫描（同一 0.98/cross 口径），用于取「首个达标日」；'
                         '缺省时回落到 --scan 自身')
    ap.add_argument('--template-code', default='601898', help='模板股，会在备注里标出')
    ap.add_argument('--out', default=os.path.join('output', 'bt_list_2026.xlsx'))
    args = ap.parse_args(argv)

    ev = pd.read_excel(args.scan, '信号列表')
    ev['代码'] = ev['stock_code'].astype(str).str.zfill(6)
    ev['金叉日(=买入日)'] = pd.to_datetime(ev['信号日']).dt.normalize()
    ev['买入价'] = ev['建仓价']
    ev['窗口score'] = ev['score']
    ev['名称'] = ev['stock_name']
    ev['确认日'] = pd.to_datetime(ev['确认日']).dt.normalize()

    # 「确认日 / 该日 score」取不去重全量的首个达标日（NMS 只决定候选集合）
    if os.path.exists(args.scan_info):
        inf = pd.read_excel(args.scan_info, '信号列表')
        inf['代码'] = inf['stock_code'].astype(str).str.zfill(6)
        inf['金叉日(=买入日)'] = pd.to_datetime(inf['信号日']).dt.normalize()
        inf = inf[['代码', '金叉日(=买入日)', '确认日', 'score']].rename(
            columns={'确认日': '确认日_info', 'score': 'score_info'})
        ev = ev.drop(columns=['确认日']).merge(
            inf, on=['代码', '金叉日(=买入日)'], how='left')
        ev['确认日'] = pd.to_datetime(ev['确认日_info']).dt.normalize()
        ev['窗口score'] = ev['score_info'].fillna(ev['窗口score'])
    info_path = args.scan_info if os.path.exists(args.scan_info) else args.scan

    bt = pd.read_excel(args.bt, '逐笔明细')
    bt['代码'] = bt['股票代码'].astype(str).str.zfill(6)
    bt['b'] = pd.to_datetime(bt['买入日']).dt.normalize()
    bt = bt[['代码', 'b', '卖出日', '买入价', '净收益(%)']].rename(
        columns={'b': '金叉日(=买入日)', '买入价': 'bt买价', '净收益(%)': '净收益%'})

    d = ev[(ev['金叉日(=买入日)'] >= pd.Timestamp(args.d0)) &
           (ev['金叉日(=买入日)'] <= pd.Timestamp(args.d1))].copy()
    d = d.merge(bt, on=['代码', '金叉日(=买入日)'], how='left')
    d['回测成交'] = np.where(d['卖出日'].notna(), '是', '否（槽位满/无持仓）')

    # 滞后交易日 & 确认日收盘（只加载一次日线）
    px = (D.load_daily(codes=sorted(d['代码'].unique()))
          .assign(dates=lambda x: pd.to_datetime(x['trade_date']),
                  px=lambda x: x['close_price']))
    cal = {c: g['dates'].sort_values().to_numpy()
           for c, g in px.groupby('stock_code')}
    px['k'] = pd.to_datetime(px['dates']).dt.normalize()
    close_map = {(r.stock_code, r.k): r.px for r in px.itertuples()}
    d['滞后交易日'] = [_lag_bars(cal, c, b, e)
                  for c, b, e in zip(d['代码'], d['金叉日(=买入日)'], d['确认日'])]
    d['确认日收盘'] = [close_map.get((c, e)) for c, e in
                  zip(d['代码'], d['确认日'])]
    d['确认日较买价%'] = ((d['确认日收盘'] / d['买入价'] - 1) * 100).round(2)
    d['备注'] = np.where(d['代码'] == args.template_code.zfill(6), '模板股', '')
    d = d.sort_values(['金叉日(=买入日)', '窗口score'], ascending=[True, False])

    out = d.reindex(columns=COLS).reset_index(drop=True)
    n_traded = int((out['回测成交'] == '是').sum())
    meta = pd.DataFrame([
        ('扫描产物（候选集，NMS 0.5）', os.path.abspath(args.scan)),
        ('确认日来源（不去重全量）', os.path.abspath(info_path)),
        ('回测产物', os.path.abspath(args.bt)),
        ('口径', '0.98 + cross（买在金叉日）+ NMS 0.5 + 不限新鲜度'),
        ('说明', '本表是「事后口径」：窗口在金叉后 14~32 根才达标，'
                 '买入价用的是金叉日收盘；「确认日」才是当时真能知道的日子'),
        ('候选笔数', len(out)),
        ('回测实际成交（K=5 槽位接住）', n_traded),
        ('未成交（槽位满被丢弃）', len(out) - n_traded),
        ('确认日晚于卖出日（逻辑不可能）',
         int(((out['回测成交'] == '是') & (out['确认日'] > pd.to_datetime(out['卖出日']))).sum())),
    ], columns=['项目', '值'])

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with pd.ExcelWriter(args.out, engine='openpyxl') as xw:
        out.to_excel(xw, sheet_name='2026候选清单', index=False)
        meta.to_excel(xw, sheet_name='口径说明', index=False)
    try:
        from mlr.report import _style_openpyxl
        _style_openpyxl(args.out)
    except Exception:                                   # noqa: BLE001
        pass
    print(f'[OK] 已保存 {args.out}：候选 {len(out)} 笔 / 回测成交 {n_traded} 笔')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
