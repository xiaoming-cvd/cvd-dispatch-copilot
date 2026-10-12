# baseline 300 秒预算诊断实验（独立成表）

- 生成：2026-10-12T06:19:53.992739+00:00（UTC）
- 预算：每场景 300000 ms（**诊断口径，非 60 秒主评测口径**）
- 对象：60s 预算内 baseline 失败的 8 个场景（冻结评测同源 E_PLAN_NOT_FOUND）
- 语义：TIMEOUT_NO_SOLUTION ≠ 证明无解；不并入三方法主比较，不改冻结评测任何数字
- 结果：FOUND 31 · TIMEOUT_NO_SOLUTION 8 · 合计 39

| 场景 | 状态 | 耗时 ms | 原计划成本(模型秒) | 路线数 | 版本 |
|---|---|---|---|---|---|
| c101-s101 | FOUND | None | 37046.0 | 3 | 3 |
| c101-s102 | TIMEOUT_NO_SOLUTION | 61 | — | — | — |
| c102-s101 | FOUND | None | 40731.0 | 3 | 1 |
| c102-s102 | TIMEOUT_NO_SOLUTION | 0 | — | — | — |
| c201-s201 | FOUND | None | 39133.0 | 2 | 1 |
| c202-s201 | FOUND | None | 40090.0 | 2 | 1 |
| kilby-c100-s101 | FOUND | None | 1487.0 | 6 | 1 |
| kilby-c100b-s101 | FOUND | None | 783.0 | 6 | 1 |
| kilby-c120-s101 | FOUND | None | 914.0 | 3 | 1 |
| kilby-c150-s101 | FOUND | None | 1867.0 | 7 | 1 |
| kilby-c199-s201 | FOUND | None | 2132.0 | 8 | 1 |
| kilby-c50-s201 | FOUND | None | 625.0 | 3 | 1 |
| kilby-c75-s201 | FOUND | None | 1085.0 | 6 | 1 |
| kilby-f134-s101 | FOUND | None | 7553.0 | 3 | 1 |
| kilby-f71-s201 | FOUND | None | 386.0 | 4 | 1 |
| kilby-tai100a-s101 | FOUND | None | 2383.0 | 6 | 1 |
| kilby-tai100b-s201 | FOUND | None | 2007.0 | 6 | 1 |
| kilby-tai100c-s101 | FOUND | None | 1342.0 | 5 | 1 |
| kilby-tai100d-s101 | FOUND | None | 1955.0 | 6 | 1 |
| kilby-tai150a-s201 | FOUND | None | 3142.0 | 7 | 1 |
| kilby-tai150b-s101 | FOUND | None | 2550.0 | 7 | 1 |
| kilby-tai150c-s201 | FOUND | None | 3650.0 | 8 | 1 |
| kilby-tai150d-s101 | FOUND | None | 2947.0 | 7 | 1 |
| kilby-tai385-s201 | FOUND | None | 16729.0 | 14 | 1 |
| kilby-tai75a-s201 | FOUND | None | 1983.0 | 7 | 1 |
| kilby-tai75b-s101 | FOUND | None | 1195.0 | 6 | 1 |
| kilby-tai75c-s201 | FOUND | None | 1784.0 | 6 | 1 |
| kilby-tai75d-s201 | FOUND | None | 1053.0 | 4 | 1 |
| r101-s101 | TIMEOUT_NO_SOLUTION | 15 | — | — | — |
| r101-s102 | TIMEOUT_NO_SOLUTION | 0 | — | — | — |
| r102-s101 | TIMEOUT_NO_SOLUTION | 92 | — | — | — |
| r102-s102 | TIMEOUT_NO_SOLUTION | 16 | — | — | — |
| r201-s101 | FOUND | None | 33686.0 | 1 | 1 |
| r201-s102 | TIMEOUT_NO_SOLUTION | 15 | — | — | — |
| r202-s101 | FOUND | None | 33217.0 | 2 | 1 |
| r202-s102 | FOUND | None | 28954.0 | 1 | 1 |
| rc101-s201 | TIMEOUT_NO_SOLUTION | 0 | — | — | — |
| rc201-s201 | FOUND | None | 42702.0 | 2 | 1 |
| rc202-s201 | FOUND | None | 41835.0 | 2 | 1 |

机器可读明细：`results.json`（逐场景）/`summary.csv`（表格）。
原始记录：本表所有数字可由 `scripts/diag_baseline_300s.py` 续跑复现；保存的物化场景在 `data/processed/scenarios/_materialized/<sid>.json`（v1，300s 诊断口径生成）。

---

## 根因诊断（策略扫描 + 约束放宽探针，2026-10-12 第二轮）

**结论：不是预算问题，是时间窗约束下的初解构造失败。**

1. **300s 预算下 8 场景全部 0~92ms 返回 ROUTING_FAIL**（`results.json`）——
   求解器不是"没跑够时间"，而是首解启发式一步都走不通，GLS 从未启动。
2. **首解策略扫描**（`strategy_sweep.json`，6 种策略 × 8 场景 = 48 次运行）：
   PARALLEL / SEQUENTIAL / LOCAL_CHEAPEST / BEST_INSERTION 全部秒级 FAIL
   （服务 0 单）；AUTOMATIC 与 PATH_MOST_CONSTRAINED 跑满 15s 预算仍 FAIL。
   没有任何一种策略能构造出初始解。
3. **约束放宽探针**（c101-s102，15 张旧单）：仅放宽时间窗
   （ready=0/due=∞），同一模型 **ROUTING_OPTIMAL、15/15 秒解**；
   加上放宽返仓窗口结果不变。瓶颈锁定为时间窗约束本身。

**含义与边界（诚实口径）**
- 这 8 个派生场景的"旧订单集合 × 当前时间窗"工程上不可物化：与冻结评测
  记录的 8 个 E_PLAN_NOT_FOUND 完全同源，且评测报告"20s 预算耗尽"的原因
  描述不准确——真实原因是秒级 FAIL，本次诊断已修正该表述。
- OR-Tools 未返回 ROUTING_INFEASIBLE，故**未证明数学不可行**；
  表述统一为"预算内未找到可行基线（初解构造失败），放宽时间窗可得最优解"。
- 数据侧提示（供总工参考）：8/39 派生场景（C102/R101/R102/R201/RC101 的
  s102/s201 种子）在 ×60 模拟尺度下服务时长 5400s 与派生时间窗组合过紧，
  属派生数据质量问题，非算法能力问题；主评测中这 8 场已按物化失败如实
  排除在三方法比较之外。
- 产品影响：场景列表"事前参考"列对这 8 场景显示"—"（待 baseline），
  与已知限制一致；不改 60s 主评测口径，不静默换策略或换数据。

**复现**：
`scripts/diag_baseline_300s.py`（300s 预算实验，幂等）、
`scripts/diag_first_solution_strategy.py`（策略扫描，monkeypatch
模块常量 `ortools_core.FIRST_SOLUTION_STRATEGY`，产品默认值未变）。
