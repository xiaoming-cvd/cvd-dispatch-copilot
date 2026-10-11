# 演示脚本（阶段 7 交付，可照抄复现）

演示主线：小样本闭环（5 分钟）→ 工作台 Web 演示（10 分钟）→
评测结果复现（可选，大场景耗时）。

**以下命令均为 Windows CMD（或 PowerShell）语法，已在 CMD 实跑验证。**
反斜杠路径与 `copy` 命令在 Git Bash 中不可用；Git Bash 用户请改用正斜杠路径
与 `cp`。代码块内不含注释行，注释一律写在块外，可直接整块照抄。

## 0. 环境（一次性）

```bat
cd 城配调度副驾
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
copy .env.example .env
```

（演示保持 `.env` 中 `LLM_PROVIDER=mock`。）

## 1. 小样本算法闭环（CLI，约 1 分钟）

```bat
.venv\Scripts\python.exe scripts\make_sample.py
.venv\Scripts\python.exe scripts\solve_cli.py --input data\sample\sample_small.json --method low_disruption --write-scenario data\sample\sample_small_scenario.json
.venv\Scripts\python.exe scripts\solve_cli.py --input data\sample\sample_small_scenario.json --method rebuild
```

预期：make_sample 输出可手算样本（V1: o1→o2，V2: o3→o4）；low_disruption
退出码 0，JSON 含 `status=feasible`、`validation.ok=true`、扰动计数与
`metrics.selection`（成本上限回显）；rebuild 同样 feasible。
退出码语义：0 可行 / 2 输入非法 / 3 未找到解 / 4 证明不可行 / 5 错误。

## 2. 自然语言约束（CLI 演示模式，约 1 分钟）

```bat
.venv\Scripts\python.exe scripts\draft_cli.py --input data\sample\sample_small_scenario.json --text "o1 不能换车，必须还是 V1 送" --store data\processed\drafts
.venv\Scripts\python.exe scripts\draft_cli.py --input data\sample\sample_small_scenario.json --confirm <draft_id> --yes --store data\processed\drafts
```

预期：stderr 明确标注"规则提取器，非真实大模型"；草案含原文依据与待
确认字段；确认后产生新场景版本（重复确认报冲突退出码 3）。

## 3. Web 工作台（约 5 分钟）

```bat
.venv\Scripts\python.exe scripts\serve.py
```

（启动后访问 http://127.0.0.1:8000。）

浏览器操作主线：场景列表 → 上传/选择草稿 → 生成原计划（baseline）
→ 约束草案确认（默认不勾，需显式 accept）→ 求解（进度可见，60s 预算）
→ 三方法对比与时刻表 → 导出 CSV（未过独立校验的导出按钮被禁用）。
口径提示：rebuild 默认 OR-Tools；页面成本标"模型行驶时间"；大场景
低扰动约 60 秒出解，等待时间与成本一并展示。

## 4. 评测结果一键重建（可选，约 2 分钟，不重新求解）

```bat
.venv\Scripts\python.exe scripts\build_evaluation_report.py
```

预期：从 reports\evaluation6\raw 重建 summary.csv、reconciliation.csv、
报告与 5 张图；stdout 打印"一致性 OK：清单 {'dev': 23, 'test': 16}"。
（重新跑评测需数小时：`scripts\run_evaluation.py --split all --copt-diag`。）

## 演示红线

- 不说"严格 60 秒同预算"（本轮为软预算）；不说"证明最优"（OR-Tools）；
  不说"问题不可行"（除非 COPT MIP_INFEASIBLE 且限定当前模型）；
  不报人民币节约（模型行驶秒）；Mock 模型与合成事件主动声明。
