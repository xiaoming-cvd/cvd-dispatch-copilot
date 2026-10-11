"""Amazon 合成结构样本解析测试 + 原始文件未被修改核对。"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from src.adapters.amazon import DEFAULT_HORIZON_S, parse_route

ROOT = Path(__file__).resolve().parent.parent
SAMPLE = ROOT / "data" / "sample" / "amazon_route_synthetic.json"
MANIFEST = ROOT / "data" / "manifests" / "solomon_manifest.json"
RAW = ROOT / "data" / "raw" / "solomon"


class TestAmazonParse:
    def _route(self):
        return parse_route(json.loads(SAMPLE.read_text(encoding="utf-8")))

    def test_volume_aggregation(self):
        r = self._route()
        stops = {s.stop_id: s for s in r.stops}
        assert stops["stop-A"].volume_cm3 == 30 * 20 * 10 + 40 * 25 * 15
        assert stops["stop-B"].volume_cm3 == 50 * 30 * 20

    def test_missing_window_declared_not_zero_deadline(self):
        r = self._route()
        b = next(s for s in r.stops if s.stop_id == "stop-B")
        assert b.window_start_s == 0 and b.window_end_s == DEFAULT_HORIZON_S
        assert any("stop-B" in a and "缺失" in a for a in r.assumptions)

    def test_directed_travel_matrix_keys(self):
        r = self._route()
        assert r.travel_time_s[("RouteSynthetic-1:depot", "RouteSynthetic-1:stop-A")] == 600
        assert ("RouteSynthetic-1:stop-A", "RouteSynthetic-1:stop-B") in r.travel_time_s

    def test_composite_keys(self):
        r = self._route()
        assert all(s.key.startswith("RouteSynthetic-1:") for s in r.stops)
        assert all(s.key.endswith(s.stop_id) for s in r.stops)

    def test_sample_marked_synthetic(self):
        meta = json.loads(SAMPLE.read_text(encoding="utf-8"))["_meta"]
        assert "合成" in meta["note"]


@pytest.mark.skipif(
    not (RAW / "C101.txt").is_file(),
    reason="缺 Solomon 原始数据（data/raw/solomon）："
           "scripts/download_solomon.py 获取后重跑")
class TestRawIntegrity:
    def test_raw_files_match_manifest(self):
        """原始文件下载后未被修改（与 manifest 哈希一致）。"""
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        assert manifest["files"], "manifest 为空"
        for e in manifest["files"]:
            p = ROOT / "data" / "raw" / e["file"]
            assert p.exists(), f"缺失原始文件 {p}"
            actual = hashlib.sha256(p.read_bytes()).hexdigest()
            assert actual == e["sha256"], f"{e['file']} 哈希不一致，原始文件可能被修改"

    def test_manifest_traceability_fields(self):
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        for e in manifest["files"]:
            assert e["source_url"].startswith("http")
            assert "sintef" in e["canonical_source"].lower()
            assert e["license_note"]
            assert e["field_mapping"]
            assert e["conversion_rules"]["time_conversion"]
            assert e["script_version"]
