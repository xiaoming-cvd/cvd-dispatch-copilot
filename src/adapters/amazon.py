"""Amazon Last Mile 路由适配（任务书 3 章适配范围）。

范围：仅单条路线的任务解析；跨路线不拼接（路线间通常缺完整出行矩阵，
不得虚构）。容积按站点汇总包裹体积；缺失时间窗记录为缺失并采用
声明规则（默认 [0, horizon]，写入 assumptions），绝不转成零点截止。
键为 route_id + stop_id 组合，避免跨路线误合并。

许可：Amazon Last Mile 为 CC BY-NC 4.0（非商用），详见
docs/amazon_field_mapping.md 与 data/manifests。真实数据未下载，
data/sample/amazon_route_synthetic.json 为按公开字段字典构造的
合成结构样本，仅用于解析器测试。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

DEFAULT_HORIZON_S = 86400  # 缺失时间窗声明规则：默认 [0, 24h]


@dataclass(frozen=True)
class AmazonStop:
    key: str  # route_id + ":" + stop_id
    stop_id: str
    lat: float
    lng: float
    volume_cm3: float  # 该站全部包裹体积之和
    window_start_s: int | None  # None = 原始数据缺失
    window_end_s: int | None
    planned_service_s: float
    is_depot: bool


@dataclass(frozen=True)
class AmazonRoute:
    route_id: str
    capacity_cm3: float
    stops: tuple[AmazonStop, ...]
    travel_time_s: dict[tuple[str, str], int]  # 有向，站点组合键
    assumptions: tuple[str, ...] = field(default_factory=tuple)


def parse_route(data: dict) -> AmazonRoute:
    """解析单条路线 JSON（字段以 MIT CAVE rc-cli 数据字典为准）。"""
    route_id = data["route_id"]
    capacity = float(data["executor_capacity"]["volume_capacity_cm3"])
    stops: list[AmazonStop] = []
    assumptions: list[str] = []

    for stop_id, s in data["stops"].items():
        vol = 0.0
        for pkg in s.get("packages", {}).values():
            dim = pkg.get("dimensions", {})
            vol += float(dim.get("height_cm", 0)) * float(dim.get("width_cm", 0)) * float(
                dim.get("depth_cm", 0))
        tw = s.get("time_window") or {}
        ws = _parse_utc_seconds(tw.get("start_time_utc"))
        we = _parse_utc_seconds(tw.get("end_time_utc"))
        if ws is None or we is None:
            assumptions.append(
                f"站点 {route_id}:{stop_id} 时间窗缺失，按声明规则使用 [0, {DEFAULT_HORIZON_S}]")
            ws = 0 if ws is None else ws
            we = DEFAULT_HORIZON_S if we is None else we
        stops.append(AmazonStop(
            key=f"{route_id}:{stop_id}", stop_id=stop_id,
            lat=float(s["lat"]), lng=float(s["lng"]), volume_cm3=vol,
            window_start_s=ws, window_end_s=we,
            planned_service_s=float(s.get("planned_service_time_seconds", 0)),
            is_depot=(s.get("type") == "depot"),
        ))

    travel: dict[tuple[str, str], int] = {}
    raw_tt = data.get("travel_times", {})
    for a, row in raw_tt.items():
        for b, t in row.items():
            travel[(f"{route_id}:{a}", f"{route_id}:{b}")] = int(round(float(t)))

    return AmazonRoute(route_id=route_id, capacity_cm3=capacity, stops=tuple(stops),
                       travel_time_s=travel, assumptions=tuple(assumptions))


def _parse_utc_seconds(v) -> int | None:
    if v in (None, ""):
        return None
    if isinstance(v, (int, float)):
        return int(v)
    # ISO 时间串：取当日秒数（UTC）
    dt = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    return int(dt.timestamp()) % DEFAULT_HORIZON_S
