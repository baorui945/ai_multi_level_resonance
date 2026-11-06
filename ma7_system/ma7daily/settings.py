# -*- coding: utf-8 -*-
"""
==============================================================================
ma7daily.settings —— 路径常量（日线上抛 v2）
==============================================================================
只保留 v2（ma7_scan_close3.py + ma7daily/upthrow.py）需要的东西：
BASE_DIR / OUTPUT_DIR / CHART_DIR / ensure_dirs()。
判定参数全部在 config/ma7_close3.json，不再从这里读。
"""
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT_DIR = os.path.join(BASE_DIR, 'output')
CHART_DIR = os.path.join(OUTPUT_DIR, 'charts')


def ensure_dirs():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    os.makedirs(CHART_DIR, exist_ok=True)
