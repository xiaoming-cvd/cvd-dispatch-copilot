"""OR-Tools 共享建模层：把场景翻译成 CP 路径模型。

阶段 2 只承担"完全重排"与"原计划生成"两类调用；插单基线
（src/solver/insert.py）是独立的枚举实现，不经过本模块。

建模约定（与独立校验器 src/validation/timetable.py 完全同语义）：
- Time 维度累积 行驶 + 服务（transit = 行驶(from,to) + 服务(from)）；
  车辆起点累积固定为 available_from_s，终点上限 return_by_s；
  订单节点到达时刻约束在 [ready_s, due_s]。
- Capacity 维度累积 demand（transit = demand(to)），上限为车辆容量。
- 锁定：assignment_locks 通过 VehicleVar 禁值实现；
  relative_order_locks 通过 AddPickupAndDelivery 保序实现
  （允许新任务穿插在锁内订单之间）。
- 本模块不解释求解结果的可行性；结果一律交独立校验器复核。
"""
from __future__ import annotations

from dataclasses import dataclass

from ortools.constraint_solver import pywrapcp, routing_enums_pb2

from ..domain.models import Scenario

SOLVER_VERSION = "stage2-ortools-9.14.6206"


@dataclass(frozen=True)
class OrtoolsOutcome:
    """求解器原始返回的规范化描述（不含任何可行性断言）。

    structured 字段（P1 H）：真实后端、版本、终止原因与 incumbent 情况
    由后端显式给出，pipeline 不再靠方法名或状态字符串猜测。
    """

    raw_status: str  # OPTIMAL / FEASIBLE / INFEASIBLE / TIMEOUT / ...
    routes: tuple[tuple[str, ...], ...]  # 每辆车一个订单序列（空 = 不出车）
    backend: str = ""            # 例如 "copt" / "ortools"
    backend_version: str = ""    # 例如 "8.0.6" / "9.14.6206"
    termination: str = ""        # optimal / feasible / timeout / infeasible / other
    has_incumbent: bool = False  # 终止时是否持有完整/部分候选
    status_detail: str = ""      # 求解器原生状态值，原样记录


