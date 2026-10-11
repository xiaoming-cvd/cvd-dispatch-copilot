"""生成阶段 0 可手算小样本（5 个节点、5 个订单、2 辆车）。

样本为自行生成的合成数据，不是真实客户业务。
结构：仓库 depot + 4 个旧任务 + 1 个新增任务（o5@B），
原计划 V1: o1->o2，V2: o3->o4，已人工验算可行：

V1: 0 出发 -> A(到达300, 服务至420) -> B(到达620, 服务至740) -> 仓库(返回1350)
V2: 0 出发 -> C(到达500, 服务至620) -> E(到达820, 服务至940) -> 仓库(返回1640)
载重 7/10、3/10；时间窗 ready=0 due=10000；返回上限 20000。
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # 保证从任意目录运行均可导入 src

from src.domain.models import (
    CostCoefficient,
    CostConfig,
    Locks,
    Order,
    OriginalPlan,
    RoutePlan,
    Scenario,
    TravelMatrix,
    Vehicle,
    input_hash,
)
from src.validation.checks import validate_scenario

TZ = timezone(timedelta(hours=8))  # 固定东八区，仅用于演示
NODES = ["depot", "A", "B", "C", "E"]


def build_sample() -> Scenario:
    matrix = TravelMatrix(
        node_ids=NODES,
        time_s=[
            [0, 300, 600, 500, 700],
            [300, 0, 200, 350, 450],
            [610, 210, 0, 250, 300],
            [500, 350, 260, 0, 200],
            [700, 450, 300, 210, 0],
        ],
    )
    orders = [
        Order(order_id="o1", node_id="A", demand=4, ready_s=0, due_s=10000, service_s=120),
        Order(order_id="o2", node_id="B", demand=3, ready_s=0, due_s=10000, service_s=120),
        Order(order_id="o3", node_id="C", demand=2, ready_s=0, due_s=10000, service_s=120),
        Order(order_id="o4", node_id="E", demand=1, ready_s=0, due_s=10000, service_s=120),
        Order(order_id="o5", node_id="B", demand=2, ready_s=2000, due_s=9000,
              service_s=120, is_new=True),
    ]
    vehicles = [
        Vehicle(vehicle_id="V1", capacity=10, available_from_s=0, return_by_s=20000),
        Vehicle(vehicle_id="V2", capacity=10, available_from_s=0, return_by_s=20000),
    ]
    plan = OriginalPlan(
        plan_id="plan-demo-1",
        version=1,
        routes=[
            RoutePlan(vehicle_id="V1", order_ids=["o1", "o2"]),
            RoutePlan(vehicle_id="V2", order_ids=["o3", "o4"]),
        ],
    )
    return Scenario(
        schema_version="1.0",
        scenario_id="sample-small-001",
        version=1,
        time_origin=datetime(2026, 10, 10, 8, 0, 0, tzinfo=TZ),
        time_unit="s",
        capacity_unit="load_units",
        depot_node_id="depot",
        orders=orders,
        vehicles=vehicles,
        travel_matrix=matrix,
        original_plan=plan,
        locks=Locks(),
        cost_config=CostConfig(coefficients={
            "travel_time": CostCoefficient(value=1.0, unit="s", source="model_travel_time"),
        }),
    )


def main() -> None:
    scn = build_sample()
    report = validate_scenario(scn)
    if not report.ok:
        raise SystemExit(f"样本未通过校验：{report.to_dict()}")

    payload = {
        "_meta": {
            "note": "自行生成的合成演示样本，非真实客户业务；时间单位为秒（相对 time_origin）",
            "input_hash": input_hash(scn),
            "generated_by": "scripts/make_sample.py",
        },
        "scenario": scn.model_dump(mode="json"),
    }
    out = Path(__file__).resolve().parent.parent / "data" / "sample" / "sample_small.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"样本已写入 {out}")
    print(f"input_hash = {payload['_meta']['input_hash']}")
    print(f"校验通过：{report.ok}，问题数 {len(report.issues)}")


if __name__ == "__main__":
    main()
