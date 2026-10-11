"""阶段 2 求解测试：两基线、状态语义（任务书 6 章）与红线。

状态语义（本文件逐条断言）：
- feasible：方法给出方案且通过独立校验器复核；
- no_feasible_found：超时/未找到；绝不写成 proven_infeasible；
- proven_infeasible：仅 OR-Tools 明确返回 ROUTING_INFEASIBLE；
- invalid_input：场景校验未过；
- error：编排层异常。
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
    Scenario,
    ScenarioDraft,
    SolveStatus,
    TravelMatrix,
    Vehicle,
)
from src.solver.insert import insert_new_orders
from src.solver.ortools_core import solve_vrptw
from src.solver.original import generate_original_plan, materialize_locks, materialize_scenario
from src.solver.pipeline import solve

ROOT = Path(__file__).resolve().parent.parent
SAMPLE = json.loads((ROOT / "data" / "sample" / "sample_small.json").read_text(encoding="utf-8"))
SCN = Scenario(**SAMPLE["scenario"])
DRAFT_FILE = ROOT / "data" / "processed" / "scenarios" / "dev" / "c101-s101.json"


def _tiny_draft(order_overrides=None, vehicle_overrides=None):
    """3 旧 1 新的内联草稿（原计划由生成器产出）。"""
    orders = [
        Order(order_id="o1", node_id="A", demand=2, ready_s=0, due_s=5000, service_s=60),
        Order(order_id="o2", node_id="B", demand=2, ready_s=0, due_s=5000, service_s=60),
        Order(order_id="o3", node_id="C", demand=2, ready_s=0, due_s=5000, service_s=60),
        Order(order_id="o4", node_id="A", demand=1, ready_s=0, due_s=5000, service_s=60, is_new=True),
    ]
    if order_overrides:
        orders = order_overrides(orders)
    vehicles = [Vehicle(vehicle_id="V1", capacity=10, available_from_s=0, return_by_s=20000),
                Vehicle(vehicle_id="V2", capacity=10, available_from_s=0, return_by_s=20000)]
    if vehicle_overrides:
        vehicles = vehicle_overrides(vehicles)
    matrix = TravelMatrix(node_ids=["depot", "A", "B", "C"], time_s=[
        [0, 300, 400, 500],
        [300, 0, 200, 250],
        [400, 200, 0, 200],
        [500, 250, 200, 0],
    ])
    return ScenarioDraft(
        scenario_id="tiny", version=1, time_origin=SCN.time_origin,
        capacity_unit="load_units", depot_node_id="depot",
        orders=orders, vehicles=vehicles, travel_matrix=matrix)


class TestOriginalPlanGeneration:
    def test_plan_covers_only_old_orders(self):
        draft = _tiny_draft()
        plan = generate_original_plan(draft, time_limit_ms=5000)
        covered = [oid for r in plan.routes for oid in r.order_ids]
        assert sorted(covered) == ["o1", "o2", "o3"]  # o4 是新增，绝不进原计划

    def test_materialize_locks_ratio_and_seed_reproducible(self):
        draft = _tiny_draft()
        plan = generate_original_plan(draft, time_limit_ms=5000)
        l1 = materialize_locks(draft, plan, lock_ratio=0.34, seed=7)
        l2 = materialize_locks(draft, plan, lock_ratio=0.34, seed=7)
        assert l1 == l2  # 同种子可复现
        assert len(l1.assignment_locks) == 1  # floor(0.34*3)=1
        # 锁只作用于旧任务
        assert all(not o.is_new for o in draft.orders
                   if o.order_id in l1.assignment_locks)

    def test_materialize_scenario_valid(self):
        draft = _tiny_draft()
        scn = materialize_scenario(draft, lock_ratio=0.5, lock_seed=3,
                                   time_limit_ms=5000)
        from src.validation.checks import validate_scenario
        assert validate_scenario(scn).ok
        assert scn.original_plan.version == 1


class TestInsertionBaseline:
    def test_inserts_new_order_without_moving_old(self):
        routes, diag = insert_new_orders(SCN)
        assert routes, diag
        served = {oid: r.vehicle_id for r in routes for oid in r.order_ids}
        assert set(served) == {o.order_id for o in SCN.orders}  # 不漏单
        # 旧任务相对顺序保持（原计划 V1: o1→o2；V2: o3→o4）
        for r in routes:
            seq = r.order_ids
            if "o1" in seq and "o2" in seq:
                assert seq.index("o1") < seq.index("o2")
            if "o3" in seq and "o4" in seq:
                assert seq.index("o3") < seq.index("o4")
        # 结果通过独立校验
        from src.validation.timetable import validate_solution
        assert validate_solution(SCN, routes).ok

    def test_insert_infeasible_returns_empty_not_proof(self):
        # 新任务需求超过所有车剩余容量 → 枚举失败，但不得声称证明无解
        def override(orders):
            orders[3] = orders[3].model_copy(update={"demand": 100})
            return orders
        draft = _tiny_draft(order_overrides=override)
        scn = materialize_scenario(draft, time_limit_ms=5000)
        routes, diag = insert_new_orders(scn)
        assert routes == []
        assert diag["inserted"][-1]["inserted"] is False


class TestRebuildBaseline:
    def test_rebuild_feasible_on_sample(self):
        outcome = solve(SCN, "rebuild", time_limit_ms=5000)
        assert outcome.result.status == SolveStatus.FEASIBLE
        assert outcome.result.metrics["orders_served"] == 5
        assert outcome.validation is not None and outcome.validation.ok

    def test_proven_infeasible_when_capacity_insufficient(self):
        # 总需求 7 > 总容量 6（V1=4 装 o1+o2，V2=2 装 o3；新任务 o4 无处可放）：
        # COPT 应证明 MIP 不可行（OR-Tools CP 对该类情形只返回 FAIL，无法证明）。
        # 2026-10-11 决策：rebuild 默认 OR-Tools，证明不可行须显式 backend="copt"。
        def vov(vehicles):
            return [vehicles[0].model_copy(update={"capacity": 4}),
                    vehicles[1].model_copy(update={"capacity": 2})]
        draft = _tiny_draft(vehicle_overrides=vov)
        plan = generate_original_plan(draft, time_limit_ms=5000)
        scn = draft.to_scenario(plan)
        outcome = solve(scn, "rebuild", time_limit_ms=5000, backend="copt")
        assert outcome.result.status == SolveStatus.PROVEN_INFEASIBLE

    def test_locks_can_make_rebuild_infeasible(self):
        # 同一车上两组矛盾的相对顺序锁：[o1,o2] 与 [o2,o1]
        # （均由旧任务组成，单独都存在、合并即循环，时间链不可能同时成立）
        # → 锁定导致的证明无解，而非超时
        draft = _tiny_draft()
        plan = generate_original_plan(draft, time_limit_ms=5000)
        locks = Locks(relative_order_locks=[
            RelativeOrderLock(vehicle_id="V1", order_ids=["o1", "o2"]),
            RelativeOrderLock(vehicle_id="V1", order_ids=["o2", "o1"]),
        ])
        scn = draft.to_scenario(plan).model_copy(update={"locks": locks})
        # 证明无解依赖 COPT MIP（OR-Tools CP 无法证明），须显式指定
        outcome = solve(scn, "rebuild", time_limit_ms=5000, backend="copt")
        assert outcome.result.status == SolveStatus.PROVEN_INFEASIBLE


class TestStatusSemantics:
    def test_invalid_input(self):
        bad = SCN.model_copy(update={
            "original_plan": OriginalPlan(plan_id="bad", version=1, routes=[
                {"vehicle_id": "V1", "order_ids": ["o1", "o2"]},
                {"vehicle_id": "V2", "order_ids": ["o3"]},  # 漏 o4
            ])})
        outcome = solve(bad, "insert")
        assert outcome.result.status == SolveStatus.INVALID_INPUT
        assert not outcome.result.validation  # 未走到独立复核

    def test_short_budget_is_not_infeasible(self):
        # 红线：超时 ≠ 证明无解。Solomon 派生草稿（15 旧 + 3 新），
        # 10ms 预算内 COPT 不可能解出，状态必须是 no_feasible_found。
        # 2026-10-11 决策：rebuild 默认 OR-Tools 对 18 节点秒回可行解，
        # 无法触发超时路径，故显式用 COPT 小预算（无 COPT 环境跳过）。
        pytest.importorskip("coptpy")
        data = json.loads(DRAFT_FILE.read_text(encoding="utf-8"))
        draft = ScenarioDraft(**data["draft"])
        scn = materialize_scenario(
            draft, lock_ratio=float(data["_meta"].get("lock_ratio_target") or 0.0),
            lock_seed=int(data["_meta"].get("seed") or 0), time_limit_ms=20000)
        outcome = solve(scn, "rebuild", time_limit_ms=10, backend="copt")
        assert outcome.result.status == SolveStatus.NO_FEASIBLE_FOUND
        assert outcome.result.status != SolveStatus.PROVEN_INFEASIBLE
        assert outcome.result.metrics.get("solver_status") != "MIP_INFEASIBLE"

    def test_insert_no_position_is_not_infeasible(self):
        def override(orders):
            orders[3] = orders[3].model_copy(update={"demand": 100})
            return orders
        draft = _tiny_draft(order_overrides=override)
        scn = materialize_scenario(draft, time_limit_ms=5000)
        outcome = solve(scn, "insert")
        assert outcome.result.status == SolveStatus.NO_FEASIBLE_FOUND

    def test_full_dev_draft_materializes_and_solves(self):
        """开发集草稿端到端：升格 → rebuild → 独立复核 feasible。"""
        data = json.loads(DRAFT_FILE.read_text(encoding="utf-8"))
        draft = ScenarioDraft(**data["draft"])
        scn = materialize_scenario(
            draft, lock_ratio=float(data["_meta"].get("lock_ratio_target") or 0.0),
            lock_seed=int(data["_meta"].get("seed") or 0), time_limit_ms=20000)
        outcome = solve(scn, "rebuild", time_limit_ms=20000)
        assert outcome.result.status == SolveStatus.FEASIBLE
        assert outcome.result.metrics["orders_served"] == len(scn.orders)
        assert outcome.result.metrics["total_travel_time_s"] > 0


class TestOrtoolsRawMapping:
    def test_status_names_are_routing_enums(self):
        # 防止状态名与 ortools 枚举脱节（本阶段已踩过）
        outcome = solve_vrptw(SCN, [o.order_id for o in SCN.orders],
                              time_limit_ms=5000)
        assert outcome.raw_status in (
            "ROUTING_SUCCESS", "ROUTING_OPTIMAL",
            "ROUTING_PARTIAL_SUCCESS_LOCAL_OPTIMUM_NOT_REACHED",
            "ROUTING_FAIL", "ROUTING_FAIL_TIMEOUT", "ROUTING_INFEASIBLE")
