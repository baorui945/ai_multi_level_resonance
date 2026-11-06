# -*- coding: utf-8 -*-
"""
==============================================================================
burst_scan.py —— 爆发形态相似度评分盘后扫描
==============================================================================

用法::

    # 最近 10 个交易日、评分最高的形态（全市场）
    python burst_scan.py --universe all

    # 指定观察日，输出全部入选（按分数降序）
    python burst_scan.py --universe all --date 2026-10-19 --top 100

    # 关闭最低结构约束，只看纯评分排序（对照用）
    python burst_scan.py --universe hs300 --no-hard --min-score 70

    # 改爆发窗口与平台窗口
    python burst_scan.py --n 3 --m 60 --recent 5

输出：``output/burst/burst_scan_<YYYYMMDD>.xlsx``
      sheets：形态明细 / 评分构成 / 口径说明
"""
import argparse
import copy
import os
import time

import numpy as np
import pandas as pd

from burst import settings as BS
from burst import score as SC
from mlr import data as D
from mlr import universe as U

# (展示名, 特征键, 权重键, 小数位, 展示倍数)
DETAIL = [
    ('突破幅度', 'S_A', 'breakout', 4, 1.0),
    ('加速度(日均%)', 'S_B', 'accel', 3, 100.0),
    ('实体膨胀', 'S_C', 'body', 2, 1.0),
    ('阳线密度', 'S_D', 'bull', 3, 1.0),
    ('逼空强度', 'S_E', 'squeeze', 3, 1.0),
]


def _parse_args():
    p = argparse.ArgumentParser(description='BURST 爆发形态相似度扫描')
    p.add_argument('--universe', default='all', choices=['all', 'hs300', 'coal'])
    p.add_argument('--codes', default='', help='额外指定代码，逗号分隔')
    p.add_argument('--date', default='', help='观察日 YYYY-MM-DD（默认缓存最新日）')
    p.add_argument('--start', default='', help='信号起始日（默认取 --recent 个交易日）')
    p.add_argument('--recent', type=int, default=10, help='最近 N 个交易日')
    p.add_argument('--n', type=int, default=0, help='覆盖爆发窗口（默认读配置）')
    p.add_argument('--m', type=int, default=0, help='覆盖平台窗口（默认读配置）')
    p.add_argument('--top', type=int, default=50, help='控制台打印前 N 条')
    p.add_argument('--min-score', type=float, default=None, help='覆盖分数下限')
    p.add_argument('--preset', default='',
                   choices=['', 'v01', 'a-heavy', 'a-only'],
                   help='权重预设（v01=原式；a-heavy=按验证方向修正）')
    p.add_argument('--no-hard', action='store_true', help='关闭最低结构约束（纯评分）')
    p.add_argument('--out', default='', help='输出 xlsx 路径')
    p.add_argument('--no-style', action='store_true', help='不套用表头样式')
    return p.parse_args()


def _apply_overrides(cfg, a):
    if a.n:
        cfg['shape']['n'] = int(a.n)
    if a.m:
        cfg['shape']['m'] = int(a.m)
    if a.min_score is not None:
        cfg['signal']['min_score'] = float(a.min_score)
    if a.preset:
        presets = cfg.get('weights_presets') or {}
        if a.preset not in presets:
            raise SystemExit(f'未知权重预设 {a.preset}，可选：{sorted(presets)}')
        cfg['weights'] = dict(presets[a.preset])
        cfg['weights_preset'] = a.preset
    if int(cfg['shape']['m']) <= int(cfg['shape']['n']):
        raise SystemExit('m 必须大于 n')
    return cfg


def _load_px(a):
    if a.universe == 'hs300':
        codes, upd = U.get_hs300_codes()
        print(f'[池] 沪深300 {len(codes)} 只（快照 {upd}，历史回看有幸存者偏差）')
    elif a.universe == 'coal':
        codes, upd = U.get_coal_codes()
        print(f'[池] 动力煤 {len(codes)} 只（快照 {upd}）')
    else:
        codes = None
        print('[池] 全市场（直接读 parquet 缓存）')
    if a.codes:
        extra = [x.strip().zfill(6) for x in a.codes.split(',') if x.strip()]
        codes = sorted(set((codes or []) + extra)) if codes else extra
    px = pd.read_parquet(BS.PX_CACHE) if codes is None else \
        D.load_daily(codes=codes, verbose=False)
    px = px[['stock_code', 'stock_name', 'trade_date', 'open_price',
             'high_price', 'low_price', 'close_price', 'volume']].copy()
    px['trade_date'] = pd.to_datetime(px['trade_date'])
    return px.sort_values(['stock_code', 'trade_date'], ignore_index=True)



