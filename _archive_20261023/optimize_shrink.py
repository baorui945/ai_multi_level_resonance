# -*- coding: utf-8 -*-
"""
==============================================================================
optimize_shrink —— 扫描「MACD柱连续 N 日缩短卖出」的最优 N
==============================================================================
思路：
    1. 复用 template_scan 的模板匹配，得到同一批买入点（金叉日）；
    2. 对每个候选 N（1..--max-n），重算卖出日 = 买入后第一次「MACD柱
       2*(DIF-DEA) 连续 N 个交易日逐日缩短」那一天，按收盘价卖出；
    3. 横向比较各 N 的 平均涨幅 / 合计收益 / 持有天数 / 日均涨幅（资金效率）；
    4. 按买入时间把样本切成前半(In) / 后半(Out)，检查最优 N 是否稳健。

⚠️ 重要：「最优 N」是在同一批历史样本里挑出来的，天然存在过拟合。
   请重点看「前半最优 N 在后半的表现」与「日均涨幅」这类综合指标，
   不要只看前半样本的最高涨幅。

交易成本（默认 A 股明细口径，可用 --cost 切到统一双边口径）：
    买入费用 = max(成交额×佣金率, 最低佣金) + 成交额×过户费率
    卖出费用 = max(成交额×佣金率, 最低佣金) + 成交额×过户费率
               + 成交额×印花税率              ← 印花税仅卖出方收取
    默认 佣金万2.5/单笔最低5元、过户费0.001%、印花税0.05%
    -> 按 10 万本金整手算，双边合计约 0.102%（每笔约 100 元）；
    --cost 0.002 则买卖各 0.1%（同 ABM 的 backtest.cost）。
    输出表中所有「净」列均为扣除成本后的实际结果。

用法::

    python optimize_shrink.py --universe all --threshold 0.98
    python optimize_shrink.py --universe hs300,coal --threshold 0.98 --max-n 20
    python optimize_shrink.py --universe all --threshold 0.98 --out output/shrink_opt.xlsx
    python optimize_shrink.py --universe all --threshold 0.98 --step 3   # 冒烟更快
    python optimize_shrink.py --universe all --cost 0.002                # 统一双边0.2%
    python optimize_shrink.py --universe all --commission 0.0003         # 自定义佣金
"""
import argparse
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mlr import data as D, settings as S
from mlr.macd import macd as calc_macd
from template_scan import (_fee_desc, _fee_model, _resolve_pool, _scan, _series,
                           zscore)


def shrink_sell_idx(hist, b, n):
    """第 b 日买入后，第一次「MACD柱连续 n 日缩短」的索引；从未出现返回 None。"""
    d = np.diff(hist) < 0                       # d[k]=True：第 k+1 天柱值缩短
    last_bad = np.maximum.accumulate(np.where(d, -1, np.arange(d.size)))
    run = np.zeros(hist.size, dtype=np.int64)   # run[i]=截至第 i 天连续缩短天数
    run[1:] = np.arange(d.size) - last_bad
    hit = np.where(run[b + 1:] >= int(n))[0]
    return None if hit.size == 0 else int(b + 1 + hit[0])


