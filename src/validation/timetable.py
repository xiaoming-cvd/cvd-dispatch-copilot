"""独立校验器（任务书阶段 2）：从输入矩阵重新计算时刻表，不采信求解器结论。

红线落实：
- 到达/装载/等待/服务开始/离开/返回时刻全部依据场景输入（矩阵、时间窗、
  车辆可用时段）由本模块重算，与任何求解器的"可行"标志无关。
- 方案级校验查唯一覆盖（不得丢单、不得重复）与锁定保持。
- 每条规则有 E_* 错误码，关键规则在 tests/test_timetable.py 中有手算预期。

语义约定（与 src/domain/models.py 一致）：
- 车辆从仓库出发，出发时刻 = available_from_s（固定出发）。
- 到达 = 上一站离开 + 行驶时间；服务开始 = max(到达, ready_s)；
  等待 = 服务开始 - 到达（不为负）；离开 = 服务开始 + service_s；
  返回 = 末站离开 + 末站→仓库行驶时间，不得晚于 return_by_s。
- 容量：配送语义，单路线装载总量 = 该路线全部订单 demand 之和，
  不得超过车辆 capacity（不随路径变化，因为不存在沿途补货）。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..domain.errors import ValidationIssue
from ..domain.models import RouteResult, Scenario, StopSchedule


@dataclass(frozen=True)
class RouteEvaluation:
    """一条路线的独立重算结果：时刻表 + 该路线内的违规。"""

    vehicle_id: str
    order_ids: tuple[str, ...]
    stops: tuple[StopSchedule, ...]
    return_s: int
    total_load: float
    travel_time_s: int  # 仓库→首站 + 站间 + 末站→仓库 的纯行驶时间
    issues: tuple[ValidationIssue, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.issues


@dataclass(frozen=True)
class SolutionValidation:
    """整个方案（多路线）的独立校验结果。

    issues 汇总所有违规（路线级 + 方案级），便于调用方一处遍历；
    routes 保留逐路线明细。ok 要求全部路线与方案级检查均无问题。
    """

    routes: tuple[RouteEvaluation, ...]
    issues: tuple[ValidationIssue, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.issues

    def to_dict(self) -> dict:
        return {
            "independent_ok": self.ok,
            "route_count": len(self.routes),
            "issues": [i.to_dict() for i in self.issues],
            "routes": [
                {
                    "vehicle_id": r.vehicle_id,
                    "order_ids": list(r.order_ids),
                    "return_s": r.return_s,
                    "total_load": r.total_load,
                    "travel_time_s": r.travel_time_s,
                    "ok": r.ok,
                    "issues": [i.to_dict() for i in r.issues],
                    "stops": [s.model_dump() for s in r.stops],
                }
                for r in self.routes
            ],
        }


def _travel(scn: Scenario, from_node: str, to_node: str) -> int:
    nodes = scn.travel_matrix.node_ids
    i, j = nodes.index(from_node), nodes.index(to_node)
    return scn.travel_matrix.time_s[i][j]


def evaluate_route(scn: Scenario, vehicle_id: str, order_ids: list[str]) -> RouteEvaluation:
    """独立重算一条路线：逐站时刻表 + 容量/时间窗/返回时刻违规。"""
    issues: list[ValidationIssue] = []
    order_by_id = {o.order_id: o for o in scn.orders}
    vehicle_by_id = {v.vehicle_id: v for v in scn.vehicles}

    if vehicle_id not in vehicle_by_id:
        return RouteEvaluation(vehicle_id, tuple(order_ids), (), 0, 0.0, 0, (
            ValidationIssue("E_ROUTE_UNKNOWN_VEHICLE", f"路线引用了不存在的车辆 {vehicle_id}"),))
    vehicle = vehicle_by_id[vehicle_id]

    if not order_ids:
        issues.append(ValidationIssue("E_ROUTE_EMPTY", f"车辆 {vehicle_id} 的路线为空"))
        return RouteEvaluation(vehicle_id, (), (), 0, 0.0, 0, tuple(issues))

    for oid in order_ids:
        if oid not in order_by_id:
            issues.append(ValidationIssue(
                "E_ROUTE_UNKNOWN_ORDER", f"路线包含不存在的订单 {oid}", "routes"))
    if len(set(order_ids)) != len(order_ids):
        dupes = sorted({x for x in order_ids if order_ids.count(x) > 1})
        issues.append(ValidationIssue(
            "E_ROUTE_DUP_ORDER", f"同一路线内订单重复：{dupes}", "routes"))

    known = [order_by_id[o] for o in order_ids if o in order_by_id]

    # 容量（配送语义：路线装载 = 全部 demand 之和）
    total_load = sum(o.demand for o in known)
    if total_load > vehicle.capacity + 1e-9:
        issues.append(ValidationIssue(
            "E_ROUTE_CAPACITY",
            f"车辆 {vehicle_id} 装载 {total_load:g} 超过容量 {vehicle.capacity:g}",
            "routes"))

    # 逐站重算
    stops: list[StopSchedule] = []
    travel_total = 0
    prev_node = scn.depot_node_id
    clock = vehicle.available_from_s  # 固定出发时刻

    for seq, order in enumerate(known, start=1):
        travel_total += _travel(scn, prev_node, order.node_id)
        arrive = clock + _travel(scn, prev_node, order.node_id)
        start = max(arrive, order.ready_s)
        wait = start - arrive
        depart = start + order.service_s
        if start > order.due_s:
            issues.append(ValidationIssue(
                "E_STOP_WINDOW",
                f"订单 {order.order_id} 服务开始 {start}s 超过截止 {order.due_s}s",
                f"routes[{vehicle_id}].order[{seq}]"))
        stops.append(StopSchedule(
            vehicle_id=vehicle_id, order_id=order.order_id, seq=seq,
            arrive_s=arrive, wait_s=wait, start_s=start, depart_s=depart))
        clock = depart
        prev_node = order.node_id

    travel_total += _travel(scn, prev_node, scn.depot_node_id)
    return_s = clock + _travel(scn, prev_node, scn.depot_node_id)
    if return_s > vehicle.return_by_s:
        issues.append(ValidationIssue(
            "E_ROUTE_RETURN_LATE",
            f"车辆 {vehicle_id} 返回 {return_s}s 超过 {vehicle.return_by_s}s",
            "routes"))

    return RouteEvaluation(
        vehicle_id=vehicle_id,
        order_ids=tuple(order_ids),
        stops=tuple(stops),
        return_s=return_s,
        total_load=total_load,
        travel_time_s=travel_total,
        issues=tuple(issues),
    )


def validate_solution(scn: Scenario, routes: list[RouteResult]) -> SolutionValidation:
    """方案级独立校验：逐路线重算 + 唯一覆盖 + 锁定保持。

    覆盖语义：场景中的全部订单（旧 + 新）必须恰好各出现一次。
    锁定语义：assignment_locks 固定旧任务车辆；relative_order_locks
    固定旧任务在某车上的先后顺序（允许新任务穿插其间）。
    """
    route_evals = tuple(
        evaluate_route(scn, r.vehicle_id, list(r.order_ids)) for r in routes)
    issues: list[ValidationIssue] = []

    # 一车一条路线（首版模型）：同车多条路线 = 同一辆车同时出发多次
    by_vehicle: dict[str, int] = {}
    for r in routes:
        by_vehicle[r.vehicle_id] = by_vehicle.get(r.vehicle_id, 0) + 1
    for vid, cnt in sorted(by_vehicle.items()):
        if cnt > 1:
            issues.append(ValidationIssue(
                "E_SOL_DUP_VEHICLE",
                f"车辆 {vid} 出现 {cnt} 条路线；首版为一车一条路线，"
                "不自动拼接也不按多趟运输解释", "routes"))

    # 声明的返回时刻必须与独立重算一致（P1 E：防止路线字段与时刻表脱节）
    for r, ev in zip(routes, route_evals):
        if r.order_ids == ev.order_ids and r.return_s != ev.return_s:
            issues.append(ValidationIssue(
                "E_SOL_RETURN_MISMATCH",
                f"车辆 {r.vehicle_id} 声明返回 {r.return_s}s，独立重算为 "
                f"{ev.return_s}s，二者不一致", "routes"))

    served = [oid for r in routes for oid in r.order_ids]
    served_set = set(served)
    if len(served) != len(served_set):
        dupes = sorted({x for x in served if served.count(x) > 1})
        issues.append(ValidationIssue(
            "E_SOL_DUP_ORDER", f"方案中订单重复出现：{dupes}", "routes"))
    expected = {o.order_id for o in scn.orders}
    missing = sorted(expected - served_set)
    if missing:
        issues.append(ValidationIssue(
            "E_SOL_MISSING_ORDER", f"方案漏单（禁止静默删除）：{missing}", "routes"))
    unknown = sorted(served_set - expected)
    if unknown:
        issues.append(ValidationIssue(
            "E_SOL_UNKNOWN_ORDER", f"方案出现场景外的订单：{unknown}", "routes"))

    # assignment locks
    vehicle_of_order = {oid: r.vehicle_id for r in routes for oid in r.order_ids}
    for oid, vid in scn.locks.assignment_locks.items():
        actual = vehicle_of_order.get(oid)
        if actual is not None and actual != vid:
            issues.append(ValidationIssue(
                "E_SOL_ASSIGN_LOCK",
                f"订单 {oid} 被换车锁固定到 {vid}，方案却分到 {actual}", "routes"))

    # relative order locks：锁语义本身包含"固定车辆"——每个锁成员都必须
    # 出现在锁指定车辆的唯一路线中，再检查先后顺序（允许新任务穿插）。
    # 不依赖生成器顺带补 assignment_locks（P0 D）。
    for rl in scn.locks.relative_order_locks:
        members_on_route = [oid for r in routes if r.vehicle_id == rl.vehicle_id
                            for oid in r.order_ids]
        seq = {oid: i for i, oid in enumerate(members_on_route)}
        for member in rl.order_ids:
            if member not in seq:
                issues.append(ValidationIssue(
                    "E_SOL_REL_ORDER_LOCK",
                    f"相对顺序锁成员 {member} 未出现在锁指定车辆 "
                    f"{rl.vehicle_id} 的路线中（该锁语义包含固定车辆，"
                    "成员不得分到别车）", "routes"))
        for a, b in zip(rl.order_ids, rl.order_ids[1:]):
            if a in seq and b in seq and seq[a] >= seq[b]:
                issues.append(ValidationIssue(
                    "E_SOL_REL_ORDER_LOCK",
                    f"车辆 {rl.vehicle_id} 上锁内顺序被破坏：{a} 应在 {b} 之前",
                    "routes"))

    return SolutionValidation(
        routes=route_evals,
        issues=tuple(i for r in route_evals for i in r.issues) + tuple(issues),
    )


def build_routes_from_evals(
    evals: list[RouteEvaluation],
) -> list[RouteResult]:
    """把通过独立校验的路线重算结果转成 RouteResult 列表（保持出车才建路线）。"""
    return [
        RouteResult(vehicle_id=e.vehicle_id, order_ids=list(e.order_ids), return_s=e.return_s)
        for e in evals
    ]


def evaluate_original_plan(scn: Scenario) -> tuple[float | None, list[dict]]:
    """原计划（baseline）总行驶成本：逐路线独立重算（容缺口径）。

    原计划只含旧订单、天然不含新订单，因此不走 validate_solution 的全覆盖
    校验（否则必然"漏单"被拒）；改为逐路线独立重算。任何一条路线自身违规
    （时间窗/超载/返仓晚）则该参考不可用：返回 (None, 明细)。

    返回 (总成本或 None, 每路线明细 [{vehicle_id, used, issues,
    travel_time_s}])，供 baseline 任务/场景详情展示与低扰动参考链使用。
    """
    total = 0.0
    details: list[dict] = []
    for r in scn.original_plan.routes:
        ev = evaluate_route(scn, r.vehicle_id, list(r.order_ids))
        codes = [i.code for i in ev.issues]
        details.append({"vehicle_id": r.vehicle_id, "used": not codes,
                        "issues": codes, "travel_time_s": ev.travel_time_s})
        if codes:
            return None, details
        total += ev.travel_time_s
    return total, details