# ═════════════════════════════════════════════════════════════════════════
# 扫描
# ═════════════════════════════════════════════════════════════════════════
def _record(code, name, dates, arr, i, st, want_conv=False):
    f = st['features']
    rec = {'代码': str(code).zfill(6), '名称': name,
           '信号日': pd.Timestamp(dates[i]),
           '收盘': round(float(arr['close'][i]), 3),
           '量比': round(float(f['vol_mult'][i]), 2)
           if np.isfinite(f['vol_mult'][i]) else np.nan}
    for label, key, _, nd, mul in DETAIL:
        val = f[key][i]
        rec[label] = round(float(val) * mul, nd) if np.isfinite(val) else np.nan
    rec['score'] = round(float(st['score'][i]), 1)
    for label, key, wkey, _, _ in DETAIL:
        z = st['conv'][wkey][i]
        rec['分' + label[0]] = round(float(z) * 100, 1) \
            if np.isfinite(z) else np.nan
    rec['前M日高点'] = round(float(f['ref_high'][i]), 3) \
        if np.isfinite(f['ref_high'][i]) else np.nan
    rec['信号价(次日开盘)'] = round(float(arr['open'][i + 1]), 3) \
        if i + 1 < arr['open'].size else np.nan
    if want_conv:                      # 供验证脚本做分位/权重敏感性，不写入 xlsx
        for label, key, wkey, _, _ in DETAIL:
            rec['_S_' + wkey] = float(f[key][i])
            rec['_z_' + wkey] = float(st['conv'][wkey][i])
    return rec


def scan(px, cfg, start, end, min_score=0.0, hard=True, want_conv=False):
    """逐股计算 5 维特征与总分，返回区间内通过筛选的信号表。"""
    p = cfg
    if not hard:
        p = copy.deepcopy(cfg)
        p['hard']['enabled'] = False
    min_bars = int(cfg['data']['min_bars'])
    cd = int(cfg['signal']['cooldown_days'])
    rows = []
    t0 = time.time()
    for k, (code, g) in enumerate(px.groupby('stock_code', sort=False), 1):
        c = g['close_price'].to_numpy('float64')
        if c.size < min_bars:
            continue
        arr = {'open': g['open_price'].to_numpy('float64'),
               'high': g['high_price'].to_numpy('float64'),
               'low': g['low_price'].to_numpy('float64'),
               'close': c,
               'volume': g['volume'].to_numpy('float64')}
        st = SC.compute(c, arr['open'], arr['high'], arr['low'],
                        arr['volume'], p, min_score=min_score)
        dates = pd.to_datetime(g['trade_date']).to_numpy()
        m = (dates >= np.datetime64(pd.Timestamp(start))) \
            & (dates <= np.datetime64(pd.Timestamp(end))) & st['entry']
        last = -10 ** 9
        name = g['stock_name'].iloc[-1]
        for i in np.flatnonzero(m):
            if i - last < cd:
                continue
            last = int(i)
            rows.append(_record(code, name, dates, arr, int(i), st,
                                want_conv=want_conv))
        if k % 500 == 0:
            print(f'  ... {k} 只，已用 {time.time() - t0:.0f}s')
    df = pd.DataFrame(rows)
    if len(df):
        df = df.sort_values(['信号日', 'score'],
                            ascending=[False, False]).reset_index(drop=True)
    return df


def _composition(df, cfg):
    """把入选信号的 5 维归一化分与权重贡献汇总成表。"""
    cols = ['维度', '原始值均值', '归一化分均值', '权重', '贡献分']
    if not len(df):
        return pd.DataFrame(columns=cols)
    rows = []
    for label, _, wkey, _, _ in DETAIL:
        rows.append({
            '维度': label,
            '原始值均值': round(float(pd.to_numeric(df[label],
                                                   errors='coerce').mean()), 4),
            '归一化分均值': round(float(df['分' + label[0]].mean()), 1),
            '权重': round(float(cfg['weights'][wkey]), 3),
        })
    out = pd.DataFrame(rows)
    out['贡献分'] = (out['归一化分均值'] * out['权重']).round(2)
    return out[cols]