def _metrics(rt, pf, hd, nrt=None, npf=None, cst=None):
    """给定一组的 涨幅/收益额/持有天数（及净口径），返回统计字典。

    rt=毛涨幅(小数)  pf=毛收益(元)  hd=持有天数
    nrt=净涨幅(小数) npf=净收益(元) cst=该笔双边成本(元)
    """
    rt, pf, hd = np.asarray(rt, float), np.asarray(pf, float), np.asarray(hd, float)
    if rt.size == 0:
        return {}
    out = {'笔数': int(rt.size),
           '胜率%': round(float((rt > 0).mean() * 100), 2),
           '平均涨幅%': round(float(rt.mean() * 100), 2),
           '中位涨幅%': round(float(np.median(rt) * 100), 2),
           '平均收益元': int(round(float(pf.mean()))),
           '合计收益元': int(round(float(pf.sum()))),
           '平均持有天': round(float(hd.mean()), 1),
           '日均涨幅%': round(float(rt.mean() * 100 / hd.mean()), 3),
           '单笔年化%': round(float(((1.0 + rt.mean()) ** (244.0 / hd.mean()) - 1.0)
                                   * 100), 1),
           '最大亏损%': round(float(rt.min() * 100), 2),
           '最大盈利%': round(float(rt.max() * 100), 2)}
    if nrt is not None and len(nrt):
        nrt = np.asarray(nrt, float)
        npf = np.asarray(npf, float)
        cst = np.asarray(cst, float)
        out['净胜率%'] = round(float((npf > 0).mean() * 100), 2)
        out['平均净涨幅%'] = round(float(nrt.mean() * 100), 2)
        out['中位净涨幅%'] = round(float(np.median(nrt) * 100), 2)
        out['合计净收益元'] = int(round(float(npf.sum())))
        out['净日均涨幅%'] = round(float(nrt.mean() * 100 / hd.mean()), 3)
        out['成本合计元'] = int(round(float(cst.sum())))
        tot = abs(float(pf.sum()))
        out['成本/毛收益%'] = round(float(cst.sum() / tot * 100), 1) if tot > 0 else None
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(prog='optimize_shrink',
                                 description='扫描「MACD柱连续N日缩短卖出」的最优N')
    ap.add_argument('--code', default='601898', help='模板股票代码')
    ap.add_argument('--start', default='2025-11-11', help='模板起始日')
    ap.add_argument('--end', default='2026-03-16', help='模板结束日')
    ap.add_argument('--universe', default=None,
                    help='匹配池 hs300 / coal / all，可逗号分隔（默认取配置）')
    ap.add_argument('--threshold', type=float, default=0.97, help='相似度阈值')
    ap.add_argument('--overlap', type=float, default=0.5, help='重叠去重比例')
    ap.add_argument('--step', type=int, default=1, help='滑动步长')
    ap.add_argument('--latest', action='store_true', help='每股只留最近一个窗口')
    ap.add_argument('--max-n', type=int, default=20, help='扫描的 N 上限（默认20）')
    ap.add_argument('--budget', type=float, default=100000.0,
                    help='单笔本金上限（元），按100股整手，默认10万')
    ap.add_argument('--commission', type=float, default=0.00025,
                    help='佣金率（双边，默认万2.5=0.00025）')
    ap.add_argument('--stamp', type=float, default=0.0005,
                    help='印花税率（仅卖出，默认0.05%%=0.0005）')
    ap.add_argument('--transfer', type=float, default=0.00001,
                    help='过户费率（双边，默认0.001%%=0.00001）')
    ap.add_argument('--min-commission', type=float, default=5.0,
                    help='单笔最低佣金（元，默认5）')
    ap.add_argument('--cost', type=float, default=None,
                    help='统一双边合计成本（如0.002），给出后覆盖上述明细费率，'
                         '口径同 ABM 的 backtest.cost')
    ap.add_argument('--out', default=None, help='结果 xlsx/csv 路径')
    args = ap.parse_args(argv)

    # ── 1. 模板 ─────────────────────────────────────────────────────────
    _, t_dates, t_dif, t_dea, t_name, mp = _series(args.code)
    m = (t_dates >= np.datetime64(args.start)) & (t_dates <= np.datetime64(args.end))
    if int(m.sum()) < 8:
        raise SystemExit(f'模板区间交易日不足（{int(m.sum())}）')
    tpl_dif_z, tpl_dea_z = zscore(t_dif[m]), zscore(t_dea[m])
    L = int(m.sum())
    print('=' * 104)
    print(f'  「MACD柱连续 N 日缩短卖出」参数扫描 —— 模板 {args.code} {t_name} '
          f'{args.start} ~ {args.end}（L={L} 交易日）')
    print('=' * 104)

    # ── 2. 同一批买入点（复用 template_scan 的匹配 + 去重）───────────────
    scan_args = argparse.Namespace(threshold=args.threshold, step=args.step,
                                   overlap=args.overlap, latest=args.latest,
                                   exclude_self=False, code=args.code,
                                   budget=args.budget, sell_rule='shrink',
                                   lead=4, shrink_days=6, limit=None,
                                   commission=args.commission, stamp=args.stamp,
                                   transfer=args.transfer,
                                   min_commission=args.min_commission,
                                   cost=args.cost)
    fee_buy, fee_sell = _fee_model(args)
    print(f'  成本口径   : {_fee_desc(args)}')
    cfg = S.load_config()
    pools = [p.strip() for p in (args.universe or
                                 cfg.get('universe', {}).get('default', 'hs300')
                                 ).split(',') if p.strip()]
    S.ensure_dirs()
    frames = []
    for pool in pools:
        codes, label = _resolve_pool(pool)
        daily = D.load_daily(codes=codes)
        print(f'\n--- 匹配池「{label}」{daily["stock_code"].nunique()} 只 / '
              f'{len(daily):,} 行 ---', flush=True)
        df = _scan(daily, tpl_dif_z, tpl_dea_z, L, mp, scan_args, keep_meta=True)
        if not len(df):
            print('（无结果，可降低 --threshold）')
            continue
        arr = {}
        for code, g in daily.groupby('stock_code', sort=False):
            g = g.sort_values('trade_date')
            c = g['close_price'].to_numpy('float64')
            arr[code] = (c, calc_macd(c, *mp)[2],
                         pd.to_datetime(g['trade_date']).to_numpy())
        frames.append((df.assign(pool=label), arr))
        print(f'[结果] {label}: {len(df)} 个匹配窗口 / '
              f'{df["stock_code"].nunique()} 只股票')
    if not frames:
        print('\n（全部匹配池均无结果）')
        return 0

    # 预先摊平匹配结果（注意：itertuples 会把以 _ 开头的列名改名，故按列取值）
    data_by_code, recs = {}, []
    for df, arr in frames:
        data_by_code.update(arr)
        for code, sname, sc, bdate, braw, pool_name in zip(
                df['stock_code'], df['stock_name'], df['score'], df['buy_date'],
                pd.to_numeric(df['_b'], errors='coerce'), df['pool']):
            if not np.isfinite(braw):
                continue
            recs.append((str(code), str(sname), float(sc), bdate, int(braw),
                         str(pool_name)))
    print(f'\n[扫描] 有效买入点 {len(recs)} 个 × N=1..{args.max_n}')

    # ── 3. 逐 N 重算卖出日与收益 ─────────────────────────────────────────
    res_rows, detail = [], []
    for n in list(range(1, args.max_n + 1)) + [0]:   # 0 = 基线：永不卖出
        ts, rt, pf, hd = [], [], [], []              # 毛口径
        nrt, npf, cst = [], [], []                   # 净口径（扣双边成本）
        all_rt, all_npf = [], []                     # 全口径（未触发按最新价计）
        for code, sname, sc, bdate, b, pool_name in recs:
            c, h, dts = data_by_code[code]
            s = (c.size - 1) if n == 0 else shrink_sell_idx(h, b, n)
            base = {'pool': pool_name, 'stock_code': code, 'stock_name': sname,
                    'score': sc, 'buy_date': bdate, 'N': n}
            bp = float(c[b])
            sh = int(args.budget // (bp * 100)) * 100 if bp > 0 else 0
            buy_amt = sh * bp
            bfee = fee_buy(buy_amt)
            if s is None:                            # 未触发 → 持有中，不参与已了结统计
                sp = float(c[c.size - 1])
                sell_amt = sh * sp
                all_rt.append(sp / bp - 1.0)
                all_npf.append(sell_amt - buy_amt - bfee - fee_sell(sell_amt))
                detail.append({**base, 'sell_date': None, 'trade_ret_pct': None,
                               'profit': None, 'buy_fee': round(bfee, 2),
                               'sell_fee': None, 'cost': None, 'net_profit': None,
                               'net_ret_pct': None, 'hold_bars': None,
                               'triggered': False})
                continue
            sp = float(c[s])
            sell_amt = sh * sp
            sfee = fee_sell(sell_amt)
            gross = sell_amt - buy_amt
            net = gross - bfee - sfee
            r = sp / bp - 1.0
            n_ret = net / (buy_amt + bfee) if buy_amt > 0 else 0.0
            all_rt.append(r)
            all_npf.append(net)
            ts.append(bdate)
            rt.append(r)
            pf.append(gross)
            hd.append(int(s - b))
            nrt.append(n_ret)
            npf.append(net)
            cst.append(bfee + sfee)
            detail.append({**base, 'sell_date': pd.Timestamp(dts[s]).date(),
                           'trade_ret_pct': round(r * 100, 2),
                           'profit': round(gross, 2),
                           'buy_fee': round(bfee, 2), 'sell_fee': round(sfee, 2),
                           'cost': round(bfee + sfee, 2),
                           'net_profit': round(net, 2),
                           'net_ret_pct': round(n_ret * 100, 2),
                           'hold_bars': int(s - b), 'triggered': True})
        row = {'N': n, '笔数': len(rt), '未触发(持有中)': len(recs) - len(rt),
               **_metrics(rt, pf, hd, nrt, npf, cst)}
        if all_rt:
            row['全口径平均涨幅%'] = round(float(np.mean(all_rt) * 100), 2)
            row['全口径净收益元'] = int(round(float(np.sum(all_npf))))
        if ts:
            order = np.argsort(np.asarray(ts), kind='stable')
            half = len(order) // 2
            ia, ib = order[:half], order[half:]

            def _sub(idx):
                idx = np.asarray(idx)
                return _metrics(np.asarray(rt)[idx], np.asarray(pf)[idx],
                                np.asarray(hd)[idx], np.asarray(nrt)[idx],
                                np.asarray(npf)[idx], np.asarray(cst)[idx])

            ma, mb = _sub(ia), _sub(ib)
            row['In平均涨幅%'] = ma.get('平均涨幅%', np.nan)
            row['Out平均涨幅%'] = mb.get('平均涨幅%', np.nan)
            row['In净涨幅%'] = ma.get('平均净涨幅%', np.nan)
            row['Out净涨幅%'] = mb.get('平均净涨幅%', np.nan)
            row['In合计净收益元'] = ma.get('合计净收益元', np.nan)
            row['Out合计净收益元'] = mb.get('合计净收益元', np.nan)
        res_rows.append(row)

    res = pd.DataFrame(res_rows)
    cols = ['N', '笔数', '未触发(持有中)', '胜率%', '净胜率%',
            '平均涨幅%', '平均净涨幅%', '中位涨幅%', '中位净涨幅%',
            '平均收益元', '合计收益元', '合计净收益元',
            '成本合计元', '成本/毛收益%',
            '平均持有天', '日均涨幅%', '净日均涨幅%', '单笔年化%',
            '最大亏损%', '最大盈利%',
            '全口径平均涨幅%', '全口径净收益元',
            'In净涨幅%', 'Out净涨幅%', 'In合计净收益元', 'Out合计净收益元']
    res = res[[c for c in cols if c in res.columns]]
    print('\n' + '=' * 104)
    print('  各 N 表现（笔数=已触发卖出的交易；从未触发的记「持有中」不计入统计）')
    print('  「净」列 = 已扣双边交易成本后的实际结果；「毛」列仅作对照')
    print('  N=0 为基线：永不卖出（持有到样本末尾），用来判断 MACD 缩短卖出是否真的加分')
    print('=' * 104)
    print(res.to_string(index=False))

    # ── 4. 最优 N 标注 + 稳健性提示 ──────────────────────────────────────
    print('\n' + '-' * 104)
    print('  【单一指标最优 N】')
    picks = [('平均净涨幅%', '扣成本后单笔平均涨幅最高'),
             ('合计净收益元', '扣成本后净收益总额最高'),
             ('净胜率%', '扣成本后胜率最高'),
             ('中位净涨幅%', '扣成本后中位涨幅最高'),
             ('净日均涨幅%', '资金效率（扣成本后日均涨幅）最高'),
             ('Out净涨幅%', '样本外净涨幅最高')]
    picks_res = res[res['N'] > 0] if (res['N'] > 0).any() else res
    for col, desc in picks:
        if col in picks_res.columns and picks_res[col].notna().any():
            r = picks_res.loc[picks_res[col].idxmax()]
            print(f'   {desc:<30s} → N={int(r["N"]):>2d}   {col}={r[col]}'
                  f'   笔数={int(r["笔数"])}  平均持有={r["平均持有天"]}天  '
                  f'胜率={r["胜率%"]}%')

    print('\n  【稳健性：用前半样本选出的 N，在后半样本是否还成立（净口径）】')
    if 'In净涨幅%' in res.columns and res['In净涨幅%'].notna().any():
        n_in = int(picks_res.loc[picks_res['In净涨幅%'].idxmax(), 'N'])
        n_out = int(picks_res.loc[picks_res['Out净涨幅%'].idxmax(), 'N'])
        r_in = res[res['N'] == n_in].iloc[0]
        r_out = res[res['N'] == n_out].iloc[0]
        print(f'   前半最优 N={n_in}（In净 {r_in["In净涨幅%"]}%）'
              f' → 同一 N 在后半只有 {r_in["Out净涨幅%"]}%')
        print(f'   后半最优 N={n_out}（Out净 {r_out["Out净涨幅%"]}%）'
              f' → 同一 N 在前半是 {r_out["In净涨幅%"]}%')
        print(f'   {"两者一致，该 N 相对稳健" if n_in == n_out else "两者不一致，说明入样的最优 N 有过拟合成分"}')

    print('\n  【解读】')
    print(f'   · 成本口径：{_fee_desc(args)}；表内「净」列均已扣除此成本，'
          '为实际到手口径。')
    print('   · 「单笔年化%」= 按 244 交易日、假设资金可连续滚动复投的外推值，'
          '只用于横向比较资金效率，不是可实现收益。')
    base = res[res['N'] == 0]
    if len(base):
        b0 = base.iloc[0]
        print(f'   · 基线 N=0（永不卖出，平均持有 {b0["平均持有天"]} 天）'
              f'净涨幅 {b0["平均净涨幅%"]}%、净收益 {b0["合计净收益元"]:,} 元：'
              f'单看绝对收益，任何 N 都比不过「一直不卖」。')
        print('     所以判断 N 的优劣要看「净日均涨幅%（资金效率）」与「净胜率」，'
              '再看未触发(持有中)列是否已经让策略退化成长期持有。')

    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        if args.out.lower().endswith(('.xlsx', '.xls')):
            try:
                from template_scan import _write_excel
                _write_excel(args.out, {'扫描汇总': res,
                                        '逐笔明细': pd.DataFrame(detail)})
            except Exception as exc:                  # noqa: BLE001
                print(f'[WARN] Excel 写出失败({exc})，改存同名 csv')
                res.to_csv(args.out + '.csv', index=False, encoding='utf-8-sig')
        else:
            res.to_csv(args.out, index=False, encoding='utf-8-sig')
        print(f'\n[OK] 已保存: {args.out}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
