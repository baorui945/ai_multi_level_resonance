# -*- coding: utf-8 -*-
"""单年度退出规则回测报告：汇总指标 + 逐笔明细 + 分月/分季度 + 出场原因。

用法：
    python report_single_year.py --year 2025 --max-pos 5
    python report_single_year.py --year 2025 --max-pos 42 --out output/xxx.md

口径与 `backtest_exit_rules.py` 完全一致（自然年 1/1~12/31、收盘买/收盘卖、单边 10bp、
单笔投入权益 1/max_pos、空仓资金 0 收益），基准为沪深 300。
"""
import argparse
import os

import pandas as pd

import backtest_exit_rules as B

HYB = '10日减半+13日清仓+5%移动止盈'
SHOW = ['规则', '笔数', '总收益%', '年化%', '最大回撤%', '夏普', '卡玛',
        '基准收益%', '超额收益%', '基准同仓位收益%', '超额(同仓位)%',
        '信息比率', 'Beta', '逐笔均值%', '逐笔中位%', '逐笔胜率%',
        '平均持有日', '平均仓位比例', '有仓天数占比%', '最好%', '最差%']


def period_returns(nav, freq):
    r = nav['净值'].resample(freq).last().pct_change()
    first = nav['净值'].resample(freq).first()
    r.iloc[0] = nav['净值'].resample(freq).last().iloc[0] / first.iloc[0] - 1
    s = nav['基准净值'].resample(freq).last().pct_change()
    s.iloc[0] = nav['基准净值'].resample(freq).last().iloc[0] / \
        nav['基准净值'].resample(freq).first().iloc[0] - 1
    out = pd.DataFrame({'策略%': r * 100, '沪深300%': s * 100})
    out['超额%'] = out['策略%'] - out['沪深300%']
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--year', type=int, default=2025)
    ap.add_argument('--list', default=None)
    ap.add_argument('--max-pos', type=int, default=5)
    ap.add_argument('--cost-bp', type=float, default=10.0)
    ap.add_argument('--out', default=None)
    args = ap.parse_args()
    lst = args.list or os.path.join('output', f'shape_gc_099_fwd10_wkmacd_{args.year}.xlsx')
    out = args.out or os.path.join('output', f'exit_rules_{args.year}_report.md')

    ns = argparse.Namespace(list=lst, max_pos=args.max_pos, cost_bp=args.cost_bp,
                            no_bench=False, cal_start=f'{args.year}-01-01',
                            cal_end=f'{args.year}-12-31')
    navs, summ, trades, n_sig = B.run(ns)
    nav = navs[HYB]
    hyb = trades[trades['规则'] == HYB].sort_values('建仓日')
    sig = pd.read_excel(lst, sheet_name=0, dtype={'代码': str})
    sig['金叉日'] = pd.to_datetime(sig['金叉日(=窗口终点)'])

    md = [f'# {args.year} 年退出规则回测（仓位上限 {args.max_pos}，单边 {args.cost_bp:g}bp）', '',
          f'* 信号清单：`{lst}`（阈值 0.99 + 周线 MACD 高位死叉过滤），共 {n_sig} 个事件；'
          f'实际建仓（混合规则）{len(hyb)} 笔。',
          f'* 回测日历：{nav.index[0].date()} ~ {nav.index[-1].date()}（{len(nav)} 个交易日）。', '',
          '## 一、汇总指标', '', '| ' + ' | '.join(SHOW) + ' |',
          '|' + '---|' * len(SHOW)]
    for _, r in summ[SHOW].iterrows():
        md.append('| ' + ' | '.join(f'{v:.2f}' if isinstance(v, float) else str(v)
                                    for v in r) + ' |')

    md += ['', '## 二、分月收益（混合规则）', '', '| 月份 | 策略% | 沪深300% | 超额% |',
           '|---|---|---|---|']
    for d, r in period_returns(nav, 'ME').iterrows():
        md.append(f'| {d:%Y-%m} | {r["策略%"]:+.2f} | {r["沪深300%"]:+.2f} | {r["超额%"]:+.2f} |')
    q = period_returns(nav, 'QE')
    md += ['', '| 季度 | 策略% | 沪深300% | 超额% |', '|---|---|---|---|']
    for d, r in q.iterrows():
        md.append(f'| {d:%Y}Q{(d.month - 1) // 3 + 1} | {r["策略%"]:+.2f} | '
                  f'{r["沪深300%"]:+.2f} | {r["超额%"]:+.2f} |')

    md += ['', '## 三、出场原因分布（混合规则）', '',
           '| 出场原因 | 笔数 | 占比% | 均值% | 中位% | 胜率% |', '|---|---|---|---|---|---|']
    g = hyb.groupby('出场原因')['净收益%']
    for k, v in g:
        md.append(f'| {k} | {len(v)} | {len(v) / len(hyb) * 100:.0f} | {v.mean():+.2f} | '
                  f'{v.median():+.2f} | {(v > 0).mean() * 100:.0f} |')

    def table(df, title):
        rows = ['', f'## {title}', '',
                '| 代码 | 名称 | 建仓日 | 卖出日 | 持有日 | 净收益% | 峰值浮盈% | 出场原因 |',
                '|---|---|---|---|---|---|---|---|']
        for _, r in df.iterrows():
            rows.append(f'| {r["代码"]} | {r["名称"]} | {r["建仓日"]:%Y-%m-%d} | '
                        f'{r["卖出日"]:%Y-%m-%d} | {r["持有交易日"]} | {r["净收益%"]:+.2f} | '
                        f'{r["峰值浮盈%"]:.2f} | {r["出场原因"]} |')
        return rows

    md += table(hyb, '四、混合规则逐笔明细（按建仓日）')
    top = hyb.nlargest(5, '净收益%')
    bot = hyb.nsmallest(5, '净收益%')
    md += table(top, '五、盈利前 5 笔')
    md += table(bot, '六、亏损前 5 笔')

    # 信号密度
    sd = sig.groupby(sig['金叉日'].dt.to_period('M')).size()
    day_cnt = sig.groupby('金叉日').size()
    md += ['', '## 七、信号密度与仓位', '',
           f'* 信号 {n_sig} 个，分布在 {sig["金叉日"].nunique()} 个交易日；'
           f'单日最多 {day_cnt.max()} 个（{day_cnt.idxmax():%Y-%m-%d}），'
           f'单日 ≥10 个的天数 {(day_cnt >= 10).sum()}。',
           f'* 实际建仓 {len(hyb)} 笔，被跳过 {n_sig - len(hyb)} 个（满仓/已在持仓/停牌/无数据）。',
           f'* 平均仓位 {nav["持仓数"].mean() / args.max_pos * 100:.1f}%，'
           f'有仓天数占比 {(nav["持仓数"] > 0).mean() * 100:.1f}%，'
           f'最多同时持仓 {nav["持仓数"].max()} 只。', '',
           '| 月份 | 信号数 |', '|---|---|']
    for d, v in sd.items():
        md.append(f'| {d} | {v} |')

    # 逐笔收益分布与集中度
    r = hyb['净收益%']
    win, lose = r[r > 0], r[r < 0]
    top3 = r.nlargest(3).sum()
    md += ['', '## 八、逐笔收益分布与集中度（混合规则）', '',
           f'* 笔数 {len(r)}，均值 {r.mean():+.2f}%，中位 {r.median():+.2f}%，'
           f'标准差 {r.std():.2f}%，区间 {r.min():+.2f}% ~ {r.max():+.2f}%。',
           f'* 胜率 {(r > 0).mean() * 100:.1f}%，盈亏比（平均盈利/平均亏损）'
           f'{win.mean() / abs(lose.mean()):.2f}。',
           f'* 前 3 笔合计 {top3:+.2f} 个百分点，占全部逐笔收益合计 '
           f'{top3 / r.sum() * 100:.0f}%（收益高度集中在少数交易）。', '',
           '| 建仓月份 | 笔数 | 均值% | 中位% | 胜率% |', '|---|---|---|---|---|']
    for d, g in hyb.groupby(hyb['建仓日'].dt.to_period('M')):
        md.append(f'| {d} | {len(g)} | {g["净收益%"].mean():+.2f} | '
                  f'{g["净收益%"].median():+.2f} | {(g["净收益%"] > 0).mean() * 100:.0f} |')

    text = '\n'.join(md) + '\n'
    with open(out, 'w', encoding='utf-8') as f:
        f.write(text)
    print(text)
    print('[OK] 已保存', out)


if __name__ == '__main__':
    main()
