# 阶段 5 审查问题逐项关闭表

- 日期：2026-10-11
- 依据：《第五阶段审查与跨电脑 AI 改进建议》（2×P0 + 4×P1）
- 代码状态：Git 提交见文末；全量测试 **325 passed**（日志 `stage5_pytest_log.txt`）
- 新增回归测试 12 条，全部在 `tests/test_api_stage5.py`

---

## P0-1 约束确认没有成为当前求解场景 —— 已关闭

**根因**：confirm 只写 `versions/<id>/vN.json`，`load_scenario` 始终读
`_materialized/<id>.json`，活动指针永远停在 v1；solve 的冲突检查比对的是
原始 `draft.version`（恒为 1）。

**修复**：
- `ScenarioIndex.save_materialized` 改为"版本归档（versions/vN.json）+ 原子
  切换活动指针（tmp + os.replace）"；confirm 走 `save_version` 复用同一路径。
- solve 提交时绑定当前活动版本与 input_hash（响应新增 `bound_version`）；
  worker 执行前核对，漂移即任务失败 `E_VERSION_CONFLICT`（不静默求解新版本）。
- solve/base_version 乐观锁改对**当前活动版本**判定（确认后 v2 可解、v1 409）。
- 重跑 baseline：已有已确认版本（v>1）时默认 409；显式 `force=true` 才重建，
  版本单调递增并附"已确认锁定不保留"警告。
- result.json / export.csv 一律加载**该任务求解时的版本快照**
  （`load_snapshot(sid, version)`），取不到快照 403/409，不用当前版本替代。

**最小复现（修复前 → 修复后）**：
- 修复前：confirm 返回 v2，但 `GET /api/scenarios/{id}` 仍显示 v1，后续 solve
  用旧锁；导出显示 v1。
- 修复后：`test_confirm_becomes_active_version`——确认后详情 v2、solve 绑定 v2
  成功且结果 version=2、提交 v1 返回 409、导出 CSV 头为 `version=2`。

**测试**：`test_confirm_becomes_active_version`、
`test_old_version_export_uses_snapshot`（v1 任务在 v2 之后导出仍显示 v1 快照）、
`test_baseline_refuses_silent_overwrite`。

## P0-2 无解结果可能被导出为执行 CSV —— 已关闭

**根因**：export.csv 只查 `validation.independent_ok`；`NO_FEASIBLE_FOUND`
  若校验字段为 true 就会产出只有表头的 CSV。

**修复**：新增 `_executability(row)` 统一判定，可执行须同时满足：
① 状态 `feasible`；② `has_solution` 且 routes 非空；
③ `independent_ok`；④ 快照存在且 `input_hash` 匹配。任一不满足 → 403
`E_EXPORT_FORBIDDEN`（原因写清）。result.json 保留诊断原始结果，但新增
`executable` 标记与醒 `notice`（"诊断原始结果，不可执行"）；前端结果页显示
"可执行：否"并禁用导出按钮。

**测试**：`test_export_rejects_timeout_no_solution`（超时无解，校验 true 也 403）、
`test_export_rejects_proven_infeasible`、`test_export_rejects_hash_mismatch`、
原有 `test_export_forbidden_when_validation_fails`（校验失败）。

## P1-1 预算记录不完整 —— 已关闭

**修复**：结果行现在分别记录 `requested_budget_ms`（请求预算）、
`effective_budget_ms`（物化后剩余 = 旧字段 budget_ms）、`materialize_ms`、
`solve_ms`（= result.elapsed_ms）、`validate_ms`（= metrics 的
elapsed_validate_ms）、`total_elapsed_ms`（端到端）、`timed_out`
（总耗时 > 请求预算）。baseline 行同步记录请求预算/物化耗时/总耗时/越界。
`budget_ms` 必须 > 0（pydantic `gt=0`，0 与负值 422）。

**测试**：`test_budget_fields_recorded`、`test_budget_must_be_positive`。

## P1-2 队列提交时未绑定版本快照 —— 已关闭

**修复**：solve 提交时把当前活动版本与 input_hash 写入任务 payload；
worker 执行前重载场景并核对，不一致则任务以 `E_VERSION_CONFLICT` 失败
（失败如实可见，不静默漂移）。

**测试**：`test_version_drift_fails_honestly`——任务 A 占住唯一 worker，
任务 B 绑定 v1 排队；排队期间确认 → v2；A 成功（执行时仍是 v1），
B 失败且错误含 `E_VERSION_CONFLICT`。

## P1-3 上传限额仅依赖 Content-Length —— 已关闭

**修复**：upload 端点改为 `request.stream()` 流式累计字节，超过上限立即
413，无 Content-Length 的分块请求不会再被一次性读入。Content-Length 前置
拒绝的中间件保留（快速路径）。

**测试**：`test_upload_streaming_limit_without_content_length`
（分块上传 >200B 且无 Content-Length → 413）、
`test_upload_streaming_under_limit_ok`。

## P1-4 JobQueue.stop() 强制置空 _thread —— 已关闭

**修复**：`stop(timeout)` 返回是否真正退出；超时未退出时**保留** `_thread`
引用，`start()` 检测到旧线程仍存活即抛 `E_QUEUE_RUNNING`，杜绝两线程并行
执行同一队列；真正退出后才允许重启。

**测试**：`test_queue_start_refuses_while_alive`。

---

## 审查方要求的回传要素

| 要求 | 位置 |
|---|---|
| 每项最小失败用例 | 上表"最小复现"与对应测试函数（先写测试复现缺陷，再修） |
| 修复前后输出 | 上表 P0-1 节；其余以断言锁定行为差异 |
| 测试名称 | 上表逐项列出，均在 `tests/test_api_stage5.py` |
| 全量测试日志 | `reports/stage5_pytest_log.txt`（325 passed） |

## 阶段 6 口径确认（按审查方要求）

- 三方法比较同一 60 秒端到端口径；OR-Tools 与 COPT 分栏报告；
- `TIMEOUT_NO_SOLUTION` 不写成不可行；OR-Tools 可行不写成最优；
- 干净环境复跑数据/合成样本清单随交付包提供（`data/sample` +
  `scripts/make_sample.py` + registry 草案）。

## 提交

- Git：`9754f5a`（阶段 5 首交）→ 本次修复见随后提交（仅本地，未上传）。
