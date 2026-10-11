"""字段级契约测试：坏数据必须被 Pydantic 拒绝并给出明确错误。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from src.domain.models import (
    FrozenDict,
    Locks,
    Order,
    RelativeOrderLock,
    Scenario,
    TravelMatrix,
    Vehicle,
    input_hash,
)

TZ = timezone(timedelta(hours=8))
BASE_TIME = datetime(2026, 10, 10, 8, 0, 0, tzinfo=TZ)


def make_order(**kw) -> Order:
    base = dict(order_id="o1", node_id="A", demand=1, ready_s=0, due_s=100, service_s=10)
    base.update(kw)
    return Order(**base)


class TestOrder:
    def test_window_inversion_rejected(self):
        with pytest.raises(ValidationError) as e:
            make_order(ready_s=200, due_s=100)
        assert "E_ORDER_WINDOW_INVERSION" in str(e.value)

    def test_equal_window_allowed(self):
        o = make_order(ready_s=100, due_s=100)
        assert o.ready_s == o.due_s  # 任务书：ready_s 不大于 due_s，等号合法

    def test_negative_demand_rejected(self):
        with pytest.raises(ValidationError):
            make_order(demand=-1)

    def test_negative_service_rejected(self):
        with pytest.raises(ValidationError):
            make_order(service_s=-5)

    def test_missing_weight_not_zero(self):
        # 禁止把缺失重量设为 0 后声称支持载重约束：
        # demand=0 在 schema 层即被允许（真实零需求），但语义上不得用于冒充重量。
        o = make_order(demand=0)
        assert o.demand == 0  # 记录行为；载重维度需另行扩展 schema（任务书 4 章）

    def test_frozen(self):
        o = make_order()
        with pytest.raises(ValidationError):
            o.demand = 5  # type: ignore[misc]


class TestVehicle:
    def test_return_before_depart_rejected(self):
        with pytest.raises(ValidationError) as e:
            Vehicle(vehicle_id="V1", capacity=10, available_from_s=100, return_by_s=50)
        assert "E_VEHICLE_RETURN_BEFORE_DEPART" in str(e.value)

    def test_zero_capacity_rejected(self):
        with pytest.raises(ValidationError):
            Vehicle(vehicle_id="V1", capacity=0, available_from_s=0, return_by_s=100)


class TestTravelMatrix:
    NODES = ["depot", "A", "B"]

    def _m(self, rows):
        return TravelMatrix(node_ids=self.NODES, time_s=rows)

    def _ok(self):
        return self._m([[0, 10, 20], [10, 0, 30], [20, 30, 0]])

    def test_valid_matrix(self):
        m = self._ok()
        assert len(m.time_s) == 3

    def test_asymmetric_allowed(self):
        m = self._m([[0, 10, 20], [12, 0, 30], [20, 30, 0]])
        assert m.time_s[1][0] == 12

    def test_row_count_mismatch(self):
        with pytest.raises(ValidationError) as e:
            self._m([[0, 10, 20], [10, 0, 30]])
        assert "E_MATRIX_DIM" in str(e.value)

    def test_row_length_mismatch(self):
        with pytest.raises(ValidationError) as e:
            self._m([[0, 10, 20], [10, 0], [20, 30, 0]])
        assert "E_MATRIX_DIM" in str(e.value)

    def test_negative_travel_rejected(self):
        with pytest.raises(ValidationError) as e:
            self._m([[0, 10, 20], [10, 0, -5], [20, 30, 0]])
        assert "E_MATRIX_NEG" in str(e.value)

    def test_nonzero_diagonal_rejected(self):
        with pytest.raises(ValidationError) as e:
            self._m([[0, 10, 20], [10, 7, 30], [20, 30, 0]])
        assert "E_MATRIX_DIAG" in str(e.value)

    def test_zero_off_diagonal_accepted_as_legal_value(self):
        # 总工程师审查修正：0 是合法非负行驶时间（同址节点/取整后为 0），
        # 不得仅凭 0 判作缺边
        m = self._m([[0, 10, 20], [10, 0, 0], [20, 30, 0]])
        assert m.time_s[1][2] == 0

    def test_null_rejected_as_explicit_missing_edge(self):
        # 缺边必须以 null 显式表达，首版拒绝（不允许隐式表达）
        with pytest.raises(ValidationError) as e:
            TravelMatrix(node_ids=["depot", "A", "B"], time_s=[
                [0, 10, 20], [10, 0, None], [20, 30, 0]])
        assert "E_MATRIX_EDGE_MISSING" in str(e.value)

    def test_duplicate_node_rejected(self):
        with pytest.raises(ValidationError) as e:
            TravelMatrix(node_ids=["depot", "A", "A"], time_s=[[0]] * 3)
        assert "E_MATRIX_DUP_NODE" in str(e.value)


class TestScenarioUnits:
    def _scenario_kw(self):
        return dict(
            scenario_id="s1",
            version=1,
            time_origin=BASE_TIME,
            capacity_unit="load_units",
            depot_node_id="depot",
            orders=[make_order()],
            vehicles=[Vehicle(vehicle_id="V1", capacity=10, available_from_s=0, return_by_s=1000)],
            travel_matrix=TravelMatrix(node_ids=["depot", "A"], time_s=[[0, 10], [10, 0]]),
        )

    def test_time_unit_conflict_rejected(self):
        # 单位冲突：接口层只接受秒
        with pytest.raises(ValidationError):
            Scenario(time_unit="min", original_plan=None, **self._scenario_kw())  # type: ignore[arg-type]

    def test_time_origin_without_tz_rejected(self):
        kw = self._scenario_kw()
        kw["time_origin"] = datetime(2026, 10, 10, 8, 0, 0)  # 无时区
        with pytest.raises(ValidationError) as e:
            Scenario(**kw)  # type: ignore[arg-type]
        assert "E_TIME_ORIGIN_NO_TZ" in str(e.value)


class TestLocksModel:
    def test_relative_order_lock_min_two(self):
        with pytest.raises(ValidationError):
            RelativeOrderLock(vehicle_id="V1", order_ids=["o1"])

    def test_locks_default_empty(self):
        locks = Locks()
        assert dict(locks.assignment_locks) == {}
        assert locks.relative_order_locks == ()
        assert isinstance(locks.assignment_locks, FrozenDict)  # 深冻结字典


class TestHash:
    def test_hash_stable_and_sensitive(self):
        a = make_order()
        b = make_order()
        assert input_hash(a) == input_hash(b)
        c = make_order(demand=2)
        assert input_hash(a) != input_hash(c)
