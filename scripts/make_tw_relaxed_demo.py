"""时间窗放宽演示场景生成 + 基线物化 + 三方法试跑（2026-10-12 队长决策）。

背景：8 个 tight 时间窗场景（冻结评测 E_PLAN_NOT_FOUND 同源）经 300 秒诊断
证实根因为"初解构造失败——时间窗宽度 < 服务时长，老订单自身即排不出合法
原计划"，与预算无关（reports/baseline300/）。本脚本按探针同方向把
时间窗放宽，生成 **8 个新场景**（原场景一字不动），验证并展示"数据修
到合理紧度后系统全链路可用"。

红线：
- 原 8 个冻结草稿/冻结评测一律不动；新场景为独立 ID（<sid>-loose），
  split=demo，不进入 evaluation6 的任何口径；
- 放宽规则如实记录：due_s += RELAX_S、return_by_s += RELAX_S（先 5400，
  个别仍失败则升级 10800，逐场景记录实际用量）；
- 结果独立成表 reports/tw_relax_demo/，标注"演示口径"；
- 幂等：已存在的草稿/物化结果跳过，重跑不重复。

用法：
    .venv\\Scripts\\python.exe scripts\\make_tw_relaxed_demo.py
"""
from __future__ import annotations

import csv
import json
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

import sys
sys.path.insert(0, str(ROOT))

from src.api.app import ScenarioIndex, Settings          # noqa: E402
from src.domain.models import (ScenarioDraft, SolveStatus,  # noqa: E402
                               input_hash)
from src.llm.config import LLMConfig                     # noqa: E402
from src.solver import pipeline                          # noqa: E402
from src.solver.original import materialize_scenario     # noqa: E402
from src.validation.checks import validate_draft         # noqa: E402
from src.validation.timetable import evaluate_original_plan  # noqa: E402

BUDGET_MS = 60_000            # 与产品默认一致（演示口径，非冻结评测）
RELAX_STEPS = [5_400, 10_800, 21_600]  # 放宽量级（模型秒）：1/2/4 个服务时长
OUT_DIR = ROOT / "reports" / "tw_relax_demo"
RESULTS = OUT_DIR / "results.json"
SUMMARY = OUT_DIR / "summary.csv"

# 300 秒诊断证实的 8 个紧时间窗场景（dev 7 + test 1）
ORIGINS = [
    ("c101-s102", "dev"), ("c102-s102", "dev"), ("r101-s101", "dev"),
    ("r101-s102", "dev"), ("r102-s101", "dev"), ("r102-s102", "dev"),
    ("r201-s102", "dev"), ("rc101-s201", "test"),
]


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _relaxed_draft(doc: dict, relax_s: int) -> dict:
    """复制草稿并把每个订单 due_s、每辆车 return_by_s 放宽 relax_s 秒。"""
    new = json.loads(json.dumps(doc))  # 深拷贝，绝不改原稿
    d = new["draft"]
    for o in d["orders"]:
        o["due_s"] = o["due_s"] + relax_s
    for v in d["vehicles"]:
        v["return_by_s"] = v["return_by_s"] + relax_s
    d["scenario_id"] = d["scenario_id"] + "-loose"
    return new


def _register(settings: Settings, sid: str, base_instance: str,
              orig_cfg: dict, relax_s: int, file_rel: str,
              draft_hash: str) -> None:
    reg_path = settings.registry_path
    reg = json.loads(reg_path.read_text(encoding="utf-8"))
    if any(e["scenario_id"] == sid for e in reg["scenarios"]):
        return
    cfg = dict(orig_cfg)
    cfg["tw_tightness"] = f"relaxed(+{relax_s}s)"
    reg["scenarios"].append({
        "scenario_id": sid, "base_instance": base_instance,
        "split": "demo", "config": cfg, "file": file_rel,
        "draft_input_hash": draft_hash})
    _atomic_write(reg_path, json.dumps(reg, ensure_ascii=False, indent=1))


