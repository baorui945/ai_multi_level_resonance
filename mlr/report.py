# -*- coding: utf-8 -*-
"""
==============================================================================
mlr.report —— 回测产物（绩效/年度/月度表 + 净值图 + Excel）
==============================================================================
"""
import os

import numpy as np
import pandas as pd

from . import settings as S

# 表头样式与 ABM 保持一致
_HEADER_FILL = '366092'
_HEADER_FONT = 'FFFFFF'


def perf_table(bt):
    """绩效总览：行 = 持有期，末行 = 沪深300 基准。"""
    rows = []
    main_h = int((bt.get('cfg', {}).get('backtest') or {}).get('main_horizon') or 0)
    for h in bt['horizons']:
        r = bt['results'][h]
        m, ts = r['metrics'], r['trade_stats']
        rows.append({
            '持有期(交易日)': h,
            '主口径': '★' if h == main_h else '',
            '信号数': r['n_sig'],
            '去重交易笔数': r['n_trade'],
            '因数据不足剔除': r['n_skip'],
            '因涨停剔除': r['n_skip_lu'],
            '因并发上限剔除': r.get('n_skip_cap', 0),
            '持有期内去重': r.get('n_dedup', 0),
            '年化收益(%)': round(m['年化收益(%)'], 2),
            'α(年化%)': round(r['alpha'], 2) if r['alpha'] == r['alpha'] else np.nan,
            'α-t': round(r['alpha_t'], 2) if r['alpha_t'] == r['alpha_t'] else np.nan,
            '年化波动(%)': round(m['年化波动(%)'], 2),
            '夏普': round(m['夏普'], 2),
            '最大回撤(%)': round(m['最大回撤(%)'], 2),
            'Calmar': round(m['Calmar'], 2) if m['Calmar'] == m['Calmar'] else np.nan,
            '资金利用率(%)': round(m['资金利用率(%)'], 1),
            '逐笔净均(%)': ts['逐笔净均(%)'],
            '逐笔净t': ts['逐笔净t'],
            '逐笔净胜率(%)': ts['逐笔净胜率(%)'],
        })
    b = bt['benchmark']
    rows.append({
        '持有期(交易日)': '基准', '主口径': '', '信号数': '-', '去重交易笔数': '-',
        '因数据不足剔除': '-', '因涨停剔除': '-', '因并发上限剔除': '-',
        '持有期内去重': '-',
        '年化收益(%)': round(b['年化收益(%)'], 2), 'α(年化%)': 0.0, 'α-t': np.nan,
        '年化波动(%)': round(b['年化波动(%)'], 2), '夏普': round(b['夏普'], 2),
        '最大回撤(%)': round(b['最大回撤(%)'], 2),
        'Calmar': round(b['Calmar'], 2) if b['Calmar'] == b['Calmar'] else np.nan,
        '资金利用率(%)': 100.0, '逐笔净均(%)': np.nan, '逐笔净t': np.nan,
        '逐笔净胜率(%)': np.nan,
    })
    return pd.DataFrame(rows)


def annual_table(bt):
    from backtest_model import engine as ABM_E
    cal = bt['panel'].calendar
    years = sorted({d.year for d in cal})
    rows = []
    for h in bt['horizons']:
        rr = ABM_E.period_returns(bt['results'][h]['nav'], cal, 'YE')
        row = {'持有期': h}
        for y in years:
            ts = pd.Timestamp(f'{y}-12-31')
            row[str(y)] = round(float(rr.get(ts, np.nan)) * 100, 2)
        rows.append(row)
    rr = ABM_E.period_returns(bt['nav_base'], cal, 'YE')
    row = {'持有期': '基准'}
    for y in years:
        ts = pd.Timestamp(f'{y}-12-31')
        row[str(y)] = round(float(rr.get(ts, np.nan)) * 100, 2)
    rows.append(row)
    return pd.DataFrame(rows)


def monthly_table(bt):
    cal = pd.DatetimeIndex(bt['panel'].calendar)
    cols = pd.period_range(cal[0], cal[-1], freq='M').astype(str)
    rows = []
    for h in bt['horizons']:
        s = pd.Series(bt['results'][h]['nav'], index=cal)
        m = s.resample('ME').last() / s.resample('ME').first() - 1.0
        m.index = m.index.to_period('M').astype(str)
        row = {'持有期': h}
        for c in cols:
            v = m.get(c, np.nan)
            row[c] = round(float(v) * 100, 2) if v == v else np.nan
        rows.append(row)
    s = pd.Series(bt['nav_base'], index=cal)
    m = s.resample('ME').last() / s.resample('ME').first() - 1.0
    m.index = m.index.to_period('M').astype(str)
    row = {'持有期': '基准'}
    for c in cols:
        v = m.get(c, np.nan)
        row[c] = round(float(v) * 100, 2) if v == v else np.nan
    rows.append(row)
    return pd.DataFrame(rows)


def nav_frame(bt):
    cal = pd.DatetimeIndex(bt['panel'].calendar)
    d = {'trade_date': cal}
    for h in bt['horizons']:
        d[f'H{h}'] = bt['results'][h]['nav']
    d['沪深300'] = bt['nav_base']
    return pd.DataFrame(d)


