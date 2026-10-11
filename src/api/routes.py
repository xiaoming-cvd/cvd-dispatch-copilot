"""阶段 5 路由与任务处理器（被 app.create_app 挂载）。

处理器在 attach() 里注册进 JobQueue：
- baseline：draft → 原计划物化（预算受控，超时如实失败）；
- solve：物化 → 单方法求解（端到端预算硬约束，剩余预算传给求解器）。
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, ValidationError

from ..domain.models import Scenario, ScenarioDraft, input_hash
from ..llm.base import LLMProviderError
from ..llm.schema import DraftStatus
from ..solver import pipeline
from ..solver.original import materialize_scenario
from ..validation.checks import validate_draft

from .app import (Settings, ScenarioIndex, build_result_csv, csv_cell)
from .jobs import JobQueue


# ================= 请求体 =================

class DraftIn(BaseModel):
    scenario_id: str
    text: str


class ConfirmIn(BaseModel):
    scenario_id: str
    base_version: int
    accept_all: bool = False
    decisions: dict[str, bool] = {}


class BaselineIn(BaseModel):
    scenario_id: str
    budget_ms: int = 60_000


class SolveIn(BaseModel):
    scenario_id: str
    method: str  # insert | rebuild | low_disruption
    backend: str | None = None  # 仅 rebuild：ortools（默认）| copt，显式记录
    budget_ms: int = 60_000
    base_version: int | None = None  # 可选乐观锁


METHODS = ("insert", "rebuild", "low_disruption")


def attach(app, settings: Settings, index: ScenarioIndex, queue: JobQueue,
           llm_service) -> None:
    """注册路由与任务处理器（create_app 调用一次）。

    注意：router 必须在 attach 内部创建——模块级共享 router 会被
    include_router 复制进多个 app，导致闭包捕获旧队列（跨测试/跨实例
    任务提交到已停止的队列，任务永远 queued）。
    """
    router = APIRouter(prefix="/api")

    # ---------------- 任务处理器 ----------------

    def _load_draft_doc(scenario_id: str) -> tuple[dict, ScenarioDraft]:
        doc = index.load_doc(scenario_id)
        try:
            return doc, ScenarioDraft(**doc["draft"])
        except (KeyError, ValidationError) as exc:
            raise ValueError(f"E_DRAFT_INVALID: {scenario_id} 草稿无法解析：{exc}") from exc

    def _materialize(scenario_id: str, budget_ms: int) -> Scenario:
        doc, draft = _load_draft_doc(scenario_id)
        meta = doc.get("_meta", {})
        started = time.monotonic()
        scn = materialize_scenario(
            draft, lock_ratio=float(meta.get("lock_ratio_target") or 0.0),
            lock_seed=int(meta.get("seed") or 0),
            time_limit_ms=budget_ms)
        elapsed = int((time.monotonic() - started) * 1000)
        if elapsed > budget_ms:
            raise ValueError(
                f"E_PLAN_TIMEOUT: 原计划生成超过预算 {budget_ms}ms（超时≠无解）")
        index.save_materialized(scn)
        return scn

    def handle_baseline(payload: dict) -> dict:
        scn = _materialize(payload["scenario_id"], int(payload["budget_ms"]))
        return {"scenario_id": scn.scenario_id, "version": scn.version,
                "input_hash": input_hash(scn), "n_routes": len(scn.original_plan.routes)}

    def handle_solve(payload: dict) -> dict:
        budget = int(payload["budget_ms"])
        t0 = time.monotonic()
        scenario_id = payload["scenario_id"]
        try:
            scn = index.load_scenario(scenario_id)
        except HTTPException:
            # 未物化：先用预算的一部分物化，剩余给求解（端到端预算硬约束）
            mat_budget = max(1000, min(20_000, budget // 3))
            scn = _materialize(scenario_id, mat_budget)
        remaining = budget - int((time.monotonic() - t0) * 1000)
        if remaining < 1000:
            raise ValueError(
                f"E_BUDGET: 物化后剩余预算 {remaining}ms 不足 1000ms，"
                "请加大 budget_ms（超时≠无解）")
        method = payload["method"]
        outcome = pipeline.solve(scn, method, remaining,
                                 backend=payload.get("backend"))
        result = outcome.result
        row = {
            "scenario_id": scenario_id,
            "scenario_version": scn.version,
            "scenario_input_hash": input_hash(scn),
            "method": method,
            "backend": result.metrics.get("backend")
            or ("ortools" if method == "rebuild" and payload.get("backend") is None
                else None),
            "backend_requested": result.metrics.get("backend_requested")
            or payload.get("backend"),
            "budget_ms": remaining,
            "has_solution": bool(result.routes),
            "result": result.model_dump(mode="json"),
        }
        return row

    queue.register("baseline", handle_baseline)
    queue.register("solve", handle_solve)

    # ---------------- 基础 ----------------

    @router.get("/health")
    def health():
        return {"status": "ok", "llm": settings.llm.redacted(),
                "queue_max": settings.max_queued_jobs,
                "default_budget_ms": settings.default_budget_ms}

    # ---------------- scenarios ----------------

    @router.get("/scenarios")
    def scenarios():
        return {"scenarios": index.list()}

    @router.get("/scenarios/{scenario_id}")
    def scenario_detail(scenario_id: str):
        doc = index.load_doc(scenario_id)
        draft = ScenarioDraft(**doc["draft"])
        report = validate_draft(draft)
        issues = [i.to_dict() for i in report.issues]
        mat_path = settings.materialized_dir / f"{scenario_id}.json"
        detail = {"scenario_id": scenario_id,
                  "draft_input_hash": input_hash(draft),
                  "n_orders": len(draft.orders),
                  "n_vehicles": len(draft.vehicles),
                  "materialized": mat_path.is_file(),
                  "issues": issues}
        if mat_path.is_file():
            scn = Scenario(**json.loads(mat_path.read_text(encoding="utf-8")))
            detail.update({"version": scn.version,
                           "input_hash": input_hash(scn),
                           "n_plan_routes": len(scn.original_plan.routes)})
        return detail

    @router.get("/template")
    def template():
        """上传模板下载（含字段说明的最小可改草稿）。"""
        example = {
            "_comment": ("上传格式 = {\"_meta\": {...}, \"draft\": {...}}；"
                         "draft 须通过 schema 校验；缺边矩阵用 null；"
                         "时间单位秒，容量单位 load_units"),
            "_meta": {"base_instance": "upload-demo", "seed": 1,
                      "lock_ratio_target": 0.0, "note": "自建上传场景"},
            "draft": {
                "schema_version": "1.0", "scenario_id": "upload-demo-001",
                "version": 1,
                "time_origin": "2026-10-11T08:00:00+08:00",
                "time_unit": "s", "capacity_unit": "load_units",
                "depot_node_id": "depot",
                "orders": [
                    {"order_id": "o1", "node_id": "n1", "demand": 5.0,
                     "ready_s": 0, "due_s": 36000, "service_s": 600,
                     "is_new": False},
                    {"order_id": "o2", "node_id": "n2", "demand": 3.0,
                     "ready_s": 0, "due_s": 36000, "service_s": 600,
                     "is_new": True},
                ],
                "vehicles": [{"vehicle_id": "V1", "capacity": 10.0,
                              "available_from_s": 0, "return_by_s": 39600}],
                "travel_matrix": {
                    "node_ids": ["depot", "n1", "n2"],
                    "time_s": [[0, 300, 500], [300, 0, 400], [500, 400, 0]],
                },
                "locks": {"assignment_locks": {}, "relative_order_locks": []},
            },
        }
        return PlainTextResponse(
            json.dumps(example, ensure_ascii=False, indent=1),
            media_type="application/json; charset=utf-8",
            headers={"Content-Disposition":
                     'attachment; filename="scenario_template.json"'})

    @router.post("/scenarios/upload")
    async def upload(request: Request):
        try:
            doc = json.loads((await request.body()).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise HTTPException(422, f"E_UPLOAD_INVALID: 不是合法 JSON：{exc}")
        if not isinstance(doc, dict) or "draft" not in doc:
            raise HTTPException(
                422, "E_UPLOAD_INVALID: 需要 {\"_meta\": ..., \"draft\": ...} 结构")
        try:
            draft = ScenarioDraft(**doc["draft"])
        except ValidationError as exc:
            located = [{"loc": list(e.get("loc", ())),
                        "type": e.get("type"),
                        "msg": e.get("msg")} for e in exc.errors()]
            raise HTTPException(
                422, json.dumps({"status": "error",
                                 "warnings": ["E_UPLOAD_INVALID: 草稿未过 schema 校验"],
                                 "field_errors": located}, ensure_ascii=False))
        if len(draft.orders) > settings.max_orders \
                or len(draft.vehicles) > settings.max_vehicles \
                or len(draft.travel_matrix.node_ids) > settings.max_nodes:
            raise HTTPException(
                422, f"E_UPLOAD_TOO_BIG: 规模超限（orders≤{settings.max_orders}, "
                     f"vehicles≤{settings.max_vehicles}, nodes≤{settings.max_nodes}）")
        report = validate_draft(draft)
        if not report.ok:
            raise HTTPException(
                422, json.dumps(
                    {"status": "error",
                     "warnings": ["E_UPLOAD_INVALID: 交叉一致性校验未过"],
                     "issues": [i.to_dict() for i in report.issues]},
                    ensure_ascii=False))
        out = settings.uploads_dir / f"{draft.scenario_id}.json"
        if out.is_file():
            raise HTTPException(409, f"E_SCENARIO_EXISTS: {draft.scenario_id} 已存在")
        out.write_text(json.dumps(doc, ensure_ascii=False, indent=1),
                       encoding="utf-8")
        return {"status": "uploaded", "scenario_id": draft.scenario_id,
                "input_hash": input_hash(draft)}

    # ---------------- baseline ----------------

    @router.post("/baseline")
    def baseline(body: BaselineIn):
        index.load_doc(body.scenario_id)  # 404 早失败
        try:
            job = queue.submit("baseline", {
                "scenario_id": body.scenario_id,
                "budget_ms": min(body.budget_ms, settings.default_budget_ms)})
        except OverflowError as exc:
            raise HTTPException(429, str(exc))
        return {"job_id": job.job_id, "status": job.status}

    # ---------------- constraints draft / confirm ----------------

    def _current_scenario(scenario_id: str) -> Scenario:
        return index.load_scenario(scenario_id)

    @router.post("/constraints/draft")
    def draft_constraint(body: DraftIn):
        try:
            scn = _current_scenario(body.scenario_id)
        except HTTPException as exc:
            raise HTTPException(
                exc.status_code,
                f"{exc.detail}；请先运行 baseline 生成原计划") from exc
        try:
            d = llm_service.create_draft(scn, body.text)
        except ValueError as exc:
            raise HTTPException(422, str(exc))
        except LLMProviderError as exc:
            raise HTTPException(502, f"E_LLM_UNAVAILABLE: {exc}")
        return {"status": "draft", "demo_mode": d.demo_mode,
                "demo_notice": settings.llm.demo_mode or None,
                "draft": d.model_dump(mode="json")}

    @router.get("/constraints/{draft_id}")
    def get_constraint(draft_id: str):
        try:
            d = llm_service._store.get_draft(draft_id)
        except KeyError as exc:
            raise HTTPException(404, str(exc))
        return {"draft": d.model_dump(mode="json")}

    @router.post("/constraints/{draft_id}/confirm")
    def confirm_constraint(draft_id: str, body: ConfirmIn):
        scn = _current_scenario(body.scenario_id)
        if body.base_version != scn.version:
            raise HTTPException(
                409, f"E_VERSION_CONFLICT: 场景已是 v{scn.version}，"
                     f"请求基于 v{body.base_version}，请刷新后重试")
        decisions = None
        if body.accept_all:
            decisions = None  # 显式全部接受（服务端语义 = 全接受）
        elif body.decisions:
            try:
                decisions = {int(k): bool(v) for k, v in body.decisions.items()}
            except ValueError as exc:
                raise HTTPException(422, f"E_DECISION_INVALID: {exc}")
        else:
            raise HTTPException(
                422, "E_ACCEPT_REQUIRED: 必须显式 accept_all=true 或提供 decisions")
        try:
            out = llm_service.confirm(draft_id, scn, decisions)
        except KeyError as exc:
            raise HTTPException(404, str(exc))
        except ValueError as exc:
            msg = str(exc)
            code = 409 if "E_DRAFT_ALREADY_CONFIRMED" in msg \
                or "E_DRAFT_BASE_MISMATCH" in msg else 422
            raise HTTPException(code, msg)
        # 新版本落盘：versions/<sid>/v<n>.json，solve/history 可读
        vdir = settings.materialized_dir.parent / "versions" / scn.scenario_id
        vdir.mkdir(parents=True, exist_ok=True)
        (vdir / f"v{out.scenario.version}.json").write_text(
            json.dumps(out.scenario.model_dump(mode="json"),
                       ensure_ascii=False, indent=1), encoding="utf-8")
        return {"status": "confirmed",
                "demo_mode": out.bundle.demo_mode,
                "bundle": out.bundle.model_dump(mode="json"),
                "scenario_version": out.scenario.version,
                "scenario_input_hash": input_hash(out.scenario)}

    # ---------------- solve + jobs ----------------

    @router.post("/solve")
    def solve(body: SolveIn):
        if body.method not in METHODS:
            raise HTTPException(422, f"E_METHOD: 未知方法 {body.method}")
        if body.backend is not None and body.backend.lower() not in ("copt", "ortools"):
            raise HTTPException(422, f"E_BACKEND: 未知后端 {body.backend}")
        if body.method != "rebuild" and body.backend is not None:
            raise HTTPException(422, "E_BACKEND: backend 仅 rebuild 方法可指定")
        doc = index.load_doc(body.scenario_id)  # 404 早失败
        if body.base_version is not None:
            current = doc["draft"].get("version", 1)
            if body.base_version != current:
                raise HTTPException(
                    409, f"E_VERSION_CONFLICT: 草稿版本已变化（当前 v{current}）")
        try:
            job = queue.submit("solve", {
                "scenario_id": body.scenario_id, "method": body.method,
                "backend": (body.backend.lower() if body.backend else None),
                "budget_ms": min(body.budget_ms, settings.default_budget_ms)})
        except OverflowError as exc:
            raise HTTPException(429, str(exc))
        return {"job_id": job.job_id, "status": job.status}

    @router.get("/jobs")
    def jobs():
        return {"jobs": queue.list()}

    @router.get("/jobs/{job_id}")
    def job_detail(job_id: str):
        meta = queue.get(job_id)
        if meta is None:
            raise HTTPException(404, f"E_JOB_NOT_FOUND: {job_id}")
        return meta

    def _finished_result(job_id: str) -> dict:
        meta = queue.get(job_id)
        if meta is None:
            raise HTTPException(404, f"E_JOB_NOT_FOUND: {job_id}")
        if meta["status"] != "succeeded":
            raise HTTPException(409, f"E_JOB_NOT_DONE: 任务状态 {meta['status']}"
                                     f"{'，' + meta['error'] if meta.get('error') else ''}")
        return queue.result(job_id)

    @router.get("/jobs/{job_id}/result.json")
    def job_result(job_id: str):
        row = _finished_result(job_id)
        scn = _current_scenario(row["scenario_id"])
        return {"job_id": job_id,
                "version": row.get("scenario_version"),
                "input_hash": row.get("scenario_input_hash"),
                "method": row.get("method"), "backend": row.get("backend"),
                "backend_requested": row.get("backend_requested"),
                "budget_ms": row.get("budget_ms"),
                "has_solution": row.get("has_solution"),
                "original_plan": [
                    {"vehicle_id": r.vehicle_id, "order_ids": list(r.order_ids)}
                    for r in scn.original_plan.routes],
                "result": row.get("result")}

    @router.get("/jobs/{job_id}/export.csv")
    def job_export_csv(job_id: str):
        row = _finished_result(job_id)
        result = row["result"]
        validation = result.get("validation") or {}
        if not validation.get("independent_ok"):
            raise HTTPException(
                403, "E_EXPORT_FORBIDDEN: 方案未通过独立校验，禁止导出（红线）")
        scn = _current_scenario(row["scenario_id"])
        csv_text = "\ufeff" + build_result_csv(scn, result)
        return PlainTextResponse(
            csv_text, media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition":
                     f'attachment; filename="result_{job_id}.csv"'})

    @router.get("/scenarios/{scenario_id}/history")
    def history(scenario_id: str):
        index.load_doc(scenario_id)  # 404 早失败
        versions = []
        vdir = settings.materialized_dir.parent / "versions" / scenario_id
        if vdir.is_dir():
            for p in sorted(vdir.glob("v*.json")):
                try:
                    scn = Scenario(**json.loads(p.read_text(encoding="utf-8")))
                    versions.append({"version": scn.version,
                                     "input_hash": input_hash(scn),
                                     "locks": scn.locks.model_dump(mode="json")})
                except (OSError, json.JSONDecodeError, ValidationError):
                    continue
        results = []
        for meta in queue.list(limit=200):
            if meta.get("status") != "succeeded":
                continue
            r = queue.result(meta["job_id"]) or {}
            if r.get("scenario_id") != scenario_id or "result" not in r:
                continue  # baseline 等非求解结果不进历史对比表
            results.append({
                "job_id": meta["job_id"],
                "method": r.get("method"), "backend": r.get("backend"),
                "budget_ms": r.get("budget_ms"),
                "has_solution": r.get("has_solution"),
                "status": r["result"]["status"],
                "cost_s": r["result"].get("metrics", {}).get("total_travel_time_s"),
                "elapsed_ms": r["result"].get("elapsed_ms"),
                "finished_at": meta.get("finished_at")})
        return {"versions": versions, "results": results}

    app.include_router(router)
