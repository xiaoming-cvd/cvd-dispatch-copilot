"""跨电脑审查 P0/P1 回归测试（R01–R09）。

测试输入全部取自审查报告第 3/4/5 章的最小反例；每条测试在修复前必须失败。
R01/R02/R06 依赖 COPT 后端（审查机未装 COPT 时应跳过，R07 验证隔离）。
"""
from __future__ import annotations

import importlib
import json
import sys
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
from src.solver.pipeline import solve
from src.validation.timetable import evaluate_route, validate_solution

ROOT = Path(__file__).resolve().parent.parent
SAMPLE = json.loads((ROOT / "data" / "sample" / "sample_small.json").read_text(encoding="utf-8"))
BASE = Scenario(**SAMPLE["scenario"])

coptpy = pytest.importorskip  # 占位；COPT 测试用单独标记


def _mk_scn(orders, vehicles, matrix_rows, plan_routes, locks=None):
    nodes = ["depot"] + sorted({o.node_id for o in orders})
    return Scenario(
        scenario_id="rv", version=1, time_origin=BASE.time_origin,
        capacity_unit="load_units", depot_node_id="depot",
        orders=orders, vehicles=vehicles,
        travel_matrix=TravelMatrix(node_ids=nodes, time_s=matrix_rows),
        original_plan=OriginalPlan(
            plan_id="p0", version=1,
            routes=[RoutePlan(vehicle_id=v, order_ids=tuple(o)) for v, o in plan_routes]),
        locks=locks or Locks(),
    )


def _rt(scn, vid, seq):
    ev = evaluate_route(scn, vid, list(seq))
    return RouteResult(vehicle_id=vid, order_ids=tuple(seq), return_s=ev.return_s)


def _codes(val):
    return {i.code for i in val.issues}


def _copt_available() -> bool:
    try:
        import coptpy  # noqa: F401
        return True
    except ImportError:
        return False

needs_copt = pytest.mark.skipif(not _copt_available(), reason="本机未安装 COPT（R07 验证隔离）")


# ---------- R03 / P0 C：同车多条路线必须拒绝 ----------

class TestDuplicateVehicleRoutes:
    def test_rejected(self):
        scn = _mk_scn(
            orders=[Order(order_id="A", node_id="A", demand=1, ready_s=0, due_s=100, service_s=1),
                    Order(order_id="B", node_id="B", demand=1, ready_s=0, due_s=100, service_s=1)],
            vehicles=[Vehicle(vehicle_id="V1", capacity=2, available_from_s=0, return_by_s=100)],
            matrix_rows=[[0, 1, 1], [1, 0, 1], [1, 1, 0]],
            plan_routes=[("V1", ["A", "B"])])
        val = validate_solution(scn, [_rt(scn, "V1", ["A"]), _rt(scn, "V1", ["B"])])
        assert not val.ok
        assert "E_SOL_DUP_VEHICLE" in _codes(val)


# ---------- R04 / P0 D：相对顺序锁成员跨车/缺失必须拒绝 ----------

class TestRelativeLockMembers:
    def _scn(self, locks=None):
        return _mk_scn(
            orders=[Order(order_id="A", node_id="A", demand=1, ready_s=0, due_s=100, service_s=1),
                    Order(order_id="B", node_id="B", demand=1, ready_s=0, due_s=100, service_s=1)],
            vehicles=[Vehicle(vehicle_id="V1", capacity=2, available_from_s=0, return_by_s=100),
                      Vehicle(vehicle_id="V2", capacity=2, available_from_s=0, return_by_s=100)],
            matrix_rows=[[0, 1, 1], [1, 0, 1], [1, 1, 0]],
            plan_routes=[("V1", ["A", "B"]), ("V2", [])] if False else [("V1", ["A", "B"])],
            locks=locks)

    def test_member_on_other_vehicle_rejected(self):
        scn = self._scn(locks=Locks(relative_order_locks=[
            RelativeOrderLock(vehicle_id="V1", order_ids=["A", "B"])]))
        val = validate_solution(scn, [_rt(scn, "V1", ["A"]), _rt(scn, "V2", ["B"])])
        assert not val.ok
        assert "E_SOL_REL_ORDER_LOCK" in _codes(val)

    def test_member_missing_rejected(self):
        scn = self._scn(locks=Locks(relative_order_locks=[
            RelativeOrderLock(vehicle_id="V1", order_ids=["A", "B"])]))
        # B 被漏单 → 已有 E_SOL_MISSING_ORDER；锁成员缺失也要报锁错误
        val = validate_solution(scn, [_rt(scn, "V1", ["A"])])
        assert not val.ok
        assert "E_SOL_REL_ORDER_LOCK" in _codes(val)

    def test_new_order_between_members_accepted(self):
        orders = [
            Order(order_id="A", node_id="A", demand=1, ready_s=0, due_s=100, service_s=1),
            Order(order_id="B", node_id="B", demand=1, ready_s=0, due_s=100, service_s=1),
            Order(order_id="N", node_id="A", demand=1, ready_s=0, due_s=100, service_s=1,
                  is_new=True),
        ]
        scn = _mk_scn(
            orders=orders,
            vehicles=[Vehicle(vehicle_id="V1", capacity=3, available_from_s=0, return_by_s=100)],
            matrix_rows=[[0, 1, 1], [1, 0, 1], [1, 1, 0]],
            plan_routes=[("V1", ["A", "B"])],
            locks=Locks(relative_order_locks=[
                RelativeOrderLock(vehicle_id="V1", order_ids=["A", "B"])]))
        val = validate_solution(scn, [_rt(scn, "V1", ["A", "N", "B"])])
        assert val.ok, _codes(val)


