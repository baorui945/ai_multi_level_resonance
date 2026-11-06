# -*- coding: utf-8 -*-
"""
==============================================================================
ma7daily —— 日线 MA7「上抛」判定（v2：C 主 + B 兜底）
==============================================================================
* ma7daily.upthrow —— 段 / 信号 / 候选判定（唯一入口：detect_frame / scan_day / active）
* ma7daily.settings —— 路径常量
* 规则与实现说明见 doc/日线扫描方案_v2.md
* 扫描 CLI 见 ma7_scan_close3.py，输出在 output/
"""
from . import settings  # noqa: F401

__version__ = '2.0.0'
