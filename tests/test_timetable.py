"""独立校验器测试：关键规则均有可手算预期（任务书阶段 2 验收要求）。

手算样本 data/sample/sample_small.json：
- V1 路线 [o1(A), o2(B)]：出发 0 → 行驶 300 到达 A → start 300, depart 420
  → 行驶 200 到达 B → start 620, depart 740 → 返回行驶 610 → return 1350。
- 等待场景与容量/时间窗/锁定违例由本文件内联构造并逐项核对。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.domain.models import (
    Locks,
    Order,
    OriginalPlan,
    RelativeOrderLock,
    RouteResult,
    Scenario,
    TravelMatrix,
    Vehicle,
)
from src.validation.timetable import evaluate_route, validate_solution

ROOT = Path(__file__).resolve().parent.parent
SAMPLE = json.loads((ROOT / "data" / "sample" / "sample_small.json").read_text(encoding="utf-8"))
SCN = Scenario(**SAMPLE["scenario"])


def _hand_made_scenario():
    """3 订单 2 车的内联手算场景（含一次必然等待）。"""
    orders = [
        Order(order_id="a", node_id="A", demand=3, ready_s=500, due_s=2000, service_s=100),
        Order(order_id="b", node_id="B", demand=3, ready_s=0, due_s=900, service_s=100),
        Order(order_id="n", node_id="C", demand=1, ready_s=0, due_s=5000, service_s=0, is_new=True),
    ]
    vehicles = [
        Vehicle(vehicle_id="V1", capacity=6, available_from_s=0, return_by_s=3000),
        Vehicle(vehicle_id="V2", capacity=10, available_from_s=100, return_by_s=4000),
    ]
    matrix = TravelMatrix(node_ids=["depot", "A", "B", "C"], time_s=[
        [0, 100, 200, 300],
        [100, 0, 150, 250],
        [200, 150, 0, 100],
        [300, 250, 100, 0],
    ])
    plan = OriginalPlan(plan_id="p1", version=1, routes=[
        {"vehicle_id": "V1", "order_ids": ["a", "b"]},
    ])
    return Scenario(
        scenario_id="hand", version=1, time_origin=SCN.time_origin,
        capacity_unit="load_units", depot_node_id="depot",
        orders=orders, vehicles=vehicles, travel_matrix=matrix,
        original_plan=plan,
    )


class TestHandComputedSchedule:
    def test_sample_v1_route_matches_hand_calculation(self):
        ev = evaluate_route(SCN, "V1", ["o1", "o2"])
        assert ev.ok, ev.issues
        s1, s2 = ev.stops
        # 手算：depot→A 300；A start 300（ready 0），depart 420；A→B 200，
        # B start 620，depart 740；B→depot 610 → return 1350
        assert (s1.arrive_s, s1.wait_s, s1.start_s, s1.depart_s) == (300, 0, 300, 420)
        assert (s2.arrive_s, s2.wait_s, s2.start_s, s2.depart_s) == (620, 0, 620, 740)
        assert ev.return_s == 1350
        assert ev.travel_time_s == 300 + 200 + 610
        assert ev.total_load == 7.0

    def test_waiting_computed_correctly(self):
        # V1 若先去 B（ready 0）再去 A（ready 500）：depot→B 200，
        # B start 200 depart 300；B→A 150，arrive 450，等到 500 才开始
        ev = evaluate_route(_hand_made_scenario(), "V1", ["b", "a"])
        s1, s2 = ev.stops
        assert (s1.arrive_s, s1.start_s) == (200, 200)
        assert (s2.arrive_s, s2.wait_s, s2.start_s) == (450, 50, 500)
        assert ev.return_s == 500 + 100 + 100  # A 服务后 depart 600，返回 100

    def test_fixed_departure_respected(self):
        # V2 available_from_s=100：首站到达 = 100 + 行驶时间
        ev = evaluate_route(_hand_made_scenario(), "V2", ["b"])
        assert ev.stops[0].arrive_s == 100 + 200


class TestViolationDetection:
    def test_time_window_violation(self):
        # 单站即迟到：行驶 300 > due 250
        orders = [Order(order_id="x", node_id="A", demand=1,
                        ready_s=0, due_s=250, service_s=100)]
        vehicles = [Vehicle(vehicle_id="V1", capacity=5,
                            available_from_s=0, return_by_s=10000)]
        matrix = TravelMatrix(node_ids=["depot", "A"], time_s=[
            [0, 300], [300, 0]])
        scn = Scenario(
            scenario_id="tw", version=1, time_origin=SCN.time_origin,
            capacity_unit="load_units", depot_node_id="depot",
            orders=orders, vehicles=vehicles, travel_matrix=matrix,
            original_plan=OriginalPlan(plan_id="p", version=1, routes=[
                {"vehicle_id": "V1", "order_ids": ["x"]}]))
        ev = evaluate_route(scn, "V1", ["x"])
        codes = [i.code for i in ev.issues]
        assert "E_STOP_WINDOW" in codes
        assert not ev.ok

    def test_capacity_violation(self):
        scn = _hand_made_scenario()
        # V1 容量 6：a(3)+b(3)=6 可行；插入 n(1) 后 7 > 6
        ev = evaluate_route(scn, "V1", ["a", "b", "n"])
        codes = [i.code for i in ev.issues]
        assert "E_ROUTE_CAPACITY" in codes
        assert not ev.ok

    def test_return_late(self):
        # 手算 V1 [a,b,n]：a start 500 d 600；b arrive 750 d 850；
        # n arrive 950 start 950；C→depot 300 → return 1250 ≤ 3000（不迟到）
        scn = _hand_made_scenario()
        ev = evaluate_route(scn, "V1", ["a", "b", "n"])
        assert ev.return_s == 1250
        # 把 V2 的 return_by 缩到 800：V2 [b,n,a] return = 950+100=1050? 见下
        tight = scn.model_copy(update={"vehicles": (
            scn.vehicles[0],
            scn.vehicles[1].model_copy(update={"return_by_s": 800}),
        )})
        ev2 = evaluate_route(tight, "V2", ["b", "n", "a"])
        # V2 出发 100：b arrive 300 d 400；n arrive 500 d 500；a arrive 750，
        # 等 ready 500 无等待 start 750 d 850；A→depot 100 → return 950 > 800
        assert "E_ROUTE_RETURN_LATE" in [i.code for i in ev2.issues]

    def test_unknown_order_and_vehicle(self):
        ev = evaluate_route(SCN, "V1", ["o1", "ghost"])
        assert "E_ROUTE_UNKNOWN_ORDER" in [i.code for i in ev.issues]
        ev2 = evaluate_route(SCN, "V9", ["o1"])
        assert "E_ROUTE_UNKNOWN_VEHICLE" in [i.code for i in ev2.issues]


class TestSolutionLevel:
    def _routes(self, *pairs):
        # return_s 必须取独立重算值（P1 E：伪造返回时刻会被拒绝）
        return [
            RouteResult(
                vehicle_id=v, order_ids=list(o),
                return_s=evaluate_route(SCN, v, list(o)).return_s)
            for v, o in pairs
        ]

    def test_missing_order_rejected(self):
        sv = validate_solution(SCN, self._routes(("V1", ["o1", "o2"]), ("V2", ["o3"])))
        assert not sv.ok
        assert "E_SOL_MISSING_ORDER" in [i.code for i in sv.issues]

    def test_duplicate_order_rejected(self):
        sv = validate_solution(SCN, self._routes(
            ("V1", ["o1", "o2"]), ("V2", ["o3", "o4", "o5", "o1"])))
        assert "E_SOL_DUP_ORDER" in [i.code for i in sv.issues]

    def test_full_coverage_ok(self):
        sv = validate_solution(SCN, self._routes(
            ("V1", ["o1", "o2", "o5"]), ("V2", ["o3", "o4"])))
        assert sv.ok, sv.to_dict()

    def test_assignment_lock_violation(self):
        locked = SCN.model_copy(update={"locks": Locks(assignment_locks={"o1": "V2"})})
        sv = validate_solution(locked, self._routes(
            ("V1", ["o1", "o2"]), ("V2", ["o3", "o4", "o5"])))
        assert "E_SOL_ASSIGN_LOCK" in [i.code for i in sv.issues]

    def test_assignment_lock_respected(self):
        locked = SCN.model_copy(update={"locks": Locks(assignment_locks={"o1": "V1"})})
        sv = validate_solution(locked, self._routes(
            ("V1", ["o1", "o2", "o5"]), ("V2", ["o3", "o4"])))
        assert sv.ok

    def test_relative_order_lock(self):
        locks = Locks(relative_order_locks=[RelativeOrderLock(
            vehicle_id="V1", order_ids=["o1", "o2"])])
        locked = SCN.model_copy(update={"locks": locks})
        # o2 在 o1 之前 → 违反
        sv = validate_solution(locked, self._routes(
            ("V1", ["o2", "o1", "o5"]), ("V2", ["o3", "o4"])))
        assert "E_SOL_REL_ORDER_LOCK" in [i.code for i in sv.issues]
        # o1 在 o2 之前，新任务 o5 穿插其间 → 允许
        sv2 = validate_solution(locked, self._routes(
            ("V1", ["o1", "o5", "o2"]), ("V2", ["o3", "o4"])))
        assert sv2.ok


class TestRedLines:
    def test_validator_ignores_solver_claims(self):
        # 红线：独立校验器不采信求解器"可行"标志。
        # 手工构造一个"求解器可能声称可行"但实际违反时间窗的方案，
        # 校验器必须逐条重算并拒绝。
        scn = _hand_made_scenario()
        bad = [RouteResult(vehicle_id="V1", order_ids=["a", "b", "n"], return_s=1250)]
        sv = validate_solution(scn, bad)
        assert not sv.ok
        assert any(i.code == "E_ROUTE_CAPACITY" for i in sv.issues)
