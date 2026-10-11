"""场景级交叉校验（Pydantic 字段校验之外的规则）。

规则清单（规则 ID 即 code）：
- E_DUP_ORDER_ID          订单 ID 重复
- E_ORDER_NODE_MISSING    订单引用了矩阵中不存在的节点
- E_VEHICLE_DUP_ID        车辆 ID 重复
- E_DEPOT_MISSING         仓库节点不在矩阵中
- E_PLAN_UNKNOWN_ORDER    原计划出现不存在的订单
- E_PLAN_DUP_ORDER        原任务在计划中重复出现
- E_PLAN_MISSING_ORDER    旧任务未在计划中出现（漏单，禁止静默删除）
- E_PLAN_NEW_ORDER        新任务出现在原计划中
- E_PLAN_UNKNOWN_VEHICLE  原计划引用了不存在的车辆
- E_LOCK_NEW_ORDER        锁定对象包含新任务（锁只针对旧任务）
- E_LOCK_UNKNOWN_ORDER    锁定对象包含不存在的订单
- E_LOCK_UNKNOWN_VEHICLE  锁定对象包含不存在的车辆
- E_LOCK_CONFLICT_ASSIGN  换车锁与相对顺序锁指定的车辆冲突

语义依据：任务书第 4 章——原任务全部恰好出现一次，新任务不出现在
原计划中；锁定只作用于旧任务；不得静默删除订单。
"""
from __future__ import annotations

from ..domain.errors import ValidationIssue, ValidationReport
from ..domain.models import Scenario, ScenarioDraft


def validate_scenario(scn: Scenario) -> ValidationReport:
    """对已通过 Pydantic 校验的场景做交叉规则检查，返回问题列表。

    本函数不抛异常：所有问题都以 ValidationIssue 返回，由调用方决定
    是否拒绝输入（status=invalid_input）。
    """
    issues: list[ValidationIssue] = []
    orders = scn.orders
    vehicles = scn.vehicles
    plan = scn.original_plan

    # ---- 订单 ----
    seen: set[str] = set()
    for idx, o in enumerate(orders):
        if o.order_id in seen:
            issues.append(ValidationIssue(
                "E_DUP_ORDER_ID", f"订单 ID 重复：{o.order_id}", f"orders[{idx}].order_id"))
        seen.add(o.order_id)
        if o.node_id not in scn.travel_matrix.node_ids:
            issues.append(ValidationIssue(
                "E_ORDER_NODE_MISSING",
                f"订单 {o.order_id} 的节点 {o.node_id} 不在出行矩阵中",
                f"orders[{idx}].node_id"))

    order_by_id = {o.order_id: o for o in orders}

    # ---- 车辆 ----
    vehicle_ids: set[str] = set()
    for idx, v in enumerate(vehicles):
        if v.vehicle_id in vehicle_ids:
            issues.append(ValidationIssue(
                "E_VEHICLE_DUP_ID", f"车辆 ID 重复：{v.vehicle_id}", f"vehicles[{idx}].vehicle_id"))
        vehicle_ids.add(v.vehicle_id)

    # ---- 仓库 ----
    if scn.depot_node_id not in scn.travel_matrix.node_ids:
        issues.append(ValidationIssue(
            "E_DEPOT_MISSING", f"仓库节点 {scn.depot_node_id} 不在出行矩阵中", "depot_node_id"))

    # ---- 原计划 vs 订单 ----
    plan_order_ids: list[str] = []
    for r_idx, route in enumerate(plan.routes):
        if route.vehicle_id not in vehicle_ids:
            issues.append(ValidationIssue(
                "E_PLAN_UNKNOWN_VEHICLE",
                f"原计划路线引用了不存在的车辆 {route.vehicle_id}",
                f"original_plan.routes[{r_idx}].vehicle_id"))
        plan_order_ids.extend(route.order_ids)

    plan_set = set(plan_order_ids)
    for oid in plan_order_ids:
        if oid not in order_by_id:
            issues.append(ValidationIssue(
                "E_PLAN_UNKNOWN_ORDER", f"原计划包含不存在的订单 {oid}", "original_plan.routes"))
    if len(plan_order_ids) != len(plan_set):
        dupes = sorted({x for x in plan_order_ids if plan_order_ids.count(x) > 1})
        issues.append(ValidationIssue(
            "E_PLAN_DUP_ORDER", f"原任务在计划中重复出现：{dupes}", "original_plan.routes"))

    old_ids = {o.order_id for o in orders if not o.is_new}
    new_ids = {o.order_id for o in orders if o.is_new}
    missing = sorted(old_ids - plan_set)
    if missing:
        issues.append(ValidationIssue(
            "E_PLAN_MISSING_ORDER", f"旧任务未被原计划覆盖（漏单）：{missing}", "original_plan.routes"))
    stray_new = sorted(new_ids & plan_set)
    if stray_new:
        issues.append(ValidationIssue(
            "E_PLAN_NEW_ORDER", f"新任务不得出现在原计划中：{stray_new}", "original_plan.routes"))

    # ---- 锁定 ----
    rel_locked: dict[str, str] = {}  # order_id -> vehicle_id，来自相对顺序锁
    for idx, rl in enumerate(scn.locks.relative_order_locks):
        if rl.vehicle_id not in vehicle_ids:
            issues.append(ValidationIssue(
                "E_LOCK_UNKNOWN_VEHICLE",
                f"相对顺序锁引用了不存在的车辆 {rl.vehicle_id}",
                f"locks.relative_order_locks[{idx}].vehicle_id"))
        for oid in rl.order_ids:
            if oid not in order_by_id:
                issues.append(ValidationIssue(
                    "E_LOCK_UNKNOWN_ORDER",
                    f"相对顺序锁引用了不存在的订单 {oid}",
                    f"locks.relative_order_locks[{idx}].order_ids"))
            elif order_by_id[oid].is_new:
                issues.append(ValidationIssue(
                    "E_LOCK_NEW_ORDER",
                    f"锁只能作用于旧任务：{oid} 是新增任务",
                    f"locks.relative_order_locks[{idx}].order_ids"))
            else:
                rel_locked[oid] = rl.vehicle_id

    for oid, vid in scn.locks.assignment_locks.items():
        if oid not in order_by_id:
            issues.append(ValidationIssue(
                "E_LOCK_UNKNOWN_ORDER", f"换车锁引用了不存在的订单 {oid}", "locks.assignment_locks"))
        elif order_by_id[oid].is_new:
            issues.append(ValidationIssue(
                "E_LOCK_NEW_ORDER", f"锁只能作用于旧任务：{oid} 是新增任务", "locks.assignment_locks"))
        if vid not in vehicle_ids:
            issues.append(ValidationIssue(
                "E_LOCK_UNKNOWN_VEHICLE",
                f"换车锁引用了不存在的车辆 {vid}", "locks.assignment_locks"))
        if oid in rel_locked and rel_locked[oid] != vid:
            issues.append(ValidationIssue(
                "E_LOCK_CONFLICT_ASSIGN",
                f"订单 {oid} 被相对顺序锁固定到 {rel_locked[oid]}，"
                f"又被换车锁固定到 {vid}",
                "locks.assignment_locks"))

    return ValidationReport(issues=tuple(issues))


