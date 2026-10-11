"""COPT MIP 重排后端（队长决策：本机已装 COPT 8.0.6，用作完全重排引擎）。

与 ortools_core 相同的调用契约（OrtoolsOutcome 结构），供 rebuild 使用：
- COPT 证明 MIP 不可行 → raw_status="MIP_INFEASIBLE"（pipeline 判 proven_infeasible）；
- 超时无完整覆盖 → "MIP_TIMEOUT_NO_SOL"（no_feasible_found）；
- 超时但有完整覆盖 → "MIP_TIMEOUT_WITH_SOL"（照常走独立校验）；
- 最优/可行 → "MIP_OPTIMAL" / "MIP_FEASIBLE"。

模型：单商品流 VRPTW（MTZ 时间链）。
大 M（P0 B 修正）：B = horizon + max_service + max_tau，
  horizon = max(return_by_s)，max_tau = 全部节点对（含仓库）最大行驶时间。
  推导：时间链约束 t_j ≥ t_i + s_i + τ_ij − B(1−x_ij) 在 x=0 时必须恒真，
  最紧情形为 t_i 取上界 horizon、s_i 取 max_service、τ_ij 取 max_tau、
  t_j 取下界 0，故 B ≥ horizon + max_service + max_tau 足以解除未选弧；
  仓库出发弧与返回时刻约束同理被同一上界覆盖。修正前 B 未含 max_tau，
  审查报告的最小反例会制造假不可行。
子回路消除（R06 修正）：纯 MTZ 时间链在服务与行驶均可为 0 时无法排除
  与仓库断开的零环（审查反例：模型返回 OPTIMAL 但路线为空）。为此引入
  单商品流连通约束：仓库供流 n，每个客户节点净消耗 1 单位流，
  弧流量 ≤ n·x_ij——与仓库断开的环不可能获得流量，被精确排除，
  不需要 epsilon 或额外大 M，也不改变时间语义。
目标（P0 A 修正）：闭环总行驶时间 = 仓库出发弧 + 客户间弧 + 客户返仓弧，
  与独立校验器 total_travel_time_s 同一定义。
锁定：换车锁直接固定 y；相对顺序锁固定 y 并加 t_b ≥ t_a + service_a。
本模块不对结果做可行性断言，结果一律交独立校验器复核。
"""
from __future__ import annotations

import coptpy

from ..domain.models import Scenario
from .ortools_core import OrtoolsOutcome

SOLVER_VERSION_COPT = "stage2-copt-8.0.6"


