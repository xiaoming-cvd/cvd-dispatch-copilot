# 阶段 3 复审问题关闭表（v2 · 2026-10-11）

复审依据：《第三阶段评估与跨电脑AI改进建议.docx》（跨电脑 AI 复审，结论：有条件通过）。
本表逐项记录修复内容、验证方式与证据位置。验证机器：`C:\Users\tianj\Desktop\城配调度副驾\`（Python 3.12.7 venv，COPT 8.0.6）。

验证基线：全量测试 **201 passed, 0 failed, 0 skipped**（`reports/stage3_v2_pytest_log.txt`）。
其中阶段 3 专属测试 21 项（`tests/test_low_disruption.py`），含四个复审反例，零 skip（`-rs` 复核无 SKIPPED 行）。

---

## P0-1 成本上限必须基于跨方法 reference_best_cost【已修复】

**问题**：原实现成本上限 = 1.10 × 本方法候选池内最低成本。只要池内存在任一低成本候选，上限即被"自我"定义，跨方法比较失真（复审实测：池最优 33,372 s 时上限 = 36,709 s，永远"达标"）。

**修复**：
- `src/solver/low_disruption.py` 新增 `reference_results` 入参与 `_reference_best_cost()`：上限 = `cost_cap_ratio × reference_best_cost`，reference 取"同输入同约束下所有已通过独立校验（`validate_solution`）方案的最低成本"；每个 reference 方案先经独立校验复核，校验不过的不参与。
- 未提供 reference 时回退池内最优并如实标注 `reference_source="pool_fallback"`，不冒充跨方法口径。
- 上限内无候选时：`selection_status="no_candidate_within_cost_cap"`、`within_reference_cap=false`，推荐池内最低成本候选并输出 warning，**不冒充达标**。
- `src/solver/pipeline.py` 新增 `compare_methods(scn, time_limit_ms, methods, cost_cap_ratio)`：顺序执行各方法，先跑方法的可行方案自动作为 low_disruption 的 reference_results（任务五配套）。
- CLI：`scripts/solve_cli.py --cost-cap-ratio`；`scripts/compare_methods.py` 同入口。

**验证**（`reports/method_compare/`）：

| 场景 | insert | rebuild(=reference) | 低扰动池最优 | 上限 1.10×ref | within_cap | selection_status |
|---|---|---|---|---|---|---|
| c101-s101 | 38,540 | 28,220 | 32,443 | 31,042 | **false** | no_candidate_within_cost_cap |
| c102-s101 | 46,106 | 30,819 | 38,196 | 33,900.9 | **false** | no_candidate_within_cost_cap |

两场景均如实暴露"低扰动池内无 ≤1.1×重排最优"的事实（复审 P0-1 指出的 33,372 > 31,042 场景已复现并正确标注）。
单元测试：`TestSelectionRule`（1.05 vs 1.20 选择池不同、无达标候选状态、reference 来源标注）、`TestReviewCounterexamples::test_reference_cap_no_candidate`（构造跨方法上限无候选反例）。

## P0-2 局部搜索预算重复扣减【已修复】

**问题**：原实现以 `remaining`（剩余毫秒）做判定，又保留原 `started` 基准换算，种子阶段耗时被扣两次，预算口径不可审计。

**修复**：预算改为**绝对截止** `deadline = started + time_limit_ms/1000`，`out_of_budget()` 闭包贯穿种子构造 / 邻域生成 / 归档全路径；只有 insert 叶子调用处换算一次 `remaining_ms`（insert 自带独立时钟）。假设已写入输出 assumptions（"绝对截止只扣一次"）。

**验证**：`TestReviewCounterexamples::test_budget_not_double_counted` —— 假时钟每次调用 +100 ms、limit=1000 ms，断言端到端 elapsed 落在 [800, 1600] ms 区间（上界放宽因截止后审计/参考计算仍走假时钟）；若重复扣减，实测必然 < 800 ms。

## P1-1 cost_cap_ratio 参数化【已修复】

`COST_CAP` 由模块常量改为 `solve_low_disruption(..., cost_cap_ratio=DEFAULT_COST_CAP)` 与 pipeline/CLI 入参；合法区间 [0.5, 2.0]（`RATIO_MIN/MAX`），越界抛 `E_COST_CAP_RATIO`。常量保留作默认值兼容。

## P1-2 核心测试零 skip【已修复】

`tests/test_low_disruption.py` 重写为 21 项，原"选择规则/相对顺序锁"用例中的 `pytest.skip` 路径全部改为真实断言（构造满足前提的场景数据而非跳过）。`pytest -rs` 复核：无 SKIPPED。

## P1-3 候选可审计【已修复】

审计输出（`LowDisruptionOutcome.audit` / metrics `audit_candidates`）每条候选含：`candidate_id`（由路线关键字段 sha1 派生，稳定可复算）、完整路线（vehicle_id + order_ids + return_s）、扰动指标、成本、`validation_ok`。另输出 `pool_best_cost`、`cap_boundary`、Pareto 前沿与 top_k。

**验证**：`TestReviewCounterexamples::test_candidate_reproducible_by_id` —— 按 candidate_id 重建候选并独立复核一致。

## P1-4 输出瘦身【已修复】

审计输出同时给出 Pareto（换车×破坏×成本非支配集）与 top_k（默认按 (换车, 破坏, 成本) 字典序），完整候选池保留在 outcome.candidates 供追溯，metrics 只挂摘要字段。

## P1-5 新任务无法移入空闲车辆【已修复】

**问题**：`_neighbors` 中新任务 relocate 的目标集合取 `sorted(seqs)`（只含已出车路线），空车永远不是插入目标。

**修复**：`_iter_neighbors` 中新任务 relocate 目标改为 `scn.vehicles` 全部车辆（含未启用空车）；邻域生成器改为惰性生成。

**验证**：`TestReviewCounterexamples::test_new_task_can_relocate_to_idle_vehicle` —— 三车场景 V3 未出车，断言邻域含 o4→V3 的移动且结果可行。
（实现备注：调用 `_iter_neighbors` 时 seqs 必须覆盖全部订单，生产路径由候选构造天然满足，测试构造数据时同样遵守。）

## P1-6 规模控制（P2 并案，基础版）【已修复】

`beam_width`（默认 64，frontier 按 (换车, 破坏, 成本) 择优限宽）、`max_neighbors_per_candidate`（默认 2000）、`max_archive_size`（默认 512）；`search_stop_reason ∈ {time_budget_exhausted, archive_full, neighborhood_budget, frontier_exhausted}` 如实记录。c102-s101 实测 candidate_count=512 命中归档上限并按实标注。

## P2-1 锁定成员移动放松【缓议 · 记录在案】

复审意见属"可提升"而非缺陷。决定：**维持保守语义（锁定成员禁止跨车移动）**，理由：放开锁成员移动与"锁 = 承诺不变"的任务书红线存在张力，防违规优先；放松方案（锁成员仅保持相对顺序、允许同车换段）留作后续增强项，需总工确认后再动。

---

## 附带发现（非阶段 3 缺陷，移交数据/阶段 0-2 线）

1. **多个 dev 草稿原计划生成超时**：r101-s101 / r101-s102 / c101-s102 / c102-s102 在 60–180 s 预算内 `E_PLAN_NOT_FOUND`（报错措辞符合"超时≠无解"红线，明示可加大预算重试）。c101-s101、c102-s101 可正常生成。建议阶段 1/2 数据线核查这几个种子的原计划生成预算或生成器参数。
2. **与并行 Kimi Code 会话的协作状态**：阶段 4 模块（`src/llm/`）由并行会话在制。本表修订期间全量测试一度 12 failed（均为 `tests/test_llm_phase4.py` 跨实例存储问题），**已由该会话自行修复**，当前 201 全绿，本表未改动 `src/llm/` 任何业务逻辑。

## 证据文件索引

| 证据 | 路径 |
|---|---|
| 全量测试日志（201 passed） | `reports/stage3_v2_pytest_log.txt` |
| 三方法对比（c101-s101） | `reports/method_compare/compare_c101-s101.json`、`summary_c101-s101.md` |
| 三方法对比（c102-s101） | `reports/method_compare/compare_c102-s101.json`、`summary_c102-s101.md` |
| 阶段 3 测试（21 项零 skip） | `tests/test_low_disruption.py` |
| 修复前旧 smoke（旧口径留档） | `reports/stage3_smoke/` |
