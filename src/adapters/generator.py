"""固定种子的情景生成器（任务书 3 章 Solomon 情景构造 + 阶段 1 提示词）。

正确顺序（禁止颠倒）：先按基础算例划分开发集/测试集 → 选择已知任务集
→ 保存待生成原计划的场景（原计划由阶段 2 生成并冻结）→ 揭示新增任务。
绝不"先解全量任务再删未来任务"：本模块不做任何求解。

分层维度：新增比例（n_new/n_known）、时间窗紧张度、车辆余量；
锁定比例记录在 _meta.lock_ratio_target，由阶段 2 生成原计划后落实为
真实 locks（换车锁需先知道任务所属车辆）。
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from datetime import datetime

from ..domain.models import (
    CostCoefficient,
    CostConfig,
    Locks,
    Order,
    ScenarioDraft,
    TravelMatrix,
    Vehicle,
)
from .solomon import SolomonInstance

TIME_SCALE = 60


@dataclass(frozen=True)
class GenConfig:
    n_known: int = 15          # 已知（旧）任务数
    n_new: int = 3             # 新增任务数
    tw_tightness: str = "normal"  # normal | tight
    vehicle_slack: str = "normal"  # tight(余量0辆) | normal(余量1辆)
    lock_ratio: float = 0.0    # 0..1，记录在 meta，阶段 2 落实
    seed: int = 0


@dataclass(frozen=True)
class GeneratedScenario:
    draft: ScenarioDraft
    event_log: tuple[dict, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict:
        return {
            "_meta": {
                "base_instance": self.event_log[0]["instance"] if self.event_log else "",
                "seed": self.event_log[0]["seed"] if self.event_log else None,
                "pending_original_plan": True,
                "lock_ratio_target": self.event_log[0].get("lock_ratio")
                if self.event_log else None,
                "synthetic_events": True,
                "note": "新增任务为按种子从基准算例合成的插单事件，"
                        "不是真实动态订单；原计划待阶段 2 生成",
                "event_log": list(self.event_log),
            },
            "draft": self.draft.model_dump(mode="json"),
        }


def split_base_instances(names: list[str], dev_fraction: float, seed: int) -> dict[str, str]:
    """按基础算例划分开发/测试集；同一算例的所有派生版本只能属于一个集合。"""
    rng = random.Random(f"split:{seed}")
    shuffled = sorted(names)
    rng.shuffle(shuffled)
    n_dev = max(1, int(round(len(shuffled) * dev_fraction)))
    return {n: ("dev" if i < n_dev else "test") for i, n in enumerate(shuffled)}


def _tighten(width: int, service_s: int) -> int:
    """tight 规则：窗口宽度减半但不小于服务时长、不超过原宽度（声明规则，留痕）。"""
    return min(width, max(service_s, width // 2))


def generate_draft(
    inst: SolomonInstance,
    cfg: GenConfig,
    scenario_id: str,
    time_origin: datetime,
) -> GeneratedScenario:
    if cfg.n_known + cfg.n_new > len(inst.customers):
        raise ValueError("E_GEN_SIZE: 已知+新增任务数超过算例客户数")
    if cfg.tw_tightness not in ("normal", "tight"):
        raise ValueError("E_GEN_TIGHTNESS")
    if cfg.vehicle_slack not in ("tight", "normal"):
        raise ValueError("E_GEN_SLACK")

    # 每个 (算例, 种子) 一个独立随机流，保证可复现且互不干扰
    rng = random.Random(f"{inst.name}:{cfg.seed}")
    event_log: list[dict] = []

    pool = [c.cust_no for c in inst.customers]
    known_nos = sorted(rng.sample(pool, cfg.n_known))
    remaining = [n for n in pool if n not in set(known_nos)]
    new_nos = sorted(rng.sample(remaining, cfg.n_new))
    event_log.append({
        "instance": inst.name, "seed": cfg.seed,
        "known": known_nos, "new": new_nos,
        "tw_tightness": cfg.tw_tightness, "vehicle_slack": cfg.vehicle_slack,
        "lock_ratio": cfg.lock_ratio,
    })

    by_no = {c.cust_no: c for c in inst.all_nodes}
    selected = [0] + known_nos + new_nos  # 0 = 仓库
    node_ids = ["depot" if n == 0 else f"n{n}" for n in selected]
    idx = {n: k for k, n in enumerate(selected)}

    m = len(selected)
    time_s = [[0] * m for _ in range(m)]
    for a in range(m):
        for b in range(m):
            if a != b:
                ca, cb = by_no[selected[a]], by_no[selected[b]]
                d = math.hypot(ca.x - cb.x, ca.y - cb.y)
                time_s[a][b] = int(round(d * TIME_SCALE))
    matrix = TravelMatrix(node_ids=node_ids, time_s=time_s)

    orders: list[Order] = []
    for n in known_nos + new_nos:
        c = by_no[n]
        ready, due, service = c.ready * TIME_SCALE, c.due * TIME_SCALE, c.service * TIME_SCALE
        if cfg.tw_tightness == "tight":
            due = ready + _tighten(due - ready, service)
        orders.append(Order(
            order_id=f"o{n}", node_id=f"n{n}", demand=c.demand,
            ready_s=int(ready), due_s=int(due), service_s=int(service),
            is_new=(n in set(new_nos)),
        ))

    total_load = sum(by_no[n].demand for n in known_nos)
    base_k = math.ceil(total_load / inst.capacity) if total_load else 1
    extra = 0 if cfg.vehicle_slack == "tight" else 1
    k = min(base_k + extra, inst.vehicle_number)
    vehicles = [
        Vehicle(vehicle_id=f"V{i+1}", capacity=inst.capacity,
                available_from_s=0, return_by_s=int(inst.depot.due * TIME_SCALE))
        for i in range(k)
    ]

    draft = ScenarioDraft(
        scenario_id=scenario_id,
        version=1,
        time_origin=time_origin,
        capacity_unit="load_units",
        depot_node_id="depot",
        orders=orders,
        vehicles=vehicles,
        travel_matrix=matrix,
        locks=Locks(),
        cost_config=CostConfig(coefficients={
            "travel_time": CostCoefficient(value=1.0, unit="s", source="model_travel_time"),
        }),
    )
    return GeneratedScenario(draft=draft, event_log=tuple(event_log))
