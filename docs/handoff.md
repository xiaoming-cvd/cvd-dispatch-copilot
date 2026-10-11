# docs/handoff.md — 阶段交接记录

## 当前状态：阶段 1 ✅ · 阶段 2 ✅ · 阶段 3 ✅ v2 复审修复完成（待总工复审）· 阶段 4 ✅ v2 复审修复完成（待验收）· 阶段 5 ✅ 复审修复完成（待验收）· 阶段 6 ✅ 冻结评测 39 场景跑完（待验收）（2026-10-11）

> **阶段 4 v2（第四阶段跨电脑复审，2026-10-11）**：复审结论"有条件通过代码骨架"。
> P0-1（显式 decisions 只接受所列项）+ P0-2（source_text 必须是原文子串，伪造依据
> 进 rejected）+ P0-3（仅澄清/全拒绝/空草案不升版本，E_CONFIRM_NEEDS_CLARIFICATION /
> E_CONFIRM_NO_EFFECT）+ P1（确认单一状态记录原子落盘 + 并发/写入失败反例、CLI
> 参数校验与互斥语义、25 条语料逐条校订接入参数化测试：supported 17 / clarify 1 /
> deferred 7）+ P2（draft_id 含基线哈希且拒同 ID 异内容、get_draft 路径防护、
> HTTP 错误脱敏）已全部修复。验证基线：全量 **279 passed, 0 failed, 0 skipped**
> （`reports/stage4_v2_pytest_log.txt`）。逐项关闭表见
> [review_closure_stage4](../reports/review_closure_stage4.md)。
> 注意：CLI 确认语义有意的行为变更——裸 `--confirm` 不再隐式全接受，必须显式
> `--yes` 或 `--accept`；deferred 语料项（时间窗/订单字段/车辆字段/新任务锁）
> 为后续阶段能力，不得声称已实现。

> 阶段 4"自然语言需求与人工确认"已交付（LLM 适配层 + 草案/确认接口 + 51 项中文需求测试
> 全过 + DeepSeek 真实模型脱敏冒烟）。本阶段不调用求解器、未经确认不生成新场景版本；
> 密钥只存本机 `.env`（gitignore 排除）/后端环境变量，全程未进仓库与产物。
> 逐项状态见 [review_closure](../reports/review_closure.md)；真实模型冒烟见
> [stage4_real_model](../reports/stage4_real_model.md)。

> 阶段验收口径按审查报告执行：P0 四项 + P1 四项全部"已修复且验证"（原生模型测试 +
> 穷举核对），但阶段 2/3 的正式通过以审查机复审为准，不在本机提前标绿。
> 逐项状态见 [review_closure](../reports/review_closure.md)。

> **阶段 3 v2（第二轮跨电脑复审，2026-10-11）**：复审结论"有条件通过"，P0-1（成本上限
> 改基于跨方法 `reference_best_cost`）+ P0-2（预算重复扣减改绝对截止）+ P1 六项
> （ratio 参数化、测试零 skip、候选可审计、输出瘦身、新任务可移入空闲车、规模上限）
> 已全部修复；P2 锁放松缓议记录。验证基线：全量 **201 passed, 0 failed, 0 skipped**
> （`reports/stage3_v2_pytest_log.txt`）；三方法对比两场景见 `reports/method_compare/`。
> 逐项关闭表见 [review_closure_stage3](../reports/review_closure_stage3.md)。

