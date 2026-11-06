# -*- coding: utf-8 -*-
"""退出规则组合回测：对比「固定 10 日」与「10 日减半 + 13 日清仓 + 5% 移动止盈」。

用法：
    python backtest_exit_rules.py
    python backtest_exit_rules.py --list output/shape_gc_099_fwd10_wkmacd_2026.xlsx \
        --max-pos 5 --cost-bp 10 --out output/exit_rules_nav.xlsx

口径：
* 建仓：金叉日（信号日）收盘价买入；卖出：规则触发日收盘价卖出（close_close）。
* 成本：买卖各 `--cost-bp` bp（默认 10bp，双边 0.2%）。
* 仓位：每个新信号投入当前权益的 1/max_pos（受现金约束，不足则用剩余现金）；
  空仓资金收益记 0。
* 移动止盈：累计浮盈首次 ≥5% 后激活，此后若收盘价较「建仓以来最高收盘」回撤 5%
  （close <= peak_close*0.95）则当日收盘清仓。
* 基准：沪深300（ABM `data/idx_000300.pkl`），同时给出
  「超额净值 = 策略/沪深300」与「同仓位基准」（按前一日实际仓位持指数、其余现金）。
* 本清单 42 笔均为不同个股，不存在同票重复建仓。
"""
import argparse

import numpy as np
import pandas as pd

from mlr import data as D
from mlr import settings as S


def load_bench(cal):
    """沪深 300 收盘（ABM data/idx_000300.pkl），按回测日历前向填充。"""
    dates, close = pd.read_pickle(S.IDX_PKL)
    idx = pd.to_datetime(dates.astype(str), format='%Y%m%d')
    s = pd.Series(close.astype(float), index=idx)
    s = s[~s.index.duplicated(keep='last')].sort_index()
    return s.reindex(cal).ffill().bfill()


RULES = {
    '固定10日': dict(hold=10),
    '固定13日': dict(hold=13),
    '固定20日': dict(hold=20),
    '10日+5%移动止盈': dict(hold=10, trail=.05),
    '10日减半+13日清仓': dict(hold=13, half=10),
    '10日减半+13日清仓+5%移动止盈': dict(hold=13, half=10, trail=.05),
}


def _metrics(nav, max_pos, bench=None):
    ret = nav['净值'] / nav['净值'].iloc[0] - 1
    dd = (nav['净值'] / nav['净值'].cummax() - 1).min()
    n = len(nav)
    ann = (1 + ret.iloc[-1]) ** (252.0 / n) - 1
    r = nav['净值'].pct_change().dropna()
    sharpe = r.mean() / r.std() * np.sqrt(252) if r.std() > 0 else np.nan
    out = {'总收益%': ret.iloc[-1] * 100, '年化%': ann * 100,
           '最大回撤%': dd * 100, '夏普': sharpe,
           '卡玛': ann / abs(dd) if dd < 0 else np.nan,
           '平均仓位比例': nav['持仓数'].mean() / max_pos,
           '有仓天数占比%': (nav['持仓数'] > 0).mean() * 100,
           '净值起始': str(nav.index[0].date()), '净值结束': str(nav.index[-1].date()),
           '交易日数': n}
    if bench is not None:
        b = bench / bench.iloc[0]
        rel = (nav['净值'] / nav['净值'].iloc[0]) / b
        rb = b.pct_change().dropna()
        ex = (r - rb.reindex(r.index)).dropna()
        beta = np.cov(r.reindex(rb.index), rb)[0, 1] / np.var(rb) if len(rb) > 2 else np.nan
        out.update({
            '基准收益%': (b.iloc[-1] - 1) * 100,
            '基准最大回撤%': (b / b.cummax() - 1).min() * 100,
            '超额收益%': (rel.iloc[-1] - 1) * 100,
            '超额年化%': (rel.iloc[-1] ** (252.0 / n) - 1) * 100,
            '超额最大回撤%': (rel / rel.cummax() - 1).min() * 100,
            '信息比率': ex.mean() / ex.std() * np.sqrt(252) if ex.std() > 0 else np.nan,
            'Beta': beta,
        })
        if '基准同仓位净值' in nav:
            be = nav['基准同仓位净值']
            out['基准同仓位收益%'] = (be.iloc[-1] - 1) * 100
            out['超额(同仓位)%'] = (nav['净值'].iloc[-1] / be.iloc[-1] - 1) * 100
    return out


