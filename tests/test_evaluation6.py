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
    # 成本上限核验（二次复审）：布尔 within_cap，可由 raw 重新导出且无违规
    assert "allowed_cost_s" in rows[0] and "within_cap" in rows[0]
    assert not [r for r in rows if r["cap_verdict"] == "VIOLATION"]


def test_within_cap_boolean_semantics():
    """二次复审验收③：60 条有数值 LD 记录中 6 条超限均标 FALSE；
    主 cap=1.10 超限 0 条；无解行 within_cap 为空（null，不写 ok）；
    有数值时标记必须等于 cost ≤ cap × reference（由 raw 重算验证）。"""
    import csv as _csv
    eval_dir = ROOT / "reports" / "evaluation6"
    raw_dir = eval_dir / "raw"
    rows = list(_csv.DictReader(
        open(eval_dir / "summary.csv", encoding="utf-8-sig")))
    ld = [r for r in rows if r["method"] == "low_disruption"]
    with_vals, no_sol, false_rows = [], [], []
    for r in ld:
        # 由 raw 重新导出（验收④）
        raw = json.loads((raw_dir / f"{r['scenario_id']}.json")
                         .read_text(encoding="utf-8"))
        raw_row = next(x for x in raw["rows"]
                       if x["method"] == "low_disruption"
                       and str(x.get("cost_cap_ratio")) == r["cost_cap_ratio"])
        if not raw_row.get("has_solution"):
            no_sol.append(r)
            assert r["within_cap"] == "", ("无解行必须 null", r)
            continue
        sel, ratio, ref = (raw_row["total_travel_time_s"],
                           raw_row["cost_cap_ratio"],
                           raw_row["reference_best_cost_s"])
        assert sel is not None and ratio is not None and ref is not None
        numeric_within = sel <= ratio * ref + 1e-6
        with_vals.append((r, numeric_within))
        if not numeric_within:
            false_rows.append(r)
            assert r["within_cap"] == "FALSE", ("超限必须 FALSE", r)
        else:
            assert r["within_cap"] == "TRUE", ("未超必须 TRUE", r)
        assert float(r["total_travel_time_s"]) == float(sel)
    assert len(with_vals) == 60, len(with_vals)
    assert len(false_rows) == 6, len(false_rows)
    assert not [r for r in false_rows if r["cost_cap_ratio"] == "1.1"]


def test_freezing_provenance_disclosed():
    """二次复审验收⑥：未补齐前置冻结证据时报告显式说明本轮为事后归档。"""
    eval_dir = ROOT / "reports" / "evaluation6"
    report = (eval_dir / "evaluation_report.md").read_text(encoding="utf-8")
    assert "事后归档校验" in report
    fi = json.loads((eval_dir / "frozen_inputs.json")
                    .read_text(encoding="utf-8"))
    assert "事后归档" in fi["freezing_provenance"]
    assert (eval_dir / "requirements_lock.txt").is_file()


def test_testset_main_rates_unchanged():
    """二次复审验收②：主方法 test 口径保持 4/16、5/16、15/16（冻结集口径）。"""
    import csv as _csv
    eval_dir = ROOT / "reports" / "evaluation6"
    rows = list(_csv.DictReader(
        open(eval_dir / "summary.csv", encoding="utf-8-sig")))
    test_ids = {r["scenario_id"] for r in rows if r["split"] == "test"}
    assert len(test_ids) == 16
    rates = {}
    for meth in ("insert", "rebuild", "low_disruption"):
        ok = sum(1 for r in rows if r["split"] == "test"
                 and r["method"] == meth and r["status"] == "feasible")
        rates[meth] = ok
    assert rates == {"insert": 4, "rebuild": 5, "low_disruption": 15}, rates


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