> **阶段 5 数据线（Kilby 补充基准，2026-10-11）**：Kilby 动态 VRP 22 算例已入库
> （`data/raw/kilby/`，溯源四件套齐，CC BY 4.0 + DOI，sha256 核对一致），经
> `src/adapters/kilby.py` + `scripts/import_kilby.py` 走阶段 0 管线转换：新增草稿 22 个
> （dev 11 / test 11，布局整组划分，split seed 同 20261010），全部过 validate_draft。
> 转换规则（manifest 留痕）：需求取绝对值、时间=欧氏距离取整（单位速度假设）、
> 无逐客窗取 [0, 仓窗末端]、cutoff=0.5 按原始 appearing time 划旧/新（**非合成事件**）、
> 车队=ceil(全部任务总量/容量)+1（曾误按旧任务配车致 MIP_INFEASIBLE，已修并加回归测试）。
> 质量报告 `reports/kilby_data_quality.md`，测试 `tests/test_kilby_import.py`（15 项），
> 全量 **216 passed**。smoke：kilby-c100-s101 / kilby-tai75b-s101 上低扰动可行
> （0 换车 0 破坏，成本 1988 / 2177），insert 枚举不可行、rebuild 60s 超时无 incumbent
> （**超时≠无解**，已如实标注）——COPT MIP 在 75+ 节点规模扩展性不足是阶段 5 评测
> 设计必须面对的已知约束（选项：加大预算 / OR-Tools 后端 / 启发式暖启动，待总工定）。

### 审查修正内容
1. **矩阵 0 值规则**（源码确认过度限制后修正）：非对角线 0 = 合法行驶时间（同址/取整），
   不再判缺边；缺边必须显式 null 表达，首版拒绝（E_MATRIX_EDGE_MISSING）。
   规则写入 `TravelMatrix` docstring，JSON Schema 表达为 integer|null。
2. **深冻结与哈希证据**（新增 `tests/test_immutability.py`，20+ 断言）：
   容器字段全部 tuple 化、锁/成本字典 FrozenDict 化（默认工厂同步），
   原地修改列表/字典现在真正抛错；哈希敏感性（矩阵/锁/计划/订单任改即变）、
   model_copy 冻结隔离（原计划对象与内容不变）、NaN/±inf（mode=before 统一
   E_NON_FINITE 拒绝）、容量/时间单位冲突（构造路径拒绝）全部有测试证据。
3. **布局感知划分**：坐标集合相同的算例整组划入同一集合（修复前 C2/R 布局组跨
   开发/测试——确认是真实缺陷，非仅记录）；新增 `tests/test_layout_split.py` 回归。
4. **执行清单补四点**：校验器分层（checks=输入结构 / timetable=路线可行性独立校验）、
   阶段验收记录表、阶段 3 预算口径（子求解耗时计入本方法总预算）、
   数据许可"链接+待确认"标注；×60 明确为模拟尺度转换。
5. 情景已按新划分重生成（17 个，registry 同步），tight 规则修正致 c101-s102 哈希变化（预期）。

- 全量测试：**111 passed**（`reports/stage0_review_fix_log.txt`）
- 日期：2026-10-11
- 代码版本：未建 Git 仓库（等队长指示）
- 项目根目录：`C:\Users\tianj\Desktop\城配调度副驾\`
- 说明：阶段 2 的骨架代码由并行会话产出，本会话完成收尾修复与 COPT 后端接入。

## 环境配置

| 项 | 值 | 验证 |
|---|---|---|
| Python | 3.12（venv 3.12.7） | ✅ |
| pydantic / pytest | 2.14.0 / 9.1.1 | ✅ |
| fastapi / uvicorn | 0.143.0 / 0.54.0 | ✅ 已装（阶段 5 用） |
| ortools | 9.14.6206 | ✅ 已装（并行会话完成安装） |
| **COPT / coptpy** | **8.0.6（完全重排后端，队长决策）** | ✅ 系统 Python 已配好；coptpy 包复制入项目 venv 验证通过 |
| **阶段 4 LLM** | DeepSeek-Flash（`deepseek-flash`，OpenAI 兼容接口 `https://api.deepseek.com`） | ✅ 真实冒烟通过；密钥仅存本机 `.env`（gitignore 排除）/后端环境变量，未进仓库 |

## 运行命令（Windows，均已实际执行）

