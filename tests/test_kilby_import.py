"""Kilby 动态 VRP 适配与导入测试（阶段 5 补充基准）。

自包含单元测试用合成 VRPTEST 文本；集成测试用 data/raw/kilby 原始 zip。
三次复审 P0 修正：原始 zip 缺失时集成类显式 skip（不得收集失败）；
恢复方式见 README「数据恢复」节（来源 Mendeley DOI 10.17632/3fwc3twwn6.1）。
"""
from __future__ import annotations

import json
import math
import zipfile
from pathlib import Path

import pytest

from src.adapters.kilby import (
    CUTOFF_DEFAULT,
    build_draft,
    parse_vrptest,
    partition_known_new,
)
from src.domain.models import input_hash
from src.validation.checks import validate_draft

RAW_ZIP = Path(__file__).resolve().parent.parent / "data" / "raw" / "kilby" / "3fwc3twwn6.zip"
ZIP_PREFIX = "Kilbys instances for Dynamic Vehicle Routing Problem/"

SYNTHETIC = """VRPTEST 1.0
COMMENT: synthetic for unit test
COMMENT: Best known objective: 123.4
NAME: synth6
NUM_DEPOTS: 1
NUM_CAPACITIES: 1
NUM_VISITS: 6
NUM_LOCATIONS: 7
NUM_VEHICLES: 6
CAPACITIES: 50
DATA_SECTION
DEPOTS
 0
DEMAND_SECTION
 1 -10
 2 -20
 3 -5
 4 -15
 5 -8
 6 -12
LOCATION_COORD_SECTION
 0 0 0
 1 10 0
 2 20 0
 3 30 0
 4 40 0
 5 50 0
 6 60 0
DEPOT_LOCATION_SECTION
 0 0
VISIT_LOCATION_SECTION
 1 1
 2 2
 3 3
 4 4
 5 5
 6 6
DURATION_SECTION
 1 3
 2 3
 3 3
 4 3
 5 3
 6 3
DEPOT_TIME_WINDOW_SECTION
 0 0 100
COMMENT: TIMESTEP: 5
TIME_AVAIL_SECTION
 1 10
 2 20
 3 30
 4 60
 5 70
 6 90
EOF
"""


class TestParseSynthetic:
    def test_fields(self):
        inst = parse_vrptest(SYNTHETIC)
        assert inst.name == "synth6"
        assert inst.n_visits == 6
        assert inst.capacity == 50.0
        assert inst.depot_window == (0, 100)
        assert inst.best_known == 123.4
        assert inst.timestep == 5.0
        # 规则 1：需求取绝对值
        assert inst.demands == (10.0, 20.0, 5.0, 15.0, 8.0, 12.0)
        assert inst.time_avail == (10, 20, 30, 60, 70, 90)

    def test_missing_section_rejected(self):
        bad = SYNTHETIC.replace("TIME_AVAIL_SECTION\n", "")
        try:
            parse_vrptest(bad)
        except ValueError as e:
            assert "E_KILBY_FORMAT" in str(e)
        else:
            raise AssertionError("缺段必须抛 E_KILBY_FORMAT")

    def test_bad_header_rejected(self):
        try:
            parse_vrptest("NAME: x\n")
        except ValueError as e:
            assert "E_KILBY_FORMAT" in str(e)
        else:
            raise AssertionError("缺 VRPTEST 头必须抛错")


class TestPartition:
    def test_cutoff_partition(self):
        inst = parse_vrptest(SYNTHETIC)
        known, new = partition_known_new(inst, 0.5)
        # horizon=100, cutoff=0.5 → TIME_AVAIL ≤ 50 为旧
        assert known == (1, 2, 3)
        assert new == (4, 5, 6)
        assert set(known) | set(new) == set(range(1, 7))
        assert not (set(known) & set(new))

    def test_cutoff_out_of_range(self):
        inst = parse_vrptest(SYNTHETIC)
        for bad in (0.0, 1.0, -0.5):
            try:
                partition_known_new(inst, bad)
            except ValueError as e:
                assert "E_CUTOFF" in str(e)
            else:
                raise AssertionError(f"cutoff={bad} 必须抛 E_CUTOFF")


