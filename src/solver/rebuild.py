"""完全重排基线（任务书阶段 2）：对全部任务重新求解。

后端（队长决策）：COPT 8.0.6 MIP（src/solver/copt_core.py）。
OR-Tools CP 版（ortools_core）保留作对照/阶段 3 候选生成。
- 尊重锁定（换车锁、相对顺序锁）；旧任务不预设保留原顺序。
- 返回原始状态 + 路线；是否可行由调用方用独立校验器复核，
  本模块不对结果做可行性断言。
"""
from __future__ import annotations

from ..domain.models import RouteResult, Scenario
from ..validation.timetable import evaluate_route
from .ortools_core import OrtoolsOutcome

BACKEND = "copt"


def rebuild_all(
    scn: Scenario,
    time_limit_ms: int,
    backend: str = BACKEND,
) -> OrtoolsOutcome:
    """对场景全部订单（旧 + 新）完全重排。"""
    all_ids = [o.order_id for o in scn.orders]
    if backend == "copt":
        # 延迟导入（P1 F）：无 COPT 环境只有显式选择 rebuild 时才报可理解错误
        from .copt_core import solve_vrptw_copt
        return solve_vrptw_copt(scn, all_ids, time_limit_ms, respect_locks=True)
    if backend == "ortools":
        from .ortools_core import solve_vrptw
        return solve_vrptw(scn, all_ids, time_limit_ms, respect_locks=True)
    raise ValueError(f"E_BACKEND: 未知重排后端 {backend}")


def outcome_to_routes(scn: Scenario, outcome: OrtoolsOutcome) -> list[RouteResult]:
    """把求解结果转成 RouteResult（仅含出车路线；不做可行性断言）。"""
    routes: list[RouteResult] = []
    for vid, seq in zip((v.vehicle_id for v in scn.vehicles), outcome.routes):
        if not seq:
            continue
        ev = evaluate_route(scn, vid, list(seq))
        routes.append(RouteResult(vehicle_id=vid, order_ids=list(seq), return_s=ev.return_s))
    return routes