```bat
cd C:\Users\tianj\Desktop\城配调度副驾
.venv\Scripts\python.exe -m pytest
.venv\Scripts\python.exe scripts\make_sample.py
.venv\Scripts\python.exe scripts\export_schema.py
.venv\Scripts\python.exe scripts\build_manifest.py
.venv\Scripts\python.exe scripts\build_scenarios.py
.venv\Scripts\python.exe scripts\solve_cli.py   :: 阶段 2 CLI
.venv\Scripts\python.exe scripts\draft_cli.py --input data\sample\sample_small.json --text "o1 不能换车"   :: 阶段 4 草案（演示模式加 set LLM_PROVIDER=mock）
.venv\Scripts\python.exe scripts\draft_cli.py --input data\sample\sample_small.json --confirm <draft_id>   :: 阶段 4 确认（产生新场景版本）
```

## 测试结果

- **142 passed in 1.36s**（`reports/review_fix_pytest_log.txt`）；阶段 4 后全量 **201 passed**
  （`reports/stage4_pytest_log.txt`，新增 `tests/test_llm_phase4.py` 51 项）
- 覆盖：阶段 0 契约校验、阶段 1（Solomon 解析/转换/生成器可复现/集合无交叉/原始文件哈希核对/Amazon 合成样本）、阶段 2（独立校验器手算预期、插单基线、完全重排、五种状态语义、proven_infeasible、锁冲突）、阶段 3（扰动指标、diff 固定词表、低扰动求解端到端、锁保持、选择规则、候选全部独立复核）、跨电脑审查回归（R01–R09：闭环目标/大 M/同车双路线/锁成员跨车/返回时刻/零值子回路/无 COPT 隔离/状态映射全表/预算传递）
- 修复前失败证据：`reports/review_p0p1_prefail_log.txt`（11 项失败）；修复后同批 18 项全过

## 跨电脑审查修复（2026-10-11，详情见 reports/review_closure.md）

1. **校验器**：E_SOL_DUP_VEHICLE（一车一路线）、相对顺序锁成员必须在锁指定车辆（E_SOL_REL_ORDER_LOCK）、E_SOL_RETURN_MISMATCH（声明返回时刻必须等于独立重算）。
2. **COPT 建模**：目标补返仓弧（与独立校验器同定义）；大 M 修正为 horizon+max_service+max_tau（推导入 docstring）；新增单商品流连通约束消除零耗子回路（修正前 MTZ 在 0 行驶+0 服务下会返回"最优空路线"，已实测复现）；路线提取加 visited/步数上限。
3. **pipeline**：COPT 改为延迟导入（无 COPT 环境 insert/low_disruption/OR-Tools 可用，显式选 rebuild 报可理解错误）；预算真实传入插单/低扰动枚举并记 elapsed_solve_ms/elapsed_validate_ms；OrtoolsOutcome 结构化（backend/termination/has_incumbent/status_detail），接受 MIP_FEASIBLE，验证拒绝显式标 validator_rejected；proven_infeasible 警告注明"仅对当前建模有效"。
4. **数据链**：manifest v1.1（时间单位措辞、canonical/mirror 分离、source_ref 如实标浮动分支、license_url/terms_url/license_status/redistributable=待确认、verification_scope 如实记录仅 C101 首行交叉核对）；新增 `scripts/check_base_fingerprint.py`（同族变体坐标+需求 J=1.0，划分已隔离）与 `scripts/download_solomon.py`（原始文件不随包，许可待确认）；归档 3 个旧划分遗留草稿（`_stale_20261011/`，含哈希，未删除）；CLI 写出场景时 pending_original_plan/materialized 不再冲突。
5. **一键审查**：`scripts/review_check.py`（有 COPT / --skip-copt 两模式均 pass）+ `scripts/exhaustive_check.py`（R01 穷举 5=5、R02 穷举 3=3）。
6. **快照**：修复前代码快照 `reports/snapshot_prefix_20261011.zip`（sha256 前缀 7491d6b7927cd533）。

## 阶段 1 产物与验收对照

