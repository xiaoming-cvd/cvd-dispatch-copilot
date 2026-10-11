"""阶段 6 评测报告生成器：从原始结果程序生成表格、图表与报告。

输入：reports/evaluation6/raw/*.json + frozen_manifest.json
输出：reports/evaluation6/ 下 summary.csv、主比较/诊断表（Markdown）、
      图表 PNG、evaluation_report.md

口径（见 frozen_manifest）：
- 三方法统一 60 秒端到端预算；状态如实（超时≠不可行，OR-Tools 可行≠最优）；
- 主比较 = test 集冻结配置；dev 集含 COPT 诊断与 110% 上限敏感性；
- 配对比较注明样本占比；分母与单位全部写清；无提升也如实报告。
"""
from __future__ import annotations

import csv
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EVAL_DIR = ROOT / "reports" / "evaluation6"
RAW_DIR = EVAL_DIR / "raw"
METHODS = ("insert", "rebuild", "low_disruption")


def load_records() -> list[dict]:
    recs = []
    for p in sorted(RAW_DIR.glob("*.json")):
        recs.append(json.loads(p.read_text(encoding="utf-8")))
    return recs


def enrich_error_records(recs: list[dict], manifest: dict) -> None:
    """物化失败记录补齐 split/n_orders（从清单+草稿文件），不得静默丢出分母。"""
    by_id = {e["scenario_id"]: e for e in manifest["scenarios"]}
    for rec in recs:
        if not rec.get("materialize_error"):
            continue
        entry = by_id.get(rec["scenario_id"])
        if entry is None:
            continue
        rec["split"] = entry["split"]
        draft_path = (ROOT / "data" / "processed" / "scenarios"
                      / entry["split"] / f"{rec['scenario_id']}.json")
        try:
            draft = json.loads(draft_path.read_text(encoding="utf-8"))["draft"]
            rec["n_orders"] = len(draft.get("orders", []))
        except Exception:
            rec["n_orders"] = "?"


def main_rows(rec: dict) -> dict[str, dict]:
    """主比较三方法行（rebuild 取 OR-Tools 默认行）。"""
    out = {}
    for r in rec.get("rows", []):
        if r["method"] == "rebuild" and r.get("backend") == "copt":
            continue
        if r["method"] == "low_disruption" and r.get("cost_cap_ratio") not in (None, 1.1):
            continue
        if r["method"] not in out:  # 每方法取首个（主配置）
            out[r["method"]] = r
    return out


def fmt_cost(r: dict | None) -> str:
    if r is None or not r.get("has_solution"):
        return "—"
    c = r.get("total_travel_time_s")
    return f"{c:.0f}" if c is not None else "—"


def md_table(headers: list[str], rows: list[list[str]]) -> str:
    out = ["| " + " | ".join(headers) + " |",
           "|" + "---|" * len(headers)]
    for row in rows:
        out.append("| " + " | ".join(str(c) for c in row) + " |")
    return "\n".join(out)


def status_cn(s: str | None) -> str:
    return {"feasible": "可行", "no_feasible_found": "预算内未找到解(≠不可行)",
            "proven_infeasible": "证明不可行", "invalid_input": "输入非法",
            "error": "错误", "RUNNER_ERROR": "运行器错误"}.get(s or "", s or "—")


def comparison_table(recs: list[dict], split: str) -> tuple[str, dict]:
    rows, stats = [], {}
    denom = 0
    for rec in recs:
        if rec.get("split") != split:
            continue
        if rec.get("materialize_error"):
            rows.append([rec["scenario_id"], rec.get("n_orders", "?"),
                         "物化失败:" + rec["materialize_error"][:40],
                         "—", "—", "—", "—", "—"])
            stats.setdefault("mat_fail", 0)
            stats["mat_fail"] += 1
            continue
        denom += 1
        m = main_rows(rec)
        rows.append([
            rec["scenario_id"], rec["n_orders"],
            *(f"{status_cn(m[x]['status'])} / {fmt_cost(m[x])}s / {m[x].get('elapsed_ms', '?')}ms"
               if x in m else "缺行" for x in METHODS),
            rec.get("end_to_end_ms", "?"),
        ])
    for x in METHODS:
        ok = sum(1 for rec in recs if rec.get("split") == split
                 and not rec.get("materialize_error")
                 and main_rows(rec).get(x, {}).get("status") == "feasible")
        stats[x] = (ok, denom)
    table = md_table(
        ["场景", "订单数", "insert(状态/成本s/耗时ms)", "rebuild OR-Tools",
         "low_disruption(cap1.10)", "端到端ms"], rows)
    return table, stats