# ---------- R05 / P1 E：伪造返回时刻必须拒绝 ----------

class TestReturnTimeConsistency:
    def test_fake_return_s_rejected(self):
        scn = _mk_scn(
            orders=[Order(order_id="A", node_id="A", demand=1, ready_s=0, due_s=100, service_s=1),
                    Order(order_id="B", node_id="B", demand=1, ready_s=0, due_s=100, service_s=1)],
            vehicles=[Vehicle(vehicle_id="V1", capacity=2, available_from_s=0, return_by_s=100)],
            matrix_rows=[[0, 1, 1], [1, 0, 1], [1, 1, 0]],
            plan_routes=[("V1", ["A", "B"])])
        good = _rt(scn, "V1", ["A", "B"])
        fake = RouteResult(vehicle_id="V1", order_ids=("A", "B"), return_s=999)
        val = validate_solution(scn, [fake])
        assert not val.ok
        assert "E_SOL_RETURN_MISMATCH" in _codes(val)
        val_good = validate_solution(scn, [good])
        assert val_good.ok

    def test_pipeline_fields_consistent(self):
        scn = _mk_scn(
            orders=[Order(order_id="A", node_id="A", demand=1, ready_s=0, due_s=100, service_s=1),
                    Order(order_id="B", node_id="B", demand=1, ready_s=0, due_s=100, service_s=1)],
            vehicles=[Vehicle(vehicle_id="V1", capacity=2, available_from_s=0, return_by_s=100)],
            matrix_rows=[[0, 1, 1], [1, 0, 1], [1, 1, 0]],
            plan_routes=[("V1", ["A", "B"])])
        outcome = solve(scn, "insert", time_limit_ms=2000)
        assert outcome.result.status == SolveStatus.FEASIBLE
        for r, ev in zip(outcome.result.routes, outcome.validation.routes):
            assert r.return_s == ev.return_s


# ---------- R01 / P0 A：闭环目标（反例矩阵，穷举最优 = 5） ----------

@needs_copt
class TestClosedLoopObjective:
    def test_optimum_matches_exhaustive(self):
        # 报告反例：最优闭环 = D→B→A→D = 2+2+1 = 5
        orders = [
            Order(order_id="a", node_id="A", demand=1, ready_s=0, due_s=100, service_s=0),
            Order(order_id="b", node_id="B", demand=1, ready_s=0, due_s=100, service_s=0),
        ]
        scn = _mk_scn(
            orders=orders,
            vehicles=[Vehicle(vehicle_id="V1", capacity=2, available_from_s=0, return_by_s=100)],
            matrix_rows=[[0, 1, 2], [1, 0, 2], [3, 2, 0]],
            plan_routes=[("V1", ["a", "b"])])
        outcome = solve(scn, "rebuild", time_limit_ms=5000)
        assert outcome.result.status == SolveStatus.FEASIBLE
        assert outcome.result.metrics["total_travel_time_s"] == 5
        seq = list(outcome.result.routes[0].order_ids)
        assert seq == ["b", "a"]


# ---------- R02 / P0 B：大 M 反例（有 1000s 未选弧，真实路线 2s） ----------

@needs_copt
class TestBigM:
    def test_not_falsely_infeasible(self):
        orders = [
            Order(order_id="a", node_id="A", demand=1, ready_s=0, due_s=100, service_s=0),
            Order(order_id="b", node_id="B", demand=1, ready_s=0, due_s=100, service_s=0),
        ]
        scn = _mk_scn(
            orders=orders,
            vehicles=[Vehicle(vehicle_id="V1", capacity=2, available_from_s=0, return_by_s=100)],
            matrix_rows=[[0, 1, 1], [1, 0, 1], [1, 1000, 0]],
            plan_routes=[("V1", ["a", "b"])])
        outcome = solve(scn, "rebuild", time_limit_ms=5000)
        assert outcome.result.status != SolveStatus.PROVEN_INFEASIBLE, \
            "大 M 不足把可行问题误判为证明无解"
        assert outcome.result.status == SolveStatus.FEASIBLE


