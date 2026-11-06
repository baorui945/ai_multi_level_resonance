# -*- coding: utf-8 -*-
"""
==============================================================================
check_scan_bt.py —— 「扫描」与「回测」金叉日期对拍（验收脚本）
==============================================================================
用途
----
确认 `scan_buy_history.py` 输出的买点（金叉日）与 `backtest_shrink.py`
实际成交的买入日，是**同一批日期、同一批价格**。

口径要求（两侧必须一致，否则必然不匹配）
----------------------------------------
1. 模板 / 阈值 / MACD 参数 / 股票池 / 新鲜度完全相同；
2. **金叉当日口径**：两侧都用 `--fresh-days 0`，此时
   扫描的「信号日 = 金叉日」，回测的「买入日 = 金叉日」，天然无前视；
3. 回测建议加 `--overlap 0`：默认 0.5 的窗口非极大值抑制只会在**同一只股票内**
   合并重叠窗口（82 根窗口相邻两天重叠 81/82）。金叉当日口径下样本内未触发，
   但换阈值/模板后可能吞掉同股相隔几天的第二个金叉；
4. 若回测设了并发上限 K，超限的买点会被资金约束丢弃（不是日期不一致），
   本脚本会把「资金约束」页的丢弃笔数与缺失数对照打印。

成对命令::

    python scan_buy_history.py --universe all --from 2015-01-01 ^
        --threshold 0.90 --fresh-days 0 --no-sync ^
        --out output\\scan_f0.xlsx

    python backtest_shrink.py --universe all --threshold 0.90 ^
        --fresh-days 0 --overlap 0 --entry-mode confirm ^
        --max-concurrent -1 --nav-mode equal ^
        --bt-start 2015-01-01 --no-chart ^
        --out output\\bt_f0.xlsx

    python check_scan_bt.py output\\scan_f0.xlsx output\\bt_f0.xlsx

判定
----
* `[PASS] 金叉日期与买入价完全一致`：两侧事件集合相同（代码 + 日期）且收盘价一致；
* `[PASS] 金叉口径一致：N 个信号因并发上限 K 未成交`：日期集合本身无差异，
  差额与「资金约束」页记录的丢弃笔数吻合（K 限制了实际成交，不是口径问题）；
* `[FAIL]`：其它情况，打印仅单侧存在的事件与价格差异明细，返回码 1。
"""
import argparse
import sys

import pandas as pd

SCAN_EVENT_COLS = ['stock_code', 'stock_name', '金叉日', '建仓价']
BT_TRADE_COLS = ['股票代码', '买入日', '买入价']


def _read(path):
    xl = pd.ExcelFile(path)
    return xl


def _scan_events(xl):
    """取扫描侧「一次金叉=一个买点」的事件表。"""
    sheet = '信号列表' if '信号列表' in xl.sheet_names else xl.sheet_names[0]
    d = pd.read_excel(xl, sheet)
    if '金叉日' in d.columns and 'stock_code' in d.columns:
        ev = d[['stock_code', 'stock_name', '金叉日', '建仓价']].copy()
        ev['stock_code'] = ev['stock_code'].astype(str).str.zfill(6)
        ev['golden_date'] = pd.to_datetime(ev['金叉日']).dt.normalize()
        ev['price'] = ev['建仓价'].astype(float)
        ev['src'] = f'{sheet}（信号日=金叉日）'
        return ev[['stock_code', 'stock_name', 'golden_date', 'price', 'src']]
    # 兼容逐日明细：同一金叉只留首个命中日
    d['stock_code'] = d['stock_code'].astype(str).str.zfill(6)
    d['golden_date'] = pd.to_datetime(d['cross_date']).dt.normalize()
    d = d.sort_values('data_day').drop_duplicates(['stock_code', 'golden_date'])
    d['price'] = d['cross_close'].astype(float)
    d['stock_name'] = d.get('stock_name', '')
    d['src'] = f'{sheet}（逐日明细折叠）'
    return d[['stock_code', 'stock_name', 'golden_date', 'price', 'src']]


def _bt_trades(xl):
    """取回测侧逐笔成交（买入日=金叉日）。"""
    sheet = '逐笔明细' if '逐笔明细' in xl.sheet_names else xl.sheet_names[-1]
    d = pd.read_excel(xl, sheet)
    d = d[BT_TRADE_COLS].copy()
    d['股票代码'] = d['股票代码'].astype(str).str.zfill(6)
    d['golden_date'] = pd.to_datetime(d['买入日']).dt.normalize()
    d['price'] = d['买入价'].astype(float)
    return d.rename(columns={'股票代码': 'stock_code'})