| 验收项 | 结果 |
|---|---|
| 来源与合成字段可追踪 | ✅ `data/manifests/solomon_manifest.json`（11 个算例：镜像 URL + SINTEF 规范源 + sha256 + 许可 + 字段映射 + 转换规则） |
| 样本通过 schema 校验 | ✅ 17 个派生情景草稿全部过 `validate_draft` |
| 数据许可状态有记录 | ✅ Solomon 学术基准注明；Amazon CC BY-NC 4.0 记录于映射文档 |
| 测试集不被调参读取 | ✅ 按基础算例划分（split seed 20261010），`registry.json` 记录 split；同一算例派生不跨集（测试断言） |
| 同种子结果一致 | ✅ 测试断言哈希一致 |

## 阶段 2 产物与验收对照

- `src/validation/timetable.py` 独立校验器（重算时刻/容量/窗/返回/覆盖/锁定，不采信求解器）
- `src/solver/insert.py` 插单基线（枚举插入，不搬旧任务，不漏单）
- `src/solver/copt_core.py` **COPT MIP 完全重排后端**（MTZ 时间链，支持两类锁，状态映射 MIP_INFEASIBLE/TIMEOUT_WITH_SOL 等）
- `src/solver/ortools_core.py` OR-Tools CP 版（保留作对照与阶段 3 候选生成）
- `src/solver/original.py` 原计划生成 + 锁定落实（lock_ratio/seed 可复现）
- `src/solver/pipeline.py` 编排 + 五种状态语义
- `scripts/solve_cli.py` CLI 输出规范 JSON/时刻表/校验报告

| 验收项 | 结果 |
|---|---|
| 可行结果均被独立复核 | ✅ pipeline 统一走 `validate_solution` |
| 拒绝漏单 | ✅ 求解器输出覆盖核对 + E_SOL_MISSING_ORDER |
| 返回时间和等待正确 | ✅ 手算预期测试（300/420/620/740/1350 等逐项断言） |
| 超时没有被写成证明无解 | ✅ 10ms 预算测试断言 no_feasible_found 且 ≠ proven_infeasible |

## 阶段 3 产物与验收对照（低扰动重排与差异解释）

| 验收项 | 结果 |
|---|---|
| 扰动指标精确定义 | ✅ `src/solver/perturbation.py`：换车数只统计旧任务；相邻破坏按"滤去新任务后旧有序对是否仍同车相邻"判定，插入顺延不计 |
| 候选生成（不调用子求解） | ✅ `src/solver/low_disruption.py`：插单种子 → 失败时单旧任务搬动枚举种子 → 尊重锁的 relocate/swap 局部搜索；全程 `evaluate_route` 独立重算 |
| 选择规则 | ✅ best_cost = 候选池最低成本；低扰动 = 成本 ≤ 1.10×best_cost 中按 (换车, 相邻破坏, 成本) 字典序最小；候选不足两个如实 warning |
| 逐任务 diff | ✅ `src/solver/diff.py`：车辆/双索引（原始+旧任务内）+ 固定词表变更原因，全部程序生成 |
| 预算口径（审查修正 3） | ✅ 低扰动不调用 COPT/OR-Tools 子求解，elapsed 即端到端耗时；假设写入输出 assumptions |
| 三方法同输入对比 | ✅ `reports/stage3_smoke/`（insert 38540s / rebuild 28220s / low_disruption 推荐 33372s，候选 216 个） |
| 测试 | ✅ 13 个新测试（无新增零扰动/顺延不计/换车只算旧任务/两类锁保持/选择规则/候选全部独立复核），全量 124 passed |

## 阶段 4 产物与验收对照（自然语言需求与人工确认，2026-10-11）

