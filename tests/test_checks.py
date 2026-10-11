"""场景级交叉校验测试：重复 ID、漏单、锁冲突等必须有明确错误码。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.domain.models import (
    Locks,
    Order,
    OriginalPlan,
    RelativeOrderLock,
    RoutePlan,
    Scenario,
    TravelMatrix,
    Vehicle,
)
from src.validation.checks import validate_scenario

TZ = timezone(timedelta(hours=8))
T0 = datetime(2026, 10, 10, 8, 0, 0, tzinfo=TZ)
NODES = ["depot", "A", "B"]
MATRIX = TravelMatrix(node_ids=NODES, time_s=[[0, 10, 20], [10, 0, 15], [20, 15, 0]])


def make_scenario(
    orders=None,
    vehicles=None,
    routes=None,
    locks=None,
    depot="depot",
) -> Scenario:
    orders = orders or [
        Order(order_id="o1", node_id="A", demand=1, ready_s=0, due_s=1000, service_s=10),
        Order(order_id="o2", node_id="B", demand=1, ready_s=0, due_s=1000, service_s=10),
    ]
    vehicles = vehicles or [
        Vehicle(vehicle_id="V1", capacity=10, available_from_s=0, return_by_s=5000),
        Vehicle(vehicle_id="V2", capacity=10, available_from_s=0, return_by_s=5000),
    ]
    routes = routes if routes is not None else [
        RoutePlan(vehicle_id="V1", order_ids=["o1", "o2"]),
    ]
    return Scenario(
        scenario_id="s-test",
        version=1,
        time_origin=T0,
        capacity_unit="load_units",
        depot_node_id=depot,
        orders=orders,
        vehicles=vehicles,
        travel_matrix=MATRIX,
        original_plan=OriginalPlan(plan_id="p1", version=1, routes=routes),
        locks=locks or Locks(),
    )


def codes(report) -> set:
    return {i.code for i in report.issues}


class TestValidBaseline:
    def test_minimal_valid_scenario(self):
        assert validate_scenario(make_scenario()).ok


class TestDuplicatesAndReferences:
    def test_duplicate_order_id(self):
        orders = [
            Order(order_id="o1", node_id="A", demand=1, ready_s=0, due_s=100, service_s=1),
            Order(order_id="o1", node_id="B", demand=1, ready_s=0, due_s=100, service_s=1),
        ]
        r = validate_scenario(make_scenario(orders=orders))
        assert "E_DUP_ORDER_ID" in codes(r)

    def test_duplicate_vehicle_id(self):
        vehicles = [
            Vehicle(vehicle_id="V1", capacity=10, available_from_s=0, return_by_s=5000),
            Vehicle(vehicle_id="V1", capacity=10, available_from_s=0, return_by_s=5000),
        ]
        r = validate_scenario(make_scenario(vehicles=vehicles))
        assert "E_VEHICLE_DUP_ID" in codes(r)

    def test_order_node_not_in_matrix(self):
        orders = [
            Order(order_id="o1", node_id="ZZZ", demand=1, ready_s=0, due_s=100, service_s=1),
            Order(order_id="o2", node_id="B", demand=1, ready_s=0, due_s=100, service_s=1),
        ]
        r = validate_scenario(make_scenario(orders=orders))
        assert "E_ORDER_NODE_MISSING" in codes(r)

    def test_depot_not_in_matrix(self):
        r = validate_scenario(make_scenario(depot="NOWHERE"))
        assert "E_DEPOT_MISSING" in codes(r)


class TestPlanConsistency:
    def test_missing_old_order_is_rejected(self):
        # 漏单 = 静默删除订单，必须报错
        r = validate_scenario(make_scenario(routes=[RoutePlan(vehicle_id="V1", order_ids=["o1"])]))
        assert "E_PLAN_MISSING_ORDER" in codes(r)

    def test_duplicate_plan_order(self):
        routes = [
            RoutePlan(vehicle_id="V1", order_ids=["o1", "o2"]),
            RoutePlan(vehicle_id="V2", order_ids=["o1"]),
        ]
        r = validate_scenario(make_scenario(routes=routes))
        assert "E_PLAN_DUP_ORDER" in codes(r)

    def test_new_order_in_plan_rejected(self):
        orders = [
            Order(order_id="o1", node_id="A", demand=1, ready_s=0, due_s=100, service_s=1),
            Order(order_id="o9", node_id="B", demand=1, ready_s=0, due_s=100, service_s=1, is_new=True),
        ]
        routes = [RoutePlan(vehicle_id="V1", order_ids=["o1", "o9"])]
        r = validate_scenario(make_scenario(orders=orders, routes=routes))
        assert "E_PLAN_NEW_ORDER" in codes(r)

    def test_unknown_order_in_plan(self):
        routes = [RoutePlan(vehicle_id="V1", order_ids=["o1", "oX"])]
        r = validate_scenario(make_scenario(routes=routes))
        assert "E_PLAN_UNKNOWN_ORDER" in codes(r)

    def test_unknown_vehicle_in_plan(self):
        routes = [RoutePlan(vehicle_id="V9", order_ids=["o1", "o2"])]
        r = validate_scenario(make_scenario(routes=routes))
        assert "E_PLAN_UNKNOWN_VEHICLE" in codes(r)


class TestLocks:
    def test_lock_on_new_order_rejected(self):
        orders = [
            Order(order_id="o1", node_id="A", demand=1, ready_s=0, due_s=100, service_s=1),
            Order(order_id="o9", node_id="B", demand=1, ready_s=0, due_s=100, service_s=1, is_new=True),
        ]
        locks = Locks(assignment_locks={"o9": "V1"})
        r = validate_scenario(make_scenario(orders=orders, locks=locks))
        assert "E_LOCK_NEW_ORDER" in codes(r)

    def test_lock_unknown_vehicle(self):
        locks = Locks(assignment_locks={"o1": "V9"})
        r = validate_scenario(make_scenario(locks=locks))
        assert "E_LOCK_UNKNOWN_VEHICLE" in codes(r)

    def test_assignment_vs_relative_conflict(self):
        locks = Locks(
            assignment_locks={"o1": "V2"},
            relative_order_locks=[RelativeOrderLock(vehicle_id="V1", order_ids=["o1", "o2"])],
        )
        r = validate_scenario(make_scenario(locks=locks))
        assert "E_LOCK_CONFLICT_ASSIGN" in codes(r)

    def test_consistent_locks_pass(self):
        locks = Locks(
            assignment_locks={"o1": "V1"},
            relative_order_locks=[RelativeOrderLock(vehicle_id="V1", order_ids=["o1", "o2"])],
        )
        assert validate_scenario(make_scenario(locks=locks)).ok
