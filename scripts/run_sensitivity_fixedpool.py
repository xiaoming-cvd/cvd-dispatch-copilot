"""阶段 6 审查 P1 修复：固定候选池的成本上限敏感性（dev 集）。

背景：主评测中的 dev 敏感性（run_evaluation.py 的 SENSITIVITY_CAPS）是
三次独立求解——候选池各自重新搜索，成本随 cap 的变化不能解释为单一候选
池上的筛选阈值效应（2026-10-11 第六阶段审查）。

本实验：同一场景用与主评测相同的预算（60s）与默认参数生成一次候选池，
再在同一池上分别按 cap ∈ {1.05, 1.10, 1.20} 选择——固定候选池与随机种子，
仅改变筛选阈值。单独成目录，不改主评测 raw，不碰 test 集。

输出：reports/evaluation6/sensitivity_fixedpool/<sid>.json
用法：./.venv/Scripts/python.exe scripts/run_sensitivity_fixedpool.py \
        --only kilby-c100-s101,kilby-c120-s101   # 分批跑（每场景 LD 60s）
      不带 --only 则跑 dev 集全部物化成功场景（跳过已有结果，--overwrite 重跑）
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.solver import pipeline  # noqa: E402
from src.solver.low_disruption import (  # noqa: E402
    _reference_best_cost, generate_candidates, select_from_pool)
from src.solver.original import materialize_scenario  # noqa: E402
from src.domain.models import ScenarioDraft, input_hash  # noqa: E402

EVAL_DIR = ROOT / "reports" / "evaluation6"
OUT_DIR = EVAL_DIR / "sensitivity_fixedpool"
BUDGET_MS = 60_000
CAPS = (1.05, 1.10, 1.20)
SCRIPT_VERSION = "sensitivity-fixedpool/1.0"


def _git_commit() -> str:
    import subprocess
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
            capture_output=True, text=True, timeout=10).stdout.strip() or "?"
    except Exception:
        return "?"


def run_one(sid: str, split: str, overwrite: bool) -> Path | None:
    out_path = OUT_DIR / f"{sid}.json"
    if out_path.is_file() and not overwrite:
        return None
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    doc_path = ROOT / "data" / "processed" / "scenarios" / split / f"{sid}.json"
    doc = json.loads(doc_path.read_text(encoding="utf-8"))
    draft = ScenarioDraft(**doc["draft"])
    meta = doc.get("_meta", {})

    t0 = time.monotonic()
    rec: dict = {
        "experiment": SCRIPT_VERSION,
        "scenario_id": sid, "split": split,
        "source": meta.get("source") or meta.get("base_instance"),
        "base_instance": meta.get("base_instance"),
        "seed": meta.get("seed"),
        "lock_ratio_target": meta.get("lock_ratio_target"),
        "budget_ms": BUDGET_MS, "caps": list(CAPS),
        "git_commit": _git_commit(),
        "scenario_draft_hash": input_hash(draft),
    }
    try:
        scn = materialize_scenario(
            draft, lock_ratio=float(meta.get("lock_ratio_target") or 0.0),
            lock_seed=int(meta.get("seed") or 0), time_limit_ms=20_000)
        rec["scenario_input_hash"] = input_hash(scn)
        rec["n_orders"] = len(draft.orders)

        # 参考成本：与主评测同协议——insert / rebuild(OR-Tools) 的可行已校验方案
        references: list = []
        ref_runs = {}
        for method, backend in (("insert", None), ("rebuild", None)):
            try:
                oc = pipeline.solve(scn, method, time_limit_ms=BUDGET_MS,
                                    backend=backend)
                ok = oc.result.status.value == "feasible" \
                    and oc.validation is not None and oc.validation.ok
                ref_runs[method] = {"status": oc.result.status.value,
                                    "cost_s": (oc.result.metrics or {}).get(
                                        "total_travel_time_s"),
                                    "validation_ok": bool(ok)}
                if ok:
                    references.append((method, list(oc.result.routes)))
            except Exception as exc:
                ref_runs[method] = {"status": "ERROR",
                                    "error": f"{type(exc).__name__}: {exc}"[:200]}
        rec["reference_runs"] = ref_runs
        ref_best, ref_details = _reference_best_cost(scn, references)
        rec["reference_best_cost_s"] = ref_best
        rec["reference_details"] = ref_details

        # 固定候选池：生成一次（同预算同默认参数），多个 cap 分别只筛选
        candidates, gen_diag = generate_candidates(scn, BUDGET_MS)
        rec["pool"] = {"n_candidates": len(candidates),
                       "elapsed_ms": gen_diag.get("elapsed_ms")}
        per_cap = {}
        for cap in CAPS:
            low, pool_best, selection = select_from_pool(
                list(candidates), cap, ref_best)
            per_cap[str(cap)] = {
                "selection_status": selection["selection_status"],
                "within_cap": selection.get("within_reference_cap"),
                "allowed_cost_s": selection.get("cost_cap_s"),
                "selected_cost_s": low.total_travel_s if low else None,
                "pool_best_cost_s": (pool_best.total_travel_s
                                     if pool_best else None),
                "vehicle_changes": (low.perturbation.vehicle_changes
                                    if low else None),
                "adjacency_breaks": (low.perturbation.adjacency_breaks
                                     if low else None),
            }
        rec["per_cap"] = per_cap
        rec["elapsed_total_ms"] = int((time.monotonic() - t0) * 1000)
    except Exception as exc:
        rec["error"] = f"{type(exc).__name__}: {exc}"
        traceback.print_exc()
    out_path.write_text(json.dumps(rec, ensure_ascii=False, indent=1),
                        encoding="utf-8")
    print(f"[{sid}] pool={rec.get('pool')} ref_best={rec.get('reference_best_cost_s')}"
          f" err={rec.get('error')}")
    return out_path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default=None,
                    help="逗号分隔的 scenario_id，只跑这些（分批用）")
    ap.add_argument("--overwrite", action="store_true")
    args = ap.parse_args()

    manifest = json.loads((EVAL_DIR / "frozen_manifest.json")
                          .read_text(encoding="utf-8"))
    entries = [e for e in manifest["scenarios"] if e["split"] == "dev"]
    if args.only:
        want = set(args.only.split(","))
        entries = [e for e in entries if e["scenario_id"] in want]
    # dev 集中物化失败的场景跳过（raw 有 materialize_error 记录）
    for e in entries:
        raw_path = EVAL_DIR / "raw" / f"{e['scenario_id']}.json"
        if raw_path.is_file():
            raw = json.loads(raw_path.read_text(encoding="utf-8"))
            if raw.get("materialize_error"):
                print(f"[{e['scenario_id']}] 物化失败场景，跳过（见 raw 记录）")
                continue
        run_one(e["scenario_id"], e["split"], args.overwrite)


if __name__ == "__main__":
    main()