def validate_draft(d: ScenarioDraft) -> ValidationReport:
    """对 ScenarioDraft 的基础校验：订单/车辆/节点引用与锁定合法性。

    不含原计划一致性检查（原计划此阶段尚不存在，由阶段 2 生成后
    通过完整 Scenario 校验）。
    """
    issues: list[ValidationIssue] = []
    seen: set[str] = set()
    order_by_id: dict[str, object] = {}
    for idx, o in enumerate(d.orders):
        if o.order_id in seen:
            issues.append(ValidationIssue(
                "E_DUP_ORDER_ID", f"订单 ID 重复：{o.order_id}", f"orders[{idx}].order_id"))
        seen.add(o.order_id)
        order_by_id[o.order_id] = o
        if o.node_id not in d.travel_matrix.node_ids:
            issues.append(ValidationIssue(
                "E_ORDER_NODE_MISSING",
                f"订单 {o.order_id} 的节点 {o.node_id} 不在出行矩阵中",
                f"orders[{idx}].node_id"))

    vehicle_ids: set[str] = set()
    for idx, v in enumerate(d.vehicles):
        if v.vehicle_id in vehicle_ids:
            issues.append(ValidationIssue(
                "E_VEHICLE_DUP_ID", f"车辆 ID 重复：{v.vehicle_id}", f"vehicles[{idx}].vehicle_id"))
        vehicle_ids.add(v.vehicle_id)

    if d.depot_node_id not in d.travel_matrix.node_ids:
        issues.append(ValidationIssue(
            "E_DEPOT_MISSING", f"仓库节点 {d.depot_node_id} 不在出行矩阵中", "depot_node_id"))

    rel_locked: dict[str, str] = {}
    for idx, rl in enumerate(d.locks.relative_order_locks):
        if rl.vehicle_id not in vehicle_ids:
            issues.append(ValidationIssue(
                "E_LOCK_UNKNOWN_VEHICLE",
                f"相对顺序锁引用了不存在的车辆 {rl.vehicle_id}",
                f"locks.relative_order_locks[{idx}].vehicle_id"))
        for oid in rl.order_ids:
            if oid not in order_by_id:
                issues.append(ValidationIssue(
                    "E_LOCK_UNKNOWN_ORDER",
                    f"相对顺序锁引用了不存在的订单 {oid}",
                    f"locks.relative_order_locks[{idx}].order_ids"))
            else:
                rel_locked[oid] = rl.vehicle_id
    for oid, vid in d.locks.assignment_locks.items():
        if oid not in order_by_id:
            issues.append(ValidationIssue(
                "E_LOCK_UNKNOWN_ORDER", f"换车锁引用了不存在的订单 {oid}", "locks.assignment_locks"))
        if vid not in vehicle_ids:
            issues.append(ValidationIssue(
                "E_LOCK_UNKNOWN_VEHICLE",
                f"换车锁引用了不存在的车辆 {vid}", "locks.assignment_locks"))
        if oid in rel_locked and rel_locked[oid] != vid:
            issues.append(ValidationIssue(
                "E_LOCK_CONFLICT_ASSIGN",
                f"订单 {oid} 被相对顺序锁固定到 {rel_locked[oid]}，又被换车锁固定到 {vid}",
                "locks.assignment_locks"))

    return ValidationReport(issues=tuple(issues))