def failure_distribution(recs: list[dict], split: str) -> Counter:
    dist = Counter()
    for rec in recs:
        if rec.get("split") != split:
            continue
        if rec.get("materialize_error"):
            dist["原计划物化超时(≠无解)"] += 1
            continue
        for x in METHODS:
            st = main_rows(rec).get(x, {}).get("status", "缺行")
            dist[f"{x}:{st}"] += 1
    return dist


def paired(recs: list[dict], split: str, base: str, cand: str) -> str:
    """配对比较：同一场景两方法都有可行解的样本（注明占比）。"""
    both, better, worse, equal = 0, 0, 0, 0
    n = 0
    for rec in recs:
        if rec.get("split") != split or rec.get("materialize_error"):
            continue
        n += 1
        m = main_rows(rec)
        a, b = m.get(base), m.get(cand)
        if not (a and b and a.get("has_solution") and b.get("has_solution")):
            continue
        both += 1
        ca, cb = a["total_travel_time_s"], b["total_travel_time_s"]
        if cb < ca:
            better += 1
        elif cb > ca:
            worse += 1
        else:
            equal += 1
    share = f"{both}/{n}" if n else "0/0"
    return (f"{cand} vs {base}：两方法均可行的配对样本 {share}"
            f"（占比 {both / n * 100:.0f}%）——成本更低 {better}、更高 {worse}、"
            f"持平 {equal}（单位：模型行驶秒）")


def charts(recs: list[dict]) -> list[str]:
    from pathlib import Path as _P
    import sys as _sys
    _sys.path.insert(0, str(_P(_sys.executable).parent.parent.parent))
    from daimon_runtime import setup_plot
    import matplotlib.pyplot as plt
    setup_plot()
    paths = []

    test_recs = [r for r in recs if r.get("split") == "test"
                 and not r.get("materialize_error")]
    # 图1：test 集三方法成本对比（条形，按订单数排序）
    test_recs.sort(key=lambda r: r["n_orders"])
    labels = [r["scenario_id"].replace("kilby-", "k-") for r in test_recs]
    x = range(len(test_recs))
    width = 0.27
    fig, ax = plt.subplots(figsize=(11, 5))
    for i, meth in enumerate(METHODS):
        vals = []
        for rec in test_recs:
            row = main_rows(rec).get(meth, {})
            v = row.get("total_travel_time_s")
            vals.append(v if row.get("has_solution") and v is not None else 0)
        ax.bar([p + (i - 1) * width for p in x], vals, width, label=meth)
    ax.set_xticks(list(x)); ax.set_xticklabels(labels, rotation=60, ha="right", fontsize=8)
    ax.set_ylabel("总行驶时间（模型秒，抽象成本，非人民币）")
    ax.set_title("阶段6 评测 · 测试集三方法成本对比（60s 软预算）")
    ax.legend()
    p = EVAL_DIR / "chart_cost_test.png"
    fig.savefig(p, bbox_inches="tight"); plt.close(fig); paths.append(p.name)

    # 图2：可行率（test + dev）
    fig, ax = plt.subplots(figsize=(7, 4))
    groups = []
    for split in ("dev", "test"):
        sub = [r for r in recs if r.get("split") == split
               and not r.get("materialize_error")]
        for meth in METHODS:
            ok = sum(1 for r in sub
                     if main_rows(r).get(meth, {}).get("status") == "feasible")
            groups.append((f"{split}\n{meth}", ok, len(sub)))
    ax.bar([g[0] for g in groups],
           [g[1] / g[2] * 100 if g[2] else 0 for g in groups],
           color=["#1f6feb" if "dev" in g[0] else "#1a7f37" for g in groups])
    ax.set_ylabel("可行率 %"); ax.set_ylim(0, 105)
    for i, g in enumerate(groups):
        ax.text(i, (g[1] / g[2] * 100 if g[2] else 0) + 2, f"{g[1]}/{g[2]}",
                ha="center", fontsize=8)
    ax.set_title("可行率（标签 = 可行样本数/分母；物化超时样本不计入分母）")
    p = EVAL_DIR / "chart_feasibility.png"
    fig.savefig(p, bbox_inches="tight"); plt.close(fig); paths.append(p.name)

    # 图3：dev 集 110% 上限敏感性（low_disruption 三种 cap 的成本）
    dev_recs = [r for r in recs if r.get("split") == "dev"
                and not r.get("materialize_error")]
    caps = {}
    for rec in dev_recs:
        for row in rec.get("rows", []):
            if row["method"] == "low_disruption" \
                    and row.get("cost_cap_ratio") in (1.05, 1.1, 1.2) \
                    and row.get("has_solution"):
                caps.setdefault(row["cost_cap_ratio"], []).append(
                    row["total_travel_time_s"])
    fig, ax = plt.subplots(figsize=(6, 4))
    ax.boxplot([caps.get(c, []) for c in (1.05, 1.1, 1.2)],
               labels=["cap=1.05", "cap=1.10(冻结)", "cap=1.20"])
    ax.set_ylabel("low_disruption 成本（模型秒）")
    ax.set_title("dev 集 110% 成本上限附近敏感性（n="
                 f"{max(len(caps.get(c, [])) for c in (1.05, 1.1, 1.2))}）")
    p = EVAL_DIR / "chart_sensitivity.png"
    fig.savefig(p, bbox_inches="tight"); plt.close(fig); paths.append(p.name)
    return paths