# ---------- R06：零服务同址订单 ----------

@needs_copt
class TestZeroTimeSameLocation:
    def test_no_broken_cycle(self):
        orders = [
            Order(order_id="a", node_id="A", demand=1, ready_s=0, due_s=100, service_s=0),
            Order(order_id="b", node_id="B", demand=1, ready_s=0, due_s=100, service_s=0),
        ]
        scn = _mk_scn(
            orders=orders,
            vehicles=[Vehicle(vehicle_id="V1", capacity=2, available_from_s=0, return_by_s=100)],
            matrix_rows=[[0, 0, 0], [0, 0, 0], [0, 0, 0]],  # 全 0：零行驶合法，非缺边
            plan_routes=[("V1", ["a", "b"])])
        outcome = solve(scn, "rebuild", time_limit_ms=5000)
        assert outcome.result.status == SolveStatus.FEASIBLE
        assert outcome.result.metrics["orders_served"] == 2
        assert outcome.validation.ok


# ---------- R07：无 COPT 环境，pipeline 可导入、insert 可运行 ----------

class TestNoCoptIsolation:
    def test_pipeline_imports_and_insert_runs_without_coptpy(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "coptpy", None)  # 使 import coptpy 抛 ImportError
        # 同时屏蔽已缓存的 COPT 模块，否则 reload 不会重新触发导入
        monkeypatch.setitem(sys.modules, "src.solver.copt_core", None)
        import src.solver.pipeline as pipe
        importlib.reload(pipe)
        try:
            scn = _mk_scn(
                orders=[Order(order_id="A", node_id="A", demand=1, ready_s=0, due_s=100,
                              service_s=1),
                        Order(order_id="B", node_id="B", demand=1, ready_s=0, due_s=100,
                              service_s=1, is_new=True)],
                vehicles=[Vehicle(vehicle_id="V1", capacity=2, available_from_s=0,
                                  return_by_s=100)],
                matrix_rows=[[0, 1, 1], [1, 0, 1], [1, 1, 0]],
                plan_routes=[("V1", ["A"])])
            ins = pipe.solve(scn, "insert", time_limit_ms=2000)
            assert ins.result.status == SolveStatus.FEASIBLE
            # 2026-10-11 决策：rebuild 默认 OR-Tools，无 COPT 依赖照常可行
            rb = pipe.solve(scn, "rebuild", time_limit_ms=2000)
            assert rb.result.status == SolveStatus.FEASIBLE
            assert rb.result.metrics.get("backend") == "ortools"
            # 显式要求 COPT 而无 coptpy：ERROR + 可理解警告，绝不静默回退
            rb_copt = pipe.solve(scn, "rebuild", time_limit_ms=2000, backend="copt")
            assert rb_copt.result.status == SolveStatus.ERROR
            assert any("COPT" in w for w in rb_copt.result.warnings)
        finally:
            sys.modules.pop("coptpy", None)
            importlib.reload(pipe)  # 恢复正常模块


# ---------- R08：状态映射全表（mock 后端返回） ----------

