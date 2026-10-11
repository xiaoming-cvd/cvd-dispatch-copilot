"""低扰动指标（任务书 5 章精确定义，任何实现不得偏离）。

- 换车数：只统计**旧任务**（新任务没有"原车"可换）。
- 相邻关系破坏数：取原计划中同车相邻的旧任务有序对 (a,b)；在新计划
  **该车**的序列中先滤去新任务，若 a、b 不再相邻（顺序颠倒或被其他
  旧任务隔开）或 a、b 不在原车辆上，记一次破坏。
  新增任务插入导致的位置索引顺延**不计**破坏（滤去新任务后仍相邻）。
"""
from __future__ import annotations

from dataclasses import dataclass

from ..domain.models import RouteResult, Scenario


@dataclass(frozen=True)
class AdjacencyStatus:
    vehicle_id: str  # 原计划中的车辆
    pair: tuple[str, str]  # 旧任务有序对 (a, b)
    preserved: bool
    reason: str  # 程序可解释的原因，供 diff 使用


def _is_new(scn: Scenario, oid: str) -> bool:
    return next((o.is_new for o in scn.orders if o.order_id == oid), False)


def original_vehicle_of(scn: Scenario) -> dict[str, str]:
    return {oid: r.vehicle_id for r in scn.original_plan.routes for oid in r.order_ids}


def new_vehicle_of(routes: list[RouteResult] | tuple[RouteResult, ...]) -> dict[str, str]:
    return {oid: r.vehicle_id for r in routes for oid in r.order_ids}


def count_vehicle_changes(scn: Scenario, routes) -> int:
    """旧任务换车数。"""
    orig = original_vehicle_of(scn)
    new = new_vehicle_of(routes)
    return sum(1 for oid, v in orig.items() if new.get(oid) != v)


def adjacency_statuses(scn: Scenario, routes) -> list[AdjacencyStatus]:
    """逐对检查原计划旧相邻关系在新计划中的保持情况。"""
    new_by_vehicle = {r.vehicle_id: list(r.order_ids) for r in routes}
    statuses: list[AdjacencyStatus] = []
    for route in scn.original_plan.routes:
        olds = list(route.order_ids)  # 原计划只含旧任务
        filtered = [oid for oid in new_by_vehicle.get(route.vehicle_id, [])
                    if not _is_new(scn, oid)]
        for a, b in zip(olds, olds[1:]):
            preserved = any(
                filtered[i] == a and filtered[i + 1] == b
                for i in range(len(filtered) - 1))
            if preserved:
                reason = "同车保序"
            else:
                newv = new_vehicle_of(routes)
                if newv.get(a) != route.vehicle_id or newv.get(b) != route.vehicle_id:
                    reason = "换车导致相邻关系失效"
                else:
                    reason = "同车旧任务顺序被改变"
            statuses.append(AdjacencyStatus(
                vehicle_id=route.vehicle_id, pair=(a, b),
                preserved=preserved, reason=reason))
    return statuses


def count_adjacency_breaks(scn: Scenario, routes) -> int:
    return sum(1 for s in adjacency_statuses(scn, routes) if not s.preserved)


@dataclass(frozen=True)
class Perturbation:
    vehicle_changes: int
    adjacency_breaks: int
    adjacency_detail: tuple[AdjacencyStatus, ...]

    def to_dict(self) -> dict:
        return {
            "vehicle_changes": self.vehicle_changes,
            "adjacency_breaks": self.adjacency_breaks,
            "adjacency_detail": [
                {"vehicle_id": s.vehicle_id, "pair": list(s.pair),
                 "preserved": s.preserved, "reason": s.reason}
                for s in self.adjacency_detail
            ],
        }


def measure(scn: Scenario, routes) -> Perturbation:
    detail = tuple(adjacency_statuses(scn, routes))
    return Perturbation(
        vehicle_changes=count_vehicle_changes(scn, routes),
        adjacency_breaks=sum(1 for s in detail if not s.preserved),
        adjacency_detail=detail,
    )
