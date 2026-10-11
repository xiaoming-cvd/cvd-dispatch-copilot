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
    ax.set_title("阶段6 冻结评测 · 测试集三方法成本对比（60s 端到端预算）")
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

    # summary.csv（原始结果一致性：报告全部来自这些行）
    with open(EVAL_DIR / "summary.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["scenario_id", "split", "n_orders", "method", "backend",
                    "cost_cap_ratio", "status", "has_solution",
                    "total_travel_time_s", "elapsed_ms", "elapsed_solve_ms",
                    "elapsed_validate_ms", "end_to_end_ms", "validation_ok",
                    "solver_status", "scenario_input_hash"])
        for rec in recs:
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
                            rec.get("scenario_input_hash")])

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

    n_test = sum(1 for r in recs if r.get("split") == "test")
    report = f"""# 阶段 6 冻结评测报告

- 生成时间：{__import__('datetime').datetime.now().isoformat(timespec='seconds')}
- 冻结清单：`frozen_manifest.json`（开跑前写入，含划分/种子/配置/预算）
- 原始结果：`raw/*.json`（每场景输入哈希/状态/指标/异常/分项耗时）+ `summary.csv`
- 口径：三方法统一 {manifest['budget_ms'] / 1000:.0f} 秒端到端预算；rebuild 主比较
  默认 OR-Tools；状态如实——{manifest['status_semantics']['TIMEOUT/NO_FEASIBLE_FOUND']}，
  {manifest['status_semantics']['OR-Tools FEASIBLE']}；COPT 仅 dev 集诊断、单独成表。

## 一、测试集（冻结配置，test，n={n_test}）

{test_table}

**可行率**（分母 = 物化成功样本；物化超时单独列出，不偷偷剔除）：
{md_table(["方法", "可行样本/分母"], [["insert", rate(test_stats, "insert")], ["rebuild OR-Tools", rate(test_stats, "rebuild")], ["low_disruption(1.10)", rate(test_stats, "low_disruption")]])}

**失败分布**：{dict(dist_test)}

## 二、开发集（dev，定参数用；含 COPT 诊断与敏感性）

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

## 五、图表（由原始结果程序生成）

{chr(10).join(f'- `{n}`' for n in chart_names)}

## 六、复现命令

```bat
.venv\\Scripts\\python.exe scripts\\run_evaluation.py --split all --copt-diag --overwrite
.venv\\Scripts\\python.exe scripts\\build_evaluation_report.py
```

## 七、诚实声明

- 成本为模型行驶秒（无真实单价，不报人民币节约）；
- 物化失败、超时、证明不可行样本全部在上表与失败分布中，未剔除；
- 若某方法无提升，以上配对比较如实呈现，不做选择性报告；
- **insert 能力边界**：插入法在大场景（kilby 75+ 节点）多为毫秒级返回
  "预算内未找到解"——这是算法快速判定无可行插入位置的真实行为，非运行错误；
  rebuild OR-Tools 同理会在部分紧时间窗场景快速 FAIL（≠证明不可行）；
- **计时粒度**：耗时以毫秒记录，亚毫秒求解计 0ms；求解器时间限制为软上限，
  端到端耗时可能略超 60000ms（如 60046ms），如实记录不修正；
- **LD 用满预算**：low_disruption 的候选池搜索在 60 秒预算内持续改进，
  因此 elapsed 多为 60000ms 左右，属于该方法设计而非异常。
"""
    (EVAL_DIR / "evaluation_report.md").write_text(report, encoding="utf-8")
    print(f"报告 -> {EVAL_DIR / 'evaluation_report.md'}")
    print(f"汇总 -> {EVAL_DIR / 'summary.csv'}（{sum(len(r.get('rows', [])) for r in recs)} 行）")
    print("图表 ->", ", ".join(chart_names))


if __name__ == "__main__":
    main()