def write_excel(path, bt, extra=None):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    sheets = {
        '绩效总览': perf_table(bt),
        '年度收益': annual_table(bt),
        '月度收益': monthly_table(bt),
        '净值曲线': nav_frame(bt),
    }
    for h in bt['horizons']:
        dt = bt['results'][h]['detail']
        if dt is not None and len(dt):
            sheets[f'逐笔明细H{h}'] = dt
    if extra is not None:
        for name, df in extra.items():
            sheets[name[:31]] = df

    try:
        import xlsxwriter  # noqa: F401
        engine = 'xlsxwriter'
    except Exception:                                # noqa: BLE001
        engine = 'openpyxl'

    with pd.ExcelWriter(path, engine=engine) as xw:
        for name, df in sheets.items():
            df.to_excel(xw, sheet_name=name, index=False)
        if engine == 'xlsxwriter':
            book = xw.book
            fmt = book.add_format({'bg_color': _HEADER_FILL,
                                   'font_color': _HEADER_FONT, 'bold': True,
                                   'border': 1})
            for sh in book.worksheets():
                sh.freeze_panes(1, 0)
                for j, col in enumerate(sh.table(0)):
                    sh.set_column(j, j, max(10, min(28, len(str(col[0])) + 4)))
                sh.set_row(0, None, fmt)
    if engine == 'openpyxl':
        _style_openpyxl(path)
    return path


def _style_openpyxl(path):
    try:
        from openpyxl import load_workbook
        from openpyxl.styles import Font, PatternFill, Alignment
        wb = load_workbook(path)
        fill = PatternFill('solid', fgColor=_HEADER_FILL)
        font = Font(color=_HEADER_FONT, bold=True)
        for ws in wb.worksheets:
            ws.freeze_panes = 'A2'
            for cell in ws[1]:
                cell.fill = fill
                cell.font = font
                cell.alignment = Alignment(horizontal='center')
            for j, col in enumerate(ws.iter_cols(min_row=1, max_row=1), start=1):
                w = max(10, min(28, len(str(col[0].value or '')) + 4))
                ws.column_dimensions[col[0].column_letter].width = w
        wb.save(path)
    except Exception:                                # noqa: BLE001
        pass


def plot_nav(bt, path, title='多周期共振'):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    for f in ('Microsoft YaHei', 'SimHei'):
        try:
            font_manager.findfont(f, fallback_to_default=False)
            matplotlib.rcParams['font.sans-serif'] = [f]
            break
        except Exception:                            # noqa: BLE001
            continue
    matplotlib.rcParams['axes.unicode_minus'] = False

    cal = pd.DatetimeIndex(bt['panel'].calendar)
    fig, axes = plt.subplots(1, 2, figsize=(16, 6))
    ax = axes[0]
    for h in bt['horizons']:
        r = bt['results'][h]
        ax.plot(cal, r['nav'], lw=1.3,
                label=f"H{h}  年化{r['metrics']['年化收益(%)']:.1f}%  "
                      f"夏普{r['metrics']['夏普']:.2f}")
    ax.plot(cal, bt['nav_base'], lw=1.6, color='k', alpha=0.6, label='沪深300')
    ax.set_yscale('log')
    ax.set_title(f'{title} · 净值曲线（对数轴）')
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3)

    ax = axes[1]
    h = bt['horizons'][0]
    nav = bt['results'][h]['nav']
    dd = nav / np.maximum.accumulate(nav) - 1.0
    ax.fill_between(cal, dd * 100, 0, color='#c0392b', alpha=0.5)
    ax.set_title(f'H{h} 回撤（%）')
    ax.grid(alpha=0.3)
    fig.tight_layout()
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    fig.savefig(path, dpi=110)
    return path


def summary_text(bt, signal_count=None):
    cfg = bt['cfg']
    lines = ['=' * 78,
             '  多周期（日/周/月）MACD 共振模型 —— 回测汇总',
             '=' * 78,
             f"  执行口径   : {bt['exec_mode']}    成本: {bt['cost'] * 100:.2f}% (双边)"
             f"    滑点: {bt.get('slippage_bp', 0):.0f}bp (单边)",
             f"  MACD 参数  : {tuple(cfg['macd'].values())}    "
             f"MA: {cfg['ma']['short']}/{cfg['ma']['long']}",
             f"  周期口径   : {cfg['data'].get('period_mode', 'closed')}"
             f"    并发上限: {bt.get('concurrent_cap', 0) or '不限'}"
             f"    退出: {bt.get('exit') or '固定持有期'}",
             f"  样本区间   : {bt['panel'].calendar[0].date()} ~ "
             f"{bt['panel'].calendar[-1].date()}"
             f"    （信号起点 {pd.Timestamp(bt.get('start', bt['panel'].calendar[0])).date()}）",
             f"  共振信号数 : {signal_count if signal_count is not None else bt['results'][bt['horizons'][0]]['n_sig']}",
             '-' * 78]
    t = perf_table(bt)
    lines.append(t.to_string(index=False))
    lines.append('-' * 78)
    lines.append('年度收益(%)')
    lines.append(annual_table(bt).to_string(index=False))
    return '\n'.join(lines)