| 验收项 | 结果 |
|---|---|
| 未经确认不执行 | ✅ 草案只读场景；确认前存储无确认包、场景版本/哈希不变（测试 `test_no_confirmation_no_new_version`）；确认与求解器调用隔离（`test_injection_text_no_side_effects` 间谍断言） |
| 密钥只从后端环境变量读取 | ✅ `LLMConfig.from_env()` + `.env` 载入（不覆盖已存在环境变量）；`redacted()` 只暴露"是否已配置"；真实 Provider 密钥仅进请求头；grep 核对全部产物无密钥原文 |
| 无密钥全流程可跑（演示模式明确标示） | ✅ `LLM_PROVIDER=mock` 或未配密钥 → demo_mode，草案/确认包全程带标记，stderr 横幅"规则提取器，非真实大模型"（`reports/stage4_demo_*.json`） |
| 无固定假回复冒充真实模型 | ✅ mock 恒 `model=None`+demo 标记，未识别需求一律澄清问题（`test_mock_never_impersonates_real_model`） |
| 大模型只输出 schema 内约束草案 | ✅ Provider 只产文本 → `ProviderPayload`（extra=forbid）→ extractor 场景成员/旧任务/去重收口；越界条目进 rejected 附原因，不静默 |
| 不执行代码/不调 URL/不写路线 | ✅ LLM 层无 exec/eval/subprocess；需求中的 URL 不会被访问（测试断言联网即失败）；服务层不 import 求解器 |
| 结构化变更+原文依据+缺失项 | ✅ 每条 item/preference 带 `source_text`；缺字段进 `missing` 不编造数值（`test_missing_does_not_fabricate_values`） |
| 区分"不能换车"与"尽量不换车" | ✅ 硬约束→Locks；偏好→确认包绝不写成锁（`test_preference_never_becomes_lock`） |
| 指代不清返回澄清问题 | ✅ mock 与真实模型均回 questions（真实模型 case4） |
| 确认后产生新场景版本 | ✅ version+1、新输入哈希、基线版本/哈希核对（错版本/错哈希拒绝）、重复确认拒绝、逐项接受/拒绝 |
| 注入无副作用 | ✅ 注入文本/内嵌 JSON/载荷越界字段均被收口（5 项注入测试 + 真实模型 case5） |
| ≥20 条自建中文需求测试 | ✅ **51 项**（明确约束 7 / 硬vs偏好 3 / 模糊指代 3 / 缺字段 4 / 冲突 3 / 越界 7 / 注入 5 / 确认边界 8 / 演示模式与密钥 7 / 真实 Provider 接线 3 / 解释 2），全过 |
| （有密钥）脱敏真实模型测试单独报告 | ✅ `reports/stage4_real_model.md`：DeepSeek-Flash 5 需求 + 1 确认全通；名称勘正与 system prompt 加固已如实记录 |

- 新增代码：`src/llm/`（schema/config/base/mock_provider/openai_provider/extractor/service/explain）、`scripts/draft_cli.py`、`tests/test_llm_phase4.py`
- 演示日志：`reports/stage4_demo_{draft,confirmed,mixed}.json` + stderr 横幅
- 设计要点：偏好项不进 Scenario schema（避免 17 个既有情景 input_hash 漂移）；确认接口为进程内服务层 + CLI，HTTP 端点留给阶段 5 薄包装
- 真实模型注意：队长提供的 "DeepSeek-V4.1-Flash" 接口不存在，按接口返回改用 `deepseek-flash`（换 Pro 系改 `.env` 的 `LLM_MODEL`）

## 重要工程决策（需队长知悉）

1. **完全重排后端采用 COPT 8.0.6（替代任务书中的 OR-Tools）**——理由（队长指示 + 技术必要性）：
   - OR-Tools CP 在"固定出发时刻"模型下 PATH_CHEAPEST_ARC 无法构造初解（实测 15 节点 20s 超时），已改 PARALLEL_CHEAPEST_INSERTION 修复；
   - OR-Tools CP 对不可行情形只返回 ROUTING_FAIL，**无法可靠给出"证明无解"**（任务书要求区分 proven_infeasible）；COPT MIP 可证明不可行；
   - OR-Tools 版保留（`backend="ortools"` 可选，阶段 3 候选生成可用）。
2. **ortools_core 移除 drop 析取**：原实现给所有节点加丢弃惩罚，导致模型永不不可行、超时也能秒出"全丢弃"假解，直接违反"不得丢单"红线。
3. **SolutionValidation.issues 现汇总路线级+方案级全部违规**（测试红线要求一处可见）。
4. coptpy 以复制方式进入项目 venv（系统 site-packages → .venv），因 pip 下载大 wheel 在本机网络反复超时；COPT 本体与 license 为机器全局配置（`C:\Program Files\copt80`、`C:\Users\tianj\copt\license.dat`）。**换机运行需重装 COPT 或改回 OR-Tools 后端**（已留开关）。

