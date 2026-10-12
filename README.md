# 城配调度副驾

**出车前临时加单的低扰动重排调度——AI 草案 + 独立校验 + 三方法对比的决策副驾**

本地 Web 应用（FastAPI + 单页前端），面向城配"计划已下发、出车前临时加单"
的调度场景：不是把题目做成界面，而是给出**可验证的算法增量**——在成本
可接受的前提下，把新单塞进计划时**尽量少改动已下发给司机的原计划**，且每个
结果都经独立校验、可复现、状态语义诚实。

---

## 团队信息

- 参赛人：黄天娇
  - 学校：东北财经大学 · 国际经济贸易学院（国际贸易专业）
  - 邮箱：huang_foky@163.com
  - 电话：15710321600
- 参赛人：谭绪敏
  - 学校：东北财经大学 · 国际经济贸易学院（国际经济与贸易专业）
  - 邮箱：tanxm68@qq.com
  - 电话：19741166796
- 个人简历：（可选，附此或链接）

---

## 一、业务场景与痛点

城配每日出车前，调度员按当时已知订单排出车计划并下发司机。**临时加单**
到来时（大客户急单、前置仓爆单），现有业务做法只有三种，各有硬伤：

| 现有做法 | 问题 |
|---|---|
| 人工电话改单 | 靠经验、分钟级响应、无全约束核验，漏单/超时/超载风险高 |
| 全盘重排（或拒单） | 司机原计划被打乱：换车、路线顺序大改，司机抵触、执行成本上升 |
| 直接采信求解器结果 | 求解器只优化其建模目标，超时/无解状态易被误读为"业务不可行" |

本题的核心矛盾：**计划可行性的代价（成本）与执行稳定性（扰动）之间的权衡**，
以及 **AI/算法输出如何让人敢用**。

## 二、核心方案：相对题设与现有做法的新增能力

题设已给出"插单基线 / 完全重排"两类方法。本作品的实质增量在五个方面：

**1. 低扰动重排算法（主创新）**
自定义扰动指标——换车数（只统计旧任务）与旧相邻关系破坏数（插入新任务
顺延不计扰动）——在"成本 ≤ 1.10×参考成本"的上限内，按
（换车数, 相邻破坏数, 成本）字典序选最优候选。参考链：跨方法已验证方案
→ baseline 原计划成本（Web 链路，容缺口径：原计划不含新单，偏紧，超限
如实标注）→ 池内回退（如实标注）。生成过程不依赖求解器原生
目标（COPT/OR-Tools 无法直接优化该指标），全部候选经独立校验器重算。
→ 相对"全盘重排"：司机计划改动最小化；相对"人工改单"：秒级、全约束。

**2. 独立校验器（可信度基础设施）**
从输入矩阵重算逐站到达/服务/离开/返回/装载/时间窗/锁定，**不采信求解器
任何标志**；未过独立校验的方案禁止导出（Web 端导出按钮直接禁用）。
→ 回答"评委会问的：你的结果凭什么可信"。

**3. 严格状态语义（不夸大）**
invalid_input / feasible / no_feasible_found / proven_infeasible / error
严格区分：**超时未找到 ≠ 证明无解**，求解器报告不可行仅对当前建模有效，
OR-Tools 可行 ≠ 证明最优。所有失败样本如实进报告，不改分母。

**4. AI 赋能的安全边界：LLM 只出草案、人工确认才生效**
自然语言（如"o1 不能换车，必须还是 V1 送"）→ 结构化约束草案（区分
**硬约束**与**偏好**、附原文依据、指代不清返回澄清）→ **逐项人工确认后**
才产生新场景版本并允许求解。LLM 不执行代码、不调 URL、不写路线；
无密钥自动进入明确标示的演示模式（MockProvider）。
→ AI 负责"听得懂人话"，人保留"拍板权"。

**5. 全链路可复现（工程可信度）**
输入哈希 + 不可变版本 + 冻结草稿 registry；数据 manifest（来源/许可/sha256）；
评测冻结清单（39 场景，dev 定参、test 不调参）；一键审查脚本
（`review_check.py`，有/无 COPT 许可两种模式）；git bundle 随交付包供提交
历史核验；空目录无原始数据开箱回归（`pytest -m delivery_sim`）。

