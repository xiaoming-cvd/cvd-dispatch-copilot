"""首版数据契约（任务书第 4 章）。

单位与语义约定（与任务书一致，不得静默改变）：
- 时间：接口层统一整数秒；time_origin 为带时区的绝对时刻，
  ready_s / due_s / service_s / available_from_s / return_by_s 均为
  相对 time_origin 的秒数。time_unit 仅允许 "s"。
- 容量：同一场景只启用一种容量维度，capacity_unit ∈ {"load_units", "cm3"}。
  禁止把缺失重量填 0 后声称支持载重约束。
- 出行矩阵：完整方阵、node_ids 唯一、对角为 0、元素有限且非负；
  零值是合法行驶时间（同址节点/取整），缺边必须以 null 显式表达，
  首版拒绝 null（不允许用隐式表达冒充，也不凭 0 误判缺边）。
- 时间窗：约束服务开始时刻；ready_s <= due_s；不支持拆单。
- 所有模型 frozen：校验通过后的对象不可原地修改（容器字段已改用
  tuple / FrozenDict 深冻结），配合版本号实现冻结语义。
"""
from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SCHEMA_VERSION = "1.0"

TimeUnit = Literal["s"]  # 首版接口层只允许秒，求解内部同为整数秒
CapacityUnit = Literal["load_units", "cm3"]  # 首版单一容量维度


