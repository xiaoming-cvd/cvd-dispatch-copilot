"""情景生成器测试：种子可复现、集合无交叉、划分确定性、草稿可校验。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.adapters.generator import (
    GenConfig,
    generate_draft,
    split_base_instances,
)
from src.adapters.solomon import load_instance
from src.domain.models import ScenarioDraft, input_hash
from src.validation.checks import validate_draft

TZ = timezone(timedelta(hours=8))
T0 = datetime(2026, 10, 10, 8, 0, 0, tzinfo=TZ)
RAW = Path(__file__).resolve().parent.parent / "data" / "raw" / "solomon"

# 三次复审 P0：缺原始数据时显式 skip，不得在收集阶段崩溃。
pytestmark = pytest.mark.skipif(
    not (RAW / "C101.txt").is_file(),
    reason="缺 Solomon 原始数据（data/raw/solomon）："
           "scripts/download_solomon.py 获取后重跑")


def _inst():
    """惰性加载 C101（模块级读取会在无原件环境导致收集失败）。"""
    return load_instance(RAW / "C101.txt")


CFG = GenConfig(n_known=15, n_new=3, tw_tightness="normal", vehicle_slack="normal",
                lock_ratio=0.2, seed=101)


class TestReproducibility:
    def test_same_seed_identical(self):
        a = generate_draft(_inst(), CFG, "x", T0)
        b = generate_draft(_inst(), CFG, "x", T0)
        assert input_hash(a.draft) == input_hash(b.draft)
        assert a.event_log == b.event_log

    def test_different_seed_differs(self):
        c2 = GenConfig(**{**CFG.__dict__, "seed": 999})
        a = generate_draft(_inst(), CFG, "x", T0)
        b = generate_draft(_inst(), c2, "x", T0)
        assert input_hash(a.draft) != input_hash(b.draft)

    def test_event_log_tracks_selection(self):
        gen = generate_draft(_inst(), CFG, "x", T0)
        ev = gen.event_log[0]
        assert len(ev["known"]) == 15 and len(ev["new"]) == 3
        assert not (set(ev["known"]) & set(ev["new"]))
        assert set(ev["known"]) | set(ev["new"]) <= set(range(1, 101))


class TestDraftValidity:
    def test_draft_passes_validation(self):
        gen = generate_draft(_inst(), CFG, "x", T0)
        assert validate_draft(gen.draft).ok

    def test_new_orders_flagged(self):
        gen = generate_draft(_inst(), CFG, "x", T0)
        new_ids = {o.order_id for o in gen.draft.orders if o.is_new}
        assert len(new_ids) == 3

    def test_tight_windows_shrink(self):
        """用内联合成算例确定性验证声明规则：
        宽窗（宽 > 2×服务时长）减半收紧；窄窗（宽 <= 2×服务时长）保持原宽。"""
        from src.adapters.solomon import SolomonCustomer, SolomonInstance
        customers = tuple(
            SolomonCustomer(i, float(i * 10), 0.0, 1, 0, 100, 10)  # 宽窗 100
            for i in range(1, 21)
        )
        inst_w = SolomonInstance("W", 5, 100.0,
                                 SolomonCustomer(0, 0, 0, 0, 0, 1000, 0), customers)
        tight = GenConfig(**{**CFG.__dict__, "tw_tightness": "tight", "n_known": 15, "n_new": 0})
        normal = generate_draft(inst_w, GenConfig(**{**CFG.__dict__, "n_known": 15, "n_new": 0, "seed": 5}), "x", T0)
        tightg = generate_draft(inst_w, GenConfig(**{**tight.__dict__, "seed": 5}), "x", T0)
        nw = sum(o.due_s - o.ready_s for o in normal.draft.orders)
        tw = sum(o.due_s - o.ready_s for o in tightg.draft.orders)
        assert tw < nw
        # 逐订单：tight 后窗宽 <= 原窗宽（绝不放宽）
        for a, b in zip(sorted(normal.draft.orders, key=lambda o: o.order_id),
                        sorted(tightg.draft.orders, key=lambda o: o.order_id)):
            assert b.due_s - b.ready_s <= a.due_s - a.ready_s

        # 窄窗算例（窗宽 15 < 2×服务时长 20×60s？单位原始：宽15 服务20）→ 保持原宽
        narrow = tuple(
            SolomonCustomer(i, float(i * 10), 0.0, 1, 0, 15, 20) for i in range(1, 21)
        )
        inst_n = SolomonInstance("N", 5, 100.0,
                                 SolomonCustomer(0, 0, 0, 0, 0, 1000, 0), narrow)
        cfg_n = GenConfig(n_known=15, n_new=0, tw_tightness="tight", seed=5)
        gn = generate_draft(inst_n, GenConfig(n_known=15, n_new=0, seed=5), "x", T0)
        gt = generate_draft(inst_n, cfg_n, "x", T0)
        for a, b in zip(sorted(gn.draft.orders, key=lambda o: o.order_id),
                        sorted(gt.draft.orders, key=lambda o: o.order_id)):
            assert b.due_s - b.ready_s == a.due_s - a.ready_s

    def test_time_conversion(self):
        """原始窗值 × 60 进入 schema（C101 客户1 窗 [912,967] -> [54720,58020]）。"""
        gen = generate_draft(_inst(), GenConfig(n_known=15, n_new=0, seed=101), "x", T0)
        by_id = {o.order_id: o for o in gen.draft.orders}
        if "o1" in by_id:
            assert by_id["o1"].ready_s == 912 * 60
            assert by_id["o1"].due_s == 967 * 60


class TestSplit:
    def test_split_deterministic(self):
        names = ["C101", "C102", "C201", "C202", "R101", "R102"]
        a = split_base_instances(names, 0.5, 42)
        b = split_base_instances(names, 0.5, 42)
        assert a == b

    def test_split_covers_all(self):
        names = ["C101", "C102", "C201", "R101", "RC101"]
        s = split_base_instances(names, 0.5, 7)
        assert set(s) == set(names)
        assert all(v in ("dev", "test") for v in s.values())

    def test_no_cross_contamination(self):
        """同一基础算例只能属于一个集合（含其全部种子派生）。"""
        names = [p.stem for p in sorted(RAW.glob("*.txt"))]
        s = split_base_instances(names, 0.5, 20261010)
        per_base = {}
        for n, grp in s.items():
            per_base.setdefault(n, grp)
        assert len(per_base) == len(names)  # 每算例恰好一个归属
        dev = {n for n, g in s.items() if g == "dev"}
        test = {n for n, g in s.items() if g == "test"}
        assert not (dev & test)