## 三、验证结果（冻结评测，60 秒软预算口径）

39 个预登记派生场景（23 dev + 16 test，Solomon + Kilby 双基准，新增量/
余量/锁定程度分层），三方法同输入同预算对比：

- **可行率（dev 冻结集 23 场景）**：插单基线 3/23（13%）、完全重排 3/23
  （13%）、**低扰动 15/23（65%）**；test 可物化 16 场景中低扰动 15/16（94%）。
- **配对成本（模型行驶秒）**：低扰动 vs 完全重排双双可行的 5 个配对样本上，
  低扰动成本**全部更低（5/5）**——即质量不降反升，同时扰动更小。
  （配对子集占比小，结论仅限该子集，完整口径见
  `reports/evaluation6/evaluation_report.md`。）
- **能力边界（如实声明）**：大规模/紧时间窗场景 insert/rebuild 常快速未找到
  解，低扰动也存在边界；产品适用范围分层：已验证=出发前、单仓场景；
  大规模紧窗=需人工复核的实验性能力。
- 测试基线：开发机 **354 passed**；无 COPT 干净环境 348 passed + 6 skipped；
  一键审查双模式 pass（含显式 COPT 冒烟与穷举最优性核对）。

## 四、应用价值与落地路径

- **效率**：改单响应从"人工电话数分钟"到"算法秒级 + 校验"；司机计划稳定性
  以可量化指标（换车数/相邻破坏数）呈现给调度员决策。
- **成本口径诚实**：无真实单价，一律报"模型行驶秒"，不报人民币节约；
  已验证效果与预期收益分开写。
- **工程化潜力**：求解后端可插拔（OR-Tools 默认 / COPT 可选，结果显式标注
  后端）；任务队列单工作进程可水平扩展；状态语义与队列落盘支持服务重启恢复。
- **企业落地**：单仓/出发前场景即可部署；数据需求=订单+距离矩阵+车队，
  均可来自现有 TMS；密钥只走后端环境变量；主要瓶颈与后续工作见
  `docs/known_limitations.md`（硬截止、大规模能力、真实路网距离）。

## 五、Demo 与运行说明

**视频演示**（推荐观看，3-5 分钟含失败案例）：
（待填写：网盘链接 + 提取码，整个评审期内有效）

**本地运行**（Windows，Python 3.12）：

```bat
cd 城配调度副驾
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
copy .env.example .env
.venv\Scripts\python.exe scripts\serve.py
```

浏览器打开 `http://127.0.0.1:8000`：场景列表 → 生成原计划 → 约束草案确认
→ 求解 → 三方法对比 → 导出 CSV/JSON。演示主线逐步说明（含预期输出与
话术红线）见 `docs/demo_script.md`。

**双模式呈现**（首次访问自选，页头随时切换，偏好只存在本机浏览器）：

- **通俗演示版**：大白话解释决策过程（全部数字来自真实运行诊断记录，无大模型
  编造）、演示化站点/车辆称谓（确定性生成，悬停可见原始 ID，可导出对照表核对）、
  座位图与候选散点图。
- **专业审查版**：原始订单/车辆 ID、诊断 JSON、候选明细与溯源信息。
- 两种模式读取**完全相同**的数据与求解结果；演示称谓不代表实际地理、客户、
  人员或业务含义。设计见 `docs/layman_mode_proposal.md`。

**一键审查**（评委复现推荐）：

```bat
.venv\Scripts\python.exe scripts\review_check.py --skip-copt
```

（`--skip-copt`：无需 COPT/原始数据）

预期：基础审查通过、overall=partial（缺原始数据为设计内行为，附获取方式）；
原始数据恢复后同命令全量 pass。详见下文「数据恢复」。

## 六、技术实现入口

| 内容 | 位置 |
|---|---|
| 模块说明与数据流 | `docs/architecture.md` |
| 三方法算法规则与状态语义 | `docs/algorithms.md` |
| 输入/输出字段字典与 JSON Schema | `docs/field_dictionary.md`、`docs/schema/scenario.schema.json` |
| 数据来源与许可 | `docs/data_licenses.md` |
| 测试结果与覆盖 | `docs/testing.md`（当前 354 条） |
| 冻结评测完整报告 | `reports/evaluation6/evaluation_report.md`（39 场景原始结果+程序生成图表） |
| 已知限制 | `docs/known_limitations.md` |
| 演示脚本（照抄可复现） | `docs/demo_script.md` |

