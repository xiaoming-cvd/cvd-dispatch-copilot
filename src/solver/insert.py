"""插单基线（任务书阶段 2）：枚举插入，不搬动旧任务，不漏单。

规则：
- 旧任务保持原计划中的相对顺序与车辆，一辆车的旧序列视为固定槽位；
- 新任务依次（按 orders 中出现顺序）尝试插入所有车的所有槽位
  （含队首、队尾与任意两旧任务之间），用独立校验器逐位置判定可行性；
- 选行驶时间增量最小的可行槽位；无可行槽位则返回未找到
  （插单基线不做"证明无解"声称）。
- 代价与可行性判定全部来自 src/validation/timetable.py 的重算结果。
- 白盒化（决策过程面板）：逐新任务记录枚举计数（检查槽位数/可行数/
  按容量、时间窗、返仓时刻分类的拒绝数）与前三名可行备选及增量，
  全部来自本轮真实枚举，不额外求解。
"""
from __future__ import annotations

import time

from ..domain.models import RouteResult, Scenario
from ..validation.timetable import evaluate_route

# 拒绝原因分类（与独立校验器 issue code 对应；一个位置可能同时多类违规，
# 按容量→时间窗→返仓→其他 的优先级取首要原因归类计数）
_REJECT_LABELS = {
    "capacity": "容量",
    "time_window": "时间窗",
    "return_late": "返回时刻",
    "other": "其他",
}


def _classify_reject(issues) -> str:
    codes = {i.code for i in issues}
    if "E_ROUTE_CAPACITY" in codes:
        return "capacity"
    if "E_STOP_WINDOW" in codes:
        return "time_window"
    if "E_ROUTE_RETURN_LATE" in codes:
        return "return_late"
    return "other"


def _travel_of(scn: Scenario, vehicle_id: str, order_ids: list[str]) -> int | None:
    ev = evaluate_route(scn, vehicle_id, order_ids)
    return ev.travel_time_s if ev.ok else None


def insert_new_orders(
    scn: Scenario,
    new_order_ids: list[str] | None = None,
    time_limit_ms: int | None = None,
) -> tuple[list[RouteResult], dict]:
    """返回 (方案, 诊断信息)。方案为空列表 = 未找到可行插法。

    time_limit_ms 为真实执行预算（P1 G）：枚举循环逐位置检查剩余时间，
    超时立即返回空方案并在 diagnostics 标记 time_budget_exhausted
    （超时 ≠ 证明无解，由 pipeline 按状态语义处理）。
    """
    started = time.monotonic()

    def _out_of_budget() -> bool:
        return (time_limit_ms is not None
                and (time.monotonic() - started) * 1000 >= time_limit_ms)

    order_by_id = {o.order_id: o for o in scn.orders}
    if new_order_ids is None:
        new_order_ids = [o.order_id for o in scn.orders if o.is_new]

    # 当前各车序列：从原计划出发（旧任务固定）
    routes: dict[str, list[str]] = {
        r.vehicle_id: list(r.order_ids) for r in scn.original_plan.routes
    }
    for v in scn.vehicles:  # 原计划未用到的车也参与插单
        routes.setdefault(v.vehicle_id, [])

    diagnostics: list[dict] = []
    for oid in new_order_ids:
        if oid not in order_by_id:
            raise ValueError(f"E_INSERT_UNKNOWN_ORDER: {oid}")
        best: tuple[int, str, int] | None = None  # (增量, 车辆, 插入位置)
        feasible: list[tuple[int, str, int]] = []  # 全部可行槽位（增量升序用）
        rec = {"order_id": oid, "slots_checked": 0, "feasible_count": 0,
               "rejected_by": {k: 0 for k in _REJECT_LABELS},
               "per_vehicle": {}}
        base_costs = {vid: _travel_of(scn, vid, seq) for vid, seq in routes.items()}
        for vid, seq in routes.items():
            if _out_of_budget():
                rec["stopped_early"] = "time_budget_exhausted"
                return [], {"inserted": diagnostics + [{
                    "order_id": oid, "inserted": False,
                    "reason": "time_budget_exhausted", **rec}]}
            if base_costs[vid] is None:  # 原计划路线本身不可行（不应发生，防御）
                continue
            pv = {"slots": 0, "feasible": 0}
            rec["per_vehicle"][vid] = pv
            for pos in range(len(seq) + 1):
                if _out_of_budget():
                    rec["stopped_early"] = "time_budget_exhausted"
                    return [], {"inserted": diagnostics + [{
                        "order_id": oid, "inserted": False,
                        "reason": "time_budget_exhausted", **rec}]}
                rec["slots_checked"] += 1
                pv["slots"] += 1
                cand = seq[:pos] + [oid] + seq[pos:]
                ev = evaluate_route(scn, vid, cand)
                if not ev.ok:
                    rec["rejected_by"][_classify_reject(ev.issues)] += 1
                    continue
                rec["feasible_count"] += 1
                pv["feasible"] += 1
                delta = ev.travel_time_s - base_costs[vid]
                feasible.append((delta, vid, pos))
                if best is None or delta < best[0]:
                    best = (delta, vid, pos)
        if best is None:
            diagnostics.append({
                "order_id": oid,
                "inserted": False,
                "reason": "所有车辆的全部插入位置均不可行（时间窗/容量/返回时刻）",
                **rec,
            })
            return [], {"inserted": diagnostics}
        delta, vid, pos = best
        # 前三名可行备选（不含已选中的槽位），供决策过程面板展示
        alts = [{"vehicle_id": a[1], "position": a[2], "travel_delta_s": a[0]}
                for a in sorted(feasible) if not (a[1] == vid and a[2] == pos)][:3]
        routes[vid] = routes[vid][:pos] + [oid] + routes[vid][pos:]
        diagnostics.append({
            "order_id": oid, "inserted": True,
            "vehicle_id": vid, "position": pos, "travel_delta_s": delta,
            "alternatives": alts, **rec,
        })

    result = [
        RouteResult(vehicle_id=vid, order_ids=seq,
                    return_s=evaluate_route(scn, vid, seq).return_s)
        for vid, seq in routes.items() if seq
    ]
    return result, {"inserted": diagnostics}
