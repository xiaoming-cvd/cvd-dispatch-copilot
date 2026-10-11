"""R01/R02 反例的穷举核对：小规模完整路线集合用暴力枚举验证求解器。

- R01（闭环目标反例）：穷举最优 = 5，COPT rebuild 的目标值必须为 5；
- R02（大 M 反例）：存在 1000s 未选弧，真实最优 = 3，不得报 proven_infeasible。

结果写入 reports/exhaustive_check.json，退出码 0 = 全部一致。
本机未装 COPT 时退出码 2（由 review_check.py 在 --skip-copt 模式下跳过）。
"""
from __future__ import annotations

import itertools
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.domain.models import Order, Scenario, Vehicle  # noqa: E402
from src.solver.pipeline import solve  # noqa: E402
from src.validation.timetable import evaluate_route  # noqa: E402

try:
    import coptpy  # noqa: F401
except ImportError:
    print("本机未装 COPT，跳过穷举核对（review_check --skip-copt 模式）")
    sys.exit(2)

SAMPLE = json.loads((ROOT / "data" / "sample" / "sample_small.json")
                    .read_text(encoding="utf-8"))
BASE = Scenario(**SAMPLE["scenario"])


def _mk(matrix_rows):
    orders = [
        Order(order_id="a", node_id="A", demand=1, ready_s=0, due_s=100, service_s=0),
        Order(order_id="b", node_id="B", demand=1, ready_s=0, due_s=100, service_s=0),
    ]
    from src.domain.models import OriginalPlan, RoutePlan, TravelMatrix
    return Scenario(
        scenario_id="rv", version=1, time_origin=BASE.time_origin,
        capacity_unit="load_units", depot_node_id="depot",
        orders=orders,
        vehicles=[Vehicle(vehicle_id="V1", capacity=2, available_from_s=0,
                          return_by_s=100)],
        travel_matrix=TravelMatrix(node_ids=["depot", "A", "B"], time_s=matrix_rows),
        original_plan=OriginalPlan(plan_id="p0", version=1,
                                   routes=[RoutePlan(vehicle_id="V1",
                                                     order_ids=("a", "b"))]))


def brute(scn) -> int | None:
    ids = [o.order_id for o in scn.orders]
    best = None
    for perm in itertools.permutations(ids):
        ev = evaluate_route(scn, "V1", list(perm))
        if ev.ok and (best is None or ev.travel_time_s < best):
            best = ev.travel_time_s
    return best


def main() -> int:
    scn1 = _mk([[0, 1, 2], [1, 0, 2], [3, 2, 0]])
    scn2 = _mk([[0, 1, 1], [1, 0, 1], [1, 1000, 0]])
    ex1, ex2 = brute(scn1), brute(scn2)
    o1 = solve(scn1, "rebuild", time_limit_ms=5000)
    o2 = solve(scn2, "rebuild", time_limit_ms=5000)
    out = {
        "R01_closed_loop": {
            "exhaustive_optimal": ex1,
            "solver_status": o1.result.status.value,
            "solver_travel": o1.result.metrics.get("total_travel_time_s"),
            "match": (ex1 == o1.result.metrics.get("total_travel_time_s")
                      and o1.result.status.value == "feasible"),
        },
        "R02_bigM": {
            "exhaustive_optimal": ex2,
            "solver_status": o2.result.status.value,
            "solver_travel": o2.result.metrics.get("total_travel_time_s"),
            "match": (ex2 == o2.result.metrics.get("total_travel_time_s")
                      and o2.result.status.value == "feasible"),
        },
    }
    (ROOT / "reports").mkdir(exist_ok=True)
    (ROOT / "reports" / "exhaustive_check.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=2))
    ok = all(v["match"] for v in out.values())
    print("overall:", "pass" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