关键技术栈：Python 3.12 / Pydantic v2（数据契约与深冻结）/ OR-Tools 9.14
（默认重排后端）/ COPT 8.0.6（可选，小规模最优性与不可行性验证）/
FastAPI + uvicorn（工作台与任务队列）/ 大模型 openai_compatible 适配
（Mock 演示模式，模型名配置化）。

## 七、合规与声明

- **开源组件**（MIT/Apache-2.0/BSD 系，完整锁定版本见 `requirements.txt`）：
  pydantic、FastAPI（starlette/uvicorn）、OR-Tools、pytest、httpx、
  python-dotenv 等；COPT 为商业求解器，**仅可选后端**，无许可时全部功能
  以 OR-Tools 运行。
- **模拟数据声明**：LLM 演示为 MockProvider；Solomon 系 17 份草稿的新增
  订单为固定种子合成事件（`synthetic_events=true`，非真实动态订单）；Kilby
  系为真实动态数据（`synthetic_events=false`）；成本为模型行驶秒（无真实单价）；
  以上在页面与报告中均有标注。
- **人工操作环节**：自然语言约束须经人工逐项确认才生效；结果导出前需人工
  复核；评测分 dev/test 冻结由人工决策锁定。
- **数据许可**：Kilby 派生场景 CC BY 4.0（署名随包）；Solomon 学术基准
  （仅来源链接+处理脚本，原始文件不入库，许可状态"待确认"已如实标注）；
  Amazon 仅字段映射+合成样例（CC BY-NC 4.0，原始数据不入库）。
  详见 `docs/data_licenses.md`。
- **尚未实现（后续工作）**：每方法独立进程硬截止；300 秒 COPT 诊断实验；
  大规模紧时间窗能力改进；真实路网距离接入。详见 `docs/known_limitations.md`。

---

## 数据恢复（先读我：仓库/交付包不含受限原始数据）

仓库与交付 zip **不含** `data/raw/` 原始数据（许可边界）。两种使用模式：

**模式 A · 仅演示/基础审查（无需原始数据）**

```bat
.venv\Scripts\python.exe -m pytest -q -m "not delivery_sim"
.venv\Scripts\python.exe scripts\review_check.py --skip-copt
.venv\Scripts\python.exe -m pytest -m delivery_sim -q
```

预期：pytest 全过、源数据用例**显式 skip**（含原因与获取方式）；review_check
基础 smoke 全通过、overall=**partial**（退出码 1）；第三条为交付包空目录无原件
自动化回归，输出 PASS（`reports/verify_empty_dir_log.txt`）。

**模式 B · 完整原始数据复核（先恢复原件）**

```bat
.venv\Scripts\python.exe scripts\download_solomon.py
.venv\Scripts\python.exe scripts\check_base_fingerprint.py
.venv\Scripts\python.exe -m pytest -q -m "not delivery_sim"
.venv\Scripts\python.exe scripts\review_check.py
```

预期：Solomon 11 个文件字节 sha256 与 manifest 登记一致（11/11）；
pytest **354 passed, 0 skipped**；review_check overall=**pass**（退出码 0；
有 COPT 时另验证 rebuild copt 冒烟与穷举核对）。

| 数据 | 放置路径 | 获取 | 许可 |
|---|---|---|---|
| Solomon 100-customers（11 个算例） | `data/raw/solomon/` | `scripts/download_solomon.py`（登记来源见 solomon_manifest.json） | 学术基准惯常公开用于研究，**未见明示许可文本，商用/比赛再分发前须自行核对**（manifest 标注"待确认"） |
| Kilby 动态 VRP | `data/raw/kilby/3fwc3twwn6.zip` | Mendeley Data DOI `10.17632/3fwc3twwn6.1`，文件名/sha256 见 `data/manifests/kilby_manifest.json` | CC BY 4.0（须署名，详见 docs/data_licenses.md） |
| Amazon last-mile | 不入库 | 仅字段映射 + 合成样例（`data/sample/`） | CC BY-NC 4.0，原始数据不进仓库 |

