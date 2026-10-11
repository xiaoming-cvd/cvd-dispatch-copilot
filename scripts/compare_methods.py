"""三方法同输入对比（第三阶段复审任务五）。

同一场景、同一输入哈希、同约束、同一预算依次运行 insert / rebuild /
low_disruption；low_disruption 自动获得先跑方法的已验证方案作为
reference_best_cost。输出每个方法的成本、扰动、耗时、终止状态、
验证结果与是否满足 reference 成本上限。

用法：
    .venv\\Scripts\\python.exe scripts\\compare_methods.py --input <场景文件>
        [--time-limit-ms 15000] [--cost-cap-ratio 1.10] [--out <目录>]

输出：<out>/compare_<场景ID>.json 与 summary.md（默认 reports/method_compare/）
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pydantic import ValidationError  # noqa: E402

from src.domain.models import Scenario, ScenarioDraft, input_hash  # noqa: E402
from src.solver.original import materialize_scenario  # noqa: E402
from src.solver.pipeline import compare_methods  # noqa: E402


def _load(path: Path):
    data = json.loads(path.read_text(encoding="utf-8"))
    if "scenario" in data:
        return Scenario(**data["scenario"]), None, data.get("_meta", {})
    if "draft" in data:
        return None, ScenarioDraft(**data["draft"]), data.get("_meta", {})
    raise ValueError("E_INPUT: 文件既无 'scenario' 也无 'draft' 顶层键")


def _row(method: str, outcome) -> dict:
    r = outcome.result
    met = r.metrics
    row = {
        "method": method,
        "status": r.status.value,
        "input_hash": r.input_hash,
        "solver_version": r.solver_version,
        "termination": met.get("solver_status") or met.get("selection_status"),
        "total_travel_time_s": met.get("total_travel_time_s"),
        "elapsed_ms": r.elapsed_ms,
        "elapsed_solve_ms": met.get("elapsed_solve_ms"),
        "validation_ok": bool(outcome.validation and outcome.validation.ok),
        "warnings": list(r.warnings),
    }
    if method == "low_disruption":
        row.update({
            "vehicle_changes": met.get("recommended_candidate", {}).get("vehicle_changes"),
            "adjacency_breaks": met.get("recommended_candidate", {}).get("adjacency_breaks"),
            "pool_best_cost_s": met.get("pool_best_cost_s"),
            "reference_best_cost_s": met.get("reference_best_cost_s"),
            "reference_source": met.get("reference_source"),
            "cost_cap_ratio": met.get("cost_cap_ratio"),
            "cost_cap_s": met.get("cost_cap_s"),
            "within_reference_cap": met.get("within_reference_cap"),
            "candidate_count": met.get("candidate_count"),
            "search_stop_reason": met.get("search_stop_reason"),
        })
    return row


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="三方法同输入对比")
    ap.add_argument("--input", required=True, type=Path)
    ap.add_argument("--time-limit-ms", type=int, default=15_000)
    ap.add_argument("--cost-cap-ratio", type=float, default=None)
    ap.add_argument("--out", type=Path,
                    default=ROOT / "reports" / "method_compare")
    args = ap.parse_args(argv)

    try:
        scn, draft, meta = _load(args.input)
    except (OSError, json.JSONDecodeError, ValueError, ValidationError) as exc:
        print(json.dumps({"status": "error", "warnings": [str(exc)]},
                         ensure_ascii=False))
        return 1
    if draft is not None:
        scn = materialize_scenario(
            draft, lock_ratio=float(meta.get("lock_ratio_target") or 0.0),
            lock_seed=int(meta.get("seed") or 0),
            time_limit_ms=args.time_limit_ms)

    outcomes = compare_methods(
        scn, time_limit_ms=args.time_limit_ms,
        cost_cap_ratio=args.cost_cap_ratio)
    rows = [_row(m, oc) for m, oc in outcomes.items()]

    args.out.mkdir(parents=True, exist_ok=True)
    payload = {
        "scenario_id": scn.scenario_id,
        "input_hash": input_hash(scn),
        "time_limit_ms": args.time_limit_ms,
        "cost_cap_ratio": args.cost_cap_ratio,
        "methods": rows,
    }
    out_json = args.out / f"compare_{scn.scenario_id}.json"
    out_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                        encoding="utf-8")

    lines = [f"# 三方法同输入对比 · {scn.scenario_id}",
             f"- input_hash: `{payload['input_hash']}`  预算: {args.time_limit_ms}ms",
             "", "| 方法 | 状态 | 成本(s) | 扰动(换车/破坏) | 耗时(ms) | 验证 | 终止 |",
             "|---|---|---|---|---|---|---|"]
    for row in rows:
        pert = ""
        if row.get("vehicle_changes") is not None:
            pert = f"{row['vehicle_changes']}/{row['adjacency_breaks']}"
        lines.append(
            f"| {row['method']} | {row['status']} | {row['total_travel_time_s']} "
            f"| {pert or '-'} | {row['elapsed_ms']} | "
            f"{'ok' if row['validation_ok'] else 'FAIL'} | {row['termination']} |")
    low = next((r for r in rows if r["method"] == "low_disruption"), None)
    if low:
        lines += ["", "## 成本口径（第三阶段复审 P0-1）",
                  f"- pool_best_cost_s = {low['pool_best_cost_s']}",
                  f"- reference_best_cost_s = {low['reference_best_cost_s']}（{low['reference_source']}）",
                  f"- cost_cap = {low['cost_cap_ratio']} × reference = {low['cost_cap_s']}",
                  f"- within_reference_cap = {low['within_reference_cap']} "
                  f"（selection_status={low['termination']}）"]
    (args.out / f"summary_{scn.scenario_id}.md").write_text(
        "\n".join(lines), encoding="utf-8")
    print(json.dumps({"rows": rows, "out": str(out_json)}, ensure_ascii=False,
                     indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