class TestDraftBuild:
    def _draft(self):
        return build_draft(parse_vrptest(SYNTHETIC), scenario_id="kilby-synth-s101",
                           lock_ratio_target=0.3, seed=101)

    def test_validate_ok(self):
        rep = validate_draft(self._draft())
        assert rep.ok, rep.to_dict()

    def test_orders_flags_and_windows(self):
        d = self._draft()
        by_id = {o.order_id: o for o in d.orders}
        assert by_id["o1"].is_new is False
        assert by_id["o4"].is_new is True
        for o in d.orders:
            assert o.ready_s == 0 and o.due_s == 100  # 声明缺省窗

    def test_matrix_rules(self):
        m = self._draft().travel_matrix
        n = len(m.time_s)
        assert m.node_ids[0] == "depot" and n == 7
        for i in range(n):
            assert m.time_s[i][i] == 0
            for j in range(n):
                v = m.time_s[i][j]
                assert isinstance(v, int) and v >= 0  # 零值合法（阶段 0 规则）
                assert m.time_s[i][j] == m.time_s[j][i]  # 欧氏对称
        # 规则 2：取整欧氏距离 spot check（(0,0)→(10,0)=10）
        assert m.time_s[0][1] == 10

    def test_fleet_covers_total_load(self):
        """规则 5 回归：车队按全部任务配（只按旧任务配车曾致 MIP_INFEASIBLE）。"""
        d = self._draft()
        total = sum(o.demand for o in d.orders)
        fleet = sum(v.capacity for v in d.vehicles)
        assert total == 70.0
        assert len(d.vehicles) == math.ceil(70 / 50) + 1
        assert fleet >= total

    def test_determinism(self):
        a = input_hash(self._draft())
        b = input_hash(self._draft())
        assert a == b


@pytest.mark.skipif(
    not RAW_ZIP.is_file(),
    reason="缺 Kilby 原始数据（data/raw/kilby/3fwc3twwn6.zip，"
           "Mendeley DOI 10.17632/3fwc3twwn6.1）：恢复步骤见 README「数据恢复」节")
class TestRealZip:
    @classmethod
    def _instances(cls):
        with zipfile.ZipFile(RAW_ZIP) as z:
            names = sorted(n for n in z.namelist() if n.endswith(".vrp"))
            return [parse_vrptest(z.read(n).decode("utf-8")) for n in names]

    def test_all_22_parse(self):
        insts = self._instances()
        assert len(insts) == 22
        names = {i.name for i in insts}
        assert {"c50", "c100", "tai100a", "f71", "tai385"} <= names

    def test_c50_known_values(self):
        inst = next(i for i in self._instances() if i.name == "c50")
        assert inst.n_visits == 50
        assert inst.capacity == 160.0
        assert inst.depot_window == (0, 351)
        assert inst.best_known == 570.89
        assert inst.coords[0] == (30.0, 40.0)  # 仓坐标

    def test_partition_and_draft_all_instances(self):
        for inst in self._instances():
            known, new = partition_known_new(inst, CUTOFF_DEFAULT)
            assert len(known) + len(new) == inst.n_visits
            assert known and new  # 每例都有旧/新任务
            d = build_draft(inst, scenario_id=f"kilby-{inst.name}-s101",
                            lock_ratio_target=0.3, seed=101)
            assert validate_draft(d).ok
            total = sum(o.demand for o in d.orders)
            assert sum(v.capacity for v in d.vehicles) >= total

    def test_layouts_unique(self):
        """各算例坐标集合互不相同 → 布局组=算例本身，天然无跨集泄漏。"""
        insts = self._instances()
        sigs = {tuple(sorted(i.coords)) for i in insts}
        assert len(sigs) == len(insts) == 22


class TestImportedRegistry:
    def test_registry_entries_exist(self):
        reg = json.loads((RAW_ZIP.parent.parent.parent / "processed" / "scenarios"
                          / "registry.json").read_text(encoding="utf-8"))
        kilby = [e for e in reg["scenarios"] if e["scenario_id"].startswith("kilby-")]
        assert len(kilby) == 22
        for e in kilby:
            f = RAW_ZIP.parent.parent.parent / "processed" / e["file"]
            assert f.exists(), e["file"]
        splits = {e["split"] for e in kilby}
        assert splits == {"dev", "test"}