"干净环境"说明：交付日志中的干净环境复验是**同机新 venv**，仍共用开发机
`data/raw`，不是净目录跨电脑复现；跨电脑开箱行为以模式 A 的 `delivery_sim`
回归为准。

**当前复现边界（如实声明，2026-10-12）**

- **其他电脑目前不能完整复现（模式 B）**。原因：①受限原始数据
  （Solomon/Kilby/Amazon 原件）按许可边界不随仓库公开分发，只留在开发机
  `data/raw/`；②COPT 为商业求解器，许可与本机安装绑定，不能放公网；
  ③评测产物依赖本机解释器环境。其他电脑可复现的上限为**模式 A**
  （基础审查 + 合成样例演示，源数据用例显式 skip）。
- **后续改进（ roadmap ）**：运行环境 Docker 化（含 OR-Tools，COPT 保持
  可选并检测许可）、原始数据恢复脚本一键化与清单化，目标是让任意电脑
  按 README 即可完整复现；完成前本节持续有效。
- **数据许可提醒**：Amazon 数据集 CC BY-NC 4.0 **禁止商业用途**（仅非商业
  研究可用，原始数据因此不入库）；Kilby CC BY 4.0 须署名；Solomon 许可
  状态"待确认"。比赛/演示以外的任何商业使用，须逐项自行核对许可条款。
  详见 `docs/data_licenses.md`。

## 目录结构

```
src/
  domain/      Pydantic 模型 + JSON Schema + 输入哈希（深冻结）
  adapters/    Solomon/Kilby/Amazon 解析转换、情景生成器
  solver/      原计划生成 / 插单 / 完全重排(OR-Tools+COPT) / 低扰动 / pipeline
  validation/  阶段0结构校验 + 独立时刻表校验（不采信求解器）
  api/ llm/    FastAPI 端点与任务队列 / 大模型适配（Mock+openai_compatible）
  evaluation/  冻结评测 runner
frontend/      单页工作台（无构建步骤）
scripts/       CLI：solve_cli / draft_cli / serve / review_check / 评测与打包
data/
  raw/         原始数据（只读，默认不提交 Git）
  sample/      自行生成的小样本
  manifests/   数据清单（来源/哈希/许可/映射）
docs/          文档与 JSON Schema
reports/       程序生成的结果（评测报告/审查摘要/证据日志）
```

## 环境搭建（Windows）

```bat
cd 城配调度副驾
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

> 注：`ortools` 锁定于 9.14.6206（Python 3.12 兼容），阶段 2 才使用；
> 若网络较慢可届时再装，阶段 0 测试不依赖它。

## 运行测试

```bat
.venv\Scripts\python.exe -m pytest
```

## 生成样本与 Schema

```bat
.venv\Scripts\python.exe scripts\make_sample.py
.venv\Scripts\python.exe scripts\export_schema.py
```

（分别生成 data/sample/sample_small.json 与 docs/schema/scenario.schema.json）

## 数据契约要点（详见 docs/schema/scenario.schema.json）

- 时间：接口层统一整数秒；`time_origin` 必须带时区；时间窗约束**服务开始时刻**。
- 容量：单一场景只启用一种容量维度（`load_units` 或 `cm3`）；缺失重量不得填 0 冒充。
- 出行矩阵：完整方阵、对角为 0、元素有限非负；**非对角 0 是合法行驶时间**
  （同址节点/取整后为 0），不得仅凭 0 判作缺边；**缺边必须显式以 null 表达**，
  首版拒绝（`E_MATRIX_EDGE_MISSING`）。与 `src/domain/models.py` 的
  `TravelMatrix` 规则一致。
- 原计划：旧任务恰好出现一次；新任务（`is_new=true`）不得出现；计划冻结后不可覆盖。
- 锁定：`assignment_locks`（换车锁）与 `relative_order_locks`（相对顺序锁）只作用于旧任务。
- 成本系数必须带单位与来源；无真实单价时以行驶秒数为优化指标（页面标"模型行驶时间"）。

## 阶段状态

见 `docs/handoff.md`。每阶段结束更新；阶段 2、3、5、6 完成后设审查点。
