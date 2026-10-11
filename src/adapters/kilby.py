"""Kilby 动态 VRP 数据集适配器（阶段 5 补充基准，溯源见 data/raw/kilby/collection_notes.md）。

数据源：Kilby & Montemanni 动态 VRP 算例（Mendeley DOI 10.17632/3fwc3twwn6.1，
CC BY 4.0）。原始格式为 VRPTEST 1.0，每个算例含点位坐标、需求、服务时长、
仓时间窗与每访客的 appearing time（订单出现时刻）。

转换规则（写入 manifest，审查可追溯）：
1. 需求取绝对值（原始文件为负值，表示配送方向）；
2. 行驶时间秒 = 欧氏距离四舍五入到整数（单位速度假设，模拟尺度）；
3. 算例无逐客时间窗：ready_s=0、due_s=仓窗末端，记为声明缺省规则；
4. 动态语义：TIME_AVAIL ≤ cutoff × 仓窗末端 为旧任务（计划开始时已知），
   其余为新任务（插单）；cutoff 默认 0.5（Kilby 文献惯用值， manifest 记录）；
5. 车队规模 = 全部任务需求总量/容量向上取整 + 1 辆余量
   （只按旧任务配车在插单后会不可行，Kilby smoke 实测复现 MIP_INFEASIBLE）。
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from src.domain.models import (
    CostConfig,
    Locks,
    Order,
    ScenarioDraft,
    TravelMatrix,
    Vehicle,
)

CUTOFF_DEFAULT = 0.5
TZ = timezone(timedelta(hours=8))
T0 = datetime(2026, 10, 10, 8, 0, 0, tzinfo=TZ)

_SECTION_RE = re.compile(r"^[A-Z][A-Z_]*$")
_BEST_KNOWN_RE = re.compile(r"Best known objective:\s*([0-9.]+)")
_TIMESTEP_RE = re.compile(r"TIMESTEP:\s*([0-9.]+)")


@dataclass(frozen=True)
class KilbyInstance:
    """一个 VRPTEST 动态算例的解析结果（1-索引访问，demand/time_avail 按访客）。"""

    name: str
    capacity: float
    n_visits: int
    coords: tuple[tuple[float, float], ...]      # 位置 0..n（0 = 仓）
    demands: tuple[float, ...]                   # 访客 1..n（已取绝对值）
    durations: tuple[int, ...]                   # 访客 1..n
    depot_window: tuple[int, int]                # 仓时间窗
    time_avail: tuple[int, ...]                  # 访客 1..n 出现时刻
    best_known: float | None
    timestep: float | None


def parse_vrptest(text: str) -> KilbyInstance:
    """解析 VRPTEST 1.0 文本（Kilby 动态算例）。缺段或数值非法直接抛错。"""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines or lines[0] != "VRPTEST 1.0":
        raise ValueError("E_KILBY_FORMAT: 缺少 VRPTEST 1.0 头")

    meta: dict[str, str] = {}
    sections: dict[str, list[str]] = {}
    cur: str | None = None
    best_known: float | None = None
    timestep: float | None = None
    for ln in lines[1:]:
        if ln == "EOF":
            break
        m = re.match(r"^(NAME|NUM_VISITS|NUM_LOCATIONS|CAPACITIES|NUM_VEHICLES):\s*(.+)$", ln)
        if m:
            meta[m.group(1)] = m.group(2)
            continue
        if ln.startswith("COMMENT:"):
            if (bk := _BEST_KNOWN_RE.search(ln)):
                best_known = float(bk.group(1))
            if (ts := _TIMESTEP_RE.search(ln)):
                timestep = float(ts.group(1))
            continue
        if _SECTION_RE.match(ln):
            cur = ln
            sections.setdefault(cur, [])
            continue
        if cur is not None:
            sections[cur].append(ln)

    def need(sec: str) -> list[str]:
        if sec not in sections:
            raise ValueError(f"E_KILBY_FORMAT: 缺少段 {sec}")
        return sections[sec]

    n_visits = int(meta.get("NUM_VISITS", "0"))
    if n_visits <= 0:
        raise ValueError("E_KILBY_FORMAT: NUM_VISITS 缺失或非法")
    capacity = float(meta["CAPACITIES"])

    coords: dict[int, tuple[float, float]] = {}
    for ln in need("LOCATION_COORD_SECTION"):
        p = ln.split()
        coords[int(p[0])] = (float(p[1]), float(p[2]))
    n_locs = int(meta.get("NUM_LOCATIONS", "0")) or max(coords) + 1
    if len(coords) != n_locs:
        raise ValueError(
            f"E_KILBY_FORMAT: 坐标数 {len(coords)} 与 NUM_LOCATIONS {n_locs} 不一致")

    demands: dict[int, float] = {}
    for ln in need("DEMAND_SECTION"):
        p = ln.split()
        demands[int(p[0])] = abs(float(p[1]))  # 规则 1：原始为负值
    durations = {int(ln.split()[0]): int(ln.split()[1])
                 for ln in need("DURATION_SECTION")}
    visit_loc = {int(ln.split()[0]): int(ln.split()[1])
                 for ln in need("VISIT_LOCATION_SECTION")}
    time_avail = {int(ln.split()[0]): int(float(ln.split()[1]))
                  for ln in need("TIME_AVAIL_SECTION")}
    depot_w = need("DEPOT_TIME_WINDOW_SECTION")[0].split()
    depot_window = (int(float(depot_w[1])), int(float(depot_w[2])))

    for v in range(1, n_visits + 1):
        for tbl, label in ((demands, "需求"), (durations, "服务时长"),
                           (visit_loc, "位置映射"), (time_avail, "出现时刻")):
            if v not in tbl:
                raise ValueError(f"E_KILBY_FORMAT: 访客 {v} 缺{label}")

    return KilbyInstance(
        name=meta.get("NAME", "unknown"),
        capacity=capacity,
        n_visits=n_visits,
        coords=tuple(coords[i] for i in range(n_locs)),
        demands=tuple(demands[v] for v in range(1, n_visits + 1)),
        durations=tuple(durations[v] for v in range(1, n_visits + 1)),
        depot_window=depot_window,
        time_avail=tuple(time_avail[v] for v in range(1, n_visits + 1)),
        best_known=best_known,
        timestep=timestep,
    )


def partition_known_new(inst: KilbyInstance, cutoff: float = CUTOFF_DEFAULT
                        ) -> tuple[tuple[int, ...], tuple[int, ...]]:
    """按规则 4 划分旧/新访客（返回 1-索引访客号）。cutoff 越界即抛错。"""
    if not 0.0 < cutoff < 1.0:
        raise ValueError(f"E_CUTOFF: cutoff={cutoff} 必须在 (0,1) 内")
    horizon = inst.depot_window[1]
    known = tuple(v for v in range(1, inst.n_visits + 1)
                  if inst.time_avail[v - 1] <= cutoff * horizon)
    new = tuple(v for v in range(1, inst.n_visits + 1) if v not in set(known))
    return known, new


def build_draft(inst: KilbyInstance, *, scenario_id: str, cutoff: float = CUTOFF_DEFAULT,
                lock_ratio_target: float, seed: int,
                vehicle_slack: int = 1) -> ScenarioDraft:
    """Kilby 算例 → ScenarioDraft（全部转换规则见模块 docstring）。"""
    known, new = partition_known_new(inst, cutoff)
    if not known:
        raise ValueError(f"E_KILBY_EMPTY_KNOWN: {inst.name} cutoff={cutoff} 下无旧任务")
    if not new:
        raise ValueError(f"E_KILBY_EMPTY_NEW: {inst.name} cutoff={cutoff} 下无新任务")

    horizon = inst.depot_window[1]
    orders = tuple(
        Order(order_id=f"o{v}", node_id=f"n{v}", demand=float(inst.demands[v - 1]),
              ready_s=0, due_s=horizon, service_s=int(inst.durations[v - 1]),
              is_new=(v in set(new)))
        for v in range(1, inst.n_visits + 1))

    total_load = sum(inst.demands)
    n_veh = max(1, math.ceil(total_load / inst.capacity)) + vehicle_slack  # 规则 5
    vehicles = tuple(
        Vehicle(vehicle_id=f"V{i}", capacity=float(inst.capacity),
                available_from_s=0, return_by_s=horizon)
        for i in range(1, n_veh + 1))

    node_ids = ["depot"] + [f"n{v}" for v in range(1, inst.n_visits + 1)]
    pts = [inst.coords[0]] + [inst.coords[v] for v in range(1, inst.n_visits + 1)]
    matrix = []
    for a in pts:
        row = []
        for b in pts:
            d = math.hypot(a[0] - b[0], a[1] - b[1])
            row.append(int(round(d)))  # 规则 2
        matrix.append(tuple(row))

    return ScenarioDraft(
        scenario_id=scenario_id,
        version=1,
        time_origin=T0,
        capacity_unit="load_units",
        depot_node_id="depot",
        orders=orders,
        vehicles=vehicles,
        travel_matrix=TravelMatrix(node_ids=tuple(node_ids),
                                   time_s=tuple(matrix)),
        locks=Locks(),
        cost_config=CostConfig(),
    )


def scenario_meta(inst: KilbyInstance, *, cutoff: float, lock_ratio_target: float,
                  seed: int, split: str) -> dict:
    """draft 外层的 _meta（事件日志 + 转换声明，供 registry/报告溯源）。"""
    known, new = partition_known_new(inst, cutoff)
    return {
        "base_instance": f"kilby-{inst.name}",
        "seed": seed,
        "pending_original_plan": True,
        "lock_ratio_target": lock_ratio_target,
        "synthetic_events": False,
        "dynamic_source": "kilby",
        "cutoff": cutoff,
        "note": ("新增任务为数据集原始 appearing time（cutoff 划分），非合成事件；"
                 "无逐客时间窗，ready/due 取 [0, 仓窗末端] 为声明缺省；"
                 "行驶时间=欧氏距离取整（单位速度假设，模拟尺度）；需求已取绝对值"),
        "event_log": [{
            "instance": f"kilby-{inst.name}",
            "seed": seed,
            "known": list(known),
            "new": list(new),
            "cutoff": cutoff,
            "lock_ratio": lock_ratio_target,
        }],
        "split": split,
    }