def run(args):
    ev = pd.read_excel(args.list, sheet_name=0, dtype={'代码': str})
    ev['金叉日'] = pd.to_datetime(ev['金叉日(=窗口终点)']).dt.normalize()
    ev = ev.sort_values('金叉日').reset_index(drop=True)
    px = D.load_daily(verbose=False).sort_values(['stock_code', 'trade_date'])
    ser = {c: g.set_index('trade_date')['close_price'].astype(float)
           for c, g in px.groupby('stock_code')}
    cal = pd.DatetimeIndex(sorted(px.trade_date.unique()))
    lo = (pd.Timestamp(args.cal_start) if getattr(args, 'cal_start', None)
          else ev['金叉日'].min() - pd.Timedelta(days=10))
    hi = (pd.Timestamp(args.cal_end) if getattr(args, 'cal_end', None) else cal[-1])
    cal = cal[(cal >= lo) & (cal <= hi)]
    cost = args.cost_bp / 10000.0
    bench = None if getattr(args, 'no_bench', False) else load_bench(cal)
    sig_by_day = {t: g for t, g in ev.groupby('金叉日')}

    navs, summary, trade_rows = {}, [], []
    for name, cfg in RULES.items():
        cash, pos, rows, closed = 1.0, {}, [], []

        def rec(code, p):
            buy = p['base'] * p['qty_entry']
            sell = sum(q * c * (1 - cost) for _, q, _, c, _ in p['exits'])
            return {'规则': name, '代码': code, '名称': p['name'], '建仓日': p['entry'],
                    '持有交易日': p['bars'], '卖出日': p['exits'][-1][0],
                    '净收益%': (sell / buy - 1) * 100,
                    '出场原因': '+'.join(x[2] for x in p['exits']),
                    '峰值浮盈%': (p['peak'] - 1) * 100}

        for t in cal:
            # ---------- 1) 出场（个股当日有成交才判定）----------
            for code in list(pos):
                p = pos[code]
                s = ser[code]
                if t not in s.index:
                    continue
                p['bars'] += 1
                c = float(s.loc[t])
                if cfg.get('trail'):
                    ratio = c / p['base']
                    p['peak'] = max(p['peak'], ratio)
                    if not p['active'] and ratio - 1 >= cfg['trail']:
                        p['active'] = True
                    elif p['active'] and c <= p['base'] * p['peak'] * (1 - cfg['trail']):
                        cash += p['qty'] * c * (1 - cost)
                        p['exits'].append((t, p['qty'], '移动止盈', c, p['peak']))
                        closed.append(rec(code, p))
                        del pos[code]
                        continue
                if cfg.get('half') and p['bars'] == cfg['half']:
                    sell = p['qty'] * 0.5
                    cash += sell * c * (1 - cost)
                    p['qty'] -= sell
                    p['exits'].append((t, sell, '第%d日减半' % cfg['half'], c, p['peak']))
                if p['bars'] >= cfg['hold']:
                    cash += p['qty'] * c * (1 - cost)
                    p['exits'].append((t, p['qty'], '第%d日清仓' % cfg['hold'], c, p['peak']))
                    closed.append(rec(code, p))
                    del pos[code]
            # ---------- 2) 入场（信号日收盘）----------
            for _, r in sig_by_day.get(t, pd.DataFrame()).iterrows():
                code = r['代码']
                if code in pos or code not in ser or t not in ser[code].index:
                    continue
                eq = cash + sum(q['qty'] * float(ser[c2].asof(t)) for c2, q in pos.items())
                amt = min(cash, eq / args.max_pos)
                if amt <= 1e-9:
                    continue
                c = float(ser[code].loc[t])
                cash -= amt
                pos[code] = dict(qty=amt * (1 - cost) / c, qty_entry=amt * (1 - cost) / c,
                                 base=c, peak=1.0, active=False, bars=0,
                                 entry=t, name=r['名称'], exits=[])
            # ---------- 3) 盯市 ----------
            mv = sum(q['qty'] * float(ser[c2].asof(t)) for c2, q in pos.items())
            rows.append({'日期': t, '净值': cash + mv, '现金': cash, '持仓数': len(pos)})

        nav = pd.DataFrame(rows).set_index('日期')
        if bench is not None:
            nav['基准净值'] = (bench.reindex(nav.index).ffill() /
                              bench.reindex(nav.index).ffill().iloc[0])
            nav['超额净值'] = nav['净值'] / nav['基准净值']
            # 同仓位基准：按前一日实际仓位比例持有沪深300，其余现金（与策略同资金占用）
            expo = (nav['持仓数'] / args.max_pos).shift(1).fillna(0)
            bret = nav['基准净值'].pct_change().fillna(0)
            nav['基准同仓位净值'] = (1 + expo * bret).cumprod()
        navs[name] = nav
        m = {'规则': name, **_metrics(nav, args.max_pos, bench=nav.get('基准净值'))}
        # 未平仓按最后可得收盘计（仅用于逐笔统计）
        last = cal[-1]
        for code in list(pos):
            p = pos[code]
            c = float(ser[code].asof(last))
            p['exits'].append((last, p['qty'], '数据末端', c, p['peak']))
            closed.append(rec(code, p))
        tr = pd.DataFrame(closed)
        trade_rows.append(tr)
        m.update({'笔数': len(tr), '逐笔均值%': tr['净收益%'].mean(),
                  '逐笔中位%': tr['净收益%'].median(),
                  '逐笔胜率%': (tr['净收益%'] > 0).mean() * 100,
                  '平均持有日': tr['持有交易日'].mean(),
                  '最好%': tr['净收益%'].max(), '最差%': tr['净收益%'].min()})
        summary.append(m)
    summ = pd.DataFrame(summary)
    num = summ.select_dtypes('number').columns
    summ[num] = summ[num].round(2)
    return navs, summ, pd.concat(trade_rows, ignore_index=True), len(ev)


