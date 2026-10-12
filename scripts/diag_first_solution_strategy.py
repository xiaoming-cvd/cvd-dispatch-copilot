"""首解策略对比诊断：8 个 baseline 秒级 FAIL 场景 × 多种首解策略。

背景：300s 诊断实验（scripts/diag_baseline_300s.py）发现 8 个紧窗场景
的 baseline 生成在 0~92ms 内返回 ROUTING_FAIL——不是超时，而是
PARALLEL_CHEAPEST_INSERTION 首解策略连初始解都构造不出，GLS 从未启动。
本实验对同一场景草稿逐一换用其他首解策略（monkeypatch 模块常量，
不动产品默认值），观察是否存在可构造初解的策略。

注意：本实验只做诊断，不作为产品行为变更依据；若采用回退链策略属产品
决策，需另行评审（会影响 rebuild 与 baseline 结果，波及冻结评测可复现性）。

用法：
    .venv\\Scripts\\python.exe scripts\\diag_first_solution_strategy.py
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ortools.constraint_solver import routing_enums_pb2  # noqa: E402

from src.api.app import ScenarioIndex, Settings          # noqa: E402
from src.domain.models import OriginalPlan, ScenarioDraft  # noqa: E402
from src.llm.config import LLMConfig                     # noqa: E402
from src.solver import ortools_core                      # noqa: E402

FAILED8 = ["c101-s102", "c102-s102", "r101-s101", "r101-s102",
           "r102-s101", "r102-s102", "r201-s102", "rc101-s201"]

STRATEGIES = {
    "PARALLEL_CHEAPEST_INSERTION":
        routing_enums_pb2.FirstSolutionStrategy.PARALLEL_CHEAPEST_INSERTION,
    "SEQUENTIAL_CHEAPEST_INSERTION":
        routing_enums_pb2.FirstSolutionStrategy.SEQUENTIAL_CHEAPEST_INSERTION,
    "LOCAL_CHEAPEST_INSERTION":
        routing_enums_pb2.FirstSolutionStrategy.LOCAL_CHEAPEST_INSERTION,
    "BEST_INSERTION":
        routing_enums_pb2.FirstSolutionStrategy.BEST_INSERTION,
    "AUTOMATIC":
        routing_enums_pb2.FirstSolutionStrategy.AUTOMATIC,
    "PATH_MOST_CONSTRAINED_ARC":
        routing_enums_pb2.FirstSolutionStrategy.PATH_MOST_CONSTRAINED_ARC,
}

BUDGET_MS = 15_000  # 策略对比用短预算：构造不出初解的策略会在此时间内 FAIL/超时


def main() -> int:
    ap = argparse.ArgumentParser(description="首解策略对比诊断")
    ap.add_argument("--only", help="只跑指定场景，逗号分隔（默认 8 个全跑）")
    a = ap.parse_args()
    todo = a.only.split(",") if a.only else FAILED8

    settings = Settings(project_root=ROOT, llm=LLMConfig(provider="mock"))
    index = ScenarioIndex(settings)
    dest = ROOT / "reports" / "baseline300" / "strategy_sweep.json"
    out: dict = json.loads(dest.read_text(encoding="utf-8")) \
        if dest.is_file() else {}
    for sid in todo:
        doc = index.load_doc(sid)
        draft = ScenarioDraft(**doc["draft"])
        old_ids = [o.order_id for o in draft.orders if not o.is_new]
        tmp = draft.to_scenario(OriginalPlan(
            plan_id="tmp", version=1, routes=[
                {"vehicle_id": draft.vehicles[0].vehicle_id,
                 "order_ids": [old_ids[0]]}]))
        row: dict[str, dict] = {}
        for name, strat in STRATEGIES.items():
            ortools_core.FIRST_SOLUTION_STRATEGY = strat
            t0 = time.monotonic()
            try:
                outcome = ortools_core.solve_vrptw(
                    tmp, old_ids, BUDGET_MS, respect_locks=False)
                elapsed = int((time.monotonic() - t0) * 1000)
                served = [oid for seq in outcome.routes for oid in seq]
                row[name] = {
                    "status": outcome.raw_status, "elapsed_ms": elapsed,
                    "served": len(served), "of": len(old_ids),
                }
            except Exception as exc:
                row[name] = {"status": f"EXC {type(exc).__name__}",
                             "elapsed_ms": int((time.monotonic() - t0) * 1000)}
            print(f"{sid} {name}: {row[name]}", flush=True)
        out[sid] = row
    # 恢复默认
    ortools_core.FIRST_SOLUTION_STRATEGY = (
        routing_enums_pb2.FirstSolutionStrategy.PARALLEL_CHEAPEST_INSERTION)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".tmp")
    tmp.write_text(json.dumps(out, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    tmp.replace(dest)
    print(f"\n已增量写出 {dest}（{len(out)}/{len(FAILED8)} 场景）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
