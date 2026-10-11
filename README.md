# 城配调度副驾（首版）

单仓、车辆出发前、普通货物的临时加单与低扰动重排工具。容量与时间窗为硬约束，
人工确认后才能导出执行方案。依据《城配调度副驾工程实施与AI编码任务书 v1.0》分阶段实施。

**当前阶段：0–6 已交付（阶段 6 评测数据"有条件通过"，待总工验收"冻结评测完成"）；阶段 7 交付整理进行中。**
一键审查入口（有 COPT / 无 COPT 两模式）：

```bat
.venv\Scripts\python.exe scripts\review_check.py            # 有 COPT 全量
.venv\Scripts\python.exe scripts\review_check.py --skip-copt  # 基础模式
```

审查问题逐项关闭表见 `reports/review_closure.md`。

## 目录结构

```
src/
  domain/      数据契约模型（Pydantic）与错误码
  adapters/    数据转换（阶段 1 使用）
  solver/      三种算法（阶段 2/3 使用）
  validation/  输入校验 + 独立校验器（阶段 2 扩展）
  api/         FastAPI 接口（阶段 5 使用）
  llm/         大模型适配器（阶段 4 使用）
  evaluation/  评测（阶段 6 使用）
frontend/      网页（阶段 5 使用）
tests/         pytest 测试
scripts/       样本生成、schema 导出等辅助脚本
data/
  raw/         原始数据（只读，默认不提交 Git）
  sample/      自行生成的小样本
  manifests/   数据清单（来源/哈希/许可/映射）
docs/          文档与 JSON Schema
reports/       程序生成的结果
```

## 环境搭建（Windows）

```bat
cd 城配调度副驾
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

> 注：`ortools` 锁定于 9.14.6206（Python 3.12 兼容），阶段 2 才使用；
> 若网络较慢可届时再装，阶段 0 测试不依赖它。

## 阶段 5：Web 工作台（启动）

```bat
.venv\Scripts\python.exe scripts\serve.py
```

（默认 http://127.0.0.1:8000）

- 浏览器打开后：场景列表 → 生成原计划（baseline）→ 约束草案确认 → 求解 → 导出 CSV/JSON。
- 端点统一 `/api/*`；前端为 `frontend/index.html` 单页（无构建步骤）。
- LLM：`.env` 的 `LLM_PROVIDER` 必须是 `mock`（演示模式）或 `openai_compatible`，
  启动即校验；密钥只走后端环境变量，任何响应不回显。模型名配置化
  （当前实测 `LLM_MODEL=deepseek-flash`），交付前按目标接口复验。
- 后端口径（2026-10-11 决策）：**rebuild 默认 OR-Tools**；COPT 8.0.6 为可选后端
  （小规模最优性/不可行性验证）。每条结果显式记录 method/backend/预算/耗时/状态/
  是否可行；COPT 超时记 TIMEOUT_NO_SOLUTION（≠不可行）；无 COPT 许可而显式选
  copt 会如实报错，绝不静默回退。无 COPT 许可的评测机可直接运行。
- 导出红线：未过独立校验的方案禁止导出（403）；CSV 已做公式注入防护。
- 任务队列：单工作进程、限排队（默认 8）、任务落盘、重启后未完成任务标记
  interrupted（不假装还在跑）。

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

## 交付文档索引（阶段 7）

| 文档 | 内容 |
|---|---|
| docs/architecture.md | 模块说明与数据流（含模块依赖图） |
| docs/field_dictionary.md | 输入/输出字段字典（对齐 docs/schema/scenario.schema.json） |
| docs/algorithms.md | 三方法算法规则与状态语义（含成本上限与超时口径） |
| docs/data_licenses.md | 数据授权清单（Solomon/Kilby/Amazon/合成事件） |
| docs/testing.md | 测试说明（运行方式、覆盖范围、当前 332 条） |
| docs/known_limitations.md | 已知限制（软预算、能力边界、Mock 声明等） |
| docs/demo_script.md | 演示脚本（命令 + 预期输出，可照抄复现） |
| docs/delivery_checklist.md | 交付包检查清单与私有仓库文件清单 |
| reports/evaluation6/evaluation_report.md | 阶段 6 冻结评测报告（60 秒软预算版） |

## 已实现 / 模拟 / 尚未实现

**已实现（有测试证据）**：输入契约与校验、输入哈希与不可变版本、
独立校验器（重算时刻表，不采信求解器）、原计划生成、插单基线、
完全重排（OR-Tools 默认 / COPT 可选）、低扰动候选池与上限选择、
三方法差异量化、自然语言→约束草案（含人工确认）、FastAPI 工作台
（上传/预览/确认/求解/导出/队列）、冻结评测全流程（39 场景）。

**模拟/替代（必须向使用者声明）**：
- LLM 演示模式为 MockProvider（`LLM_PROVIDER=mock`），不冒充真实模型；
  真实后端为 openai_compatible，密钥只走后端环境变量。
- 新增任务的来源按数据族区分（以场景 `_meta` 为准）：**Solomon 系 17 份草稿**
  的新增订单为按固定种子合成的插单事件，**不是真实动态订单**；**Kilby 系
  22 份草稿**（`synthetic_events=false`）的新增任务按源数据真实 appearing
  time 划分，是真实基准的动态出现时间，非合成事件（`_meta.event_log` 可查）。
- 成本为模型行驶秒（抽象成本），**无真实单价，不报人民币节约**；
  页面与报告均标注"模型行驶时间"。
- 评测为 **60 秒软预算**（求解器软上限，无进程硬截断），不得宣传为
  严格同预算；COPT 诊断单独成表。

**尚未实现（后续工作）**：每方法独立进程硬截止；300 秒 COPT 诊断
（待指定 3 个 dev 大场景）；物化失败场景的构造器诊断与预注册复测；
insert/rebuild 在大规模与紧时间窗场景的能力改进。

## 数据与许可（摘要，详见 docs/data_licenses.md）

- Kilby 派生场景：CC BY 4.0，署名与修改说明随包；Solomon：仅来源链接
  + 处理脚本，原始数据不入库；Amazon：原始数据不入库，仅字段映射 +
  合成样例；以上原始受限数据均不进入交付 Git。
