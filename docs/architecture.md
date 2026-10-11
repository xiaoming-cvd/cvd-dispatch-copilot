# 架构与模块说明（阶段 7 交付）

## 系统定位

单仓、车辆出发前、普通货物的临时加单与低扰动重排工具。容量与时间窗为
硬约束；任何方案必须经**独立校验器**复核且**人工确认约束**后才能导出。
大模型只做"自然语言 → 约束草案"，不直接执行调度。

## 模块依赖图

```
frontend/index.html（单页，无构建）
        │ HTTP /api/*
        ▼
src/api/  routes.py ── jobs.py（单 worker 队列，任务落盘）
        │              app.py（ScenarioIndex：草稿/版本/快照，原子切换）
        ▼
src/llm/  service.py ── extractor.py（文本→约束草案，schema 限定）
        │                mock_provider.py / openai_provider.py（可替换）
        ▼
src/solver/  pipeline.py（编排：校验→求解→独立复核→SolveResult）
        │      ├─ original.py   原计划生成（OR-Tools，20s 预算）
        │      ├─ insert.py     插单基线（枚举插入，不搬动旧任务）
        │      ├─ rebuild.py    完全重排（默认 OR-Tools；COPT 可选显式指定）
        │      │   ├─ ortools_core.py / copt_core.py（后端隔离，延迟导入）
        │      └─ low_disruption.py  低扰动（候选池生成 + 成本上限筛选）
        │            perturbation.py / diff.py（扰动与差异量化）
        ▼
src/validation/  checks.py（输入校验）/ timetable.py（独立重算校验器）
        ▲ 依据输入矩阵重算访问/装载/等待/服务/返回，检查唯一覆盖与锁定；
        │ 不调用求解器内部"可行"结论
src/domain/  models.py（Pydantic 数据契约，冻结模型+输入哈希）/ errors.py
src/adapters/  solomon.py / kilby.py / amazon.py / generator.py（数据转换）
data/  raw/（只读，不入 Git）→ processed/scenarios（冻结草稿+registry）
reports/  程序生成产物（评测 raw/summary/报告，不可手改）
```

## 关键数据流

1. **场景接入**：原始数据（Solomon/Kilby/合成）→ adapters 统一转换 →
   `data/processed/scenarios/<split>/<sid>.json`（草稿 + `_meta` 事件日志）。
2. **评测**：`run_evaluation.py` 按 frozen_manifest 物化草稿（原计划构造）
   → 三方法同预算求解 → 每行记录输入哈希/状态/指标/耗时 → raw 落盘；
   `build_evaluation_report.py` 从 raw 一键重建 summary/对账/报告/图。
3. **工作台**：上传草稿 → baseline 生成原计划 → LLM 约束草案 → 人工
   confirm（版本单调递增，原子切换活动指针）→ solve（提交时绑定版本+
   输入哈希，worker 执行前核对漂移）→ 独立校验 → 导出 CSV/JSON。
   未过独立校验禁止导出（403）。

## 版本与一致性机制

- 场景不可变：`input_hash` 覆盖全部输入字段；修改产生新 `version`，
  旧版本快照留档（`versions/<sid>/v<n>.json`）。
- 任务绑定：solve 提交时记录 `bind_version`/`bind_hash`；执行前核对，
  漂移即 `E_VERSION_CONFLICT` 失败，不静默求解新版本。
- 状态语义：`TIMEOUT_NO_SOLUTION ≠ 不可行`；`OR-Tools 可行 ≠ 证明最优`；
  仅 COPT 对当前 MIP 模型报 `MIP_INFEASIBLE` 时可写"证明不可行"。
