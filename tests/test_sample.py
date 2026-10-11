"""小样本验收测试：独立重算原计划时刻表，验证人工手算结果。

这里的重算逻辑是测试内独立编写的（不调用任何求解器），
作为"手算预期"对照 scripts/make_sample.py 中的注释：
V1: 0 -> A(300,420) -> B(620,740) -> 仓库 1350
V2: 0 -> C(500,620) -> E(820,940) -> 仓库 1640
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from src.domain.models import Scenario, input_hash
from src.validation.checks import validate_scenario
from scripts.make_sample import build_sample

ROOT = Path(__file__).resolve().parent.parent


def recompute_schedule(scn: Scenario):
    """独立重算每条路线的逐站时刻（测试内手写，故意不复用产品代码）。"""
    m = scn.travel_matrix
    idx = {n: i for i, n in enumerate(m.node_ids)}
    order_by_id = {o.order_id: o for o in scn.orders}
    veh_by_id = {v.vehicle_id: v for v in scn.vehicles}
    result = {}
    for route in scn.original_plan.routes:
        veh = veh_by_id[route.vehicle_id]
        t = veh.available_from_s
        load = 0.0
        stops = []
        prev = scn.depot_node_id
        for oid in route.order_ids:
            o = order_by_id[oid]
            t += m.time_s[idx[prev]][idx[o.node_id]]
            arrive = t
            assert arrive <= o.due_s, f"{oid} 到达 {arrive} 超过 due {o.due_s}"
            start = max(arrive, o.ready_s)
            t = start + o.service_s
            stops.append((oid, arrive, start, t))
            load += o.demand
            prev = o.node_id
        t += m.time_s[idx[prev]][idx[scn.depot_node_id]]
        assert load <= veh.capacity, f"{route.vehicle_id} 超载 {load}>{veh.capacity}"
        assert t <= veh.return_by_s, f"{route.vehicle_id} 返回超时 {t}>{veh.return_by_s}"
        result[route.vehicle_id] = (stops, t)
    return result


class TestSample:
    def test_sample_passes_validation(self):
        scn = build_sample()
        report = validate_scenario(scn)
        assert report.ok, report.to_dict()

    def test_sample_hand_computed_schedule(self):
        scn = build_sample()
        sched = recompute_schedule(scn)
        v1_stops, v1_ret = sched["V1"]
        v2_stops, v2_ret = sched["V2"]
        assert v1_stops == [("o1", 300, 300, 420), ("o2", 620, 620, 740)]
        assert v1_ret == 1350
        assert v2_stops == [("o3", 500, 500, 620), ("o4", 820, 820, 940)]
        assert v2_ret == 1640

    def test_committed_sample_file_matches(self):
        """已入库的样本文件必须与生成器输出一致（哈希可复现）。"""
        path = ROOT / "data" / "sample" / "sample_small.json"
        assert path.exists(), "样本文件缺失，请先运行 scripts/make_sample.py"
        payload = json.loads(path.read_text(encoding="utf-8"))
        scn = Scenario(**payload["scenario"])
        assert payload["_meta"]["input_hash"] == input_hash(scn)
        assert payload["_meta"]["note"], "样本必须标注数据来源说明"

    def test_schema_exported(self):
        schema_path = ROOT / "docs" / "schema" / "scenario.schema.json"
        assert schema_path.exists(), "请先运行 scripts/export_schema.py"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        assert schema["title"] == "Scenario"
        assert "orders" in schema["properties"]