class FrozenDict(dict):
    """不可变字典：pydantic 校验后冻结，防止内部字典被原地篡改。

    输入端接受普通 dict，校验通过后转为 FrozenDict（所有写方法拒绝）。
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._hash = hash(tuple(sorted(self.items())))

    def __hash__(self):
        return self._hash

    def _blocked(self, *a, **k):
        raise TypeError("FrozenDict 不可修改（输入契约对象已冻结）")

    __setitem__ = __delitem__ = _blocked
    update = setdefault = pop = popitem = clear = _blocked

    def __repr__(self):
        return f"FrozenDict({dict.__repr__(self)})"


def _finite(value: float, path: str) -> float:
    """拒绝 NaN / 无穷大：不是有限数的输入一律非法。"""
    if not math.isfinite(value):
        raise ValueError(f"E_NON_FINITE: {path} 不是有限数（NaN/无穷大被拒绝）")
    return value


class SolveStatus(str, Enum):
    """求解状态（任务书 6 章结果契约）。阶段 0 仅定义枚举，阶段 2 开始使用。"""

    INVALID_INPUT = "invalid_input"
    FEASIBLE = "feasible"
    NO_FEASIBLE_FOUND = "no_feasible_found"
    PROVEN_INFEASIBLE = "proven_infeasible"
    ERROR = "error"


class Order(BaseModel):
    """一个不可拆分的服务单元：只能分配给一辆车并服务一次。"""

    model_config = ConfigDict(frozen=True)

    order_id: str = Field(min_length=1)
    node_id: str = Field(min_length=1)  # 与 order_id 分开管理；同位置多订单共享 node_id
    demand: float = Field(ge=0)  # 单位 = 场景 capacity_unit；缺失重量不得填 0 冒充
    ready_s: int = Field(ge=0)  # 服务开始不早于此时
    due_s: int = Field(ge=0)  # 服务开始不晚于此时
    service_s: int = Field(ge=0)  # 服务时长，非负
    is_new: bool = False  # 新增任务；不得出现在 original_plan 中

    @field_validator("demand", mode="before")
    @classmethod
    def _demand_finite(cls, v) -> float:
        return _finite(v, "Order.demand")

    @model_validator(mode="after")
    def _window_order(self) -> "Order":
        if self.ready_s > self.due_s:
            raise ValueError("E_ORDER_WINDOW_INVERSION: ready_s 不得大于 due_s")
        return self


class Vehicle(BaseModel):
    """车辆：起终点均为同一仓库；未启用时不产生启用费（首版所有车均启用）。"""

    model_config = ConfigDict(frozen=True)

    vehicle_id: str = Field(min_length=1)
    capacity: float = Field(gt=0)
    available_from_s: int = Field(ge=0)  # 固定出发时刻
    return_by_s: int = Field(ge=0)  # 返回不得晚于此时

    @field_validator("capacity", mode="before")
    @classmethod
    def _capacity_finite(cls, v) -> float:
        return _finite(v, "Vehicle.capacity")

    @model_validator(mode="after")
    def _return_after_depart(self) -> "Vehicle":
        if self.return_by_s < self.available_from_s:
            raise ValueError("E_VEHICLE_RETURN_BEFORE_DEPART: return_by_s 不得早于 available_from_s")
        return self


class TravelMatrix(BaseModel):
    """节点间行驶时间矩阵。允许非对称；首版拒绝不完整矩阵。

    数据规则（总工程师审查修正）：
    - 零值 = 合法的非负行驶时间（不同节点同址、或按声明规则取整后为 0），
      不得仅凭 0 判作缺边；
    - 缺边 = 字段缺失 / null（明确的不可达标记），首版直接拒绝
      （E_MATRIX_EDGE_MISSING），不允许用 null 以外的隐式表达。
    """

    model_config = ConfigDict(frozen=True)

    node_ids: tuple[str, ...] = Field(min_length=1)
    time_s: tuple[tuple[int | None, ...], ...]

    @field_validator("node_ids")
    @classmethod
    def _unique_nodes(cls, v: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(v)) != len(v):
            raise ValueError("E_MATRIX_DUP_NODE: node_ids 存在重复")
        return v

    @model_validator(mode="after")
    def _matrix_shape(self) -> "TravelMatrix":
        n = len(self.node_ids)
        if len(self.time_s) != n:
            raise ValueError(
                f"E_MATRIX_DIM: time_s 行数 {len(self.time_s)} 与 node_ids 数 {n} 不一致"
            )
        for i, row in enumerate(self.time_s):
            if len(row) != n:
                raise ValueError(
                    f"E_MATRIX_DIM: time_s 第 {i} 行长度 {len(row)} 与阶数 {n} 不一致"
                )
            for j, v in enumerate(row):
                if v is None:
                    raise ValueError(
                        f"E_MATRIX_EDGE_MISSING: time_s[{i}][{j}] 为 null（明确缺边标记），"
                        "首版拒绝不完整矩阵；零值是合法行驶时间，不得与缺边混淆"
                    )
                if v < 0:
                    raise ValueError(f"E_MATRIX_NEG: time_s[{i}][{j}] 为负数")
                if i == j and v != 0:
                    raise ValueError(f"E_MATRIX_DIAG: time_s[{i}][{i}] 必须为 0")
        return self


class RoutePlan(BaseModel):
    """原计划中的一条路线：一辆车 + 有序订单列表。"""

    model_config = ConfigDict(frozen=True)

    vehicle_id: str = Field(min_length=1)
    order_ids: tuple[str, ...] = Field(min_length=1)


class OriginalPlan(BaseModel):
    """冻结的基准计划。原任务全部恰好出现一次，新任务不得出现。"""

    model_config = ConfigDict(frozen=True)

    plan_id: str = Field(min_length=1)
    version: int = Field(ge=1)
    routes: tuple[RoutePlan, ...] = Field(min_length=1)


class RelativeOrderLock(BaseModel):
    """某车上若干旧任务的相对顺序锁：保持给定先后顺序，同时固定其车辆。"""

    model_config = ConfigDict(frozen=True)

    vehicle_id: str = Field(min_length=1)
    order_ids: tuple[str, ...] = Field(min_length=2)  # 保持给定先后顺序的旧任务


class Locks(BaseModel):
    """锁定条件。

    assignment_locks: 固定某旧任务所属车辆。
    relative_order_locks: 固定某车上若干旧任务的相对顺序；
        允许在它们之间插入新任务。
    首版不实现"整条路线锁死"。
    """

    model_config = ConfigDict(frozen=True)

    assignment_locks: dict[str, str] = Field(default_factory=FrozenDict)  # order_id -> vehicle_id
    relative_order_locks: tuple[RelativeOrderLock, ...] = Field(default_factory=tuple)

    @field_validator("assignment_locks")
    @classmethod
    def _freeze_locks(cls, v: dict) -> FrozenDict:
        return FrozenDict(v)


class CostCoefficient(BaseModel):
    """成本系数必须带单位与来源；没有真实单价时以行驶秒数为优化指标。"""

    model_config = ConfigDict(frozen=True)

    value: float
    unit: str = Field(min_length=1)  # 例如 "s"（模型行驶时间）
    source: str = Field(min_length=1)  # 例如 "model_travel_time" 或真实单价出处

    @field_validator("value", mode="before")
    @classmethod
    def _value_finite(cls, v) -> float:
        return _finite(v, "CostCoefficient.value")


class CostConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    objective: Literal["min_travel_time_s"] = "min_travel_time_s"
    coefficients: dict[str, CostCoefficient] = Field(default_factory=FrozenDict)

    @field_validator("coefficients")
    @classmethod
    def _freeze_coeffs(cls, v: dict) -> FrozenDict:
        return FrozenDict(v)


class Scenario(BaseModel):
    """一个完整调度场景。校验通过后对象冻结，版本号不可变。"""

    model_config = ConfigDict(frozen=True)

    schema_version: str = SCHEMA_VERSION
    scenario_id: str = Field(min_length=1)
    version: int = Field(ge=1)  # 不可变版本号；确认约束草案后 +1
    time_origin: datetime  # 必须带时区
    time_unit: TimeUnit = "s"
    capacity_unit: CapacityUnit
    depot_node_id: str = Field(min_length=1)
    orders: tuple[Order, ...] = Field(min_length=1)
    vehicles: tuple[Vehicle, ...] = Field(min_length=1)
    travel_matrix: TravelMatrix
    original_plan: OriginalPlan
    locks: Locks = Field(default_factory=Locks)
    cost_config: CostConfig = Field(default_factory=CostConfig)

    @field_validator("time_origin")
    @classmethod
    def _tz_required(cls, v: datetime) -> datetime:
        if v.tzinfo is None or v.utcoffset() is None:
            raise ValueError("E_TIME_ORIGIN_NO_TZ: time_origin 必须带时区")
        return v


# ---- SolveResult（任务书 6 章结果契约；阶段 0 定义模型，阶段 2 起填充） ----


class RouteResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    vehicle_id: str
    order_ids: tuple[str, ...]
    return_s: int  # 返回仓库时刻（相对 time_origin）


class StopSchedule(BaseModel):
    """逐站时刻表：车辆、任务、序号、到达、等待、服务开始、离开。"""

    model_config = ConfigDict(frozen=True)

    vehicle_id: str
    order_id: str
    seq: int = Field(ge=1)
    arrive_s: int
    wait_s: int = Field(ge=0)
    start_s: int
    depart_s: int


class SolveResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    input_hash: str
    config: dict
    solver_version: str
    elapsed_ms: int = Field(ge=0)
    status: SolveStatus
    routes: tuple[RouteResult, ...] = Field(default_factory=tuple)
    schedule: tuple[StopSchedule, ...] = Field(default_factory=tuple)
    metrics: dict = Field(default_factory=dict)
    diff: dict = Field(default_factory=dict)
    validation: dict = Field(default_factory=dict)
    warnings: tuple[str, ...] = Field(default_factory=tuple)
    assumptions: tuple[str, ...] = Field(default_factory=tuple)


def canonical_json(model: BaseModel) -> str:
    """规范化 JSON：排序键、紧凑分隔，用于输入哈希（与序列化格式无关）。"""
    return json.dumps(
        model.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )


def input_hash(model: BaseModel) -> str:
    """输入内容哈希（SHA-256）。任何字段变化都会导致哈希变化。"""
    return hashlib.sha256(canonical_json(model).encode("utf-8")).hexdigest()


def scenario_json_schema() -> dict:
    return Scenario.model_json_schema()


class ScenarioDraft(BaseModel):
    """阶段 1 产物：待生成原计划的场景（任务书阶段 1 提示词）。

    与 Scenario 的差别仅在于不含 original_plan 与 locks 的生效语义：
    原计划由阶段 2 基于"当时已知任务"生成并冻结后，调用 to_scenario()
    升格为完整 Scenario。除此用途外不得用 Draft 代替 Scenario 进入求解。
    """

    model_config = ConfigDict(frozen=True)

    schema_version: str = SCHEMA_VERSION
    scenario_id: str = Field(min_length=1)
    version: int = Field(ge=1)
    time_origin: datetime
    time_unit: TimeUnit = "s"
    capacity_unit: CapacityUnit
    depot_node_id: str = Field(min_length=1)
    orders: tuple[Order, ...] = Field(min_length=1)
    vehicles: tuple[Vehicle, ...] = Field(min_length=1)
    travel_matrix: TravelMatrix
    locks: Locks = Field(default_factory=Locks)
    cost_config: CostConfig = Field(default_factory=CostConfig)

    @field_validator("time_origin")
    @classmethod
    def _tz_required(cls, v: datetime) -> datetime:
        if v.tzinfo is None or v.utcoffset() is None:
            raise ValueError("E_TIME_ORIGIN_NO_TZ: time_origin 必须带时区")
        return v

    def to_scenario(self, original_plan: OriginalPlan) -> Scenario:
        """阶段 2 生成并冻结原计划后升格为完整场景。"""
        return Scenario(
            schema_version=self.schema_version,
            scenario_id=self.scenario_id,
            version=self.version,
            time_origin=self.time_origin,
            time_unit=self.time_unit,
            capacity_unit=self.capacity_unit,
            depot_node_id=self.depot_node_id,
            orders=self.orders,
            vehicles=self.vehicles,
            travel_matrix=self.travel_matrix,
            original_plan=original_plan,
            locks=self.locks,
            cost_config=self.cost_config,
        )
