# -*- coding: utf-8 -*-
"""
==============================================================================
coil —— 独立策略：长期横盘蓄势 → 放量突破（COIL = Consolidation → Impulse Launch）
==============================================================================

本包与工程内既有策略（`mlr.resonance` 多周期共振、`mlr.morph` 挖坑起爆、
`template_scan` 模板匹配）**完全隔离**：

* 不 import 旧策略的任何模块（连 `mlr.macd` / `mlr.morph` 都不复用）；
* 只单向复用**数据管道**：`mlr.data.load_daily` / `mlr.settings` 的路径与表名；
* 参数唯一来源：`config/coil.json`（缺项回落 `coil/settings.py:DEF`）；
* 产物目录：`output/coil/`；记录文档：`doc/coil_策略记录.md`。

数学口径（详见 `coil/features.py` 的模块 docstring）：

    蓄势：P'(t)≈0、P''(t)≈0、Var(P)→min、均线极限收敛、MACD 零轴粘合
    突破：P(t) > 箱体上沿、放量、金叉新鲜、红柱放大、均线多头排列

删除本包对旧策略无任何影响（依赖方向严格单向）。
"""

__version__ = '0.1.0'
