# 字段字典（阶段 7 交付）

机器可读 Schema 见 `docs/schema/scenario.schema.json`（可由
`scripts/export_schema.py` 重新导出）。时间接口层统一**整数秒**；
`time_origin` 必须带时区。

## 场景草稿（ScenarioDraft，输入）

| 字段 | 类型 | 说明 |
|---|---|---|
| schema_version | int | 契约版本号 |
| scenario_id | str | 场景唯一 ID |
| version | int | 草稿版本（确认后单调递增） |
| time_origin / time_unit | str | 时间原点（带时区）/ 单位（秒） |
| capacity_unit | str | `load_units` 或 `cm3`，单场景只启用一种 |
| depot_node_id | str | 仓库节点 |
| orders[] | | 订单：order_id、node_id、demand、ready_s、due_s、service_s、is_new（新任务标记，不得出现在原计划） |
| vehicles[] | | 车辆：vehicle_id、capacity、available_from_s、return_by_s |
| travel_matrix | [[num]] | 完整方阵、对角 0、元素有限非负；非对角 0 是合法行驶时间（同址/取整），缺边必须显式 null 表达、首版拒绝（与 `src/domain/models.py` 一致） |
| locks | | assignment_locks（换车锁）/ relative_order_locks（相对顺序锁），只作用于旧任务 |
| cost_config | | 成本系数（必须带单位与来源）；无真实单价时以行驶秒为指标 |

## 求解结果行（summary.csv / raw 行，输出）

| 字段 | 说明 |
|---|---|
| scenario_id / split / n_orders | 场景与冻结分组 |
| method / backend | insert / rebuild / low_disruption；rebuild 显式记录实际后端 |
| cost_cap_ratio | 低扰动成本上限倍率（冻结 test 配置 1.10） |
| status | feasible / no_feasible_found / proven_infeasible / error；超时记 TIMEOUT_NO_SOLUTION（≠不可行） |
| has_solution / validation_ok | 是否产出路线 / 是否通过独立校验器复核 |
| total_travel_time_s | 总行驶时间（模型秒，抽象成本，非人民币） |
| elapsed_ms 等 | 端到端/求解/校验分项耗时（毫秒；本轮为软预算口径） |
| allowed_cost_s / within_cap | 低扰动成本上限（=cap×参考最佳）与机器可读布尔达标标记 |
| scenario_input_hash / input_hash | 输入哈希（一致性核对） |
| budget_ms | 请求预算（本轮 60000，软上限） |

## 状态与错误码（src/domain/errors.py）

- `E_VERSION_CONFLICT`：提交后场景已变化，任务失败而非静默漂移。
- `E_PLAN_NOT_FOUND`：原计划构造预算内未找到（物化阶段，≠场景不可行）。
- `E_EXPORT_FORBIDDEN`：未过独立校验禁止导出。
- `E_COST_CAP_RATIO`：上限倍率超出 [0.5, 2.0]。
- `E_BACKEND`：未知重排后端；无 COPT 许可显式选 copt 如实报错，不静默回退。