def solve_vrptw_copt(
    scn: Scenario,
    order_ids: list[str],
    time_limit_ms: int,
    respect_locks: bool = True,
) -> OrtoolsOutcome:
    order_by_id = {o.order_id: o for o in scn.orders}
    selected = [order_by_id[o] for o in order_ids]
    n = len(selected)
    nodes = scn.travel_matrix.node_ids

    def tau(node_a: str, node_b: str) -> int:
        return scn.travel_matrix.time_s[nodes.index(node_a)][nodes.index(node_b)]

    K = len(scn.vehicles)
    horizon = max(v.return_by_s for v in scn.vehicles)
    max_service = max((o.service_s for o in selected), default=0)
    flat = [t for row in scn.travel_matrix.time_s for t in row if t is not None]
    max_tau = max(flat, default=0)
    B = horizon + max_service + max_tau  # 安全大 M，推导见模块 docstring

    env = coptpy.Envr()
    m = env.createModel("vrptw_rebuild")
    m.setParam("TimeLimit", max(time_limit_ms, 1) / 1000.0)
    m.setParam("Logging", 0)  # 求解日志不刷 stdout；需要诊断时在调用层打开

    x = {}
    for v in range(K):
        for i in range(0, n + 1):
            for j in range(1, n + 2):
                if i != j:
                    x[(v, i, j)] = m.addVar(vtype=coptpy.COPT.BINARY,
                                            name=f"x_{v}_{i}_{j}")

    t = {(v, i): m.addVar(lb=0, ub=B, vtype=coptpy.COPT.CONTINUOUS, name=f"t_{v}_{i}")
         for v in range(K) for i in range(1, n + 1)}

    y = {}
    for v in range(K):
        for i in range(1, n + 1):
            y[(v, i)] = m.addVar(vtype=coptpy.COPT.BINARY, name=f"y_{v}_{i}")
            m.addConstr(y[(v, i)] == coptpy.quicksum(
                x[(v, i, j)] for j in range(1, n + 2) if j != i))

    # 单商品流连通（R06）：仓库供流 n，每客户净耗 1，弧流量 ≤ n·x_ij。
    # 与仓库断开的（零耗时）子环不可能获得流量 → 精确排除，无需 epsilon。
    f = {}
    for v in range(K):
        for (i, j) in [k[1:] for k in x if k[0] == v]:
            f[(v, i, j)] = m.addVar(lb=0, ub=n, vtype=coptpy.COPT.CONTINUOUS,
                                    name=f"f_{v}_{i}_{j}")
            m.addConstr(f[(v, i, j)] <= n * x[(v, i, j)])
        for i in range(1, n + 1):
            m.addConstr(
                coptpy.quicksum(f[(v, a, i)] for a in range(0, n + 1) if a != i)
                - coptpy.quicksum(f[(v, i, b)] for b in range(1, n + 2) if b != i)
                == y[(v, i)])

    used = [m.addVar(vtype=coptpy.COPT.BINARY, name=f"used_{v}") for v in range(K)]

    # 覆盖：每个订单恰好一次
    for i in range(1, n + 1):
        m.addConstr(coptpy.quicksum(y[(v, i)] for v in range(K)) == 1)

    for v in range(K):
        veh = scn.vehicles[v]
        # 流守恒：进入 i 的次数 = y
        for i in range(1, n + 1):
            m.addConstr(coptpy.quicksum(x[(v, a, i)] for a in range(0, n + 1) if a != i)
                        == y[(v, i)])
        # 出仓库 = used；回仓库 = used
        m.addConstr(used[v] == coptpy.quicksum(x[(v, 0, j)] for j in range(1, n + 1)))
        m.addConstr(coptpy.quicksum(x[(v, j, n + 1)] for j in range(1, n + 1)) == used[v])
        # 容量
        m.addConstr(coptpy.quicksum(selected[i - 1].demand * y[(v, i)]
                                    for i in range(1, n + 1)) <= veh.capacity)
        # 时间链
        for i in range(1, n + 1):
            oi = selected[i - 1]
            m.addConstr(t[(v, i)] >= oi.ready_s * y[(v, i)])
            m.addConstr(t[(v, i)] <= oi.due_s * y[(v, i)] + B * (1 - y[(v, i)]))
            m.addConstr(x[(v, 0, i)] <= used[v])
            m.addConstr(t[(v, i)] >= veh.available_from_s + tau(scn.depot_node_id, oi.node_id)
                        - B * (1 - x[(v, 0, i)]))
            for j in range(1, n + 1):
                if i != j:
                    oj = selected[j - 1]
                    m.addConstr(t[(v, j)] >= t[(v, i)] + oi.service_s
                                + tau(oi.node_id, oj.node_id) - B * (1 - x[(v, i, j)]))
        # 返回时刻
        rv = m.addVar(lb=0, ub=veh.return_by_s, vtype=coptpy.COPT.CONTINUOUS, name=f"r_{v}")
        for i in range(1, n + 1):
            oi = selected[i - 1]
            m.addConstr(rv >= t[(v, i)] + oi.service_s + tau(oi.node_id, scn.depot_node_id)
                        - B * (1 - x[(v, i, n + 1)]))

    # 锁定
    if respect_locks:
        veh_index = {veh.vehicle_id: v for v, veh in enumerate(scn.vehicles)}
        for oid, vid in scn.locks.assignment_locks.items():
            i = next((k + 1 for k, o in enumerate(selected) if o.order_id == oid), None)
            if i is None or vid not in veh_index:
                continue
            for v in range(K):
                m.addConstr(y[(v, i)] == (1 if v == veh_index[vid] else 0))
        for rl in scn.locks.relative_order_locks:
            v = veh_index.get(rl.vehicle_id)
            if v is None:
                continue
            members = [next((k + 1 for k, o in enumerate(selected) if o.order_id == oid), None)
                       for oid in rl.order_ids]
            members = [i for i in members if i is not None]
            for i in members:
                m.addConstr(y[(v, i)] == 1)
            for a, b in zip(members, members[1:]):
                m.addConstr(t[(v, b)] >= t[(v, a)] + selected[a - 1].service_s)

    # 目标：闭环总行驶时间 = 仓库出发弧 + 客户间弧 + 客户返仓弧，
    # 与独立校验器 total_travel_time_s 完全同一定义（P0 A 修正：
    # 修正前漏算客户→仓库返仓弧，报告反例会把 D→A→B→D(6) 当成优于
    # 真正最优 D→B→A→D(5)）。
    obj = 0
    for v in range(K):
        for i in range(0, n + 1):
            for j in range(1, n + 1):
                if i != j:
                    nj = selected[j - 1].node_id
                    ni = scn.depot_node_id if i == 0 else selected[i - 1].node_id
                    obj += tau(ni, nj) * x[(v, i, j)]
        for j in range(1, n + 1):
            obj += tau(selected[j - 1].node_id, scn.depot_node_id) * x[(v, j, n + 1)]
            m.addConstr(x[(v, j, n + 1)] <= used[v])
    m.setObjective(obj, sense=coptpy.COPT.MINIMIZE)

    m.solve()

    if m.status == coptpy.COPT.INFEASIBLE:
        return OrtoolsOutcome(
            raw_status="MIP_INFEASIBLE", routes=tuple(() for _ in range(K)),
            backend="copt", backend_version="8.0.6",
            termination="infeasible", has_incumbent=False,
            status_detail=str(m.status))

    # 路线提取：visited 集合 + 步数上限（R06：零行驶/零服务时 MTZ 时间链
    # 不能保证访问先后，提取层必须自带防环与防死循环保护）
    routes: list[tuple[str, ...]] = []
    for v in range(K):
        seq: list[str] = []
        visited: set[int] = set()
        cur = 0
        for _ in range(n + 2):
            nxt = None
            for j in range(1, n + 2):
                var = x.get((v, cur, j))
                if var is None:
                    continue
                try:
                    if var.x > 0.5:
                        nxt = j
                        break
                except Exception:  # 超时无 incumbent 时读值会失败，按无边处理
                    continue
            if nxt is None or nxt == n + 1:
                break
            if nxt in visited:  # 环保护：不重复访问客户节点
                break
            visited.add(nxt)
            seq.append(selected[nxt - 1].order_id)
            cur = nxt
        routes.append(tuple(seq))

    served = sorted(oid for seq in routes for oid in seq)
    complete = served == sorted(order_ids)
    if m.status == coptpy.COPT.TIMEOUT:
        raw = "MIP_TIMEOUT_WITH_SOL" if complete else "MIP_TIMEOUT_NO_SOL"
        term, inc = "timeout", complete
    elif m.status == coptpy.COPT.OPTIMAL:
        raw, term, inc = "MIP_OPTIMAL", "optimal", True
    else:  # 其他终止但持有候选（如 MIP_FEASIBLE）：不得被映射丢弃（P1 H）
        raw, term = "MIP_FEASIBLE", "feasible"
        inc = complete
    return OrtoolsOutcome(
        raw_status=raw, routes=tuple(routes),
        backend="copt", backend_version="8.0.6",
        termination=term, has_incumbent=inc,
        status_detail=str(m.status))
