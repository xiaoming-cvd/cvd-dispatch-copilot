"""布局感知划分回归测试（总工程师审查补充）。

坐标集合完全相同的算例（同一布局组）只能整体属于开发集或测试集，
否则等于用高度相似的数据做独立泛化测试。
"""
from __future__ import annotations

import json
from pathlib import Path

from src.adapters.solomon import load_instance

ROOT = Path(__file__).resolve().parent.parent
REGISTRY = ROOT / "data" / "processed" / "scenarios" / "registry.json"
RAW = ROOT / "data" / "raw" / "solomon"


def _layout_groups() -> dict[tuple, list[str]]:
    groups: dict[tuple, list[str]] = {}
    for p in sorted(RAW.glob("*.txt")):
        inst = load_instance(p)
        sig = tuple(sorted((c.x, c.y) for c in inst.customers))
        groups.setdefault(sig, []).append(inst.name)
    return groups


class TestLayoutAwareSplit:
    def test_no_layout_group_spans_dev_and_test(self):
        registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
        split_of = {s["base_instance"]: s["split"] for s in registry["scenarios"]}
        for sig, names in _layout_groups().items():
            sets = {split_of[n] for n in names}
            assert len(sets) == 1, (
                f"布局组 {names} 横跨集合 {sets}：同一坐标布局不得分入开发/测试")

    def test_every_instance_registered(self):
        registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
        # 注册表现含 Kilby 补充基准（kilby- 前缀），本断言只针对 Solomon 集
        registered = {s["base_instance"] for s in registry["scenarios"]
                      if not s["base_instance"].startswith("kilby-")}
        on_disk = {p.stem for p in RAW.glob("*.txt")}
        assert registered == on_disk