def _notes(cfg, a, asof):
    s, hc, sig = cfg['shape'], cfg['hard'], cfg['signal']
    pool = {'all': '全市场缓存（含退市股，无幸存者偏差）',
            'hs300': '沪深300（当前快照，历史回看有幸存者偏差）',
            'coal': '动力煤板块（当前快照）'}[a.universe]
    nn = cfg['norm']
    return pd.DataFrame({'项目': [
        '策略', '观察日', '股票池', '爆发窗口 N', '平台窗口 M',
        '维度与公式', '归一化锚点', '权重', '最低结构约束', '分数下限',
        '冷却期', '排序', '数据口径', '未来函数控制', '注意'],
        '说明': [
        '独立包 burst：只看近期形态与「标准爆发」的相似度，不要求前期横盘',
        str(pd.Timestamp(asof).date()), pool,
        f'{s["n"]} 个交易日（含信号日 T）',
        f'{s["m"]} 个交易日（前 M 日最高价 / 实体基准）',
        'A 突破=C_T/max(H_{T-M..T-1})-1；B 加速度=(近 N 日 OLS 斜率−前段斜率)/C_T；'
        'C 实体膨胀=近 N 日均|C-O| ÷ 前段均|C-O|；D 阳线密度；'
        'E 逼空=近 N 日均 (C-L)/(H-L)',
        ' / '.join(f'{k} {v["lo"]}~{v["hi"]}' for k, v in nn.items())
        + '（线性映射，超界截断）',
        ' / '.join(f'{k}={v}' for k, v in cfg['weights'].items())
        + f'（preset={cfg.get("weights_preset") or "自定义"}，'
        'score=100×Σw·分/Σw）',
        (f'需 S_A≥{hc["min_breakout"]}、S_D≥{hc["min_bull"]}、'
         f'S_E≥{hc["min_squeeze"]}' if hc.get('enabled', True)
         else '已关闭（纯评分）'),
        f'{sig["min_score"]} 分（0 = 只要求通过最低结构约束）',
        f'{sig["cooldown_days"]} 个交易日内同一股票只记一次',
        '信号日降序，同日按 score 降序',
        '后复权日线 parquet 缓存（backward_tdx_daily），成交量不除权',
        'T 日收盘后确认，T+1 开盘可执行；所有窗口数据在 T 日收盘时已知',
        '评分高不代表买得到：一字板/大幅高开会剔除，需人工复核'],
    })


def _print_reference(px, cfg, ref):
    code = str(ref['code']).zfill(6)
    day = pd.Timestamp(ref['date'])
    g = px[px['stock_code'] == code].sort_values('trade_date')
    if not len(g):
        print(f'[参考] {code} 不在缓存中')
        return
    arr = {k: g[v].to_numpy('float64') for k, v in (
        ('open', 'open_price'), ('high', 'high_price'),
        ('low', 'low_price'), ('close', 'close_price'), ('volume', 'volume'))}
    st = SC.compute(arr['close'], arr['open'], arr['high'], arr['low'],
                    arr['volume'], cfg, min_score=0.0)
    dates = pd.to_datetime(g['trade_date']).to_numpy()
    pos = np.flatnonzero(dates == np.datetime64(day))
    if not len(pos):
        print(f'[参考] {code} 在 {day.date()} 无交易')
        return
    i = int(pos[0])
    print(f'--- 参考 {code} {day.date()} 5 维形态 ---')
    for label, key, wkey, _, mul in DETAIL:
        print(f'  {label:14s} {st["features"][key][i] * mul:>10.4f}'
              f'  → {st["conv"][wkey][i] * 100:5.1f} 分')
    print(f'  score={st["score"][i]:.1f}  hard={bool(st["hard"][i])}'
          f'  entry={bool(st["entry"][i])}')


def main():
    a = _parse_args()
    cfg = _apply_overrides(BS.load_config(), a)
    BS.ensure_dirs()
    px = _load_px(a)
    asof = pd.Timestamp(a.date) if a.date else pd.Timestamp(px['trade_date'].max())
    dates = np.sort(px.loc[px['trade_date'] <= asof, 'trade_date'].unique())
    if not len(dates):
        raise SystemExit(f'观察日 {asof.date()} 之前无数据')
    end = pd.Timestamp(dates[-1])
    start = pd.Timestamp(a.start) if a.start else \
        pd.Timestamp(dates[max(0, len(dates) - int(a.recent))])
    print(f'[扫描] {start.date()} ~ {end.date()} | N={cfg["shape"]["n"]} '
          f'M={cfg["shape"]["m"]} | hard={not a.no_hard} '
          f'min_score={cfg["signal"]["min_score"]}')
    df = scan(px, cfg, start, end, cfg['signal']['min_score'],
              hard=not a.no_hard)
    n_code = df['代码'].nunique() if len(df) else 0
    print(f'[结果] {len(df)} 条信号，涉及 {n_code} 只')

    out = a.out or os.path.join(
        BS.OUTPUT_DIR, f'burst_scan_{end.strftime("%Y%m%d")}.xlsx')
    with pd.ExcelWriter(out, engine='openpyxl') as xw:
        (df if len(df) else pd.DataFrame(columns=['代码'])).to_excel(
            xw, sheet_name='形态明细', index=False)
        _composition(df, cfg).to_excel(xw, sheet_name='评分构成', index=False)
        _notes(cfg, a, end).to_excel(xw, sheet_name='口径说明', index=False)
    if not a.no_style:
        try:
            from mlr.report import _style_openpyxl
            _style_openpyxl(out)
        except Exception:                                  # noqa: BLE001
            pass
    print(f'[输出] {out}')

    if len(df) and a.top:
        cols = ['代码', '名称', '信号日', '收盘', 'score', '突破幅度',
                '加速度(日均%)', '实体膨胀', '阳线密度', '逼空强度']
        print(df[cols].head(int(a.top)).to_string(index=False))

    ref = cfg.get('reference', {})
    if ref.get('code') and ref.get('date'):
        _print_reference(px, cfg, ref)


if __name__ == '__main__':
    main()

