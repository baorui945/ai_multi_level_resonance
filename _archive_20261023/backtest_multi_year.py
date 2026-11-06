# -*- coding: utf-8 -*-
"""多年份退出规则回测：逐年生成信号清单 → 逐年回测 → 链接成多年净值，并对沪深 300。

用法：
    python backtest_multi_year.py               # 默认 2019-2025
    python backtest_multi_year.py --years 2020-2025 --max-pos 5

说明：
* 信号清单复用 `export_shape_list.py` 的口径（阈值 0.99 + 周线高位死叉过滤），
  文件命名 `output/shape_gc_099_fwd10_wkmacd_{year}.xlsx`，缺失时自动生成。
* 组合口径与 `backtest_exit_rules.py` 完全一致（收盘买/收盘卖、单边 10bp、
  单笔投入权益 1/max_pos、现金无收益）。
* 年度净值按自然年（1/1~12/31）回测后再链接（每年年初净值重置为 1 后累乘），
  基准同样按年链接；同时输出「超额净值」与「同仓位基准超额」两种口径。
* **注意**：模板区间取自 2025-11~2026-02，属于“事后选出的形态”，历史年份
  回测存在模板选择偏差（见口径说明），结果偏乐观。
"""
import argparse
import os
from types import SimpleNamespace

import numpy as np
import pandas as pd

import backtest_exit_rules as B
import export_shape_list as E

MAIN_RULES = ('固定10日', '固定13日', '10日+5%移动止盈',
              '10日减半+13日清仓+5%移动止盈')


def ensure_list(year, threshold, prefix):
    path = os.path.join('output', f'{prefix}_{year}.xlsx')
    if os.path.isfile(path):
        return path
    E.main(['--threshold', str(threshold), '--fwd', '10', '--weekly-filter',
            '--from', f'{year}-01-01', '--to', f'{year}-12-31',
            '--out', path])
    return path