class TestStatusMapping:
    def _scn(self):
        return _mk_scn(
            orders=[Order(order_id="A", node_id="A", demand=1, ready_s=0, due_s=100, service_s=1),
                    Order(order_id="B", node_id="B", demand=1, ready_s=0, due_s=100, service_s=1)],
            vehicles=[Vehicle(vehicle_id="V1", capacity=2, available_from_s=0, return_by_s=100)],
            matrix_rows=[[0, 1, 1], [1, 0, 1], [1, 1, 0]],
            plan_routes=[("V1", ["A", "B"])])

    def _run(self, monkeypatch, outcome):
        import src.solver.rebuild as rb
        monkeypatch.setattr(rb, "rebuild_all", lambda scn, ms, backend="copt": outcome)
        import src.solver.pipeline as pipe
        return pipe.solve(self._scn(), "rebuild", time_limit_ms=100)

    def test_optimal(self, monkeypatch):
        from src.solver.ortools_core import OrtoolsOutcome
        o = self._run(monkeypatch, OrtoolsOutcome(
            raw_status="MIP_OPTIMAL", routes=(("A", "B"),)))
        assert o.result.status == SolveStatus.FEASIBLE

    def test_feasible_not_optimal_kept(self, monkeypatch):
        # P1 H：非最优终止但有完整候选（COPT MIP_FEASIBLE）不得被丢弃
        from src.solver.ortools_core import OrtoolsOutcome
        o = self._run(monkeypatch, OrtoolsOutcome(
            raw_status="MIP_FEASIBLE", routes=(("A", "B"),)))
        assert o.result.status == SolveStatus.FEASIBLE

    def test_timeout_with_sol(self, monkeypatch):
        from src.solver.ortools_core import OrtoolsOutcome
        o = self._run(monkeypatch, OrtoolsOutcome(
            raw_status="MIP_TIMEOUT_WITH_SOL", routes=(("A", "B"),)))
        assert o.result.status == SolveStatus.FEASIBLE

    def test_timeout_no_sol(self, monkeypatch):
        from src.solver.ortools_core import OrtoolsOutcome
        o = self._run(monkeypatch, OrtoolsOutcome(
            raw_status="MIP_TIMEOUT_NO_SOL", routes=((),)))
        assert o.result.status == SolveStatus.NO_FEASIBLE_FOUND
        assert o.result.status != SolveStatus.PROVEN_INFEASIBLE

    def test_proven_infeasible(self, monkeypatch):
        from src.solver.ortools_core import OrtoolsOutcome
        o = self._run(monkeypatch, OrtoolsOutcome(
            raw_status="MIP_INFEASIBLE", routes=((),)))
        assert o.result.status == SolveStatus.PROVEN_INFEASIBLE

    def test_validator_rejected_distinct(self, monkeypatch):
        # 求解器声称最优且覆盖完整，但方案违反相对顺序锁 → 独立校验拒绝，
        # 明确标注 validator_rejected（与"搜索未找到"区分）
        from src.solver.ortools_core import OrtoolsOutcome
        scn = _mk_scn(
            orders=[Order(order_id="A", node_id="A", demand=1, ready_s=0, due_s=100,
                          service_s=1),
                    Order(order_id="B", node_id="B", demand=1, ready_s=0, due_s=100,
                          service_s=1)],
            vehicles=[Vehicle(vehicle_id="V1", capacity=2, available_from_s=0,
                              return_by_s=100)],
            matrix_rows=[[0, 1, 1], [1, 0, 1], [1, 1, 0]],
            plan_routes=[("V1", ["A", "B"])],
            locks=Locks(relative_order_locks=[
                RelativeOrderLock(vehicle_id="V1", order_ids=["A", "B"])]))
        import src.solver.rebuild as rb
        monkeypatch.setattr(rb, "rebuild_all",
                            lambda s, ms, backend="copt": OrtoolsOutcome(
                                raw_status="MIP_OPTIMAL", routes=(("B", "A"),)))
        import src.solver.pipeline as pipe
        o = pipe.solve(scn, "rebuild", time_limit_ms=100)
        assert o.result.status == SolveStatus.NO_FEASIBLE_FOUND
        assert o.result.metrics.get("validator_rejected") is True

    def test_backend_error(self, monkeypatch):
        import src.solver.rebuild as rb
        def boom(scn, ms, backend="copt"):
            raise RuntimeError("modeling failed")
        monkeypatch.setattr(rb, "rebuild_all", boom)
        import src.solver.pipeline as pipe
        o = pipe.solve(self._scn(), "rebuild", time_limit_ms=100)
        assert o.result.status == SolveStatus.ERROR


# ---------- R09：预算传递（确定性时钟替身，不依赖机器速度） ----------

class TestBudgetPassed:
    def test_insert_respects_deadline(self, monkeypatch):
        import src.solver.insert as ins
        real_mono = ins.time.monotonic
        # 时钟替身：第一次返回 0，之后永远超过截止时间
        fake = iter([0.0] + [1e9] * 100000)
        monkeypatch.setattr(ins.time, "monotonic", lambda: next(fake))
        scn = _mk_scn(
            orders=[Order(order_id="A", node_id="A", demand=1, ready_s=0, due_s=100, service_s=1),
                    Order(order_id="B", node_id="B", demand=1, ready_s=0, due_s=100, service_s=1,
                          is_new=True)],
            vehicles=[Vehicle(vehicle_id="V1", capacity=2, available_from_s=0, return_by_s=100)],
            matrix_rows=[[0, 1, 1], [1, 0, 1], [1, 1, 0]],
            plan_routes=[("V1", ["A"])])
        routes, diag = ins.insert_new_orders(scn, time_limit_ms=1000)
        assert routes == []
        assert diag["inserted"][-1].get("reason") == "time_budget_exhausted"
        assert real_mono is not None  # 避免 linter 未用告警