def _run_methods(scn) -> dict:
    """三方法试跑（60s 演示口径；low_disruption 用产品参考链）+ rebuild
    OR-Tools 失败时的 COPT 诊断（可选后端，如实记录；无许可环境记 ERROR，
    不静默回退——与阶段5决策一致）。"""
    methods = {}
    for meth in ("insert", "rebuild", "low_disruption"):
        t1 = time.monotonic()
        try:
            out = pipeline.solve(
                scn, meth, BUDGET_MS,
                backend=("ortools" if meth == "rebuild" else None),
                use_baseline_reference=(meth == "low_disruption"))
            res = out.result  # SolveResult：单方法结果（无 rows 聚合）
            methods[meth] = {
                "status": res.status.value,
                "has_solution": res.status == SolveStatus.FEASIBLE,
                "total_travel_time_s": res.metrics.get(
                    "total_travel_time_s"),
                "elapsed_ms": res.elapsed_ms,
                "backend": res.metrics.get("backend"),
                "validation_ok": res.validation.get("independent_ok"),
                "solver_status": res.metrics.get("solver_status"),
                "wall_ms": int((time.monotonic() - t1) * 1000),
            }
        except Exception as exc:  # 如实记录异常，不静默
            methods[meth] = {"status": "ERROR",
                             "error": f"{type(exc).__name__}: {exc}"[:200],
                             "wall_ms": int((time.monotonic() - t1) * 1000)}
    if not methods.get("rebuild", {}).get("has_solution"):
        t2 = time.monotonic()
        try:
            rc = pipeline.solve(scn, "rebuild", BUDGET_MS,
                                backend="copt").result
            methods["rebuild_copt_diag"] = {
                "status": rc.status.value,
                "has_solution": rc.status == SolveStatus.FEASIBLE,
                "total_travel_time_s": rc.metrics.get("total_travel_time_s"),
                "elapsed_ms": rc.elapsed_ms,
                "backend": rc.metrics.get("backend"),
                "validation_ok": rc.validation.get("independent_ok"),
                "solver_status": rc.metrics.get("solver_status"),
                "wall_ms": int((time.monotonic() - t2) * 1000),
            }
        except Exception as exc:
            methods["rebuild_copt_diag"] = {
                "status": "ERROR",
                "error": f"{type(exc).__name__}: {exc}"[:200],
                "wall_ms": int((time.monotonic() - t2) * 1000)}
    return methods


