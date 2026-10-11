# Amazon Last Mile 字段映射与适配范围

> 依据：任务书 [2][3]（AWS Open Data 目录 / MIT CAVE rc-cli 数据字典）。
> **许可：CC BY-NC 4.0（非商用）**；公开可下载 ≠ 可商用或可上传比赛仓库，
> 提交前由队长再次核对授权范围。本阶段未下载真实数据，仅用合成结构样本测试解析器。

## 适配范围（任务书 3 章）

- 首轮只做**单条路线**的任务解析与受控插单回放。
- 路线间通常缺少完整出行矩阵，**不得拼成真实车队**；若阶段 6 需多车场景，
  须单独说明同站同日筛选与跨路线矩阵来源（本阶段不做）。
- 站点键为 `route_id + stop_id` 组合，避免跨路线误合并。

## 字段映射（公开数据字典 → 本项目 schema）

| Amazon 字段 | 本项目字段 | 规则 |
|---|---|---|
| `route_id` + `stops.<id>` | 组合键 `route_id:stop_id` | 全程使用，不拆用 |
| `executor_capacity.volume_capacity_cm3` | `Vehicle.capacity` | capacity_unit = `cm3`（体积维度，**不虚构重量**） |
| 包裹 `dimensions.{h,w,d}_cm` 连乘 | 站点容积 | 同站全部包裹体积**汇总** |
| `stops.<id>.time_window.{start,end}_time_utc` | `Order.ready_s / due_s` | 转秒；**缺失时记录为缺失**，按声明规则用 [0, 86400]，绝不转零点截止 |
| `stops.<id>.planned_service_time_seconds` | `Order.service_s` | 原值 |
| `travel_times.<a>.<b>` | `TravelMatrix.time_s` | **有向**，路线内已有矩阵直接用；缺边视为数据缺失上报 |
| 历史实际访问顺序 | — | **隔离**：不得作为在线决策输入（任务书 3 章） |
| `new_*` 文件 | — | 是评测输入快照，不是动态插单记录；新增事件一律标注合成 |

## 已声明的处理规则

1. 缺失时间窗：记为 missing，采用 `[0, 86400]` 秒默认窗并写入 `assumptions`。
2. 容积为 0 的站点（无包裹）不生成订单。
3. 矩阵缺边：解析保留，进入 schema 前由校验器拒绝并报告（与 Solomon 一致）。
4. 经纬度不换算为路网距离；路线无真实路网时页面只显示示意图（阶段 5）。