def chain_metrics(nav, bench, max_pos, bench_e=None):
    """链接后的净值指标（含基准/超额）。"""
    r = nav.pct_change().dropna()
    dd = (nav / nav.cummax() - 1).min()
    n = len(nav)
    ann = nav.iloc[-1] ** (252.0 / n) - 1
    b = bench / bench.iloc[0]
    rb = b.pct_change().dropna()
    ex = (r - rb.reindex(r.index)).dropna()
    rel = nav / b
    beta = np.cov(r.reindex(rb.index), rb)[0, 1] / np.var(rb) if len(rb) > 2 else np.nan
    out = {'总收益%': (nav.iloc[-1] - 1) * 100,
           '年化%': ann * 100,
           '最大回撤%': dd * 100,
           '夏普': r.mean() / r.std() * np.sqrt(252) if r.std() > 0 else np.nan,
           '卡玛': ann / abs(dd) if dd < 0 else np.nan,
           '基准收益%': (b.iloc[-1] - 1) * 100,
           '超额收益%': (rel.iloc[-1] - 1) * 100,
           '超额年化%': (rel.iloc[-1] ** (252.0 / n) - 1) * 100,
           '超额最大回撤%': (rel / rel.cummax() - 1).min() * 100,
           '信息比率': ex.mean() / ex.std() * np.sqrt(252) if ex.std() > 0 else np.nan,
           'Beta': beta}
    if bench_e is not None:
        be = bench_e / bench_e.iloc[0]
        out['基准同仓位收益%'] = (be.iloc[-1] - 1) * 100
        out['超额(同仓位)%'] = (nav.iloc[-1] / be.iloc[-1] - 1) * 100
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--years', default='2019-2025', help='如 2019-2025 或 2020,2023')
    ap.add_argument('--threshold', type=float, default=0.99)
    ap.add_argument('--max-pos', type=int, default=5)
    ap.add_argument('--cost-bp', type=float, default=10.0)
    ap.add_argument('--out', default=r'output\multi_year_exit.xlsx')
    args = ap.parse_args()
    years = ([int(y) for y in args.years.split(',')] if ',' in args.years else
             list(range(int(args.years.split('-')[0]), int(args.years.split('-')[1]) + 1)))
    prefix = f'shape_gc_{round(args.threshold * 100):03d}_fwd10_wkmacd'

    rows, per_rule_nav, per_rule_bench, per_rule_bench_e = [], {}, {}, {}
    cum = {r: 1.0 for r in MAIN_RULES}
    bcum = {r: 1.0 for r in MAIN_RULES}
    all_trades = []
    for y in years:
        lst = ensure_list(y, args.threshold, prefix)
        ns = SimpleNamespace(list=lst, max_pos=args.max_pos,
                             cost_bp=args.cost_bp, no_bench=False,
                             cal_start=f'{y}-01-01', cal_end=f'{y}-12-31')
        navs, summ, trades, n_sig = B.run(ns)
        for rule in MAIN_RULES:
            # run() 的日历延伸到数据末端，必须按自然年截断后再链接
            navy = navs[rule].loc[f'{y}-01-01':f'{y}-12-31']
            if navy.empty:
                continue
            s = navy['净值'] / navy['净值'].iloc[0]
            b = navy['基准净值'] / navy['基准净值'].iloc[0]
            be = navy['基准同仓位净值'] / navy['基准同仓位净值'].iloc[0]
            per_rule_nav.setdefault(rule, []).append(s * cum[rule])
            per_rule_bench.setdefault(rule, []).append(b * bcum[rule])
            per_rule_bench_e.setdefault(rule, []).append(be * bcum[rule])
            cum[rule] = (s * cum[rule]).iloc[-1]
            bcum[rule] = (b * bcum[rule]).iloc[-1]
            mm = chain_metrics(s, b, args.max_pos, be)
            m = summ[summ['规则'] == rule].iloc[0]
            rows.append({'年份': y, '规则': rule, '信号数': n_sig, '笔数': m['笔数'],
                         '策略收益%': mm['总收益%'],
                         '基准收益%': mm['基准收益%'],
                         '基准同仓位收益%': mm['基准同仓位收益%'],
                         '超额%': mm['超额收益%'],
                         '超额(同仓位)%': mm['超额(同仓位)%'],
                         '最大回撤%': mm['最大回撤%'], '夏普': mm['夏普'],
                         '逐笔胜率%': m['逐笔胜率%'], '平均持有日': m['平均持有日'],
                         '有仓天数占比%': (navy['持仓数'] > 0).mean() * 100,
                         '平均仓位比例': navy['持仓数'].mean() / args.max_pos})
        all_trades.append(trades)
        rec = {r['规则']: r for r in rows[-len(MAIN_RULES):]}
        print(f'[{y}] 信号 {n_sig:>3}  ' +
              '  '.join(f'{r}: {rec[r]["策略收益%"]:+.1f}%'
                        f'(基准{rec[r]["基准收益%"]:+.1f}%/同仓位{rec[r]["基准同仓位收益%"]:+.1f}%)'
                        for r in MAIN_RULES), flush=True)

    yr = pd.DataFrame(rows)
    nav_df, summ_rows = {}, []
    for rule in MAIN_RULES:
        s = pd.concat(per_rule_nav[rule]).sort_index()
        s = s[~s.index.duplicated(keep='last')]
        b = pd.concat(per_rule_bench[rule]).sort_index()
        b = b[~b.index.duplicated(keep='last')]
        b = b / b.iloc[0]
        be = pd.concat(per_rule_bench_e[rule]).sort_index()
        be = be[~be.index.duplicated(keep='last')]
        be = be / be.iloc[0]
        nav_df[rule] = pd.DataFrame({'净值': s, '基准净值': b,
                                     '基准同仓位净值': be, '超额净值': s / b})
        summ_rows.append({'规则': rule, '区间': f'{years[0]}~{years[-1]}',
                          '交易日数': len(s),
                          **chain_metrics(s, b, args.max_pos, be)})
    sm = pd.DataFrame(summ_rows)
    num = sm.select_dtypes('number').columns
    sm[num] = sm[num].round(2)

    tr = pd.concat(all_trades, ignore_index=True)
    tr = tr[tr['规则'].isin(MAIN_RULES)]
    st = (tr.groupby('规则')['净收益%']
            .agg(笔数='size', 均值='mean', 中位='median',
                 胜率=lambda x: (x > 0).mean() * 100, 最好='max', 最差='min')
            .reset_index())
    st['t值'] = [tr[tr['规则'] == r]['净收益%'].mean() /
                 (tr[tr['规则'] == r]['净收益%'].std(ddof=1) /
                  np.sqrt(len(tr[tr['规则'] == r]))) for r in st['规则']]

    print('\n年度对比：')
    print(yr.round(2).to_string(index=False))
    print('\n多年链接汇总：')
    print(sm.to_string(index=False))
    print('\n逐年汇总逐笔（含成本）：')
    print(st.round(2).to_string(index=False))

    png = args.out.replace('.xlsx', '.png')
    B.make_plot(nav_df, png, f'多年份回测：{years[0]}-{years[-1]}（阈值 {args.threshold}，'
                             f'仓位上限 {args.max_pos}）')
    with pd.ExcelWriter(args.out) as w:
        yr.round(3).to_excel(w, sheet_name='年度对比', index=False)
        sm.to_excel(w, sheet_name='多年汇总', index=False)
        pd.DataFrame({k: v['净值'] for k, v in nav_df.items()}).to_excel(w, sheet_name='日净值')
        st.round(3).to_excel(w, sheet_name='逐笔统计', index=False)
        tr.round(3).to_excel(w, sheet_name='逐笔明细', index=False)
    pd.DataFrame({k: v['净值'] for k, v in nav_df.items()}).to_csv(
        args.out.replace('.xlsx', '.csv'), encoding='utf-8-sig')
    print('\n[OK] 已保存', args.out, '/', png)


if __name__ == '__main__':
    main()

