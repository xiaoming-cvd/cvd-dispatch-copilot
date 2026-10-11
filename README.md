# 城配调度副驾（首版）

单仓、车辆出发前、普通货物的临时加单与低扰动重排工具。容量与时间窗为硬约束，
人工确认后才能导出执行方案。依据《城配调度副驾工程实施与AI编码任务书 v1.0》分阶段实施。

**当前阶段：0–3 已交付（阶段 1/2/3 待队长验收）；跨电脑审查 P0/P1 修复已提交待复审。**
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

## 运行测试

```bat
.venv\Scripts\python.exe -m pytest
```

## 生成样本与 Schema

```bat
.venv\Scripts\python.exe scripts\make_sample.py     :: 生成 data/sample/sample_small.json
.venv\Scripts\python.exe scripts\export_schema.py   :: 导出 docs/schema/scenario.schema.json
```

## 数据契约要点（详见 docs/schema/scenario.schema.json）

- 时间：接口层统一整数秒；`time_origin` 必须带时区；时间窗约束**服务开始时刻**。
- 容量：单一场景只启用一种容量维度（`load_units` 或 `cm3`）；缺失重量不得填 0 冒充。
- 出行矩阵：完整方阵、对角为 0、元素有限非负；非对角 0 视为缺边，首版拒绝。
- 原计划：旧任务恰好出现一次；新任务（`is_new=true`）不得出现；计划冻结后不可覆盖。
- 锁定：`assignment_locks`（换车锁）与 `relative_order_locks`（相对顺序锁）只作用于旧任务。
- 成本系数必须带单位与来源；无真实单价时以行驶秒数为优化指标（页面标"模型行驶时间"）。

## 阶段状态

见 `docs/handoff.md`。每阶段结束更新；阶段 2、3、5、6 完成后设审查点。
