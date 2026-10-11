"""阶段 6 冻结评测 runner（任务书阶段 6 + 2026-10-11 评测口径决策）。

口径（冻结，先写清单再开跑）：
- 三方法 insert / rebuild / low_disruption，统一 60 秒端到端预算；
- rebuild 主比较默认 OR-Tools；COPT 仅小规模最优性/不可行性验证（诊断，
  只跑 dev 集，单独成表，不混入主比较）；
- 状态如实：TIMEOUT_NO_SOLUTION ≠ 不可行；OR-Tools 可行 ≠ 证明最优；
- dev 集定参数；test 集冻结配置（cost_cap_ratio=1.10）不再调参；
- 每次运行保存输入哈希/状态/指标/异常/物化/求解/校验/端到端耗时；
- 断点续跑：raw 文件已存在则跳过（--overwrite 强制重跑）。

用法：
    .venv\\Scripts\\python.exe scripts\\run_evaluation.py --split dev
    .venv\\Scripts\\python.exe scripts\\run_evaluation.py --scenarios c101-s101,c101-s102
    .venv\\Scripts\\python.exe scripts\\run_evaluation.py --split dev --copt-diag
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.domain.models import Scenario, ScenarioDraft, input_hash  # noqa: E402
from src.solver import pipeline  # noqa: E402
from src.solver.original import materialize_scenario  # noqa: E402

RAW_DIR = ROOT / "reports" / "evaluation6" / "raw"
MANIFEST_PATH = ROOT / "reports" / "evaluation6" / "frozen_manifest.json"

BUDGET_MS = 60_000          # 三方法统一端到端预算（2026-10-11 决策）
COST_CAP = 1.10             # 低扰动参考成本上限（冻结配置）
SENSITIVITY_CAPS = (1.05, 1.20)  # dev 集 110% 附近敏感性（测试集不用）


def _now() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def load_registry() -> list[dict]:
    reg = json.loads((ROOT / "data" / "processed" / "scenarios"
                      / "registry.json").read_text(encoding="utf-8"))
    return reg["scenarios"]


def freeze_manifest(entries: list[dict]) -> None:
    """开跑前冻结：清单/划分/种子/锁定比例/配置/预算。已存在则不覆盖。"""
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    if MANIFEST_PATH.is_file():
        return
    manifest = {
        "frozen_at": _now(),
        "budget_ms": BUDGET_MS,
        "methods": ["insert", "rebuild", "low_disruption"],
        "rebuild_backend_main": "ortools",
        "cost_cap_ratio": COST_CAP,
        "sensitivity_caps_dev_only": list(SENSITIVITY_CAPS),
        "copt_diagnostic": "dev 集 rebuild backend=copt，单独成表",
        "status_semantics": {
            "TIMEOUT/NO_FEASIBLE_FOUND": "不等于不可行",
            "OR-Tools FEASIBLE": "不等于证明最优",
            "PROVEN_INFEASIBLE": "仅 COPT MIP 可给出",
        },
        "scenarios": [
            {"scenario_id": e["scenario_id"], "split": e["split"],
             "source": e.get("config", {}).get("source", "solomon"),
             "base_instance": e["base_instance"],
             "seed": e.get("config", {}).get("seed"),
             "lock_ratio_target": e.get("config", {}).get("lock_ratio_target"),
             "n_new_orders": e.get("config", {}).get("n_new_orders")}
            for e in entries],
    }
    MANIFEST_PATH.write_text(json.dumps(manifest, ensure_ascii=False, indent=1),
                             encoding="utf-8")


def _load_draft(entry: dict) -> tuple[dict, ScenarioDraft]:
    p = ROOT / "data" / "processed" / entry["file"]
    doc = json.loads(p.read_text(encoding="utf-8"))
    return doc, ScenarioDraft(**doc["draft"])


def _row(scn: Scenario, method: str, t0: float,
         outcome=None, error: str | None = None,
         backend: str | None = None, cap: float | None = None) -> dict:
    base = {
        "method": method,
        "backend": backend,
        "cost_cap_ratio": cap,
        "scenario_id": scn.scenario_id,
        "scenario_version": scn.version,
        "scenario_input_hash": input_hash(scn),
        "end_to_end_ms": int((time.monotonic() - t0) * 1000),
        "error": error,
    }
    if outcome is None:
        base.update({"status": "RUNNER_ERROR", "has_solution": False})
        return base
    r = outcome.result
    met = r.metrics
    base.update({
        "status": r.status.value,
        "has_solution": bool(r.routes),
        "input_hash": r.input_hash,
        "solver_version": r.solver_version,
        "solver_status": met.get("solver_status") or met.get("selection_status"),
        "backend_actual": met.get("backend"),
        "total_travel_time_s": met.get("total_travel_time_s"),
        "elapsed_ms": r.elapsed_ms,
        "elapsed_solve_ms": met.get("elapsed_solve_ms"),
        "elapsed_validate_ms": met.get("elapsed_validate_ms"),
        "validation_ok": bool(outcome.validation and outcome.validation.ok),
        "n_routes": len(r.routes),
        "warnings": list(r.warnings),
    })
    if method == "low_disruption":
        rec = met.get("recommended_candidate") or {}
        base.update({
            "vehicle_changes": rec.get("vehicle_changes"),
            "adjacency_breaks": rec.get("adjacency_breaks"),
            "pool_best_cost_s": met.get("pool_best_cost_s"),
            "reference_best_cost_s": met.get("reference_best_cost_s"),
        })
    return base


def run_scenario(entry: dict, copt_diag: bool,
                 overwrite: bool) -> Path | None:
    sid = entry["scenario_id"]
    out_path = RAW_DIR / f"{sid}.json"
    if out_path.is_file() and not overwrite:
        return None
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    doc, draft = _load_draft(entry)
    meta = doc.get("_meta", {})
    t0 = time.monotonic()
    materialize_ms = 0
    try:
        t_mat = time.monotonic()
        scn = materialize_scenario(
            draft, lock_ratio=float(meta.get("lock_ratio_target") or 0.0),
            lock_seed=int(meta.get("seed") or 0),
            time_limit_ms=20_000)
        materialize_ms = int((time.monotonic() - t_mat) * 1000)
    except Exception as exc:
        record = {"scenario_id": sid, "materialize_error":
                  f"{type(exc).__name__}: {exc}", "rows": []}
        out_path.write_text(json.dumps(record, ensure_ascii=False, indent=1),
                            encoding="utf-8")
        return out_path

    rows: list[dict] = []
    references: list = []

    def _solve(method: str, backend: str | None = None,
               cap: float | None = None) -> None:
        nonlocal references
        ts = time.monotonic()
        try:
            kwargs: dict = {}
            if method == "low_disruption":
                kwargs["cost_cap_ratio"] = cap if cap is not None else COST_CAP
                kwargs["reference_results"] = list(references)
            oc = pipeline.solve(scn, method, time_limit_ms=BUDGET_MS,
                                backend=backend, **kwargs)
            rows.append(_row(scn, method, ts, outcome=oc,
                             backend=backend, cap=kwargs.get("cost_cap_ratio")))
            if oc.result.status.value == "feasible" \
                    and oc.validation is not None:
                references.append((method, list(oc.result.routes)))
        except Exception as exc:
            rows.append(_row(scn, method, ts,
                             error=f"{type(exc).__name__}: {exc}"[:300],
                             backend=backend, cap=cap))
            traceback.print_exc()

    for m in ("insert", "rebuild", "low_disruption"):
        _solve(m)  # rebuild 默认 OR-Tools（pipeline BACKEND）
    if copt_diag and entry["split"] == "dev":
        _solve("rebuild", backend="copt")
        for cap in SENSITIVITY_CAPS:  # dev 集 110% 附近敏感性
            _solve("low_disruption", cap=cap)

    record = {
        "scenario_id": sid,
        "split": entry["split"],
        "source": entry.get("config", {}).get("source", "solomon"),
        "base_instance": entry["base_instance"],
        "n_orders": len(scn.orders),
        "n_vehicles": len(scn.vehicles),
        "draft_input_hash": input_hash(draft),
        "scenario_input_hash": input_hash(scn),
        "materialize_ms": materialize_ms,
        "end_to_end_ms": int((time.monotonic() - t0) * 1000),
        "budget_ms": BUDGET_MS,
        "rows": rows,
    }
    out_path.write_text(json.dumps(record, ensure_ascii=False, indent=1),
                        encoding="utf-8")
    return out_path


def main() -> None:
    ap = argparse.ArgumentParser(description="阶段 6 冻结评测")
    ap.add_argument("--split", choices=["dev", "test", "all"],
                    default="all")
    ap.add_argument("--scenarios", default=None,
                    help="逗号分隔的 scenario_id 列表（优先于 --split）")
    ap.add_argument("--copt-diag", action="store_true",
                    help="dev 集加跑 COPT 诊断与成本上限敏感性")
    ap.add_argument("--overwrite", action="store_true")
    args = ap.parse_args()

    entries = load_registry()
    freeze_manifest(entries)  # 首次运行先冻结清单
    if args.scenarios:
        wanted = set(args.scenarios.split(","))
        entries = [e for e in entries if e["scenario_id"] in wanted]
    elif args.split != "all":
        entries = [e for e in entries if e["split"] == args.split]
    if not entries:
        print("E_NO_SCENARIOS: 没有匹配的场景")
        sys.exit(1)

    done, skipped = 0, 0
    for e in entries:
        p = run_scenario(e, args.copt_diag, args.overwrite)
        if p is None:
            skipped += 1
            print(f"skip {e['scenario_id']}（已有 raw，--overwrite 重跑）")
        else:
            done += 1
            print(f"done {e['scenario_id']} -> {p.name}")
    print(f"完成 {done}，跳过 {skipped}；清单 {MANIFEST_PATH.name}")


if __name__ == "__main__":
    main()
