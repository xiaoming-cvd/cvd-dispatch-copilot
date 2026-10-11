"""Solomon 算例解析与统一转换（任务书 3 章）。

流程：解析原始文本（保留原始载荷/时间单位）→ 转换为 ScenarioDraft。
转换约定（与 data/manifests/solomon_manifest.json 一致）：
- 时间：原始值 * 60 得秒；- 行驶时间：欧氏距离(双精度) * 60 后四舍五入取整。
坐标与载荷为基准单位，不得直接称为公里、公斤。
禁止先求全量解再删"未来任务"：新增任务由情景生成器在选定已知任务后
从剩余客户中按种子随机选取，事件与种子全部留痕。
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

TIME_SCALE = 60  # 1 个原始时间单位 -> 60 秒；记录于 manifest，不得静默改变


@dataclass(frozen=True)
class SolomonCustomer:
    cust_no: int
    x: float
    y: float
    demand: float
    ready: float  # 原始时间单位
    due: float
    service: float


@dataclass(frozen=True)
class SolomonInstance:
    name: str
    vehicle_number: int  # 原始车辆数上限（仅供参考）
    capacity: float  # 原始容量
    depot: SolomonCustomer  # cust_no = 0
    customers: tuple[SolomonCustomer, ...]  # cust_no = 1..N

    @property
    def all_nodes(self) -> tuple[SolomonCustomer, ...]:
        return (self.depot,) + self.customers


def parse_solomon(text: str) -> SolomonInstance:
    """解析 Solomon 标准文本格式（CUST NO. XCOORD. YCOORD. DEMAND READY DUE SERVICE）。"""
    name = text.strip().splitlines()[0].strip()
    lines = text.splitlines()
    rows: list[list[float]] = []
    vehicle_number = capacity = None
    for ln in lines:
        parts = ln.split()
        if len(parts) == 7:
            try:
                rows.append([float(p) for p in parts])
            except ValueError:
                continue
        elif len(parts) == 2 and vehicle_number is None:
            try:
                vehicle_number, capacity = int(parts[0]), float(parts[1])
            except ValueError:
                continue
    if vehicle_number is None or capacity is None or not rows:
        raise ValueError(f"E_SOLOMON_FORMAT: 无法解析算例 {name}（缺少车辆行或客户行）")
    rows.sort(key=lambda r: r[0])
    customers = tuple(
        SolomonCustomer(int(r[0]), r[1], r[2], r[3], r[4], r[5], r[6]) for r in rows
    )
    depot = customers[0]
    if depot.cust_no != 0:
        raise ValueError(f"E_SOLOMON_FORMAT: 算例 {name} 缺少仓库行 CUST 0")
    return SolomonInstance(
        name=name,
        vehicle_number=vehicle_number,
        capacity=capacity,
        depot=depot,
        customers=tuple(c for c in customers if c.cust_no != 0),
    )


def load_instance(path: Path) -> SolomonInstance:
    return parse_solomon(path.read_text(encoding="utf-8"))


def travel_seconds(inst: SolomonInstance, i: int, j: int) -> int:
    """节点间行驶时间（秒）：欧氏距离(双精度) * TIME_SCALE 四舍五入取整。

    i/j 为 all_nodes 的索引（0=仓库）。
    """
    a, b = inst.all_nodes[i], inst.all_nodes[j]
    d = math.hypot(a.x - b.x, a.y - b.y)
    return int(round(d * TIME_SCALE))


def build_travel_matrix(inst: SolomonInstance) -> tuple[list[str], list[list[int]]]:
    """生成 schema 所需的 node_ids 与 time_s 完整方阵。"""
    nodes = inst.all_nodes
    node_ids = ["depot" if c.cust_no == 0 else f"n{c.cust_no}" for c in nodes]
    idx = {c.cust_no: k for k, c in enumerate(nodes)}
    n = len(nodes)
    time_s = [[0] * n for _ in range(n)]
    for a in range(n):
        for b in range(n):
            if a != b:
                ca, cb = nodes[a], nodes[b]
                d = math.hypot(ca.x - cb.x, ca.y - cb.y)
                time_s[a][b] = int(round(d * TIME_SCALE))
    return node_ids, time_s
