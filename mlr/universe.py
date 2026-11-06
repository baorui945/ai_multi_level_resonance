# -*- coding: utf-8 -*-
"""
==============================================================================
mlr.universe —— 股票池（成分股）加载
==============================================================================
复用 realTimeTradeFactor_V4.1 的本地成分股 JSON 口径：

    {"update_date": "2026-08-10", "constituents": ["000001", ...], "total": 300}

默认读取本工程 ``config/hs300_constituents.json``；若缺失则回退读取 V4.1 的
``monitor/hs300_constituents.json``，保证口径一致。只返回 6 位代码（本工程
查 ``backward_tdx_daily`` 无需交易所前缀）。

⚠️ 时变性：成分股每半年调整一次，该文件是「当前最新一期」快照。用于盘后
扫描当天无偏差；用于历史回测会引入幸存者偏差（只保留当前仍在指数的股票），
报告里应显式标注 update_date。
"""
import json
import os

from . import settings as S


def load_constituents_json(path):
    """加载成分股 JSON，返回 (6位代码列表, update_date)。去重 + 校验。"""
    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    codes = data.get('constituents', [])
    seen, clean = set(), []
    for c in codes:
        c = str(c).strip().zfill(6)
        if c.isdigit() and len(c) == 6 and c not in seen:
            seen.add(c)
            clean.append(c)
    return clean, data.get('update_date', '')


def get_hs300_codes(path=None):
    """返回沪深300成分股 (6位代码列表, update_date)。

    Args:
        path: 指定成分股文件；None 时先读本工程 config，缺失回退 V4.1。
    """
    if path:
        return load_constituents_json(path)
    for p in (S.HS300_JSON, S.HS300_JSON_V41):
        if p and os.path.isfile(p):
            return load_constituents_json(p)
    raise FileNotFoundError(
        '找不到沪深300成分股文件；请在 config/hs300_constituents.json 提供 '
        '{"update_date": "...", "constituents": ["000001", ...]}')


def get_coal_codes(path=None):
    """返回动力煤板块 (6位代码列表, update_date)。

    Args:
        path: 指定板块文件；None 时读本工程 config/coal_constituents.json。
    """
    p = path or S.COAL_JSON
    if not os.path.isfile(p):
        raise FileNotFoundError(
            f'找不到动力煤板块文件: {p}；请提供 '
            '{"update_date": "...", "constituents": ["601088", ...]}')
    return load_constituents_json(p)