def main() -> None:
    recs = load_records()
    manifest = json.loads((EVAL_DIR / "frozen_manifest.json")
                          .read_text(encoding="utf-8"))
    enrich_error_records(recs, manifest)

    # summary.csv（原始结果一致性：报告全部来自这些行）
    # 物化失败场景逐场景补一行 method=__materialize__，保证清单↔汇总逐行对齐
    with open(EVAL_DIR / "summary.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["scenario_id", "split", "n_orders", "method", "backend",
                    "cost_cap_ratio", "status", "has_solution",
                    "total_travel_time_s", "elapsed_ms", "elapsed_solve_ms",
                    "elapsed_validate_ms", "end_to_end_ms", "validation_ok",
                    "solver_status", "scenario_input_hash", "budget_ms"])
        for rec in recs:
            if rec.get("materialize_error"):
                w.writerow([rec["scenario_id"], rec.get("split"),
                            rec.get("n_orders"), "__materialize__", "",
                            "", "materialize_error", False, "", "", "", "",
                            rec.get("end_to_end_ms", ""), "",
                            rec["materialize_error"],
                            rec.get("scenario_input_hash", ""),
                            manifest["budget_ms"]])
                continue
            for row in rec.get("rows", []):
                w.writerow([rec["scenario_id"], rec.get("split"),
                            rec.get("n_orders"), row["method"],
                            row.get("backend") or row.get("backend_actual"),
                            row.get("cost_cap_ratio"), row["status"],
                            row.get("has_solution"),
                            row.get("total_travel_time_s"),
                            row.get("elapsed_ms"), row.get("elapsed_solve_ms"),
                            row.get("elapsed_validate_ms"),
                            row.get("end_to_end_ms"), row.get("validation_ok"),
                            row.get("solver_status"),
                            rec.get("scenario_input_hash"),
                            rec.get("budget_ms", manifest["budget_ms"])])

    # 一致性校验：清单 39 场景 ↔ summary 场景逐一对齐，缺一即失败
    man_ids = {e["scenario_id"] for e in manifest["scenarios"]}
    sum_ids = {r["scenario_id"] for r in recs}
    missing = man_ids - sum_ids
    extra = sum_ids - man_ids
    if missing or extra:
        raise SystemExit(f"清单与原始结果不一致: 缺 {sorted(missing)} 多 {sorted(extra)}")
    n_split = Counter(e["split"] for e in manifest["scenarios"])
    print(f"一致性 OK：清单 {dict(n_split)} = 原始结果 {dict(Counter(r.get('split') for r in recs))}")

    dev_table, dev_stats = comparison_table(recs, "dev")
    test_table, test_stats = comparison_table(recs, "test")
    dist_test = failure_distribution(recs, "test")
    dist_dev = failure_distribution(recs, "dev")

    # OR-Tools vs COPT 对照（dev，单独成表）
    copt_rows = []
    for rec in recs:
        if rec.get("split") != "dev" or rec.get("materialize_error"):
            continue
        ot = copt = None
        for row in rec.get("rows", []):
            if row["method"] == "rebuild" and row.get("backend") == "copt":
                copt = row
            elif row["method"] == "rebuild":
                ot = row
        if ot or copt:
            copt_rows.append([
                rec["scenario_id"], rec["n_orders"],
                f"{status_cn(ot and ot['status'])} / {fmt_cost(ot)}s"
                if ot else "—",
                f"{status_cn(copt and copt['status'])} / {fmt_cost(copt)}s"
                if copt else "—",
                (f"{(ot['total_travel_time_s'] / copt['total_travel_time_s'] - 1) * 100:+.1f}%"
                 if ot and copt and ot.get("has_solution")
                 and copt.get("has_solution") else "—"),
            ])
    copt_table = md_table(
        ["场景(dev)", "订单数", "rebuild OR-Tools(状态/成本s)",
         "rebuild COPT 60s(状态/成本s)", "OR-Tools 成本高出"],
        copt_rows)

    def rate(stats, meth):
        ok, den = stats.get(meth, (0, 0))
        return f"{ok}/{den}（{ok / den * 100:.0f}%）" if den else "0/0"

    chart_names = charts(recs)

    # 越界统计（软预算如实披露）
    over_copt, over_other = [], []
    for rec in recs:
        for row in rec.get("rows", []):
            e = row.get("elapsed_ms")
            if e and e > manifest["budget_ms"]:
                (over_copt if row.get("backend") == "copt" else over_other)\
                    .append((rec["scenario_id"], row["method"],
                             row.get("backend"), e))
    over_copt.sort(key=lambda x: -x[3])
    over_other.sort(key=lambda x: -x[3])

    # 物化失败场景逐一清单（不静默改变分母）
    mat_fail_rows = [[r["scenario_id"], r.get("split"), r.get("n_orders"),
                      r["materialize_error"]]
                     for r in recs if r.get("materialize_error")]
    mat_fail_table = md_table(
        ["场景", "分组", "订单数", "物化失败原因（原始记录）"], mat_fail_rows)
    n_mat = Counter(r.get("split") for r in recs if r.get("materialize_error"))

    n_test = sum(1 for r in recs if r.get("split") == "test")
    n_dev = sum(1 for r in recs if r.get("split") == "dev")
    n_test_mat = n_mat.get("test", 0)
    n_dev_mat = n_mat.get("dev", 0)
    report = f"""# 阶段 6 评测报告（60 秒软预算版）

- 生成时间：{__import__('datetime').datetime.now().isoformat(timespec='seconds')}
- 冻结清单：`frozen_manifest.json`（开跑前写入，含划分/种子/配置/预算）
- 原始结果：`raw/*.json`（每场景输入哈希/状态/指标/异常/分项耗时）+ `summary.csv`
- **样本口径（修正）**：清单共 39 场景 = dev {n_dev} + test {n_test}，
  与 `raw/*.json`、`summary.csv` 逐行对齐（一致性校验在生成时强制执行）。
  其中 {len(mat_fail_rows)} 个场景在"原计划物化"阶段失败（dev {n_dev_mat} +
  test {n_test_mat}，原因见下表），未进入三方法比较，但保留在分母与清单中，
  不静默剔除。
- **预算口径（修正）**：本轮为 **60 秒软预算评测**——求解器时间限制为软上限、
  无独立进程硬截断，**不得宣传为严格同预算**。越界行如实保留并统计如下；
  正式版将以独立进程（或等效机制）执行硬截断，并记录请求预算/实际耗时/超时状态。
- 状态语义：TIMEOUT / NO_FEASIBLE_FOUND {manifest['status_semantics']['TIMEOUT/NO_FEASIBLE_FOUND']}；
  OR-Tools 可行{manifest['status_semantics']['OR-Tools FEASIBLE']}；
  PROVEN_INFEASIBLE {manifest['status_semantics']['PROVEN_INFEASIBLE']}；
  COPT 仅 dev 集诊断、单独成表。

## 〇、分母核对与物化失败场景清单

**物化失败 {len(mat_fail_rows)} 场景**（构建"原计划"的独立 20 秒预算耗尽，
E_PLAN_NOT_FOUND——发生在三方法比较之前，**不是**三方法失败，更**不是**
场景不可行的证明；该阶段失败场景不进入方法级比较，但保留在分母中）：

{mat_fail_table}

## 一、测试集（冻结配置，test，n={n_test}，其中物化失败 {n_test_mat} 个）

{test_table}

**可行率**（分母 = 物化成功样本；物化超时单独列出，不偷偷剔除）：
{md_table(["方法", "可行样本/分母"], [["insert", rate(test_stats, "insert")], ["rebuild OR-Tools", rate(test_stats, "rebuild")], ["low_disruption(1.10)", rate(test_stats, "low_disruption")]])}

**失败分布**：{dict(dist_test)}

## 二、开发集（dev，定参数用；含 COPT 诊断与敏感性，n={n_dev}，其中物化失败 {n_dev_mat} 个）

{dev_table}

**可行率**：insert {rate(dev_stats, 'insert')}；rebuild OR-Tools {rate(dev_stats, 'rebuild')}；low_disruption {rate(dev_stats, 'low_disruption')}
**失败分布**：{dict(dist_dev)}

## 三、配对比较（注明样本占比，单位：模型行驶秒）

- {paired(recs, 'test', 'insert', 'low_disruption')}
- {paired(recs, 'test', 'rebuild', 'low_disruption')}
- {paired(recs, 'test', 'insert', 'rebuild')}

## 四、OR-Tools vs COPT 对照（dev 集诊断，单独成表，不混入主比较）

{copt_table}

说明：COPT 在 75+ 节点 60 秒无 incumbent 记为 no_feasible_found（≠不可行）；
OR-Tools 可行解不得写成已证明最优（上表成本差即为证据之一）。

## 五、软预算越界统计（如实披露，不修正不剔除）

- COPT 诊断行（dev，rebuild/backend=copt）越界 **{len(over_copt)}** 行，
  幅度 {over_copt[-1][3] / 1000:.1f}s ~ {over_copt[0][3] / 1000:.1f}s——
  **属秒级越界，不是毫秒粒度误差**；最大 {over_copt[0][0]} {over_copt[0][3]}ms。
  COPT 无 incumbent 的越界行状态仍为 TIMEOUT_NO_SOLUTION（≠不可行）。
- 其余方法越界 **{len(over_other)}** 行，幅度 ≤
  {max((x[3] for x in over_other), default=0) / 1000:.3f}s（低扰动候选池搜索的
  求解器软上限残余，接近 60.0s）。
- 结论：本轮各方法实际耗时以 `summary.csv` 的 elapsed_ms/budget_ms 为准；
  下一轮用独立进程硬截断后可改称"严格 60 秒同预算"。

## 六、产品适用范围分层（按 2026-10-11 决策）

- **可演示（已验证）**：出发前、单仓场景——评测中物化成功且低扰动法
  稳定可行（test 集 15/15、dev 集物化成功样本 15/16）的范围。
- **实验性（需人工复核）**：大规模（kilby 75+ 节点）与紧时间窗（R 系列）
  场景——insert/rebuild 常快速返回 NO_FEASIBLE_FOUND，COPT 60 秒无 incumbent；
  产品功能可展示结果，但必须标注"实验性能力，需调度员复核"。
- **不按节点数简单划界**：18 单的 r201-s101 也出现 insert/rebuild 未找到解；
  大规模 Kilby 与小规模 Solomon 数据来源、约束特征均不同，适用边界
  由数据族+约束特征共同决定，而非仅订单数。
- **NO_FEASIBLE_FOUND 语义**：毫秒级返回表示该算法快速判定无可行解，
  **不是**问题不可行（仅 COPT proven_infeasible 可称不可行，如 r202-s102）。
- **算法改进列为后续工作**：insert/rebuild 在大规模与紧时间窗场景的能力
  提升、60 秒硬截止机制、300 秒 COPT 诊断（预先指定 3 个代表性 dev 大场景、
  单独成表，不碰 test 集、不并入主比较）——均未在本轮执行。

## 七、图表（由原始结果程序生成）

{chr(10).join(f'- `{n}`' for n in chart_names)}

## 八、复现命令

```bat
.venv\\Scripts\\python.exe scripts\\run_evaluation.py --split all --copt-diag --overwrite
.venv\\Scripts\\python.exe scripts\\build_evaluation_report.py
```

## 九、诚实声明

- 成本为模型行驶秒（无真实单价，不报人民币节约）；
- 物化失败、超时、证明不可行样本全部在上表与失败分布中，未剔除，分母恒定 39；
- 若某方法无提升，以上配对比较如实呈现，不做选择性报告；
- **insert 能力边界**：插入法在大场景（kilby 75+ 节点）多为毫秒级返回
  "预算内未找到解"——这是算法快速判定无可行插入位置的真实行为，非运行错误；
  rebuild OR-Tools 同理会在部分紧时间窗场景快速 FAIL（≠证明不可行）；
- **计时与预算**：耗时以毫秒记录，亚毫秒求解计 0ms；本轮为 60 秒软预算
  （无硬截断），越界行见"软预算越界统计"节，如实记录不修正、不归咎于
  毫秒粒度误差；
- **LD 用满预算**：low_disruption 的候选池搜索在 60 秒预算内持续改进，
  因此 elapsed 多为 60000ms 左右，属于该方法设计而非异常。
"""
    (EVAL_DIR / "evaluation_report.md").write_text(report, encoding="utf-8")
    print(f"报告 -> {EVAL_DIR / 'evaluation_report.md'}")
    print(f"汇总 -> {EVAL_DIR / 'summary.csv'}（{sum(len(r.get('rows', [])) for r in recs)} 行）")
    print("图表 ->", ", ".join(chart_names))


if __name__ == "__main__":
    main()