def main() -> int:
    settings = Settings(project_root=ROOT, llm=LLMConfig(provider="mock"))
    index = ScenarioIndex(settings)
    results = {"started_at": datetime.now(tz=timezone.utc).isoformat(),
               "budget_ms": BUDGET_MS, "scenarios": {}}
    if RESULTS.is_file():  # 幂等续跑
        results = json.loads(RESULTS.read_text(encoding="utf-8"))

    for sid, split in ORIGINS:
        new_sid = f"{sid}-loose"
        if new_sid in results["scenarios"]:
            print(f"  {new_sid}: 已完成，跳过")
            continue
        doc = json.loads((settings.project_root / "data" / "processed"
                          / "scenarios" / split / f"{sid}.json")
                         .read_text(encoding="utf-8"))
        meta = doc.get("_meta") or {}
        entry = {"origin": sid, "origin_split": split}

        # 1) 逐级放宽：基线可构造且至少一个方法找到可行解即停；
        #    全部阶梯都试过仍无可行解则保留最后一级结果（如实记录，不硬凑）
        draft_path = (settings.project_root / "data" / "processed"
                      / "scenarios" / "demo" / f"{new_sid}.json")
        chosen = None  # (relax_s, new_doc, scn, methods)
        last = None
        for relax_s in RELAX_STEPS:
            new_doc = _relaxed_draft(doc, relax_s)
            draft = ScenarioDraft(**new_doc["draft"])
            report = validate_draft(draft)
            if not report.ok:
                raise SystemExit(f"FAIL {new_sid}: 放宽后草稿校验不通过："
                                 + "; ".join(i.code for i in report.issues))
            try:
                scn = materialize_scenario(
                    draft, lock_ratio=float(meta.get("lock_ratio_target") or 0.0),
                    lock_seed=int(meta.get("seed") or 0),
                    time_limit_ms=BUDGET_MS)
            except ValueError:
                continue  # 基线仍构造不出：升级放宽量级
            methods = _run_methods(scn)
            last = (relax_s, new_doc, scn, methods)
            n_ok = sum(1 for m in methods.values() if m.get("has_solution"))
            if n_ok >= 1:
                chosen = last
                break
            print(f"  {new_sid}: +{relax_s}s 三方法均不可行，升级放宽量级",
                  flush=True)
        if last is None:
            entry.update(status="STILL_FAIL",
                         note="全部放宽阶梯后基线仍构造失败（如实记录，不硬凑）")
            results["scenarios"][new_sid] = entry
            _atomic_write(RESULTS, json.dumps(results, ensure_ascii=False,
                                              indent=1))
            print(f"  {new_sid}: STILL_FAIL")
            continue
        relax_used, new_doc, scn, methods = chosen or last

        # 2) 落盘草稿 + 注册（幂等）
        new_doc["_meta"] = dict(meta)
        new_doc["_meta"]["relaxed_from"] = sid
        new_doc["_meta"]["relax_rule"] = (f"due_s += {relax_used} 模型秒；"
                                          f"return_by_s += {relax_used}")
        new_doc["_meta"]["note"] = (
            (meta.get("note") or "") + "；时间窗放宽演示版（根因与证据见 "
            "reports/baseline300/）")
        if not draft_path.is_file():
            _atomic_write(draft_path,
                          json.dumps(new_doc, ensure_ascii=False, indent=1))
        reg = json.loads(settings.registry_path.read_text(encoding="utf-8"))
        orig_entry = next(e for e in reg["scenarios"]
                          if e["scenario_id"] == sid)
        _register(settings, new_sid, orig_entry["base_instance"],
                  orig_entry.get("config", {}), relax_used,
                  f"scenarios/demo/{new_sid}.json",
                  input_hash(ScenarioDraft(**new_doc["draft"])))

        # 3) 物化基线（与产品同一代码路径）
        t0 = time.monotonic()
        index.save_materialized(scn)
        mat_ms = int((time.monotonic() - t0) * 1000)
        plan_cost, _ = evaluate_original_plan(scn)

        entry.update(status="OK", relax_s=relax_used,
                     version=scn.version, materialize_ms=mat_ms,
                     plan_total_travel_time_s=plan_cost,
                     n_plan_routes=len(scn.original_plan.routes),
                     scenario_input_hash=input_hash(scn), methods=methods)
        results["scenarios"][new_sid] = entry
        _atomic_write(RESULTS, json.dumps(results, ensure_ascii=False,
                                          indent=1))
        ok = sum(1 for m in methods.values() if m.get("has_solution"))
        print(f"  {new_sid}: 放宽+{relax_used}s · 基线成本={plan_cost} 模型秒 "
              f"({mat_ms}ms) · 方法可行 {ok}/{len(methods)}", flush=True)

    # 汇总表（独立成表，不并入 evaluation6）
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with SUMMARY.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["scenario_id", "origin", "relax_s", "version",
                    "baseline_cost_s", "n_plan_routes", "method", "status",
                    "has_solution", "total_travel_time_s", "elapsed_ms",
                    "backend", "validation_ok", "solver_status"])
        for new_sid, e in sorted(results["scenarios"].items()):
            if e.get("status") != "OK":
                w.writerow([new_sid, e.get("origin"), "", "", "", "",
                            "—", e.get("status"), "", "", "", "", "", ""])
                continue
            for meth, m in sorted(e["methods"].items()):
                w.writerow([new_sid, e["origin"], e["relax_s"], e["version"],
                            e["plan_total_travel_time_s"], e["n_plan_routes"],
                            meth, m.get("status"), m.get("has_solution"),
                            m.get("total_travel_time_s"), m.get("elapsed_ms"),
                            m.get("backend"), m.get("validation_ok"),
                            m.get("solver_status")])
    print(f"\n汇总 -> {SUMMARY}")
    print(f"明细 -> {RESULTS}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