def _cap_dropped(xl):
    """读「资金约束」页里的并发上限丢弃笔数（没有则 None）。"""
    if '资金约束' not in xl.sheet_names:
        return None
    c = pd.read_excel(xl, '资金约束')
    if c.shape[1] < 2:
        return None
    hit = c[c.iloc[:, 0].astype(str).str.contains('丢弃', na=False)]
    if hit.empty:
        return None
    try:
        return int(float(hit.iloc[0, 1]))
    except (TypeError, ValueError):
        return None


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog='check_scan_bt',
        description='扫描买点（金叉日）与回测买入日对拍')
    ap.add_argument('scan_xlsx', help='scan_buy_history.py 的输出 xlsx')
    ap.add_argument('bt_xlsx', help='backtest_shrink.py 的输出 xlsx')
    ap.add_argument('--tol', type=float, default=1e-6,
                    help='收盘价允许误差（默认 1e-6）')
    ap.add_argument('--show', type=int, default=20,
                    help='最多打印多少条差异（默认 20）')
    args = ap.parse_args(argv)

    sx, bx = _read(args.scan_xlsx), _read(args.bt_xlsx)
    sev, btr = _scan_events(sx), _bt_trades(bx)
    sev = sev.drop_duplicates(['stock_code', 'golden_date'])
    btr = btr.drop_duplicates(['stock_code', 'golden_date'])

    skey = set(zip(sev['stock_code'], sev['golden_date']))
    bkey = set(zip(btr['stock_code'], btr['golden_date']))
    only_s = sorted(skey - bkey)
    only_b = sorted(bkey - skey)
    inter = skey & bkey

    m = (sev.merge(btr, on=['stock_code', 'golden_date'],
                   suffixes=('_scan', '_bt')))
    dpx = (m['price_scan'] - m['price_bt']).abs()
    max_dpx = float(dpx.max()) if len(dpx) else 0.0

    dropped = _cap_dropped(bx)
    n_s, n_b = len(sev), len(btr)
    n_i = len(inter)
    print('=' * 78)
    print(f'  扫描事件 {n_s} 个（{sev["src"].iloc[0] if n_s else "-"}）')
    print(f'  回测买点 {n_b} 个（同日去重后）')
    print(f'  交集 {n_i} 个 / 仅扫描 {len(only_s)} 个 / 仅回测 {len(only_b)} 个')
    print(f'  同日买入价 max|Δ| = {max_dpx:.6g}（容差 {args.tol:g}）')
    if dropped is not None:
        print(f'  回测「资金约束」因并发上限丢弃 {dropped} 笔'
              f'（仅扫描 {len(only_s)} 个 → 差额可由此解释）')
    if only_s:
        print('\n  [仅扫描有] 代码 / 姓名 / 金叉日 / 扫描建仓价：')
        for c, d in only_s[:args.show]:
            r = sev[(sev['stock_code'] == c) & (sev['golden_date'] == d)].iloc[0]
            print(f'    {c}  {r["stock_name"]}  {d.date()}  {r["price"]:.3f}')
        if len(only_s) > args.show:
            print(f'    ... 其余 {len(only_s) - args.show} 条省略')
    if only_b:
        print('\n  [仅回测有] 代码 / 买入日 / 买入价：')
        for c, d in only_b[:args.show]:
            r = btr[(btr['stock_code'] == c) & (btr['golden_date'] == d)].iloc[0]
            print(f'    {c}  {d.date()}  {r["price"]:.3f}')
        if len(only_b) > args.show:
            print(f'    ... 其余 {len(only_b) - args.show} 条省略')

    exact = not only_s and not only_b and max_dpx <= args.tol
    # 口径一致、仅因并发上限未成交：扫描信号与回测信号仍是同一批金叉
    cap_only = (not only_b and max_dpx <= args.tol and bool(only_s)
                and dropped == len(only_s))
    if exact:
        msg = '[PASS] 金叉日期与买入价完全一致'
    elif cap_only:
        msg = (f'[PASS] 金叉口径一致：{len(only_s)} 个信号因并发上限 K 未成交，'
               '日期集合本身无差异')
    else:
        msg = '[FAIL] 存在日期或价格差异，见上表'
    print('\n  ' + msg)
    print('=' * 78)
    return 0 if (exact or cap_only) else 1


if __name__ == '__main__':
    sys.exit(main())
