"""baseline 300 秒预算诊断实验（2026-10-12 队长决策）。

对象：60 秒预算内 baseline 原计划生成失败的 8 个场景（与冻结评测记录的
E_PLAN_NOT_FOUND 同源）。目的：诊断这些场景的基线生成瓶颈是"时间不够"
还是"建模/实例本身难"。

红线：
- 独立成表：结果只写 reports/baseline300/，**不并入** 60 秒三方法主比较，
  不改冻结评测任何数字；
- 诚实语义：300 秒内未找到 = TIMEOUT_NO_SOLUTION（≠ 证明无解）；
- 找到的方案保存为场景 v1（与正常 baseline 同一代码路径），场景列表的
  "事前参考"列随之显示成本，但报告标注"300s 诊断口径"；
- 幂等可续跑：每场景完成即落盘 results.json，重跑自动跳过已完成项。

用法：
    .venv\\Scripts\\python.exe scripts\\diag_baseline_300s.py            # 全部待办
    .venv\\Scripts\\python.exe scripts\\diag_baseline_300s.py --only r101-s101
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.api.app import ScenarioIndex, Settings          # noqa: E402
from src.domain.models import ScenarioDraft              # noqa: E402
from src.llm.config import LLMConfig                     # noqa: E402
from src.solver.original import materialize_scenario     # noqa: E402
from src.validation.checks import validate_draft         # noqa: E402
from src.validation.timetable import evaluate_original_plan  # noqa: E402

BUDGET_MS = 300_000  # 5 分钟诊断预算（仅本实验，非主评测口径）
OUT_DIR = ROOT / "reports" / "baseline300"
RESULTS = OUT_DIR / "results.json"
SUMMARY = OUT_DIR / "summary.csv"
REPORT = OUT_DIR / "diagnostic_report.md"


def _load_results() -> dict:
    if RESULTS.is_file():
        return json.loads(RESULTS.read_text(encoding="utf-8"))
    return {"started_at": datetime.now(tz=timezone.utc).isoformat(),
            "budget_ms": BUDGET_MS, "scenarios": {}}


def _save(results: dict) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    tmp = RESULTS.with_suffix(".tmp")
    tmp.write_text(json.dumps(results, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    tmp.replace(RESULTS)


def _write_summary_and_report(results: dict) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    rows = results["scenarios"]
    with SUMMARY.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["scenario_id", "status", "budget_ms", "elapsed_ms",
                    "plan_total_travel_time_s", "n_routes", "version",
                    "saved", "finished_at", "note"])
        for sid, r in sorted(rows.items()):
            w.writerow([sid, r["status"], r["budget_ms"], r["elapsed_ms"],
                        r.get("plan_total_travel_time_s"), r.get("n_routes"),
                        r.get("version"), r.get("saved"),
                        r.get("finished_at"), r.get("note", "")])

    n_ok = sum(1 for r in rows.values() if r["status"] == "FOUND")
    n_to = sum(1 for r in rows.values() if r["status"] == "TIMEOUT_NO_SOLUTION")
    lines = [
        "# baseline 300 秒预算诊断实验（独立成表）",
        "",
        f"- 生成：{results['started_at']}（UTC）",
        f"- 预算：每场景 {BUDGET_MS} ms（**诊断口径，非 60 秒主评测口径**）",
        "- 对象：60s 预算内 baseline 失败的 8 个场景（冻结评测同源 "
        "E_PLAN_NOT_FOUND）",
        "- 语义：TIMEOUT_NO_SOLUTION ≠ 证明无解；不并入三方法主比较，"
        "不改冻结评测任何数字",
        f"- 结果：FOUND {n_ok} · TIMEOUT_NO_SOLUTION {n_to} · "
        f"合计 {len(rows)}",
        "",
        "| 场景 | 状态 | 耗时 ms | 原计划成本(模型秒) | 路线数 | 版本 |",
        "|---|---|---|---|---|---|",
    ]
    for sid, r in sorted(rows.items()):
        lines.append(f"| {sid} | {r['status']} | {r['elapsed_ms']} | "
                     f"{r.get('plan_total_travel_time_s') or '—'} | "
                     f"{r.get('n_routes') or '—'} | "
                     f"{r.get('version') or '—'} |")
    lines += ["",
              "机器可读明细：`results.json`（逐场景）/`summary.csv`（表格）。",
              "原始记录：本表所有数字可由 `scripts/diag_baseline_300s.py` "
              "续跑复现；保存的物化场景在 `data/processed/scenarios/"
              "_materialized/<sid>.json`（v1，300s 诊断口径生成）。"]
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description="baseline 300s 诊断实验")
    ap.add_argument("--only", help="只跑指定场景（默认跑全部未完成）")
    args = ap.parse_args()

    settings = Settings(project_root=ROOT, llm=LLMConfig(provider="mock"))
    index = ScenarioIndex(settings)
    results = _load_results()

    todo = []
    for row in index.list():
        sid = row["scenario_id"]
        if args.only and sid != args.only:
            continue
        if sid in results["scenarios"]:
            continue  # 已完成，幂等跳过
        if row.get("materialized"):
            # 已被其他流程物化：补记为 FOUND（不复跑）
            try:
                scn = index.load_scenario(sid)
                cost, _ = evaluate_original_plan(scn)
            except Exception:
                cost = None
            results["scenarios"][sid] = {
                "status": "FOUND", "budget_ms": BUDGET_MS,
                "elapsed_ms": None, "plan_total_travel_time_s": cost,
                "n_routes": len(scn.original_plan.routes),
                "version": scn.version, "saved": True,
                "finished_at": datetime.now(tz=timezone.utc).isoformat(),
                "note": "预热脚本/正常 baseline 已生成，补记",
            }
            _save(results)
            continue
        todo.append(sid)

    print(f"待诊断 {len(todo)} 个场景，预算 {BUDGET_MS}ms/个")
    for sid in todo:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {sid} 开始…", flush=True)
        t0 = time.monotonic()
        try:
            doc = index.load_doc(sid)
            draft = ScenarioDraft(**doc["draft"])
            report = validate_draft(draft)
            if not report.ok:
                raise ValueError("草稿校验未通过："
                                 + "; ".join(i.code for i in report.issues))
            meta = doc.get("_meta") or {}
            scn = materialize_scenario(
                draft, lock_ratio=float(meta.get("lock_ratio_target") or 0.0),
                lock_seed=int(meta.get("seed") or 0),
                time_limit_ms=BUDGET_MS)
            elapsed = int((time.monotonic() - t0) * 1000)
            index.save_materialized(scn)
            cost, details = evaluate_original_plan(scn)
            bad = [d for d in details if not d["used"]]
            results["scenarios"][sid] = {
                "status": "FOUND", "budget_ms": BUDGET_MS,
                "elapsed_ms": elapsed,
                "plan_total_travel_time_s": cost,
                "n_routes": len(details), "version": scn.version,
                "saved": True,
                "finished_at": datetime.now(tz=timezone.utc).isoformat(),
                "note": ("原计划自身路线违规，成本仅参考" if bad else
                         "300s 诊断口径生成"),
            }
            print(f"  {sid}: FOUND 成本={cost} 模型秒 ({elapsed}ms)", flush=True)
        except Exception as exc:
            elapsed = int((time.monotonic() - t0) * 1000)
            msg = f"{type(exc).__name__}: {exc}"
            status = ("TIMEOUT_NO_SOLUTION"
                      if "E_PLAN_NOT_FOUND" in msg or "预算" in msg
                      else "ERROR")
            results["scenarios"][sid] = {
                "status": status, "budget_ms": BUDGET_MS,
                "elapsed_ms": elapsed, "plan_total_travel_time_s": None,
                "n_routes": None, "version": None, "saved": False,
                "finished_at": datetime.now(tz=timezone.utc).isoformat(),
                "note": msg[:300],
            }
            print(f"  {sid}: {status} ({elapsed}ms) {msg[:120]}", flush=True)
        _save(results)  # 每场景落盘，断点可续

    _write_summary_and_report(results)
    done = results["scenarios"]
    print(f"\n汇总：FOUND {sum(1 for r in done.values() if r['status'] == 'FOUND')}"
          f" · TIMEOUT {sum(1 for r in done.values() if r['status'] == 'TIMEOUT_NO_SOLUTION')}"
          f" · 共 {len(done)}；报告 {REPORT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