## 已知风险 / 未完成

1. COPT 求解日志已关闭（Logging=0）；诊断时可在调用层打开。
2. `travel_seconds()` 冗余函数未清理（无害）；`build_travel_matrix` 中未使用变量 `idx`。
3. Git 仓库已按 2026-10-11 决策本地初始化并提交（**仅本地，未上传远端、未邀请协作者**；
   `.gitignore` 排除 .env、许可文件、原始受限数据、venv 与缓存；`.env` 已核实未入库）。
   私有远端与协作者留待队长确认。
4. 阶段 4：真实模型输出不确定（冒烟 5 例合规，但非覆盖率测试）；生产使用须人工复核草案。
   DeepSeek 模型名以接口返回为准（`deepseek-flash`/`deepseek-v4-pro`），`.env` 的
   `LLM_MODEL` 需按实际可用名填写。
5. 阶段 4：偏好项（如"尽量不换车"）已随确认包保存，但接线到求解配置是阶段 5/6 的工作；
   mock 词表仅覆盖任务书锁定语义内的小型中文需求，未覆盖句式一律澄清问题（不猜测）。

## 阶段 6 审查修复（2026-10-11 第六阶段审查，逐项关闭见 reports/review_closure_stage6.md）

- **P0-1 分母不一致**：报告〇节加全 39 ID manifest 对账表（物化状态/失败
  原因/草稿哈希/是否进入主评测/三方法状态），完整 SHA-256 见
  reconciliation.csv；可行率双口径并报（冻结集 test LD 15/16 vs 可物化
  15/15）；生成器强制一致性校验 + 测试断言。未补跑、未换清单。
