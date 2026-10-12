"""阶段 3 测试：低扰动重排、扰动指标、diff 与选择规则（第三阶段复审修订版）。

覆盖：
- 无新增任务：扰动为 0；只插新任务：顺延不计扰动；跨车：换车只统计旧任务；
- 两类锁保持（确定性场景，不 skip）；
- 选择规则：成本上限比例参数化（1.05 vs 1.20 池不同）；
- 跨方法成本上限（P0-1）：reference 更优时无达标候选，状态如实；
- 预算不重复扣减（P0-2）：假时钟断言搜索跑到绝对截止；
- 新任务可移入空闲车辆（P1）：邻域生成含空路线车辆；
- 候选可审计（P1）：稳定 candidate_id + 完整路线 + validation_ok；
- 规模上限（P1/P2）：archive 满时 stop_reason=archive_full。
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
    RoutePlan,
    RouteResult,
    Scenario,
    SolveStatus,
    TravelMatrix,
    Vehicle,
)
from src.solver import diff as diff_mod
from src.solver import low_disruption as low_mod
from src.solver import perturbation as pert_mod
from src.solver.pipeline import solve
from src.validation.timetable import (
    evaluate_original_plan, evaluate_route, validate_solution)

ROOT = Path(__file__).resolve().parent.parent
SAMPLE = json.loads((ROOT / "data" / "sample" / "sample_small.json").read_text(encoding="utf-8"))
SCN = Scenario(**SAMPLE["scenario"])


def _scn(orders, vehicles, matrix, routes, locks=None):
    return Scenario(
        scenario_id="t3", version=1, time_origin=SCN.time_origin,
        capacity_unit="load_units", depot_node_id="depot",
        orders=orders, vehicles=vehicles, travel_matrix=matrix,
        original_plan=OriginalPlan(
            plan_id="p0", version=1,
            routes=[RoutePlan(vehicle_id=v, order_ids=tuple(o)) for v, o in routes]),
        locks=locks or Locks(),
    )


def _matrix():
    return TravelMatrix(node_ids=["depot", "A", "B", "C"], time_s=[
        [0, 300, 400, 500],
        [300, 0, 200, 250],
        [400, 200, 0, 200],
        [500, 250, 200, 0],
    ])


def _base(lock=None):
    """V1: o1→o2（A→B），V2: o3（C）；宽时间窗，必然可行。"""
    orders = [
        Order(order_id="o1", node_id="A", demand=1, ready_s=0, due_s=5000, service_s=60),
        Order(order_id="o2", node_id="B", demand=1, ready_s=0, due_s=5000, service_s=60),
        Order(order_id="o3", node_id="C", demand=1, ready_s=0, due_s=5000, service_s=60),
    ]
    vehicles = [Vehicle(vehicle_id="V1", capacity=10, available_from_s=0, return_by_s=20000),
                Vehicle(vehicle_id="V2", capacity=10, available_from_s=0, return_by_s=20000)]
    return _scn(orders, vehicles, _matrix(),
                [("V1", ["o1", "o2"]), ("V2", ["o3"])], locks=lock)


def _routes(scn, mapping):
    out = []
    for vid, seq in mapping.items():
        ev = evaluate_route(scn, vid, list(seq))
        out.append(RouteResult(vehicle_id=vid, order_ids=tuple(seq),
                               return_s=ev.return_s))
    return out


def _mk_cand(key, travel, vc, ab):
    return low_mod.Candidate(
        key=key, routes=(), total_travel_s=travel,
        perturbation=pert_mod.Perturbation(
            vehicle_changes=vc, adjacency_breaks=ab, adjacency_detail=()))


class TestPerturbationMetric:
    def test_no_new_orders_zero_perturbation(self):
        scn = _base()
        p = pert_mod.measure(scn, _routes(scn, {"V1": ["o1", "o2"], "V2": ["o3"]}))
        assert p.vehicle_changes == 0
        assert p.adjacency_breaks == 0

    def test_insertion_shift_does_not_break_adjacency(self):
        scn = _base()
        new = scn.orders[2].model_copy(update={"order_id": "o4", "node_id": "A",
                                               "is_new": True})
        scn2 = scn.model_copy(update={"orders": tuple(list(scn.orders) + [new])})
        p = pert_mod.measure(scn2, _routes(scn2, {"V1": ["o1", "o4", "o2"], "V2": ["o3"]}))
        assert p.vehicle_changes == 0
        assert p.adjacency_breaks == 0

    def test_vehicle_change_counts_only_old(self):
        scn = _base()
        new = scn.orders[2].model_copy(update={"order_id": "o4", "node_id": "A",
                                               "is_new": True})
        scn2 = scn.model_copy(update={"orders": tuple(list(scn.orders) + [new])})
        p = pert_mod.measure(scn2, _routes(scn2, {"V1": ["o1", "o2", "o3"], "V2": ["o4"]}))
        assert p.vehicle_changes == 1
        assert p.adjacency_breaks == 0

    def test_old_reorder_breaks_adjacency(self):
        scn = _base()
        p = pert_mod.measure(scn, _routes(scn, {"V1": ["o2", "o1"], "V2": ["o3"]}))
        assert p.adjacency_breaks == 1
        reasons = {s.pair: s.reason for s in p.adjacency_detail}
        assert reasons[("o1", "o2")] == "同车旧任务顺序被改变"


class TestDiff:
    REASONS = {
        "新增任务（原计划不存在）",
        "车辆与相对位置未变",
        "位置因新任务插入顺延（不计扰动）",
        "同车旧任务顺序改变",
    }

    def test_reasons_from_fixed_vocabulary(self):
        scn = _base()
        new = scn.orders[2].model_copy(update={"order_id": "o4", "node_id": "A",
                                               "is_new": True})
        scn2 = scn.model_copy(update={"orders": tuple(list(scn.orders) + [new])})
        d = diff_mod.diff_orders(scn2, _routes(scn2, {"V1": ["o1", "o4", "o2"], "V2": ["o3"]}))
        for oid, entry in d["orders"].items():
            assert entry["change_reason"] in self.REASONS | {"从 V1 换到 V2", "从 V2 换到 V1"}
        assert d["orders"]["o4"]["change_reason"] == "新增任务（原计划不存在）"
        assert d["orders"]["o2"]["change_reason"] == "位置因新任务插入顺延（不计扰动）"
        assert d["orders"]["o1"]["change_reason"] == "车辆与相对位置未变"
        assert d["orders"]["o3"]["change_reason"] == "车辆与相对位置未变"

    def test_no_new_orders_all_unchanged(self):
        scn = _base()
        d = diff_mod.diff_orders(scn, _routes(scn, {"V1": ["o1", "o2"], "V2": ["o3"]}))
        assert all(e["change_reason"] == "车辆与相对位置未变"
                   for e in d["orders"].values())
        assert d["perturbation"]["vehicle_changes"] == 0


class TestSelectionRule:
    """选择规则单元测试（确定性候选，不依赖搜索）。"""

    CANDS = [
        _mk_cand((("V1", ("a",)),), 100, 0, 0),
        _mk_cand((("V1", ("b",)),), 108, 0, 1),
        _mk_cand((("V1", ("c",)),), 118, 1, 0),
        _mk_cand((("V1", ("d",)),), 140, 0, 0),
    ]

    def test_ratio_parameter_changes_pool(self):
        diag: dict = {}
        low1, _, sel1 = low_mod._select(list(self.CANDS), diag, 1.05, None)
        assert sel1["cost_cap_s"] == 105.0
        assert low1.total_travel_s == 100  # 105 内只有 100
        low2, _, sel2 = low_mod._select(list(self.CANDS), diag, 1.20, None)
        assert sel2["cost_cap_s"] == 120.0
        assert low2.total_travel_s == 100  # (0,0,100) 仍字典序最小
        # 上限放宽改变了池构成（108/118 进入池）
        assert sel1["pool_size"] == 1 and sel2["pool_size"] == 3

    def test_ratio_out_of_range_rejected(self):
        with pytest.raises(ValueError):
            low_mod.solve_low_disruption(_base(), 100, cost_cap_ratio=3.0)

    def test_no_candidate_within_cap_status(self):
        cands = [self.CANDS[3]]  # 唯一候选 140
        diag: dict = {}
        low, best, sel = low_mod._select(list(cands), diag, 1.10, 100.0)
        assert sel["selection_status"] == "no_candidate_within_cost_cap"
        assert sel["within_reference_cap"] is False
        assert sel["reference_best_cost_s"] == 100.0
        assert sel["cost_cap_s"] == 110.0
        assert low is best  # 无达标候选 → 推荐池内最低成本

    def test_reference_used_when_given(self):
        diag: dict = {}
        _, _, sel = low_mod._select(list(self.CANDS), diag, 1.10, 90.0)
        assert sel["reference_source"] == "reference_results"
        assert sel["cost_cap_s"] == 99.0


class TestLowDisruptionSolve:
    def test_pipeline_feasible_and_validated(self):
        outcome = solve(SCN, "low_disruption", time_limit_ms=5000)
        assert outcome.result.status == SolveStatus.FEASIBLE
        assert outcome.validation is not None and outcome.validation.ok
        assert outcome.result.metrics["orders_served"] == 5

    def test_candidates_auditable_by_id(self):
        """P1 可审计：推荐/最低成本/Pareto 候选有稳定 ID、完整路线、validation_ok。"""
        outcome = solve(SCN, "low_disruption", time_limit_ms=5000)
        audit = outcome.result.metrics["audit_candidates"]
        assert audit, "审计候选不得为空"
        ids = set()
        for entry in audit:
            assert entry["candidate_id"].startswith("cand-")
            assert entry["routes"], f"{entry['candidate_id']} 缺完整路线"
            assert entry["validation_ok"] is True
            ids.add(entry["candidate_id"])
        assert len(ids) == len(audit), "candidate_id 必须稳定唯一"
        rec_id = outcome.result.metrics["recommended_candidate"]["candidate_id"]
        assert any(e["candidate_id"] == rec_id and e["role"] == "recommended"
                   for e in audit)

    def test_relative_order_lock_members_not_moved(self):
        lock = Locks(relative_order_locks=[
            RelativeOrderLock(vehicle_id="V1", order_ids=["o1", "o2"])])
        scn = _base(lock=lock)
        new = scn.orders[1].model_copy(update={"order_id": "o4", "node_id": "A",
                                               "demand": 1, "is_new": True})
        scn2 = scn.model_copy(update={"orders": tuple(list(scn.orders) + [new])})
        outcome = solve(scn2, "low_disruption", time_limit_ms=3000)
        assert outcome.result.status == SolveStatus.FEASIBLE, outcome.result.warnings
        seqs = {r.vehicle_id: list(r.order_ids) for r in outcome.result.routes}
        assert "o1" in seqs["V1"] and "o2" in seqs["V1"]
        assert seqs["V1"].index("o1") < seqs["V1"].index("o2")

    def test_assignment_lock_keeps_task_on_vehicle(self):
        lock = Locks(assignment_locks={"o3": "V2"})
        scn = _base(lock=lock)
        new = scn.orders[1].model_copy(update={"order_id": "o4", "node_id": "A",
                                               "is_new": True})
        scn2 = scn.model_copy(update={"orders": tuple(list(scn.orders) + [new])})
        outcome = low_mod.solve_low_disruption(scn2, time_limit_ms=3000)
        assert outcome.candidates, "该场景必然有候选（插单种子）"
        for cand in outcome.candidates:
            seqs = {r.vehicle_id: list(r.order_ids) for r in cand.routes}
            assert "o3" not in seqs.get("V1", [])

    def test_every_candidate_passes_independent_validation(self):
        outcome = low_mod.solve_low_disruption(SCN, time_limit_ms=3000)
        assert outcome.candidates
        for cand in outcome.candidates:
            assert validate_solution(SCN, list(cand.routes)).ok

    def test_no_feasible_is_not_proven_infeasible(self):
        orders = [
            Order(order_id="o1", node_id="A", demand=5, ready_s=0, due_s=5000, service_s=60),
            Order(order_id="o2", node_id="B", demand=5, ready_s=0, due_s=5000, service_s=60,
                  is_new=True),
        ]
        vehicles = [Vehicle(vehicle_id="V1", capacity=6, available_from_s=0, return_by_s=20000)]
        matrix = TravelMatrix(node_ids=["depot", "A", "B"], time_s=[
            [0, 300, 400], [300, 0, 200], [400, 200, 0]])
        scn = _scn(orders, vehicles, matrix, [("V1", ["o1"])])
        outcome = solve(scn, "low_disruption", time_limit_ms=2000)
        assert outcome.result.status == SolveStatus.NO_FEASIBLE_FOUND
        assert outcome.result.status != SolveStatus.PROVEN_INFEASIBLE


class TestReviewCounterexamples:
    """第三阶段复审要求的四个确定性反例（不允许 skip）。"""

    def test_cross_method_cap_no_candidate(self):
        """P0-1：reference（跨方法，需移动旧任务才能得到）优于池内最优时，
        上限内无候选，selection_status 如实标注。"""
        orders = [
            Order(order_id="o1", node_id="A", demand=1, ready_s=0, due_s=5000, service_s=60),
            Order(order_id="o2", node_id="B", demand=1, ready_s=0, due_s=5000, service_s=60),
            Order(order_id="o3", node_id="C", demand=1, ready_s=0, due_s=5000, service_s=60),
            Order(order_id="o4", node_id="A", demand=1, ready_s=0, due_s=5000, service_s=60,
                  is_new=True),
        ]
        vehicles = [Vehicle(vehicle_id="V1", capacity=2, available_from_s=0, return_by_s=20000),
                    Vehicle(vehicle_id="V2", capacity=10, available_from_s=0, return_by_s=20000)]
        # 任意节点间行驶 100s；V1 满（o1+o2），插单只能把 o4 放到 V2
        matrix = TravelMatrix(node_ids=["depot", "A", "B", "C"], time_s=[
            [0, 100, 100, 100],
            [100, 0, 100, 100],
            [100, 100, 0, 100],
            [100, 100, 100, 0],
        ])
        scn2 = _scn(orders, vehicles, matrix, [("V1", ["o1", "o2"]), ("V2", ["o3"])])
        # 跨方法参考（完全重排才可能得到）：o2 移到 V2，o4 与 o1 同址同车
        ref_routes = _routes(scn2, {"V1": ["o1", "o4"], "V2": ["o3", "o2"]})
        assert validate_solution(scn2, ref_routes).ok
        outcome = low_mod.solve_low_disruption(
            scn2, time_limit_ms=0, reference_results=[("rebuild", ref_routes)])
        assert len(outcome.candidates) == 1, "0ms 预算下应只有插单种子候选"
        sel = outcome.selection
        assert sel["reference_source"] == "reference_results"
        ref_cost = sel["reference_best_cost_s"]
        assert ref_cost < sel["pool_best_cost_s"], "反例构造前提：参考成本更低"
        assert sel["selection_status"] == "no_candidate_within_cost_cap"
        assert sel["within_reference_cap"] is False

    def test_budget_not_double_counted(self, monkeypatch):
        """P0-2：假时钟每次推进 100ms；搜索必须跑到绝对截止（≈1000ms），
        若重复扣减种子耗时会在约 500–900ms 处提前停。"""
        clock = {"t": 0.0}
        fake_time = type(low_mod.time)  # 保留 sleep 等属性
        import types
        fake = types.SimpleNamespace(
            monotonic=lambda: clock.__setitem__("t", clock["t"] + 0.1) or clock["t"],
            sleep=low_mod.time.sleep)
        monkeypatch.setattr(low_mod, "time", fake)
        scn = _base()
        new = scn.orders[1].model_copy(update={"order_id": "o4", "node_id": "A",
                                               "is_new": True})
        scn2 = scn.model_copy(update={"orders": tuple(list(scn.orders) + [new])})
        outcome = low_mod.solve_low_disruption(scn2, time_limit_ms=1000)
        elapsed = outcome.diagnostics["elapsed_ms"]
        # 下界：搜索必须跑到绝对截止（重复扣减会提前数百 ms 停）；
        # 上界放宽：截止后还有审计/参考计算等收尾，假时钟下会略超
        assert 800 <= elapsed <= 1600, f"搜索应在绝对截止处停止，实际 {elapsed}ms"
        assert outcome.diagnostics["local_search"]["evaluated"] > 0

    def test_new_task_can_relocate_to_idle_vehicle(self):
        """P1：邻域生成必须把未启用/空路线车辆作为新任务 relocate 目标。"""
        orders = [
            Order(order_id="o1", node_id="A", demand=1, ready_s=0, due_s=5000, service_s=60),
            Order(order_id="o2", node_id="B", demand=1, ready_s=0, due_s=5000, service_s=60),
            Order(order_id="o3", node_id="C", demand=1, ready_s=0, due_s=5000, service_s=60),
            Order(order_id="o4", node_id="A", demand=1, ready_s=0, due_s=5000, service_s=60,
                  is_new=True),
        ]
        vehicles = [
            Vehicle(vehicle_id="V1", capacity=10, available_from_s=0, return_by_s=20000),
            Vehicle(vehicle_id="V2", capacity=10, available_from_s=0, return_by_s=20000),
            Vehicle(vehicle_id="V3", capacity=10, available_from_s=0, return_by_s=20000),
        ]
        scn2 = _scn(orders, vehicles, _matrix(),
                    [("V1", ["o1", "o2"]), ("V2", ["o3"])])  # V3 未启用
        # 候选只含出车路线：V3 空闲
        seqs = {"V1": ["o1", "o2", "o4"], "V2": ["o3"]}
        neighbors = list(low_mod._iter_neighbors(scn2, seqs))
        assert neighbors
        assert any("o4" in n.get("V3", []) for n in neighbors), \
            "新任务 o4 必须能 relocate 到空闲车辆 V3"
        assert any("o4" in n.get("V2", []) for n in neighbors), \
            "新任务 o4 必须能 relocate 到已有路线的 V2"

    def test_candidate_findable_by_id(self):
        """P1：由 candidate_id 可在候选池中重建并复核完整路线。"""
        outcome = low_mod.solve_low_disruption(SCN, time_limit_ms=3000)
        by_id = {c.candidate_id: c for c in outcome.candidates}
        for entry in outcome.audit:
            c = by_id[entry["candidate_id"]]
            assert [[r.vehicle_id, list(r.order_ids), r.return_s] for r in c.routes] == \
                [[r["vehicle_id"], r["order_ids"], r["return_s"]] for r in entry["routes"]]
            assert validate_solution(SCN, list(c.routes)).ok

    def test_archive_full_stop_reason(self):
        """规模上限：archive 满时停止原因可见。"""
        outcome = low_mod.solve_low_disruption(
            SCN, time_limit_ms=5000, max_archive_size=1)
        assert outcome.diagnostics["local_search"]["stop_reason"] == "archive_full"
        assert len(outcome.candidates) == 1


class TestBaselineReference:
    """baseline 原计划成本作为低扰动参考（2026-10-12 队长决策：
    "在每个 baseline 推荐成本，作为参考"）。

    参考链优先级：跨方法 reference_results > baseline（opt-in 显式开启）
    > 池内最优回退。baseline 原计划只含旧订单，走容缺口径逐路线独立重算
    （evaluate_original_plan）；任一路线自身违规则该参考不可用并如实回退。
    use_baseline_reference 默认 False：冻结评测（run_evaluation.py）语义
    不变，历史结果可复现；Web 任务链路显式开启。
    """

    def test_baseline_reference_used_when_enabled(self):
        outcome = low_mod.solve_low_disruption(
            SCN, time_limit_ms=5000, use_baseline_reference=True)
        sel = outcome.selection
        assert sel["reference_source"] == "baseline"
        expected, _ = evaluate_original_plan(SCN)
        assert expected is not None
        assert sel["reference_best_cost_s"] == expected
        base_entry = [d for d in outcome.diagnostics["reference_details"]
                      if str(d.get("name", "")).startswith("baseline")]
        assert base_entry and base_entry[0]["used"] is True

    def test_reference_results_take_precedence_over_baseline(self):
        ref_routes = _routes(SCN, {"V1": ["o1", "o2"], "V2": ["o3", "o4", "o5"]})
        assert validate_solution(SCN, ref_routes).ok
        outcome = low_mod.solve_low_disruption(
            SCN, time_limit_ms=5000,
            reference_results=[("rebuild", ref_routes)],
            use_baseline_reference=True)
        assert outcome.selection["reference_source"] == "reference_results"

    def test_baseline_disabled_by_default(self):
        """冻结评测兼容：默认不传 baseline，无跨方法参考时仍记池内回退。"""
        outcome = low_mod.solve_low_disruption(SCN, time_limit_ms=5000)
        assert "pool_fallback" in outcome.selection["reference_source"]
        assert not any(str(d.get("name", "")).startswith("baseline")
                       for d in outcome.diagnostics["reference_details"])

    def test_baseline_route_violation_falls_back_to_pool(self, monkeypatch):
        monkeypatch.setattr(
            low_mod, "evaluate_original_plan",
            lambda scn: (None, [{"vehicle_id": "V1", "used": False,
                                 "issues": ["E_ROUTE_LATE"],
                                 "travel_time_s": 0}]))
        outcome = low_mod.solve_low_disruption(
            SCN, time_limit_ms=5000, use_baseline_reference=True)
        assert "pool_fallback" in outcome.selection["reference_source"]
        base_entry = [d for d in outcome.diagnostics["reference_details"]
                      if str(d.get("name", "")).startswith("baseline")]
        assert base_entry and base_entry[0]["used"] is False

    def test_pipeline_opt_in_and_default_off(self):
        on = solve(SCN, "low_disruption", time_limit_ms=5000,
                   use_baseline_reference=True)
        assert on.result.metrics["reference_source"] == "baseline"
        off = solve(SCN, "low_disruption", time_limit_ms=5000)
        assert "pool_fallback" in off.result.metrics["reference_source"]
