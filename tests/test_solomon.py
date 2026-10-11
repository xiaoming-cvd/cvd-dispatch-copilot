"""Solomon 解析与转换测试：用公开文献记载的已知值做核对。"""
from __future__ import annotations

import math
from pathlib import Path

from src.adapters.solomon import build_travel_matrix, load_instance, parse_solomon

RAW = Path(__file__).resolve().parent.parent / "data" / "raw" / "solomon"


class TestParse:
    def test_c101_known_values(self):
        """C101 公开已知值：仓库(40,50) 窗[0,1236]；客户1 (45,68) 需求10 窗[912,967] 服务90。"""
        inst = load_instance(RAW / "C101.txt")
        assert inst.name == "C101"
        assert inst.vehicle_number == 25
        assert inst.capacity == 200
        assert len(inst.customers) == 100
        assert (inst.depot.x, inst.depot.y) == (40, 50)
        assert inst.depot.due == 1236
        c1 = inst.customers[0]
        assert (c1.x, c1.y, c1.demand, c1.ready, c1.due, c1.service) == (45, 68, 10, 912, 967, 90)

    def test_all_files_parse(self):
        for p in sorted(RAW.glob("*.txt")):
            inst = load_instance(p)
            assert len(inst.customers) == 100, p.name
            assert inst.depot.cust_no == 0

    def test_original_units_preserved(self):
        """原始载荷与时间单位必须原样保留（转换只发生在 draft 层）。"""
        inst = load_instance(RAW / "R101.txt")
        assert inst.capacity == 200
        assert all(isinstance(c.ready, float) for c in inst.customers)


class TestMatrix:
    def test_matrix_complete_square(self):
        inst = load_instance(RAW / "C101.txt")
        node_ids, time_s = build_travel_matrix(inst)
        n = len(node_ids)
        assert n == 101
        assert all(len(row) == n for row in time_s)
        assert all(time_s[i][i] == 0 for i in range(n))
        assert all(v > 0 for i, row in enumerate(time_s) for j, v in enumerate(row) if i != j)

    def test_scaling_and_rounding(self):
        """行驶时间 = 欧氏距离 × 60 四舍五入取整（声明规则）。"""
        text = (
            "T1\n\nVEHICLE\nNUMBER     CAPACITY\n  5         100\n\nCUSTOMER\n"
            "CUST NO. XCOORD. YCOORD. DEMAND READY TIME DUE DATE SERVICE TIME\n"
            "    0      0        0        0        0      1000      0\n"
            "    1      3        4       10        0       500     10\n"  # 距离 5 -> 300s
            "    2      0        1       10        0       500     10\n"  # 距离 1 -> 60s
        )
        inst = parse_solomon(text)
        node_ids, time_s = build_travel_matrix(inst)
        assert node_ids == ["depot", "n1", "n2"]
        assert time_s[0][1] == int(round(5 * 60)) == 300
        assert time_s[0][2] == int(round(1 * 60)) == 60
        assert math.isclose(math.hypot(3, 4), 5.0)
