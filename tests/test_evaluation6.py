"""阶段 6 评测 runner 冒烟测试（小场景快速验证，不动真实 raw 目录）。"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_api_stage5 import make_doc  # noqa: E402


def _load_runner():
    spec = importlib.util.spec_from_file_location(
        "run_evaluation", ROOT / "scripts" / "run_evaluation.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _setup_tmp_registry(tmp_path: Path, sid: str = "eval-smoke-001") -> dict:
    doc = make_doc(sid)
    sdir = tmp_path / "data" / "processed" / "scenarios" / "dev"
    sdir.mkdir(parents=True)
    (sdir / f"{sid}.json").write_text(
        json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    entry = {"scenario_id": sid, "split": "dev", "base_instance": "test",
             "file": f"scenarios/dev/{sid}.json",
             "config": {"source": "smoke", "seed": 1}}
    (tmp_path / "data" / "processed" / "scenarios" / "registry.json"
     ).write_text(json.dumps({"scenarios": [entry]}, ensure_ascii=False),
                  encoding="utf-8")
    return entry


def test_runner_smoke(tmp_path, monkeypatch):
    ev = _load_runner()
    entry = _setup_tmp_registry(tmp_path)
    monkeypatch.setattr(ev, "ROOT", tmp_path)
    monkeypatch.setattr(ev, "RAW_DIR", tmp_path / "raw")
    monkeypatch.setattr(ev, "MANIFEST_PATH", tmp_path / "manifest.json")

    ev.freeze_manifest([entry])
    assert ev.MANIFEST_PATH.is_file()
    frozen = json.loads(ev.MANIFEST_PATH.read_text(encoding="utf-8"))
    assert frozen["budget_ms"] == 60_000
    assert frozen["cost_cap_ratio"] == 1.10

    out = ev.run_scenario(entry, copt_diag=False, overwrite=True)
    assert out is not None and out.is_file()
    rec = json.loads(out.read_text(encoding="utf-8"))
    assert rec["scenario_id"] == entry["scenario_id"]
    assert rec["split"] == "dev"
    assert rec["scenario_input_hash"]
    methods = [r["method"] for r in rec["rows"]]
    assert methods == ["insert", "rebuild", "low_disruption"]
    for r in rec["rows"]:
        assert r["status"] == "feasible", r
        assert r["has_solution"] and r["validation_ok"]
        assert r["scenario_version"] == 1

    # 断点续跑：不 overwrite 时跳过
    assert ev.run_scenario(entry, copt_diag=False, overwrite=True) is not None
    assert ev.run_scenario(entry, copt_diag=False, overwrite=False) is None


def test_runner_materialize_error_recorded(tmp_path, monkeypatch):
    """物化失败如实记录 materialize_error，不静默跳过。"""
    ev = _load_runner()
    entry = _setup_tmp_registry(tmp_path, sid="eval-smoke-bad")
    monkeypatch.setattr(ev, "ROOT", tmp_path)
    monkeypatch.setattr(ev, "RAW_DIR", tmp_path / "raw")
    monkeypatch.setattr(ev, "MANIFEST_PATH", tmp_path / "manifest.json")

    # 模拟物化阶段抛错（如预算内未找到原计划）：
    # runner 必须如实记录 materialize_error，不得静默跳过。
    def _boom(*_a, **_k):
        raise ValueError("E_PLAN_NOT_FOUND: 预算内未找到可执行原计划")

    monkeypatch.setattr(ev, "materialize_scenario", _boom)

    out = ev.run_scenario(entry, copt_diag=False, overwrite=True)
    rec = json.loads(out.read_text(encoding="utf-8"))
    assert rec["materialize_error"]
    assert rec["rows"] == []
