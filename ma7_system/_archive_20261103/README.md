# _archive_20261103 —— 旧逻辑归档（2026-11-03）

日线上抛只保留 **v2**（`doc/日线扫描方案_v2.md»）后，以下旧逻辑全部移到这里。
**归档代码不能就地运行**（import 路径已变），需要时按下面的「恢复」拷回原位。

## 内容

| 路径 | 是什么 | 原位置 |
|---|---|---|
| `code/ma7_scan.py» | v1 日线扫描（MA7 上拐 + K 线质量） | `ma7_system/» |
| `code/ma7_backtest.py» | v1 日线回测 | `ma7_system/» |
| `code/ma7_tf.py» | 单票周线/月线回测 | `ma7_system/» |
| `code/ma7_tf_scan.py» | 周线/月线市场扫描 | `ma7_system/» |
| `code/ma7_scan_dw.py» | 日线×周线共振扫描（含方案 3 开关） | `ma7_system/» |
| `code/ma7_dw_check.py» | 日周共振回归对拍 | `ma7_system/» |
| `code/ma7daily/signal.py» | v1 判定引擎（signal/state） | `ma7daily/» |
| `code/ma7daily/report.py» | v1 报告 | `ma7daily/» |
| `code/ma7daily/weekly.py» | 周线 MA7/斜率段/上抛区间 | `ma7daily/» |
| `code/ma7daily/dw.py» | 日周共振匹配 + 交易模拟 | `ma7daily/» |
| `config/ma7_dw.json» | 日周共振参数 | `config/» |
| `config/ma7_daily.json» | v1 独立参数（ma7/state/backtest/presets） | `config/» |
| `doc/日线MA7上抛规则.md» | v1 规则与案例 | `doc/» |
| `doc/日线扫描逻辑评审.md» | v1 评审 | `doc/» |
| `doc/日线周线共振方案_冻结版.md» | 日周共振冻结版规则 | `doc/» |
| `doc/日周共振逻辑评审.md» | 日周共振评审与实测 | `doc/» |
| `output/» | 上述逻辑产生的 CSV / xlsx / 图表 | `output/» |

## 恢复某一套

**v1 日线**（ma7_scan.py + 回测）：

```powershell
Copy-Item _archive_20261103\code\ma7_scan.py, _archive_20261103\code\ma7_backtest.py .
Copy-Item _archive_20261103\code\ma7daily\signal.py, _archive_20261103\code\ma7daily\report.py ma7daily\
Copy-Item _archive_20261103\config\ma7_daily.json config\
```
（v1 依赖 `ma7daily.settings» 里的参数与 `S.load_config()»，而现在的 settings.py 已精简，
恢复时需同时恢复当时的 settings.py，或改用归档里的完整副本。）

**周线 / 月线扫描**：

```powershell
Copy-Item _archive_20261103\code\ma7_tf.py, _archive_20261103\code\ma7_tf_scan.py, _archive_20261103\code\ma7daily\signal.py .
Copy-Item _archive_20261103\code\ma7daily\signal.py ma7daily\
```

**日线×周线共振**：

```powershell
Copy-Item _archive_20261103\code\ma7_scan_dw.py, _archive_20261103\code\ma7_dw_check.py .
Copy-Item _archive_20261103\code\ma7daily\weekly.py, _archive_20261103\code\ma7daily\dw.py ma7daily\
Copy-Item _archive_20261103\config\ma7_dw.json config\
Copy-Item _archive_20261103\doc\*.md doc\
```
（共振的回归对拍还依赖 `output/» 里的 `600188_opt3_exit3.csv»、`600188_dw_resonance_entries.csv»、
`000400_weekly_full_segments.csv»，恢复时一并拷回 `output/»。另：当时的 `ma7daily/upthrow.py»
带方案 3 分支，现在的 upthrow 已是纯 v2，若需复跑方案 3 需恢复归档时的 upthrow 副本——归档里未保留，
可在 git/备份中找 2026-11-03 22:20 之前的版本。）

## 为什么是归档而不是删除

仓库没有版本控制，直接删除不可逆；归档保证随时可以取回。
确认永久不用后，直接删除整个 `_archive_20261103/» 目录即可。
