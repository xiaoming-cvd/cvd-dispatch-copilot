"""不可变性与输入哈希补充测试（总工程师审查第 3 条）。

核查要点：
1. frozen 不仅禁止字段重赋值，还必须禁止内部容器被原地修改
   （列表字段已改 tuple、锁/系数字典已改 FrozenDict）；
2. 修改路线、矩阵、锁定后，输入哈希必须变化；
3. model_copy 产生新场景不得连带改变原计划（冻结隔离）；
4. NaN / 无穷大 / 容量单位冲突必须被拒绝。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from src.domain.models import (
    CostCoefficient,
    CostConfig,
    FrozenDict,
    Locks,
    Order,
    Scenario,
    TravelMatrix,
    Vehicle,
    input_hash,
)

ROOT = Path(__file__).resolve().parent.parent
SAMPLE = json.loads((ROOT / "data" / "sample" / "sample_small.json").read_text(encoding="utf-8"))
SCN = Scenario(**SAMPLE["scenario"])
BASE_HASH = input_hash(SCN)


class TestDeepImmutability:
    def test_attribute_reassignment_blocked(self):
        with pytest.raises(ValidationError):
            SCN.version = 2  # type: ignore[misc]

    def test_orders_append_blocked(self):
        with pytest.raises(AttributeError):
            SCN.orders.append(SCN.orders[0])  # tuple 无 append
        with pytest.raises((TypeError, ValidationError)):
            SCN.orders += (SCN.orders[0],)  # type: ignore[operator]

    def test_matrix_rows_blocked(self):
        with pytest.raises(AttributeError):
            SCN.travel_matrix.node_ids.append("ZZ")  # type: ignore[attr-defined]
        with pytest.raises(TypeError):
            SCN.travel_matrix.time_s[0] += (1,)  # type: ignore[index]

    def test_plan_routes_blocked(self):
        with pytest.raises(AttributeError):
            SCN.original_plan.routes[0].order_ids.append("ox")  # type: ignore[attr-defined]

    def test_lock_dict_blocked(self):
        locks = Locks(assignment_locks={"o1": "V1"})
        assert isinstance(locks.assignment_locks, FrozenDict)
        with pytest.raises(TypeError):
            locks.assignment_locks["o2"] = "V2"
        with pytest.raises(TypeError):
            locks.assignment_locks.update({"o2": "V2"})
        with pytest.raises(TypeError):
            locks.assignment_locks.pop("o1")

    def test_coeff_dict_blocked(self):
        cfg = CostConfig(coefficients={"t": CostCoefficient(
            value=1.0, unit="s", source="model_travel_time")})
        with pytest.raises(TypeError):
            cfg.coefficients["x"] = cfg.coefficients["t"]


class TestHashSensitivity:
    def test_matrix_change_changes_hash(self):
        m2 = SCN.travel_matrix.model_copy(update={
            "time_s": ((0, 300, 600, 500, 700),
                       (300, 0, 200, 350, 450),
                       (610, 210, 0, 250, 300),
                       (500, 350, 260, 0, 201),  # 200 -> 201
                       (700, 450, 300, 210, 0))})
        scn2 = SCN.model_copy(update={"travel_matrix": m2})
        assert input_hash(scn2) != BASE_HASH

    def test_lock_change_changes_hash(self):
        scn2 = SCN.model_copy(update={
            "locks": Locks(assignment_locks={"o1": "V2"})})
        assert input_hash(scn2) != BASE_HASH

    def test_plan_change_changes_hash(self):
        plan2 = SCN.original_plan.model_copy(update={"version": 2})
        scn2 = SCN.model_copy(update={"original_plan": plan2})
        assert input_hash(scn2) != BASE_HASH

    def test_order_change_changes_hash(self):
        o = SCN.orders[0].model_copy(update={"demand": 99})
        scn2 = SCN.model_copy(update={"orders": (o,) + SCN.orders[1:]})
        assert input_hash(scn2) != BASE_HASH


class TestFreezeIsolation:
    def test_copy_does_not_alias_mutable_inputs(self):
        """model_copy 修改新场景不得连带改变原计划（冻结对象共享是安全的）。"""
        plan_before = SCN.original_plan
        o = SCN.orders[0].model_copy(update={"due_s": 12345})
        scn2 = SCN.model_copy(update={"orders": (o,) + SCN.orders[1:]})
        # 原计划对象未被改动
        assert scn2.original_plan is plan_before
        assert input_hash(scn2.original_plan) == input_hash(plan_before)
        assert SCN.orders[0].due_s != 12345  # 原场景不变

    def test_frozen_containers_shared_safely(self):
        """共享的 tuple/Order 在场景间复用时不可被任一方篡改。"""
        scn2 = SCN.model_copy(update={"version": 2})
        assert scn2.orders is SCN.orders  # 同一 tuple，物理不可变
        assert scn2.travel_matrix is SCN.travel_matrix


class TestNonFiniteAndUnits:
    @pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
    def test_nan_inf_demand_rejected(self, bad):
        with pytest.raises(ValidationError) as e:
            Order(order_id="x", node_id="A", demand=bad, ready_s=0, due_s=10, service_s=1)
        assert "E_NON_FINITE" in str(e.value)

    @pytest.mark.parametrize("bad", [float("nan"), float("inf")])
    def test_nan_inf_capacity_rejected(self, bad):
        with pytest.raises(ValidationError) as e:
            Vehicle(vehicle_id="V", capacity=bad, available_from_s=0, return_by_s=10)
        assert "E_NON_FINITE" in str(e.value)

    def test_nan_coefficient_rejected(self):
        with pytest.raises(ValidationError) as e:
            CostCoefficient(value=float("nan"), unit="s", source="model_travel_time")
        assert "E_NON_FINITE" in str(e.value)

    def _unit_conflict(self, field: str, value: str):
        # model_copy 不做校验，必须走构造路径验证单位冲突
        d = SCN.model_dump(mode="json")
        d[field] = value
        with pytest.raises(ValidationError):
            Scenario(**d)

    def test_capacity_unit_conflict_rejected(self):
        self._unit_conflict("capacity_unit", "kg")

    def test_time_unit_conflict_rejected(self):
        self._unit_conflict("time_unit", "min")
