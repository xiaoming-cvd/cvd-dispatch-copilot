# 阶段 3 · 三方法同输入对比 smoke

- 输入：`data/processed/scenarios/dev/c101-s101.json`（同一草稿升格场景，锁比例/种子一致）
- 运行命令：
  ```
  .venv\Scripts\python.exe scripts\solve_cli.py --input data\processed\scenarios\dev\c101-s101.json --method <m> --time-limit-ms 15000 --out reports\stage3_smoke\<m>.json
  ```
  其中 `<m>` ∈ {insert, rebuild, low_disruption}；三个结果原始文件同目录，可溯源。
- 本次刷新：2026-10-11（跨电脑审查 P0/P1 修复后重跑；rebuild 已使用修正后的闭环目标模型）
- 汇总（数字均由结果 JSON 程序生成）：

| 方法 | status | 总行驶时间(s) | 总等待(s) | 最大返回(s) | 耗时(ms) | 备注 |
|---|---|---|---|---|---|---|
| insert | feasible | 38540 | 56399 | 69894 | 0 | 基线：不搬动旧任务 |
| rebuild | feasible | 28220 | 63526 | 66853 | 296 | COPT 完全重排（闭环目标） |
| low_disruption | feasible | 33372 | 58374 | 66853 | 4406 | 候选 216 个 |

- low_disruption 内部对比：
  - best_cost 候选：travel=32443，换车=4，相邻破坏=6
  - 推荐（低扰动）候选：travel=33372（≤ 1.10 × 32443 = 35687 ✓），换车=2，相邻破坏=3
  - 全部候选摘要见 `low_disruption.json` 的 `metrics.candidates`；逐任务 diff 见 `diff.orders`
- 口径说明：low_disruption 不调用 COPT/OR-Tools 子求解，4406ms 即该方法端到端总耗时
  （含插单种子与尊重锁的 relocate/swap 局部搜索，受 15000ms 预算硬截止）。