def make_plot(navs, path, title):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib import font_manager as fm
    for f in ('Microsoft YaHei', 'SimHei'):
        if any(f in x.name for x in fm.fontManager.ttflist):
            plt.rcParams['font.sans-serif'] = [f]
            break
    plt.rcParams['axes.unicode_minus'] = False
    fig, ax = plt.subplots(3, 1, figsize=(11, 10), sharex=True,
                           gridspec_kw={'height_ratios': [3, 1, 1.3]})
    for k, v in navs.items():
        n = v['净值'] / v['净值'].iloc[0]
        ax[0].plot(n.index, n.values, lw=1.8 if '固定10日' in k else 1.2, label=k,
                   alpha=0.95 if '固定10日' in k else 0.8)
        ax[1].plot(n.index, (n / n.cummax() - 1) * 100, lw=1.0, alpha=0.75)
        if '超额净值' in v:
            ax[2].plot(n.index, v['超额净值'].values, lw=1.8 if '固定10日' in k else 1.2,
                       label=k, alpha=0.9)
    first = next(iter(navs.values()))
    if '基准净值' in first:
        ax[0].plot(first.index, first['基准净值'].values, color='k', ls='--', lw=1.4,
                   label='沪深300（买入持有）')
        ax[0].legend(fontsize=8, loc='upper left')
        ax[2].axhline(1.0, color='k', lw=.8, ls=':')
        ax[2].legend(fontsize=7, loc='upper left')
        ax[2].set_ylabel('超额净值\n(策略/沪深300)')
    else:
        ax[0].legend(fontsize=8, loc='upper left')
    ax[0].set_yscale('log')
    ax[0].set_ylabel('净值（对数轴）')
    ax[0].set_title(title)
    ax[0].grid(alpha=.3)
    ax[1].set_ylabel('回撤 %')
    ax[1].grid(alpha=.3)
    ax[2].grid(alpha=.3)
    fig.tight_layout()
    fig.savefig(path, dpi=140)
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--list', default=r'output\shape_gc_099_fwd10_wkmacd_2026.xlsx')
    ap.add_argument('--max-pos', type=int, default=5)
    ap.add_argument('--cost-bp', type=float, default=10.0)
    ap.add_argument('--trail', type=float, default=None,
                    help='覆盖移动止盈阈值（默认 0.05），用于敏感性检验')
    ap.add_argument('--cal-start', dest='cal_start', default=None,
                    help='回测日历起点，如 2025-01-01（默认首个信号前 10 天）')
    ap.add_argument('--cal-end', dest='cal_end', default=None,
                    help='回测日历终点，如 2025-12-31（默认数据末端）')
    ap.add_argument('--out', default=r'output\exit_rules_nav.xlsx')
    ap.add_argument('--no-bench', action='store_true', help='不加载沪深300基准')
    args = ap.parse_args()
    if args.trail:
        for cfg in RULES.values():
            if 'trail' in cfg:
                cfg['trail'] = args.trail
    navs, summ, trades, n_sig = run(args)
    show = ['规则', '笔数', '总收益%', '年化%', '最大回撤%', '夏普', '卡玛',
            '逐笔均值%', '逐笔中位%', '逐笔胜率%', '平均持有日',
            '平均仓位比例', '有仓天数占比%', '最好%', '最差%']
    if '基准收益%' in summ.columns:
        show[8:8] = ['基准收益%', '超额收益%', '基准同仓位收益%', '超额(同仓位)%',
                     '超额年化%', '超额最大回撤%', '信息比率', 'Beta']
    print('\n仓位上限 %d 只、单边成本 %gbp、信号日收盘买/触发日收盘卖' %
          (args.max_pos, args.cost_bp))
    print('=' * 150)
    print(summ[show].to_string(index=False))
    print('\n出场原因分布：')
    print(trades.groupby(['规则', '出场原因']).size().to_string())
    print('\n混合规则逐笔（按建仓日）：')
    h = trades[trades['规则'] == '10日减半+13日清仓+5%移动止盈']
    print(h.sort_values('建仓日')[['代码', '名称', '建仓日', '持有交易日', '净收益%',
                                   '峰值浮盈%', '出场原因']].round(2).to_string(index=False))

    nav_csv = args.out.replace('.xlsx', '.csv')
    pd.DataFrame({k: v['净值'] for k, v in navs.items()}).to_csv(nav_csv, encoding='utf-8-sig')
    png = args.out.replace('.xlsx', '.png')
    make_plot(navs, png, '金叉信号退出规则净值对比（%d 笔信号，仓位上限 %d）' %
              (n_sig, args.max_pos))
    with pd.ExcelWriter(args.out) as w:
        pd.DataFrame({k: v['净值'] for k, v in navs.items()}).to_excel(w, sheet_name='日净值')
        summ.to_excel(w, sheet_name='汇总指标', index=False)
        trades.round(3).to_excel(w, sheet_name='逐笔明细', index=False)
        for k, v in navs.items():
            v.reset_index().to_excel(w, sheet_name=k[:28], index=False)
    print('\n[OK] 已保存', args.out, '/', nav_csv, '/', png)


if __name__ == '__main__':
    main()


