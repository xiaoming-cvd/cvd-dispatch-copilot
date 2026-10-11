# 第六阶段审查逐项关闭表（2026-10-11）

对照《第六阶段审查与跨电脑 AI 改进建议》逐项修复与证据位置。

## P0-1 冻结清单与评测分母不一致 —— 已修复

- manifest 对账表：报告〇节列出全部 39 个冻结 ID 的物化状态/失败原因/
  草稿哈希(前12位)/是否进入主评测/三方法状态；完整 SHA-256 见
  `reconciliation.csv`（39 行，含每场景完整草稿哈希）。
- 8 个物化失败场景（dev 7 + test 1）逐一列明原因：原计划物化独立 20s
  预算耗尽（E_PLAN_NOT_FOUND），发生在三方法比较之前，≠方法失败≠不可行；
  `summary.csv` 每场景补 `__materialize__` 失败行。
- 双口径并报：可行率同时给"冻结集口径"（test LD 15/16）与"可物化子集
  口径"（test LD 15/15），图表与文字均注明分母含义。
- 断言：`build_evaluation_report.py` 生成时强制校验清单 ID 集合 = 原始
  结果 ID 集合（缺一即失败退出）；`tests/test_evaluation6.py::
  test_summary_covers_all_manifest_scenarios` 断言 39 场景对齐、8 失败行、
  无预算列空缺。未补跑任何场景、未更换清单或测试集。

## P0-2 原始证据与冻结信息不足 —— 已补齐

- 本审查包含 `raw/*.json`（39 个逐场景原始记录）与冻结输入草稿
  （`scenarios/dev|test/*.json`，39 个）。
- 新增 `frozen_inputs.json`：每场景草稿 SHA-256、种子、锁比例、基准算例、
  场景输入哈希，外加依赖版本（ortools 9.14.6206 / pydantic 2.14.0 /
  pytest 9.1.1）、Python 3.12.14、git 提交号。
- 报告可由 `raw/*.json` 一键程序重建（`build_evaluation_report.py`），
  未手改汇总；重建命令见报告第八节。
- 复算路径：草稿 SHA-256 ↔ frozen_inputs.json ↔ raw 的 scenario_input_hash
  ↔ summary.csv ↔ 报告。

## P0-3 预算宣称与实际耗时冲突 —— 已修正口径

- 报告标题与首页定名"60 秒软预算版"，明确"不得宣传为严格同预算"。
- 越界统计单独成节：COPT 诊断 11 行 62.5~77.3s（最大 kilby-tai150b-s101
  77296ms），写明"属秒级越界，不是毫秒粒度误差"；其余 10 行 ≤60.046s。
- COPT 诊断与主比较完全隔离（第四节单独成表，不混入三方法比较）；
  TIMEOUT_NO_SOLUTION 一律不写作不可行。
- 进程级硬截止实现列为后续工作（未在本轮声称完成）。

## P1 成本上限验证 —— 已补逐行核验

- `summary.csv` 新增 `allowed_cost_s`（=cap_ratio×reference_best_cost）
  与 `within_cap` 列；全部 60 个低扰动已求解行核验结果：
  **违规 0 行**；54 行未超上限；6 行超上限但已如实标注
  `within_reference_cap=false`（no_candidate_within_cost_cap 语义）。
- reference_best 来自跨方法已验证方案；缺失时回退候选池最优并标注
  pool_fallback（raw 行 warnings 可查）。
- 测试：`test_summary_covers_all_manifest_scenarios` 断言无 VIOLATION 行。

## P1 敏感性图 —— 已改为固定候选池实验

- 根因：raw 中 dev 敏感性行是三次独立求解，不能解释为阈值效应。
- 新实验 `scripts/run_sensitivity_fixedpool.py`（dev 16 个物化成功场景
  全部跑完，结果在 `sensitivity_fixedpool/`）：同一候选池（同预算同默认
  参数生成一次）上仅改筛选阈值；每记录含 git 提交号与草稿/输入哈希。
- 低扰动求解器配套重构：`generate_candidates`（只生成池）与
  `select_from_pool`（只筛选）公开分离，`solve_low_disruption` 行为不变
  （21 条低扰动回归测试通过）。
- 图表标题如实说明：cap 放宽⇒可选集合单调增大；选中成本按扰动优先
  规则不保证单调。
- 测试：`test_fixedpool_sensitivity_dev_complete` 断言 16 场景齐全且
  within_cap 语义一致。

## P1 图表与产品定位 —— 已改

- 测试集成本图按数据来源拆分（Solomon/Kilby 各一张），解决量级不可读。
- 新增配对成本差图（`chart_cost_diff.png`，n=7，标题注明"不能据此推断
  总体节约"）。
- 报告明示配对样本少（insert×rebuild 仅 5 个），成本优势结论仅限配对
  子集；演示口径要求响应延迟（约 60s 用满软预算）与成本一并呈现。

## P1 算法边界分层 —— 已补分层证据

- 报告第六节附分层可行率表：按数据来源/时间窗紧度/锁比例/订单规模
  四个维度分层（可物化子集口径），不再仅用"75+ 节点"划界。

## r202-s102 COPT 不可行证明 —— 已附限定条件

- 报告第四节注明：该证明仅针对该输入与 COPT 建模；附输入哈希与求解器
  版本（raw 行 solver_version）；独立校验器未采信该结论以外的求解器状态。

## 阶段 5 端到端证据 —— 已附

- 报告第八节列出 4 条回归测试名（test_confirm_becomes_active_version /
  test_version_drift_fails_honestly / test_solve_version_conflict_409 /
  test_old_version_export_uses_snapshot），随全量 329 passed 通过；
  `tests/test_api_stage5.py` 与 `tests/test_evaluation6.py` 随包移交。

## 本轮未执行（按决策保留）

- 300 秒 COPT 诊断：未启动；待验收后预先指定 3 个代表性 dev 大场景，
  单独成表，不碰 test、不并入 60s 主比较。
- 进程级硬截止：列为下一版工作。

## 测试

- 全量 `329 passed, 158 warnings`（新增 2 条：summary 上限核验断言、
  固定池完整性与语义断言）。
