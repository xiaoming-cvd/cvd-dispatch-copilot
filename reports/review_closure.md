# 跨电脑审查问题关闭表（2026-10-11）

依据：《阶段1评估与跨电脑AI改进建议》（审查机 2026-10-11）。
审查包基于旧快照（report1，7 文件、88 项测试）；本表按**当前真实源码**逐条复现后填写。
每项状态：✅ 已修复且验证 ／ ⏳ 部分完成 ／ ❌ 未修复。验证方式标注原生模型测试（native）或隔离/mock 测试。

## P0（阶段 3 前必须修复）

| 问题 | 状态 | 修复 | 验证证据 |
|---|---|---|---|
| P0 A COPT 目标漏返仓弧 | ✅ native | 目标补入客户→仓库弧，与独立校验器 total_travel_time_s 同定义（copt_core.py） | R01 测试 + `reports/exhaustive_check.json`：穷举最优 5 = 求解目标 5（修复前为 6） |
| P0 B 大 M 不足制造假不可行 | ✅ native | B = horizon + max_service + max_tau，推导写入模块 docstring | R02 测试 + 穷举核对：含 1000s 未选弧反例，最优 3，不再 proven_infeasible |
| P0 C 同车多条路线被接受 | ✅ native（隔离反例输入） | validate_solution 新增 E_SOL_DUP_VEHICLE | `test_duplicate_vehicle_routes_rejected`；修复前失败日志 `reports/review_p0p1_prefail_log.txt` |
| P0 D 顺序锁成员跨车被接受 | ✅ native | 锁成员必须出现在锁指定车辆路线中再查顺序；正例"新单穿插"保持通过 | `test_member_on_other_vehicle_rejected` / `test_member_missing_rejected` / `test_new_order_between_members_accepted` |
| 附带：零耗时子回路（R06 部分） | ✅ native | 纯 MTZ 在 0 行驶+0 服务时无法排除断仓库零环（实测复现 OPTIMAL 空路线）；引入单商品流连通约束（仓库供流 n、每客户净耗 1、弧流 ≤ n·x） | `test_no_broken_cycle` |

## P1（下次验收前补齐）

| 问题 | 状态 | 修复 | 验证证据 |
|---|---|---|---|
| P1 E 返回时刻不一致未拒绝 | ✅ native | E_SOL_RETURN_MISMATCH；routes/schedule/metrics/validation 同源重算 | `test_fake_return_s_rejected` / `test_pipeline_fields_consistent` |
| P1 F COPT 隐式依赖 | ✅ native（sys.modules 屏蔽模拟无 COPT 环境） | pipeline/rebuild 全部改为选择后端时延迟导入；显式选 rebuild 而无 COPT 返回可理解 ERROR | `test_pipeline_imports_and_insert_runs_without_coptpy`；`review_check.py --skip-copt` 基础模式 overall pass |
| P1 G 预算未传递 | ✅ mock（时钟替身，不依赖机器速度） | insert/low_disruption 种子与枚举接入统一截止时间；metrics 记录 elapsed_solve_ms / elapsed_validate_ms / elapsed_ms | `test_insert_respects_deadline`；R09 |
| P1 H 状态与版本靠方法名猜 | ✅ mock | OrtoolsOutcome 增加 backend/backend_version/termination/has_incumbent/status_detail；pipeline 结构化判定并接受 MIP_FEASIBLE；验证拒绝显式标 validator_rejected；proven_infeasible 警告注明"仅对当前建模有效" | `TestStatusMapping` 全表 7 种情形；`test_feasible_not_optimal_kept`；`test_validator_rejected_distinct` |

## R01–R10 回归测试

| 编号 | 状态 | 说明 |
|---|---|---|
| R01 闭环目标反例 | ✅ native + 穷举核对 | 5 = 5 |
| R02 大 M 反例 | ✅ native + 穷举核对 | 3 = 3，非 infeasible |
| R03 同车双路线 | ✅ | E_SOL_DUP_VEHICLE |
| R04 相对锁成员跨车/缺失 | ✅ | 含穿插新单正例 |
| R05 伪造返回时刻 | ✅ | E_SOL_RETURN_MISMATCH |
| R06 零服务同址（零矩阵） | ✅ | 全 0 矩阵合法（非缺边），无断环，feasible |
| R07 无 COPT 环境 | ✅ mock | 屏蔽 coptpy 后 pipeline 可导入、insert 可运行、rebuild 报可理解错误 |
| R08 状态映射全表 | ✅ mock | 最优/可行非最优/超时带解/超时无解/证明无解/验证拒绝/异常 7 情形 |
| R09 预算传递 | ✅ mock | 时钟替身确定性验证 |
| R10 数据重建 | ⏳ 部分完成 | 开发机已验证 17 个草稿哈希与 registry 一致（0 mismatch）；从原始文件全量重建需审查机执行 `scripts/download_solomon.py` + `scripts/build_scenarios.py`，待审查机确认 |

## D1–D4 数据链

| 条目 | 状态 | 说明 |
|---|---|---|
| D1 可重算数据链 | ⏳ 部分完成 | 解析器/生成器/拆分/适配器/模型/测试/生成命令均已在包内（src/adapters、src/domain、scripts、tests）；17 个草稿 + registry 随包；原始文件许可"待确认"故不随包转发，提供 `scripts/download_solomon.py`（URL + sha256 核对） |
| D2 溯源措辞修正 | ✅ | manifest v1.1：时间单位改为"基准单位→60 模拟秒"；canonical_source 只存规范 URL，访问说明独立字段；source_ref 如实标注浮动分支；新增 license_url/terms_url/license_status/redistributable（均如实标"待确认"）；verification_scope/status 如实记录实际核对范围（仅 C101 首行交叉核对） |
| D3 覆盖扩展 + 指纹 | ✅（指纹）／⏳（扩展属阶段 6 前改进，不提前强加） | `scripts/check_base_fingerprint.py` + `reports/base_fingerprint.json`：11 算例同族变体坐标+需求 Jaccard=1.0（仅时间窗不同），布局感知划分已保证同族不跨 dev/test（registry 复核一致）；发现并归档 3 个旧划分遗留草稿至 `_stale_20261011/`（含哈希记录，未删除） |
| D4 结构合法≠业务可行 + 状态规范 | ✅ | 草稿不内嵌原计划（不造假）；materialize 失败带原因抛错不静默删除；CLI 写场景时 `_meta.pending_original_plan` 与 `materialized` 不再冲突 |

## 测试计数

- 修复前：`tests/test_review_p0p1.py` 18 项中 11 项失败（证据 `reports/review_p0p1_prefail_log.txt`）
- 修复后全量：**142 passed**（`reports/review_fix_pytest_log.txt`）
- 一键审查：有 COPT 与 `--skip-copt` 两种模式 overall 均为 pass（`reports/review_summary.json`）
- 修复前快照：`reports/snapshot_prefix_20261011.zip`（sha256 前缀 7491d6b7927cd533）

## 未关闭 / 待审查机确认

1. R10 全量重建需在审查机执行下载脚本后确认（原始文件不随包）。
2. 数据许可仍为"待确认"（SINTEF 未见明示条款），商用/比赛再分发前须人工核对。
3. 求解器"证明无解"仅对当前建模有效——数学求解器不替错误模型背书，已写入警告文案与 handoff。
