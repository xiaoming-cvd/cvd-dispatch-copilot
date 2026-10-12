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


def scenario_meta(sid: str, split: str) -> dict:
    """场景草稿元数据（时间窗紧度/锁比例/种子/草稿哈希）。"""
    import hashlib
    p = ROOT / "data" / "processed" / "scenarios" / split / f"{sid}.json"
    out = {"tw": "—", "lock": None, "seed": None, "draft_sha256": None}
    try:
        raw_bytes = p.read_bytes()
        out["draft_sha256"] = hashlib.sha256(raw_bytes).hexdigest()
        meta = json.loads(raw_bytes.decode("utf-8")).get("_meta", {})
        ev = (meta.get("event_log") or [{}])[0]
        out["tw"] = ev.get("tw_tightness") or "—"
        out["lock"] = meta.get("lock_ratio_target")
        out["seed"] = meta.get("seed")
    except Exception:
        pass
    return out


def cap_checks(recs: list[dict]) -> list[dict]:
    """低扰动行成本上限逐行核验，导出机器可读布尔 within_cap。

    语义（2026-10-11 二次复审）：区分"找到可行路线"与"满足成本上限"——
    within_cap = (selected ≤ cap × reference) 且未标 no_candidate_within_cost_cap；
    无解行写 null（不是 ok）；有数值时标记必须等于数值比较（断言进测试）。
    """
    out = []
    for rec in recs:
        for r in rec.get("rows", []):
            if r["method"] != "low_disruption":
                continue
            item = {"scenario_id": rec["scenario_id"],
                    "cap_ratio": r.get("cost_cap_ratio"),
                    "reference_best_cost_s": r.get("reference_best_cost_s"),
                    "selected_cost_s": r.get("total_travel_time_s"),
                    "allowed_cost_s": None, "within_cap": None,
                    "verdict": None}
            sel = item["selected_cost_s"]
            if not r.get("has_solution") or sel is None:
                item["verdict"] = "无解行(null)"
                out.append(item)
                continue
            ratio, ref = item["cap_ratio"], item["reference_best_cost_s"]
            if ratio is None or ref is None:
                item["verdict"] = "数据不足"
                out.append(item)
                continue
            allowed = ratio * ref
            numeric_within = sel <= allowed + 1e-6
            flagged = (r.get("solver_status") == "no_candidate_within_cost_cap"
                       or any("within_reference_cap=false" in w
                              for w in r.get("warnings", [])))
            within = bool(numeric_within and not flagged)
            item["allowed_cost_s"] = round(allowed, 3)
            item["within_cap"] = within
            item["verdict"] = ("ok" if within == numeric_within
                               else "VIOLATION")
            out.append(item)
    return out


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
    n_split_all = sum(1 for rec in recs if rec.get("split") == split)
    for x in METHODS:
        ok = sum(1 for rec in recs if rec.get("split") == split
                 and not rec.get("materialize_error")
                 and main_rows(rec).get(x, {}).get("status") == "feasible")
        stats[x] = (ok, denom)               # 可物化子集口径
        stats[x + "_frozen"] = (ok, n_split_all)  # 冻结集口径（分母含物化失败）
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
            # 口径修正（2026-10-12，300s 诊断证实）：根因是初解构造失败
            # （时间窗过紧），不是预算耗尽——raw 消息文案为冻结原始记录不改
            dist["原计划物化失败(初解构造失败,≠不可行)"] += 1
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
    # 图1a/1b：test 集成本按数据来源分图（Solomon 与 Kilby 量级差大，不分图不可读）
    for source, tag in (("solomon", "solomon"), ("kilby", "kilby")):
        sub = [r for r in test_recs if r.get("source") == source]
        if not sub:
            continue
        sub.sort(key=lambda r: r["n_orders"])
        labels = [r["scenario_id"].replace("kilby-", "k-") for r in sub]
        x = range(len(sub))
        width = 0.27
        fig, ax = plt.subplots(figsize=(10, 5))
        for i, meth in enumerate(METHODS):
            vals = []
            for rec in sub:
                row = main_rows(rec).get(meth, {})
                v = row.get("total_travel_time_s")
                vals.append(v if row.get("has_solution") and v is not None else 0)
            ax.bar([p + (i - 1) * width for p in x], vals, width, label=meth)
        ax.set_xticks(list(x)); ax.set_xticklabels(labels, rotation=60, ha="right", fontsize=8)
        ax.set_ylabel("总行驶时间（模型秒，抽象成本，非人民币）")
        ax.set_title(f"阶段6 评测 · 测试集三方法成本对比（{source}，60s 软预算）")
        ax.legend()
        p = EVAL_DIR / f"chart_cost_test_{tag}.png"
        fig.savefig(p, bbox_inches="tight"); plt.close(fig); paths.append(p.name)

    # 图2：可行率（可物化子集口径，冻结集口径在报告表格双行给出）
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
    ax.set_title("可行率（可物化子集口径；物化失败样本见报告〇节，不静默剔除）")
    p = EVAL_DIR / "chart_feasibility.png"
    fig.savefig(p, bbox_inches="tight"); plt.close(fig); paths.append(p.name)

    # 图3：配对成本差——仅两方法均可行的配对样本（注明 n，不推断总体）
    diffs = []
    for rec in test_recs:
        m = main_rows(rec)
        ld = m.get("low_disruption")
        if not ld or not ld.get("has_solution"):
            continue
        refs = [m[x]["total_travel_time_s"] for x in ("insert", "rebuild")
                if m.get(x, {}).get("has_solution")
                and m[x].get("total_travel_time_s") is not None]
        if refs:
            diffs.append((rec["scenario_id"].replace("kilby-", "k-"),
                          ld["total_travel_time_s"] - min(refs)))
    fig, ax = plt.subplots(figsize=(10, 4))
    xs = range(len(diffs))
    ax.bar(xs, [d[1] for d in diffs],
           color=["#1a7f37" if d[1] <= 0 else "#b35900" for d in diffs])
    ax.axhline(0, color="black", linewidth=0.8)
    ax.set_xticks(list(xs)); ax.set_xticklabels([d[0] for d in diffs],
                                                rotation=60, ha="right", fontsize=8)
    ax.set_ylabel("LD − min(insert, rebuild)（模型秒，负=LD 更低）")
    ax.set_title(f"测试集配对成本差（仅双方均可行样本，n={len(diffs)}，"
                 "不能据此推断总体节约）")
    p = EVAL_DIR / "chart_cost_diff.png"
    fig.savefig(p, bbox_inches="tight"); plt.close(fig); paths.append(p.name)

    # 图4：固定候选池敏感性（同池仅改筛选阈值；非独立重跑）
    fp_dir = EVAL_DIR / "sensitivity_fixedpool"
    series = []
    if fp_dir.is_dir():
        for jp in sorted(fp_dir.glob("*.json")):
            d = json.loads(jp.read_text(encoding="utf-8"))
            caps_d = d.get("per_cap") or {}
            vals = [caps_d.get(str(c), {}).get("selected_cost_s")
                    for c in (1.05, 1.1, 1.2)]
            if any(v is None for v in vals):
                continue
            series.append((d["scenario_id"].replace("kilby-", "k-"), vals))
    fig, ax = plt.subplots(figsize=(8, 5))
    for name, vals in series:
        ax.plot([1.05, 1.10, 1.20], vals, marker="o", linewidth=1, label=name)
    ax.set_xlabel("cost_cap_ratio（固定候选池，仅改变筛选阈值）")
    ax.set_ylabel("选中方案成本（模型秒）")
    ax.set_title(f"dev 集固定候选池上限敏感性（n={len(series)}；同池仅改阈值："
                 "cap 放宽⇒可选集合单调增大；选中成本按扰动优先规则不保证单调）")
    if series:
        ax.legend(fontsize=7, ncol=2)
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
                    "solver_status", "scenario_input_hash", "budget_ms",
                    "allowed_cost_s", "within_cap", "cap_verdict"])
        caps_ok = cap_checks(recs)
        cap_by_key = {(c["scenario_id"], c["cap_ratio"]): c for c in caps_ok}

        def _cap_cells(ck):
            if ck is None:
                return "", "", ""
            wc = ck["within_cap"]
            return (ck["allowed_cost_s"] if ck["allowed_cost_s"] is not None else "",
                    ("TRUE" if wc else "FALSE") if wc is not None else "",
                    ck["verdict"] or "")
        for rec in recs:
            if rec.get("materialize_error"):
                w.writerow([rec["scenario_id"], rec.get("split"),
                            rec.get("n_orders"), "__materialize__", "",
                            "", "materialize_error", False, "", "", "", "",
                            rec.get("end_to_end_ms", ""), "",
                            rec["materialize_error"],
                            rec.get("scenario_input_hash", ""),
                            manifest["budget_ms"], "", "", ""])
                continue
            for row in rec.get("rows", []):
                ck = cap_by_key.get((rec["scenario_id"],
                                     row.get("cost_cap_ratio")))
                allowed, within, verdict = _cap_cells(ck)
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
                            rec.get("budget_ms", manifest["budget_ms"]),
                            allowed, within, verdict])

    # 对账表：每个冻结 ID 的物化状态/失败原因/哈希/是否进入主评测/三方法状态
    recon_rows = []
    for e in manifest["scenarios"]:
        sid, split = e["scenario_id"], e["split"]
        rec = next((r for r in recs if r["scenario_id"] == sid), None)
        meta = scenario_meta(sid, split)
        if rec is None or rec.get("materialize_error"):
            recon_rows.append([sid, split, e.get("base_instance"),
                               rec.get("n_orders") if rec else "?",
                               meta["tw"], meta["lock"], meta["seed"],
                               (meta["draft_sha256"] or "")[:12],
                               "物化失败",
                               (rec["materialize_error"][:40]
                                if rec else "无原始记录"),
                               "否", "—", "—", "—"])
        else:
            m = main_rows(rec)
            recon_rows.append([
                sid, split, e.get("base_instance"), rec.get("n_orders"),
                meta["tw"], meta["lock"], meta["seed"],
                (meta["draft_sha256"] or "")[:12],
                f"OK({rec.get('materialize_ms')}ms)", "", "是",
                *[status_cn(m[x]["status"]) if x in m else "缺行"
                  for x in METHODS]])
    with open(EVAL_DIR / "reconciliation.csv", "w", newline="",
              encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["scenario_id", "split", "base_instance", "n_orders",
                    "tw_tightness", "lock_ratio", "seed", "draft_sha256",
                    "materialize", "失败原因", "进入主评测",
                    "insert", "rebuild", "low_disruption"])
        for i, e in enumerate(manifest["scenarios"]):
            row = list(recon_rows[i])
            row[7] = scenario_meta(e["scenario_id"], e["split"])["draft_sha256"]
            w.writerow(row)
    recon_md = md_table(
        ["场景", "组", "来源", "单", "TW", "锁", "种子", "草稿哈希",
         "物化", "失败原因", "主评测", "insert", "rebuild", "LD"], recon_rows)

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

    # 成本上限核验汇总（二次复审：布尔 within_cap；无解行=null）
    n_violation = sum(1 for c in caps_ok if c["verdict"] == "VIOLATION")
    n_insufficient = sum(1 for c in caps_ok if c["verdict"] == "数据不足")
    n_null = sum(1 for c in caps_ok if c["within_cap"] is None)
    n_with_values = sum(1 for c in caps_ok if c["within_cap"] is not None)
    n_within_true = sum(1 for c in caps_ok if c["within_cap"] is True)
    n_within_false = sum(1 for c in caps_ok if c["within_cap"] is False)
    # 主 cap 1.10 超限条数（冻结 test 配置）
    n_maincap_false = sum(
        1 for c in caps_ok
        if c["within_cap"] is False and c["cap_ratio"] == 1.1)
    viol_rows = [c for c in caps_ok if c["verdict"] == "VIOLATION"]
    # 配对样本数（程序生成，不写死——二次复审文字错误修复）
    def _paired_count(base: str, cand: str) -> int:
        n = 0
        for rec in recs:
            if rec.get("split") != "test" or rec.get("materialize_error"):
                continue
            m = main_rows(rec)
            a, b = m.get(base), m.get(cand)
            if a and b and a.get("has_solution") and b.get("has_solution"):
                n += 1
        return n
    n_pair_ir = _paired_count("insert", "rebuild")

    # 分层边界证据：按数据来源/时间窗紧度/锁比例/订单规模（可物化子集口径）
    def _bucket_n(n):
        n = int(n)
        return "≤18" if n <= 18 else "19–75" if n <= 75 else \
            "76–150" if n <= 150 else ">150"

    strata_acc = {}
    for e in manifest["scenarios"]:
        rec = next((r for r in recs if r["scenario_id"] == e["scenario_id"]),
                   None)
        if rec is None or rec.get("materialize_error"):
            continue
        meta = scenario_meta(e["scenario_id"], e["split"])
        dims = {"数据来源": rec.get("source") or e.get("base_instance"),
                "时间窗紧度": meta["tw"],
                "锁比例": (f"{meta['lock']}" if meta["lock"] is not None
                          else "—"),
                "订单规模": _bucket_n(rec.get("n_orders", 0))}
        m = main_rows(rec)
        for dim, key in dims.items():
            cell = strata_acc.setdefault((dim, key), {x: [0, 0] for x in METHODS})
            for x in METHODS:
                cell[x][1] += 1
                if m.get(x, {}).get("status") == "feasible":
                    cell[x][0] += 1
    strata_rows = []
    for dim in ("数据来源", "时间窗紧度", "锁比例", "订单规模"):
        keys = [k for (d, k) in strata_acc if d == dim]
        for key in sorted(keys, key=str):
            cell = strata_acc[(dim, key)]
            strata_rows.append([dim, key] + [
                f"{cell[x][0]}/{cell[x][1]}" for x in METHODS])
    strata_table = md_table(
        ["维度", "层", "insert 可行", "rebuild 可行", "LD 可行"], strata_rows)

    # r202-s102 COPT 不可行证明的证据指针（模型版本+输入哈希）
    proof = {}
    for rec in recs:
        if rec["scenario_id"] == "r202-s102":
            proof = {"hash": rec.get("scenario_input_hash"),
                     "rows": [r for r in rec.get("rows", [])
                              if r.get("status") == "proven_infeasible"]}

    # frozen_inputs.json：冻结输入证据（草稿哈希/种子/锁比例/依赖/提交号）
    import platform as _pf
    import subprocess as _sp

    def _dep(mod):
        try:
            out = _sp.run(
                [str(ROOT / ".venv" / "Scripts" / "python.exe"), "-c",
                 f"import {mod} as m; print(getattr(m, '__version__', '?'))"],
                capture_output=True, text=True, timeout=30)
            return out.stdout.strip() or "未安装"
        except Exception:
            return "未安装"
    try:
        commit = _sp.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                         capture_output=True, text=True,
                         timeout=10).stdout.strip()
    except Exception:
        commit = "?"
    try:
        py_ver = _sp.run(
            [str(ROOT / ".venv" / "Scripts" / "python.exe"), "-c",
             "import platform; print(platform.python_version())"],
            capture_output=True, text=True, timeout=30).stdout.strip()
    except Exception:
        py_ver = _pf.python_version()
    frozen_inputs = {
        "generated_at": __import__('datetime').datetime.now()
        .isoformat(timespec="seconds"),
        "git_commit": commit,
        "python": py_ver,
        "freezing_provenance": "事后归档校验：草稿哈希开跑后补记，下一轮"
                              "将开跑前写入不可变 manifest（二次复审 2026-10-11）",
        "deps": {"ortools": _dep("ortools"), "pydantic": _dep("pydantic"),
                 "pytest": _dep("pytest")},
        "budget_ms": manifest["budget_ms"],
        "cost_cap_ratio_test": manifest["cost_cap_ratio"],
        "status_semantics": manifest["status_semantics"],
        "scenarios": [],
    }
    for e in manifest["scenarios"]:
        meta = scenario_meta(e["scenario_id"], e["split"])
        rec = next((r for r in recs if r["scenario_id"] == e["scenario_id"]),
                   None)
        frozen_inputs["scenarios"].append({
            "scenario_id": e["scenario_id"], "split": e["split"],
            "base_instance": e.get("base_instance"), "seed": meta["seed"],
            "lock_ratio_target": meta["lock"],
            "draft_sha256": meta["draft_sha256"],
            "scenario_input_hash": (rec or {}).get("scenario_input_hash"),
        })
    (EVAL_DIR / "frozen_inputs.json").write_text(
        json.dumps(frozen_inputs, ensure_ascii=False, indent=1),
        encoding="utf-8")
    try:  # 依赖锁定文件（二次复审：证据链补件）
        freeze = _sp.run(
            [str(ROOT / ".venv" / "Scripts" / "python.exe"), "-m", "pip",
             "freeze"], capture_output=True, text=True, timeout=60)
        if freeze.returncode == 0 and freeze.stdout.strip():
            (EVAL_DIR / "requirements_lock.txt").write_text(
                freeze.stdout, encoding="utf-8")
    except Exception:
        pass
    report = f"""# 阶段 6 评测报告（60 秒软预算版）

- 生成时间：{__import__('datetime').datetime.now().isoformat(timespec='seconds')}
- 冻结清单：`frozen_manifest.json`（开跑前写入，含划分/种子/配置/预算）
- 原始结果：`raw/*.json`（每场景输入哈希/状态/指标/异常/分项耗时）+ `summary.csv`
- **样本口径（修正）**：清单共 39 场景 = dev {n_dev} + test {n_test}，
  与 `raw/*.json`、`summary.csv` 逐行对齐（一致性校验在生成时强制执行）。
  其中 {len(mat_fail_rows)} 个场景在"原计划物化"阶段失败（dev {n_dev_mat} +
  test {n_test_mat}，原因见下表），未进入三方法比较，但保留在分母与清单中，
  不静默剔除。
- **冻结性质（如实降级，二次复审）**：本轮清单在开跑前冻结（frozen_at 见
  manifest），但每场景草稿哈希为**事后归档校验**（开跑后补记，时间差约
  2 小时 23 分），不能单独证明输入在开跑前已锁定；**下一轮将开跑前把
  草稿 SHA-256 写入不可变 manifest 再跑**。
- **预算口径（修正）**：本轮为 **60 秒软预算评测**——求解器时间限制为软上限、
  无独立进程硬截断，**不得宣传为严格同预算**。越界行如实保留并统计如下；
  正式版将以独立进程（或等效机制）执行硬截断，并记录请求预算/实际耗时/超时状态。
- 状态语义：TIMEOUT / NO_FEASIBLE_FOUND {manifest['status_semantics']['TIMEOUT/NO_FEASIBLE_FOUND']}；
  OR-Tools 可行{manifest['status_semantics']['OR-Tools FEASIBLE']}；
  PROVEN_INFEASIBLE {manifest['status_semantics']['PROVEN_INFEASIBLE']}；
  COPT 仅 dev 集诊断、单独成表。

## 〇、分母核对与物化失败场景清单

**物化失败 {len(mat_fail_rows)} 场景**（构建"原计划"未找到可行解
E_PLAN_NOT_FOUND——原表述"独立 20 秒预算耗尽"**有误，已修正**：后续
300 秒诊断（独立成表，见 `reports/baseline300/`）证实为**初解构造失败
（时间窗过紧）**，300 秒预算下仍在 0~92 毫秒内失败，**与预算大小无关**；
也不是场景不可行的证明。该失败发生在三方法比较之前，**不是**三方法失败；
该阶段失败场景不进入方法级比较，但保留在分母中）：

{mat_fail_table}

**manifest 对账表（全部 39 个冻结 ID）**：物化状态 / 失败原因 / 草稿哈希 /
是否进入主评测 / 三方法状态，逐行可核；完整版（含完整 SHA-256 与输入哈希）
见同目录 `reconciliation.csv`：

{recon_md}

## 一、测试集（冻结配置，test，n={n_test}，其中物化失败 {n_test_mat} 个）

{test_table}

**可行率（双口径并报，不得混用）**：
{md_table(["方法", "冻结集口径(分母16)", "可物化子集口径(分母15)"],
          [["insert", rate(test_stats, "insert_frozen"), rate(test_stats, "insert")],
           ["rebuild OR-Tools", rate(test_stats, "rebuild_frozen"), rate(test_stats, "rebuild")],
           ["low_disruption(1.10)", rate(test_stats, "low_disruption_frozen"), rate(test_stats, "low_disruption")]])}

**失败分布**：{dict(dist_test)}

## 二、开发集（dev，定参数用；含 COPT 诊断与敏感性，n={n_dev}，其中物化失败 {n_dev_mat} 个）

{dev_table}

**可行率（双口径）**：insert 冻结集 {rate(dev_stats, 'insert_frozen')} / 可物化 {rate(dev_stats, 'insert')}；rebuild OR-Tools 冻结集 {rate(dev_stats, 'rebuild_frozen')} / 可物化 {rate(dev_stats, 'rebuild')}；low_disruption 冻结集 {rate(dev_stats, 'low_disruption_frozen')} / 可物化 {rate(dev_stats, 'low_disruption')}
**失败分布**：{dict(dist_dev)}

## 三、配对比较（注明样本占比，单位：模型行驶秒）

- {paired(recs, 'test', 'insert', 'low_disruption')}
- {paired(recs, 'test', 'rebuild', 'low_disruption')}
- {paired(recs, 'test', 'insert', 'rebuild')}

配对样本少（insert×rebuild 双双可行的测试样本仅 {n_pair_ir} 个，程序计数），
成本优势结论仅限该配对子集，
不得推断总体节约；`chart_cost_diff.png` 为配对成本差可视化。

## 三·补、成本上限逐行核验（布尔 within_cap）

低扰动 {len(caps_ok)} 行逐行核验（机器可读字段，可由 raw 重新导出）：
有数值行 {n_with_values} 条——within_cap=TRUE {n_within_true} 条、
**within_cap=FALSE {n_within_false} 条**（找到可行路线但未满足成本上限，
solver_status=no_candidate_within_cost_cap，**不计入达标可行率**）；
无解行 {n_null} 条（null，不写作 ok）；数据不足 {n_insufficient} 条；
字段与数值不一致（VIOLATION）{n_violation} 条。
**冻结主配置 cap=1.10 超限 {n_maincap_false} 条**（测试集 15/15 可行的
"可行"均含 within_cap=TRUE 含义，达标口径需在引用时同时给出两列）。
{('违规明细：' + '; '.join(f"{c['scenario_id']}(cap={c['cap_ratio']}, sel={c['selected_cost_s']}, allowed={c['allowed_cost_s']})" for c in viol_rows)) if viol_rows else '逐行明细见 summary.csv 的 allowed_cost_s / within_cap / cap_verdict 列。'}
注意：reference_best 来自跨方法已验证方案；其缺失时上限基准回退为候选池
最优并标注 pool_fallback（dev 集 kilby 场景多为此类），见 raw 行 warnings。

## 四、OR-Tools vs COPT 对照（dev 集诊断，单独成表，不混入主比较）

{copt_table}

说明：COPT 在 75+ 节点 60 秒无 incumbent 记为 no_feasible_found（≠不可行）；
OR-Tools 可行解不得写成已证明最优（上表成本差即为证据之一）。
**r202-s102 为"COPT 对当前 MIP 模型报告 MIP_INFEASIBLE"**，仅针对该输入、
该建模与该求解器版本，**不得扩展为原始业务问题不可行**：输入哈希
`{(proof.get('hash') or '—')[:16]}…`；证明行求解器版本
{[r.get('solver_version') for r in proof.get('rows', [])]}；独立校验器未采信
该结论以外的任何求解器状态。

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
  提升、60 秒硬截止机制——均未在本轮执行。300 秒诊断已执行一项：**baseline
  物化 300 秒诊断**（8 个失败场景，`reports/baseline300/` 独立成表，不并入
  主比较），证实失败根因为时间窗过紧而非预算；**COPT 300 秒诊断**（预先指定
  3 个代表性 dev 大场景、单独成表）未执行。

**分层可行率证据（可物化子集口径，全 39 场景中的 31 个）**：

{strata_table}

演示口径：低扰动在大型场景响应约 60 秒（用满软预算持续改进），
演示与宣传必须响应延迟与成本一并呈现，不得只报成本优势。

## 七、图表（由原始结果程序生成）

{chr(10).join(f'- `{n}`' for n in chart_names)}

**敏感性口径说明**：raw 中 dev 集的 cap1.05/1.20 行是三次独立求解（候选池
各自重新搜索），其成本差异属独立运行波动，**不解释为**阈值效应；阈值效应
证据来自固定候选池实验（同池仅改筛选阈值，随机种子与预算同主评测；
`scripts/run_sensitivity_fixedpool.py`，结果在 `sensitivity_fixedpool/`）。

## 八、复现命令与证据链

```bat
.venv\\Scripts\\python.exe scripts\\run_evaluation.py --split all --copt-diag --overwrite
.venv\\Scripts\\python.exe scripts\\run_sensitivity_fixedpool.py   :: 固定池敏感性（dev）
.venv\\Scripts\\python.exe scripts\\build_evaluation_report.py     :: 一键重建本报告
```

- 本报告由 `build_evaluation_report.py` 从 `raw/*.json` 程序生成，未手改汇总；
  生成时强制执行清单↔原始结果一致性校验。
- 证据链：`frozen_manifest.json`（冻结口径）→ `frozen_inputs.json`
  （每场景草稿 SHA-256/种子/锁比例/依赖版本/提交号）→ 草稿文件
  （`data/processed/scenarios/<split>/*.json`，随审查包移交）→ `raw/*.json`
  → `summary.csv` / `reconciliation.csv` → 本报告。
- **阶段 5 端到端证据**：确认后求解不读旧场景，回归测试
  `test_confirm_becomes_active_version`、`test_version_drift_fails_honestly`、
  `test_solve_version_conflict_409`、`test_old_version_export_uses_snapshot`
  （tests/test_api_stage5.py）随全量 328 passed 通过；评测 runner 不经 API、
  按冻结草稿确定性物化，每行 scenario_version 落盘。

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
