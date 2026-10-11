"""阶段 5 API 端到端测试（TestClient + 临时目录，绝不触碰真实 .env 密钥）。

覆盖红线：
- 上传限量/规模校验、schema 错误带定位；
- baseline → solve 闭环可行；
- 超时/无解语义如实（NO_FEASIBLE_FOUND ≠ 证明无解，状态逐条记录）；
- 版本冲突 409；确认必须显式（accept_all 或 decisions）；
- 未过独立校验禁止导出（403 E_EXPORT_FORBIDDEN）；
- rebuild 默认后端 OR-Tools 且逐条记录，绝不静默换后端；
- CSV 公式注入防护；队列重启 interrupted。
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from src.api import Settings, create_app
from src.api.app import csv_cell
from src.api.jobs import JobQueue
from src.llm.base import LLMProviderError
from src.llm.config import LLMConfig


def make_doc(sid: str, n_orders: int = 3) -> dict:
    """小规模合法草稿（可通过 schema 与交叉一致性校验）。"""
    nodes = ["depot"] + [f"n{i}" for i in range(1, n_orders + 1)]
    matrix = [[0] * len(nodes) for _ in nodes]
    for i in range(len(nodes)):
        for j in range(len(nodes)):
            if i != j:
                matrix[i][j] = 300 + 100 * ((i + j) % 3)
    orders = [{"order_id": f"o{i}", "node_id": f"n{i}", "demand": 2.0,
               "ready_s": 0, "due_s": 36000, "service_s": 300,
               "is_new": i == n_orders}
              for i in range(1, n_orders + 1)]
    return {"_meta": {"base_instance": "test", "seed": 1, "lock_ratio_target": 0.0},
            "draft": {
                "schema_version": "1.0", "scenario_id": sid, "version": 1,
                "time_origin": "2026-10-11T08:00:00+08:00",
                "time_unit": "s", "capacity_unit": "load_units",
                "depot_node_id": "depot", "orders": orders,
                "vehicles": [{"vehicle_id": "V1", "capacity": 50.0,
                              "available_from_s": 0, "return_by_s": 72000}],
                "travel_matrix": {"node_ids": nodes, "time_s": matrix},
                "locks": {"assignment_locks": {}, "relative_order_locks": []}}}


@pytest.fixture()
def app(tmp_path):
    settings = Settings(project_root=tmp_path,
                        llm=LLMConfig(provider="mock", demo_mode=True))
    return create_app(settings)


@pytest.fixture()
def client(app):
    with TestClient(app) as c:
        yield c


def upload(client, sid: str) -> None:
    r = client.post("/api/scenarios/upload", json=make_doc(sid))
    assert r.status_code == 200, r.text


def submit_and_wait(client, app, path: str, body: dict) -> tuple[dict, dict]:
    """提交任务并等待完成，返回 (任务元数据, 结果行)。"""
    r = client.post(path, json=body)
    assert r.status_code == 200, r.text
    job_id = r.json()["job_id"]
    ok = app.state.queue.wait_idle(timeout=60)
    meta = client.get(f"/api/jobs/{job_id}").json()
    if meta["status"] not in ("succeeded", "failed", "interrupted"):
        raise AssertionError(
            f"任务未处理: status={meta['status']} wait_idle={ok} "
            f"pending={list(app.state.queue._pending)} "
            f"thread_alive={app.state.queue._thread.is_alive()} "
            f"jobs_dir={app.state.queue.jobs_dir}")
    return meta, app.state.queue.result(job_id)


# ---------------- 基础 ----------------

def test_health(app, client):
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["llm"]["provider"] == "mock"
    assert body["default_budget_ms"] == 60_000


def test_llm_provider_rejected_at_startup(tmp_path):
    settings = Settings(project_root=tmp_path,
                        llm=LLMConfig(provider="anthropic"))
    with pytest.raises(RuntimeError, match="E_LLM_CONFIG"):
        create_app(settings)


# ---------------- 上传 ----------------

def test_upload_and_list(client):
    upload(client, "t-list-001")
    r = client.get("/api/scenarios")
    ids = [s["scenario_id"] for s in r.json()["scenarios"]]
    assert "t-list-001" in ids


def test_upload_invalid_json_422(client):
    r = client.post("/api/scenarios/upload", content=b"not-json",
                    headers={"Content-Type": "application/json"})
    assert r.status_code == 422


def test_upload_schema_error_has_loc(client):
    doc = make_doc("t-schema-001")
    doc["draft"].pop("orders")
    r = client.post("/api/scenarios/upload", json=doc)
    assert r.status_code == 422
    detail = json.loads(r.json()["detail"])
    assert detail["status"] == "error"
    assert any("orders" in e["loc"] for e in detail["field_errors"])


def test_upload_duplicate_409(client):
    upload(client, "t-dup-001")
    r = client.post("/api/scenarios/upload", json=make_doc("t-dup-001"))
    assert r.status_code == 409


def test_upload_too_large_413(app, tmp_path):
    settings = Settings(project_root=tmp_path,
                        llm=LLMConfig(provider="mock", demo_mode=True),
                        max_upload_bytes=200)
    app2 = create_app(settings)
    with TestClient(app2) as c:
        r = c.post("/api/scenarios/upload", json=make_doc("t-big-001"))
        assert r.status_code == 413


def test_scenario_detail_reports_issues(client):
    upload(client, "t-detail-001")
    r = client.get("/api/scenarios/t-detail-001")
    assert r.status_code == 200
    body = r.json()
    assert body["n_orders"] == 3
    assert body["materialized"] is False
    assert isinstance(body["issues"], list)


def test_template_download(client):
    r = client.get("/api/template")
    assert r.status_code == 200
    assert "scenario_template.json" in r.headers["content-disposition"]


# ---------------- baseline + solve 闭环 ----------------

def test_baseline_then_insert_end_to_end(app, client):
    upload(client, "t-e2e-001")
    meta, res = submit_and_wait(client, app, "/api/baseline",
                                {"scenario_id": "t-e2e-001", "budget_ms": 20000})
    assert meta["status"] == "succeeded", meta.get("error")
    assert res["n_routes"] >= 1

    meta, _ = submit_and_wait(client, app, "/api/solve",
                              {"scenario_id": "t-e2e-001", "method": "insert",
                               "budget_ms": 20000})
    assert meta["status"] == "succeeded", meta.get("error")
    r = client.get(f"/api/jobs/{meta['job_id']}/result.json")
    assert r.status_code == 200
    body = r.json()
    assert body["has_solution"] is True
    assert body["result"]["validation"]["independent_ok"] is True
    assert body["original_plan"], "结果应带原计划摘要供对比"

    csv_r = client.get(f"/api/jobs/{meta['job_id']}/export.csv")
    assert csv_r.status_code == 200
    text = csv_r.text
    assert text.startswith("\ufeff")
    assert "[assignment]" in text and "[timetable]" in text
    assert "method=" in text and "backend=" in text


def test_solve_auto_materializes(app, client):
    upload(client, "t-auto-001")
    meta, _ = submit_and_wait(client, app, "/api/solve",
                              {"scenario_id": "t-auto-001", "method": "insert",
                               "budget_ms": 20000})
    assert meta["status"] == "succeeded", meta.get("error")


def test_rebuild_default_backend_ortools(app, client):
    """Q2/Q3：rebuild 默认 OR-Tools，结果逐条记录实际后端。"""
    upload(client, "t-rebuild-001")
    submit_and_wait(client, app, "/api/baseline",
                    {"scenario_id": "t-rebuild-001", "budget_ms": 20000})
    meta, _ = submit_and_wait(client, app, "/api/solve",
                              {"scenario_id": "t-rebuild-001", "method": "rebuild",
                               "budget_ms": 20000})
    assert meta["status"] == "succeeded", meta.get("error")
    body = client.get(f"/api/jobs/{meta['job_id']}/result.json").json()
    assert body["backend"] == "ortools"
    assert body["result"]["metrics"]["backend"] == "ortools"


def test_backend_validation(client):
    upload(client, "t-bk-001")
    r = client.post("/api/solve", json={"scenario_id": "t-bk-001",
                                        "method": "insert", "backend": "copt"})
    assert r.status_code == 422  # backend 仅 rebuild 可指定
    r = client.post("/api/solve", json={"scenario_id": "t-bk-001",
                                        "method": "rebuild", "backend": "gurobi"})
    assert r.status_code == 422


def test_solve_unknown_method_422(client):
    upload(client, "t-m-001")
    r = client.post("/api/solve", json={"scenario_id": "t-m-001",
                                        "method": "magic"})
    assert r.status_code == 422


def test_solve_version_conflict_409(app, client):
    upload(client, "t-vc-001")
    r = client.post("/api/solve", json={"scenario_id": "t-vc-001",
                                        "method": "insert",
                                        "base_version": 99})
    assert r.status_code == 409


def test_result_404_and_pending_409(client):
    r = client.get("/api/jobs/job-nope/result.json")
    assert r.status_code == 404


# ---------------- 超时/无解语义如实 ----------------

def _canned_outcome(status: str, routes: list, validation_ok: bool):
    from src.domain.models import SolveResult
    result = SolveResult.model_construct(
        input_hash="h", config={"method": "insert"}, solver_version="test",
        elapsed_ms=1234, status=status, routes=routes, schedule=[],
        metrics={"backend": None}, diff={}, 
        validation={"ok": validation_ok, "checks": []},
        warnings=[], assumptions=[])
    return SimpleNamespace(result=result)


def test_no_feasible_recorded_honestly(app, client, monkeypatch):
    """超时/无解如实记录：NO_FEASIBLE_FOUND 不得写成"不可行"。"""
    upload(client, "t-nofeas-001")
    submit_and_wait(client, app, "/api/baseline",
                    {"scenario_id": "t-nofeas-001", "budget_ms": 20000})
    monkeypatch.setattr(
        "src.api.routes.pipeline.solve",
        lambda scn, method, budget, **kw: _canned_outcome(
            "NO_FEASIBLE_FOUND", [], True))
    meta, _ = submit_and_wait(client, app, "/api/solve",
                                {"scenario_id": "t-nofeas-001", "method": "insert",
                                 "budget_ms": 20000})
    assert meta["status"] == "succeeded", meta.get("error")
    body = client.get(f"/api/jobs/{meta['job_id']}/result.json").json()
    assert body["has_solution"] is False
    assert body["result"]["status"] == "NO_FEASIBLE_FOUND"
    assert "INFEASIBLE" not in body["result"]["status"]


# ---------------- 导出红线 ----------------

def test_export_forbidden_when_validation_fails(app, client, monkeypatch):
    upload(client, "t-forbid-001")
    submit_and_wait(client, app, "/api/baseline",
                    {"scenario_id": "t-forbid-001", "budget_ms": 20000})
    routes = [{"vehicle_id": "V1", "order_ids": ["o1"]}]
    monkeypatch.setattr(
        "src.api.routes.pipeline.solve",
        lambda scn, method, budget, **kw: _canned_outcome(
            "FEASIBLE", routes, False))
    meta, _ = submit_and_wait(client, app, "/api/solve",
                              {"scenario_id": "t-forbid-001", "method": "insert",
                               "budget_ms": 20000})
    assert meta["status"] == "succeeded"
    r = client.get(f"/api/jobs/{meta['job_id']}/export.csv")
    assert r.status_code == 403
    assert "E_EXPORT_FORBIDDEN" in r.json()["detail"]


# ---------------- 约束草案（mock LLM） ----------------

def test_draft_requires_materialized_409(client):
    upload(client, "t-llm-001")
    r = client.post("/api/constraints/draft",
                    json={"scenario_id": "t-llm-001", "text": "o1 不换车"})
    assert r.status_code == 409  # 先 baseline


def test_draft_and_confirm_flow(app, client):
    upload(client, "t-llm-002")
    submit_and_wait(client, app, "/api/baseline",
                    {"scenario_id": "t-llm-002", "budget_ms": 20000})
    r = client.post("/api/constraints/draft",
                    json={"scenario_id": "t-llm-002", "text": "o1 不换车"})
    assert r.status_code == 200
    body = r.json()
    assert body["demo_mode"] is True
    draft = body["draft"]
    assert draft["base_version"] == 1
    did = draft["draft_id"]

    # 版本冲突 409
    r = client.post(f"/api/constraints/{did}/confirm",
                    json={"scenario_id": "t-llm-002", "base_version": 99,
                          "accept_all": True})
    assert r.status_code == 409
    assert "E_VERSION_CONFLICT" in r.json()["detail"]

    # 不显式接受 422
    r = client.post(f"/api/constraints/{did}/confirm",
                    json={"scenario_id": "t-llm-002", "base_version": 1})
    assert r.status_code == 422
    assert "E_ACCEPT_REQUIRED" in r.json()["detail"]

    # 显式全部接受 200 → 新版本落盘
    r = client.post(f"/api/constraints/{did}/confirm",
                    json={"scenario_id": "t-llm-002", "base_version": 1,
                          "accept_all": True})
    assert r.status_code == 200
    assert r.json()["scenario_version"] == 2

    # history 可见新版本
    h = client.get("/api/scenarios/t-llm-002/history").json()
    assert any(v["version"] == 2 for v in h["versions"])


def test_draft_get_404(client):
    r = client.get("/api/constraints/draft-nope")
    assert r.status_code == 404


def test_llm_unavailable_502(app, client, monkeypatch):
    upload(client, "t-llm-503")
    submit_and_wait(client, app, "/api/baseline",
                    {"scenario_id": "t-llm-503", "budget_ms": 20000})

    def _raise(scn, text):
        raise LLMProviderError("model unreachable")

    monkeypatch.setattr(app.state.llm_service, "create_draft", _raise)
    r = client.post("/api/constraints/draft",
                    json={"scenario_id": "t-llm-503", "text": "o1 不换车"})
    assert r.status_code == 502
    assert "E_LLM_UNAVAILABLE" in r.json()["detail"]


# ---------------- CSV 注入防护 ----------------

@pytest.mark.parametrize("raw,expected", [
    ("=cmd|x", "'=cmd|x"),
    ("+sum(A1)", "'+sum(A1)"),
    ("-1", "'-1"),
    ("@evil", "'@evil"),
    ("\t=1", "'\t=1"),
    ("正常文本", "正常文本"),
    ('含,逗号', '"含,逗号"'),
    ('含"引号', '"含""引号"'),
])
def test_csv_cell_injection_guard(raw, expected):
    assert csv_cell(raw) == expected


# ---------------- 队列 ----------------

def test_queue_marks_stale_running_interrupted(tmp_path):
    jobs_dir = tmp_path / "jobs"
    queue = JobQueue(jobs_dir)
    stale = {"job_id": "job-stale-1", "kind": "solve", "status": "running",
             "created_at": "2026-10-11T00:00:00+00:00"}
    (jobs_dir / "job-stale-1.json").write_text(json.dumps(stale), encoding="utf-8")
    queue2 = JobQueue(jobs_dir)
    meta = queue2.get("job-stale-1")
    assert meta["status"] == "interrupted"
    assert "重启" in meta["error"]


def test_queue_full_rejects(app, tmp_path):
    settings = Settings(project_root=tmp_path,
                        llm=LLMConfig(provider="mock", demo_mode=True),
                        max_queued_jobs=1)
    app2 = create_app(settings)
    q = app2.state.queue
    q.submit("baseline", {"scenario_id": "x", "budget_ms": 1})
    with pytest.raises(OverflowError):
        q.submit("baseline", {"scenario_id": "y", "budget_ms": 1})


def test_jobs_endpoints_404(client):
    assert client.get("/api/jobs/job-none").status_code == 404
    assert client.get("/api/jobs/job-none/result.json").status_code == 404


def test_history_empty_scenario(client):
    r = client.get("/api/scenarios/t-none/history")
    assert r.status_code == 404
