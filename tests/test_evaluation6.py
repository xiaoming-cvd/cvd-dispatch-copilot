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


def test_summary_covers_all_manifest_scenarios():
    """summary.csv 必须与冻结清单逐场景对齐：39 场景、物化失败场景有失败行，
    不得静默改变分母（2026-10-11 决策②）。"""
    import csv
    eval_dir = ROOT / "reports" / "evaluation6"
    manifest = json.loads((eval_dir / "frozen_manifest.json")
                          .read_text(encoding="utf-8"))
    man_ids = {e["scenario_id"] for e in manifest["scenarios"]}
    with open(eval_dir / "summary.csv", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    sum_ids = {r["scenario_id"] for r in rows}
    assert sum_ids == man_ids, (man_ids - sum_ids, sum_ids - man_ids)
    mat = [r for r in rows if r["method"] == "__materialize__"]
    assert len(mat) == 8
    for r in mat:
        assert r["status"] == "materialize_error"
        assert "E_PLAN_NOT_FOUND" in r["solver_status"]
        assert r["budget_ms"] == str(manifest["budget_ms"])
    # 每个场景至少有一行，且预算列无空缺
    assert all(r["budget_ms"] for r in rows)
    # 成本上限核验列存在且无违规（P1：selected ≤ allowed 或已如实标注）
    assert "allowed_cost_s" in rows[0] and "within_cap" in rows[0]
    assert not [r for r in rows if r["within_cap"] == "VIOLATION"]


def test_fixedpool_sensitivity_dev_complete():
    """固定候选池敏感性：dev 集全部物化成功场景（16）均有结果，且
    同池筛选语义一致——selected > allowed 时 within_cap 必须为 False。"""
    eval_dir = ROOT / "reports" / "evaluation6"
    fp = eval_dir / "sensitivity_fixedpool"
    manifest = json.loads((eval_dir / "frozen_manifest.json")
                          .read_text(encoding="utf-8"))
    raw_dir = eval_dir / "raw"
    expected = []
    for e in manifest["scenarios"]:
        if e["split"] != "dev":
            continue
        rec = json.loads((raw_dir / f"{e['scenario_id']}.json")
                         .read_text(encoding="utf-8"))
        if not rec.get("materialize_error"):
            expected.append(e["scenario_id"])
    done = {p.stem for p in fp.glob("*.json")}
    assert done == set(expected), (set(expected) - done, done - set(expected))
    for p in fp.glob("*.json"):
        rec = json.loads(p.read_text(encoding="utf-8"))
        assert rec["experiment"] == "sensitivity-fixedpool/1.0"
        assert rec["git_commit"]
        for cap, d in rec["per_cap"].items():
            sel, allowed = d["selected_cost_s"], d["allowed_cost_s"]
            if sel is not None and allowed is not None:
                if sel > allowed + 1e-6:
                    assert d["within_cap"] is False, (p.stem, cap)
                else:
                    assert d["within_cap"] is True, (p.stem, cap)