- **P0-2 证据链**：审查包含 raw/*.json（39）+ 冻结草稿（39）+
  frozen_inputs.json（草稿 SHA-256/种子/锁比例/依赖/提交号）；报告一键
  程序重建，未手改汇总。
- **P0-3 预算**：定名 60 秒软预算版；越界统计单独成节（COPT 11 行
  62.5~77.3s 写明秒级越界非毫秒误差）；COPT 诊断与主比较完全隔离。
- **P1 成本上限**：summary 增 allowed_cost_s/within_cap 列，60 行 LD 核验
  违规 0、超上限已如实标注 6；测试断言无 VIOLATION。
- **P1 敏感性**：新增固定候选池实验（`generate_candidates` /
  `select_from_pool` 公开分离，dev 16 场景跑完，sensitivity_fixedpool/）；
  raw 中旧敏感性行明确标注为独立运行波动；图标题不写错误单调性断言。
- **P1 图表/定位**：成本图按来源分图；新增配对成本差图（n=7 注明不可
  推断总体）；报告附按来源/TW紧度/锁比例/订单规模分层可行率表；演示
  口径要求延迟与成本一并呈现。
- r202-s102 COPT 证明附输入哈希与求解器版本、限定"仅针对该输入与建模"。
- 阶段 5 确认生效端到端证据：4 条测试名入报告第八节，tests 随包移交。
- 测试：329 passed（新增 2 条）。审查包 v3：reports/
  review_portable_stage6_20261011.zip（110 文件，sha256 1a755f80…）。
- 未执行：300s COPT 诊断（待指定 3 个 dev 大场景）、进程级硬截止（下版）。

## 阶段 5 完成状态（2026-10-11，待队长验收）

- **已交付**：FastAPI 后端（`src/api/`：app/jobs/routes，端点 health、scenarios
  列表/详情/上传、template、baseline、constraints draft/get/confirm、solve、
  jobs 列表/详情/result.json/export.csv、history）；单文件前端
  （`frontend/index.html`，无构建步骤，全 textContent 渲染防 HTML 注入）；
  启动脚本 `scripts/serve.py`；端到端测试 `tests/test_api_stage5.py`（34 条）。
- **测试**：全量 313 passed（含 4 条按 Q2/Q3 决策更新的旧断言：默认后端切换
  后，证明不可行/超时语义测试显式指定 `backend="copt"`）。
- **口径落地（2026-10-11 决策）**：
  - 三方法统一 60 秒端到端预算（`Settings.default_budget_ms=60000`，端点层封顶）；
  - rebuild 默认 OR-Tools（`src/solver/rebuild.py BACKEND="ortools"`），每条结果
    含 method/backend/backend_requested/预算/耗时/状态/has_solution；
  - COPT 超时 = TIMEOUT_NO_SOLUTION（≠不可行）；OR-Tools 可行 ≠ 证明最优；
    无许可而显式选 copt → ERROR + 可理解警告，绝不静默回退；
  - 未过独立校验禁止导出（403 E_EXPORT_FORBIDDEN）；
  - 草案确认必须显式（accept_all 或逐项 decisions，默认不勾）；
  - 队列：单工作进程、限排队 8、任务落盘、重启 interrupted。
- **修复的真实缺陷**（实施中发现）：模块级共享 `APIRouter` 导致多实例闭包
  串队列（任务提交到已停止队列永远 queued）——router 改为 attach() 内创建；
  OR-Tools 结果缺 backend 标注；history 端点对 baseline 结果行 KeyError。
- **数据边界**：Solomon 仅来源链接+处理脚本（`scripts/download_solomon.py`）；
  Kilby（CC BY 4.0）已入库含署名与修改说明，提交前再核对数据页；
  Amazon 原始数据不入库（`docs/amazon_field_mapping.md` + 合成样例）。
- 演示模式可全流程操作（mock LLM 明确标注，不冒充真实模型）。

## 阶段 6 完成状态（2026-10-11 冻结评测，待队长验收）

- **规模**：39 个冻结场景全部跑完（dev 24 + test 15），raw 逐场景落盘
  `reports/evaluation6/raw/*.json`（39 个文件），`summary.csv` 141 行，
  报告 `reports/evaluation6/evaluation_report.md` + 3 张程序生成图
  （chart_cost_test / chart_feasibility / chart_sensitivity）。
- **口径（冻结，见 `frozen_manifest.json`）**：三方法（insert / rebuild /
  low_disruption）统一 60 秒端到端预算；test 集 LD cost_cap=1.10；
  dev 集附加 COPT 60s 诊断与 LD cap 1.05/1.20 敏感性（单独成表）；
  超时记 TIMEOUT_NO_SOLUTION（≠不可行）；OR-Tools 可行 ≠ 证明最优。
- **关键结果**：test 集 LD 15/15 可行（cap 1.10 内），insert 4/15、
  rebuild（OR-Tools）5/15；dev 集 LD 15/16；r202-s102 全方法无解且
  COPT `proven_infeasible` 佐证——如实记录。insert/rebuild 在 kilby 大场景
  毫秒级 FAIL 是真实算法行为，已在报告"诚实声明"节写明，未粉饰。
- **每行数据含**：scenario_id/split/method/backend/请求与有效预算/耗时
  （requested/effective/materialize/solve/validate/total/timed_out）/状态/
  has_solution/validation_ok/输入与快照哈希/成本与差距指标。
- **复现**：`./.venv/Scripts/python.exe scripts/run_evaluation.py --split all
  --copt-diag`（大场景每方法跑满 60s，需逐场景分批跑，单批 ≤300s），
  报告 `./.venv/Scripts/python.exe scripts/build_evaluation_report.py`。
- **测试**：新增 runner 冒烟测试 2 条（含物化异常如实记录 materialize_error），
  全量 327 passed。
- **可选项（未执行，待确认）**：300 秒 COPT 诊断实验（决策允许，单独成表；
  成本约每场景 5 分钟 × 多场景）。

## 阶段 6 修订（2026-10-11 按队长三项决策，待总工验收"冻结评测完成"）

- **样本口径修正**：清单 39 = dev 23 + test 16，与 raw/*.json、summary.csv
  逐行对齐（生成器强制执行一致性校验，缺一即失败）；8 个物化失败场景
  （c101-s102、c102-s102、r101-s101、r101-s102、r102-s101、r102-s102、
  r201-s102、rc101-s201）在报告〇节逐一列明原因（原计划物化独立 20s 预算
  耗尽，E_PLAN_NOT_FOUND，≠三方法失败、≠不可行），summary.csv 每场景补
  `__materialize__` 失败行，分母不变。
- **软预算改名**：本轮定名"60 秒软预算评测"，不称严格同预算；越界统计
  单独成节（COPT 11 行 62.5~77.3s，属秒级越界非毫秒误差；其余 10 行
  ≤60.046s）。正式版以独立进程硬截断（后续工作）。
- **产品适用范围分层**写入报告第六节：可演示=出发前单仓已验证场景；
  大规模/紧时间窗=实验性需人工复核；不按节点数简单划界（r201-s101
  仅 18 单亦未找到解；Kilby 与 Solomon 来源、约束特征不同）；
  毫秒级 NO_FEASIBLE_FOUND≠不可行；算法改进列为后续工作。
- **300 秒 COPT 诊断**：暂不启动；待验收后可预先指定 3 个代表性 dev 大
  场景单独成表（不碰 test、不并入 60s 主比较）。
- **阶段 5 旧场景问题确认已修复**：confirm 走 `index.save_version` 原子
  切换活动指针；solve 提交时绑定当前活动版本+输入哈希，worker 执行前
  核对漂移即 E_VERSION_CONFLICT（routes.py:131-138、387-389、407-430）；
  回归测试 test_confirm_becomes_active_version / test_version_drift_fails_honestly
  等 4 条在跑。评测 runner 不经 API，直接按冻结草稿确定性物化，无旧锁
  版本问题（每行 scenario_version 落盘可核）。
- 测试：328 passed（新增 summary↔清单 39 场景对齐断言）。

## 阶段 7 交付整理（2026-10-11，进行中待验收）

- **文档交付**：README（已实现/模拟/未实现 + 交付索引）、docs/ 下
  architecture（模块说明与数据流）、field_dictionary、algorithms、
  data_licenses、testing、known_limitations、demo_script、
  delivery_checklist（检查清单+私有仓库文件清单）。
- **干净环境验证**（reports/stage7_clean_env_log.txt）：独立 venv 按
  requirements.txt 全新安装 → **326 passed + 6 skipped**（COPT 用例
  importorskip，无许可可移植）→ serve.py 冒烟（health 200 / 前端 200）
  → CLI 演示闭环退出码 0。验证发现并修复 2 个可移植性问题：
  requirements.txt 补 httpx==0.28.1；serve.py 自加项目根到 sys.path。
- **未执行（待确认）**：私有远端创建与推送（待队长确认）；总工"冻结
  评测完成"最终验收；下一轮前置冻结（开跑前写入草稿哈希到不可变
  manifest）+ 300s COPT 诊断（指定 3 个 dev 大场景）+ 进程硬截止。
- 测试：开发 venv 332 passed；干净 venv 326 passed + 6 skipped。

## 下一阶段所需输入（阶段 7 收尾）

- 阶段 6 验收结论（若需 300s COPT 诊断实验，确认场景范围）。
- LLM_MODEL 交付前按目标接口复验（当前实测 `deepseek-flash`；不写不存在的
  DeepSeek-V4.1-Flash）。
- 阶段 7 打包范围确认：是否含原始受限数据（默认不含，仅映射文档+合成样例）。

## 已决事项（2026-10-11 队长决策）

- Git：本地初始化并提交，暂不上传远端/邀请协作者（已执行）；
- 默认后端：交付默认 OR-Tools，COPT 可选；README 写明安装/许可/无许可行为；
- 数据许可：按上文数据边界执行；Amazon 原始数据不入库；
- 预算口径：正式评测三方法均限 60 秒端到端；300 秒 COPT 诊断单独成表。
