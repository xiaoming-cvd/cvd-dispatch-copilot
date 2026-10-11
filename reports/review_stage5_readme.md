# 阶段 5 审查说明（Web 工作台与导出）

- 日期：2026-10-11
- 代码状态：Git 本地提交 `9754f5a`（仅本地，未上传远端）
- 测试：全量 313 passed（日志见 `stage5_pytest_log.txt`；新增 e2e 34 条于 `tests/test_api_stage5.py`）

## 本包内容

- `src/` 全部源码（domain / solver / validation / llm / api / adapters / evaluation）
- `frontend/index.html`：单页工作台（无构建步骤）
- `tests/`：全部测试；`scripts/`：全部脚本（含 `serve.py` 启动入口）
- `docs/`：执行清单、handoff（含 Q2–Q6 决策记录）、数据边界文档
- `README.md`、`requirements.txt`
- 附：测试日志、本说明

> 不含：`.venv`、`.git`、`.env`、COPT 许可文件、原始受限数据（data/raw）、
> 历史审查 zip。`.env.example` 仅为配置模板，无真实密钥。

## 启动（Windows）

```bat
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe scripts\serve.py     :: http://127.0.0.1:8000
.venv\Scripts\python.exe -m pytest            :: 313 passed
```

无 COPT 许可也可运行（rebuild 默认 OR-Tools）。开发机装有 COPT 8.0.6；
显式选 COPT 而无许可会如实报错，不静默回退。

## 页面流程

场景列表（registry + 上传）→ 详情与校验问题 → 生成原计划（baseline）→
约束草案（mock 演示或 .env 真实模型）→ 显式逐项/全部接受 → 求解
（insert / rebuild / low_disruption，rebuild 可选后端，预算默认 60000ms）→
任务轮询 → 结果（原计划 vs 新计划高亮新任务、时刻表、指标）→ 导出 CSV/JSON。

## 口径与红线（2026-10-11 决策，已测试锁定）

1. 三方法统一 60 秒端到端预算；不混算不同预算或后端。
2. rebuild 默认 OR-Tools；每条结果显式 method/backend/backend_requested/
   预算/耗时/状态/是否可行；COPT 超时 = TIMEOUT_NO_SOLUTION（≠不可行）；
   OR-Tools 可行 ≠ 证明最优。
3. 未过独立校验禁止导出（403 E_EXPORT_FORBIDDEN）。
4. 草案确认必须显式（accept_all 或逐项 decisions，默认不勾选）；
   版本冲突 409。
5. 队列：单工作进程、限排队 8、任务落盘、重启后未完成任务标记 interrupted。
6. 注入防护：CSV 公式前缀加 `'`；前端全 textContent 渲染，无 innerHTML 数据路径。
7. 页头常驻"示意图非导航线路"；所有结果须人工复核后方可执行。

## 实施中修复的缺陷（请总工重点复核）

- **模块级共享 APIRouter**：多实例闭包捕获旧队列，任务会提交到已停止的队列
  永远 queued。已改为 attach() 内创建 router，并有用例锁定。
- OR-Tools 结果此前无 backend 标注（空串），已补 `backend="ortools"` + 版本号。
- history 端点对 baseline 结果行 KeyError，已修。
- 4 条阶段 2 旧测试按新口径更新（默认后端切换后，证明不可行/超时语义
  须显式 `backend="copt"`）。

## 已知限制

- 75+ 节点场景 COPT 60 秒无 incumbent（Q2 决策根源）：主评测用 OR-Tools，
  COPT 仅小规模验证与 300 秒诊断实验（单独成表）。
- r101 系等 4 个 Solomon 草稿原计划生成会超时，前端如实呈现。
- mock LLM 仅覆盖锁定语义内的小型中文词表，未覆盖句式一律澄清（不猜测）；
  生产使用须人工复核草案。LLM_MODEL 当前实测 `deepseek-flash`，交付前复验。