def solve_vrptw(
    scn: Scenario,
    order_ids: list[str],
    time_limit_ms: int,
    respect_locks: bool = True,
) -> OrtoolsOutcome:
    """对给定订单子集（含仓库）解 VRPTW，返回按车辆分组的订单序列。

    状态语义（任务书 6 章）：
    - INFEASIBLE → 可上报 proven_infeasible；
    - TIMEOUT / NO_SOLUTION / PARTIAL 等 → 只能上报 no_feasible_found。
    """
    nodes = scn.travel_matrix.node_ids
    order_by_id = {o.order_id: o for o in scn.orders}
    selected = [order_by_id[o] for o in order_ids]
    # 节点 0 = 仓库；订单节点 1..n
    service = [0]
    for o in selected:
        service.append(o.service_s)

    n_vehicles = len(scn.vehicles)
    # 本模型节点编号：0 = 仓库，1..n = selected 中的订单
    manager = pywrapcp.RoutingIndexManager(len(selected) + 1, n_vehicles, 0)
    routing = pywrapcp.RoutingModel(manager)

    time_s = scn.travel_matrix.time_s

    def travel_time(from_i: int, to_i: int) -> int:
        f = manager.IndexToNode(from_i)
        t = manager.IndexToNode(to_i)
        fn = _node_at(selected, f, scn)
        tn = _node_at(selected, t, scn)
        return time_s[nodes.index(fn)][nodes.index(tn)] + service[f]

    def demand_cb(from_i: int, to_i: int) -> int:
        t = manager.IndexToNode(to_i)
        if t == 0:
            return 0
        return int(round(selected[t - 1].demand))

    transit_cb = routing.RegisterTransitCallback(travel_time)
    demand_transit = routing.RegisterTransitCallback(demand_cb)

    routing.AddDimensionWithVehicleCapacity(
        demand_transit, 0,
        [int(round(v.capacity)) for v in scn.vehicles],
        True, "Capacity")
    # slack 必须允许等待：cumul 语义为"服务开始"，
    # 早到车辆通过 arc slack 等待到 ready_s（与独立校验器 wait=max(0,ready-到达) 同语义）
    routing.AddDimension(transit_cb, _horizon(scn), _horizon(scn), False, "Time")
    time_dim = routing.GetDimensionOrDie("Time")

    for i, o in enumerate(selected, start=1):
        idx = manager.NodeToIndex(i)
        time_dim.CumulVar(idx).SetRange(o.ready_s, o.due_s)

    for v, veh in enumerate(scn.vehicles):
        start_cumul = time_dim.CumulVar(routing.Start(v))
        start_cumul.SetRange(veh.available_from_s, veh.available_from_s)
        time_dim.CumulVar(routing.End(v)).SetMax(veh.return_by_s)

    if respect_locks:
        _apply_locks(scn, routing, manager, selected, order_ids)

    # 不加 drop 析取：任务书红线"不得丢单换取漂亮指标"。
    # 全部订单必须被服务 → 约束不可满足时 OR-Tools 返回 ROUTING_INFEASIBLE，
    # 由 pipeline 判为 proven_infeasible；超时/未覆盖一律 no_feasible_found。
    params = pywrapcp.DefaultRoutingSearchParameters()
    params.time_limit.FromMilliseconds(time_limit_ms)
    # PATH_CHEAPEST_ARC 在"固定出发时刻"模型下无法构造初解（实测 15 节点即超时），
    # PARALLEL_CHEAPEST_INSERTION 可秒解同规模问题
    params.first_solution_strategy = (
        routing_enums_pb2.FirstSolutionStrategy.PARALLEL_CHEAPEST_INSERTION)
    params.local_search_metaheuristic = (
        routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH)
    params.log_search = False

    solution = routing.SolveWithParameters(params)

    desc = routing_enums_pb2.RoutingSearchStatus.DESCRIPTOR
    by_number = {v.number: v.name for v in desc.enum_values_by_name.values()}
    code = routing.status()
    raw = by_number.get(code, f"UNKNOWN({code})")

    routes: list[tuple[str, ...]] = []
    for v in range(n_vehicles):
        seq: list[str] = []
        if solution is not None:
            idx = routing.Start(v)
            while not routing.IsEnd(idx):
                node = manager.IndexToNode(idx)
                if node != 0:
                    seq.append(selected[node - 1].order_id)
                idx = solution.Value(routing.NextVar(idx))
        routes.append(tuple(seq))
    return OrtoolsOutcome(raw_status=raw, routes=tuple(routes))


def _node_at(selected: list, i: int, scn: Scenario) -> str:
    return scn.depot_node_id if i == 0 else selected[i - 1].node_id


def _horizon(scn: Scenario) -> int:
    return max(v.return_by_s for v in scn.vehicles)


def _apply_locks(scn, routing, manager, selected, order_ids) -> None:
    pos = {o.order_id: i + 1 for i, o in enumerate(selected)}
    for oid, vid in scn.locks.assignment_locks.items():
        if oid not in pos:
            continue
        veh_idx = next(
            (i for i, v in enumerate(scn.vehicles) if v.vehicle_id == vid), None)
        if veh_idx is None:
            continue
        idx = manager.NodeToIndex(pos[oid])
        var = routing.VehicleVar(idx)
        for v in range(len(scn.vehicles)):
            if v != veh_idx:
                var.RemoveValue(v)

    for rl in scn.locks.relative_order_locks:
        veh_idx = next(
            (i for i, v in enumerate(scn.vehicles) if v.vehicle_id == rl.vehicle_id), None)
        if veh_idx is None:
            continue
        for a, b in zip(rl.order_ids, rl.order_ids[1:]):
            if a not in pos or b not in pos:
                continue
            ia = manager.NodeToIndex(pos[a])
            ib = manager.NodeToIndex(pos[b])
            routing.AddPickupAndDelivery(ia, ib)
            routing.solver().Add(
                routing.VehicleVar(ia) == routing.VehicleVar(ib))
