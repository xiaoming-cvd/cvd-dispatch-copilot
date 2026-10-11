"""阶段 5 后端：FastAPI 端点 + 单工作进程任务队列 + 静态前端。

端点（任务书阶段 5）：scenarios / baseline / constraints draft+confirm /
solve+jobs / export。边界：
- 完整闭环、本地可操作（默认仅监听本机）；未过独立校验禁止导出；
- rebuild 后端显式（默认 OR-Tools，COPT 可选），每条结果记录 method/
  backend/预算/耗时/状态/是否找到可行解，禁止静默回退（Q2/Q3）；
- 确认语义继承阶段 4 v2：显式逐项决定或显式全部接受，无隐式全接受；
- 上传限量（大小/规模/排队），CSV 公式注入与前端 HTML 注入防护；
- 密钥只走后端环境变量，任何响应不回显。
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ValidationError

from ..domain.models import Scenario, ScenarioDraft, input_hash
from ..llm.base import LLMProviderError
from ..llm.config import DEMO_MODE_NOTICE, LLMConfig
from ..llm.service import DraftStore, LLMService
from ..solver import pipeline
from ..solver.original import materialize_scenario
from ..validation.checks import validate_draft

from .jobs import JobQueue

log = logging.getLogger("chengpei.api")


# ================= 设置 =================

@dataclass(frozen=True)
class Settings:
    project_root: Path
    llm: LLMConfig
    host: str = "127.0.0.1"  # 首版本机监听（任务书）
    port: int = 8000
    max_upload_bytes: int = 1_000_000
    max_orders: int = 300
    max_vehicles: int = 100
    max_nodes: int = 400
    max_queued_jobs: int = 8
    default_budget_ms: int = 60_000
    extra: dict = field(default_factory=dict)

    @property
    def registry_path(self) -> Path:
        return self.project_root / "data" / "processed" / "scenarios" / "registry.json"

    @property
    def uploads_dir(self) -> Path:
        return self.project_root / "data" / "processed" / "scenarios" / "uploads"

    @property
    def drafts_dir(self) -> Path:
        return self.project_root / "data" / "processed" / "drafts"

    @property
    def jobs_dir(self) -> Path:
        return self.project_root / "data" / "processed" / "jobs"

    @property
    def materialized_dir(self) -> Path:
        return self.project_root / "data" / "processed" / "scenarios" / "_materialized"


# ================= 场景索引 =================

class ScenarioIndex:
    """registry + uploads 的统一视图（draft / 已物化 / 校验问题定位）。"""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.settings.uploads_dir.mkdir(parents=True, exist_ok=True)
        self.settings.materialized_dir.mkdir(parents=True, exist_ok=True)

    def _registry(self) -> list[dict]:
        if not self.settings.registry_path.is_file():
            return []
        try:
            return json.loads(self.settings.registry_path.read_text(
                encoding="utf-8")).get("scenarios", [])
        except (OSError, json.JSONDecodeError):
            return []

    def list(self) -> list[dict]:
        out = []
        for e in self._registry():
            out.append({"scenario_id": e["scenario_id"], "split": e["split"],
                        "base_instance": e["base_instance"],
                        "source": e.get("config", {}).get("source", "solomon"),
                        "pending_original_plan": True, "uploaded": False})
        for p in sorted(self.settings.uploads_dir.glob("*.json")):
            try:
                doc = json.loads(p.read_text(encoding="utf-8"))
                meta = doc.get("_meta", {})
                sid = doc["draft"]["scenario_id"]
                out.append({"scenario_id": sid,
                            "split": meta.get("split", "upload"),
                            "base_instance": meta.get("base_instance", "upload"),
                            "source": "upload",
                            "pending_original_plan": True, "uploaded": True})
            except (OSError, json.JSONDecodeError, KeyError):
                continue
        for row in out:
            row["materialized"] = (self.settings.materialized_dir
                                   / f"{row['scenario_id']}.json").is_file()
        return out

    def doc_path(self, scenario_id: str) -> Path | None:
        for e in self._registry():
            if e["scenario_id"] == scenario_id:
                return self.settings.project_root / "data" / "processed" / e["file"]
        p = self.settings.uploads_dir / f"{scenario_id}.json"
        return p if p.is_file() else None

    def load_doc(self, scenario_id: str) -> dict:
        path = self.doc_path(scenario_id)
        if path is None:
            raise HTTPException(404, f"E_SCENARIO_NOT_FOUND: {scenario_id}")
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise HTTPException(404, f"E_SCENARIO_CORRUPT: {exc}") from exc

    def load_scenario(self, scenario_id: str) -> Scenario:
        """已物化场景（含 original_plan）。未物化则 409。"""
        p = self.settings.materialized_dir / f"{scenario_id}.json"
        if not p.is_file():
            raise HTTPException(
                409, f"E_NOT_MATERIALIZED: {scenario_id} 尚未生成原计划，"
                     "请先运行 baseline 任务")
        try:
            return Scenario(**json.loads(p.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError, ValidationError) as exc:
            raise HTTPException(404, f"E_SCENARIO_CORRUPT: {exc}") from exc

    def save_materialized(self, scn: Scenario) -> None:
        (self.settings.materialized_dir / f"{scn.scenario_id}.json").write_text(
            json.dumps(scn.model_dump(mode="json"), ensure_ascii=False, indent=1),
            encoding="utf-8")


# ================= CSV 导出（注入防护） =================

_CSV_DANGEROUS = ("=", "+", "-", "@", "\t", "\r")


def csv_cell(value: object) -> str:
    """CSV 公式注入防护：危险前缀加单引号；含引号/逗号/换行做标准转义。"""
    text = "" if value is None else str(value)
    if text.startswith(_CSV_DANGEROUS):
        text = "'" + text
    if any(c in text for c in ('"', ",", "\n")):
        text = '"' + text.replace('"', '""') + '"'
    return text


def build_result_csv(scn: Scenario, result: dict) -> str:
    lines = ["# 城配调度副驾 · 结果导出（非导航线路，仅调度示意）",
             f"# scenario={scn.scenario_id} version={scn.version} "
             f"input_hash={result['input_hash']}",
             f"# method={result['config'].get('method')} "
             f"backend={result['metrics'].get('backend') or '-'} "
             f"status={result['status']} elapsed_ms={result['elapsed_ms']}",
             "", "[assignment]"]
    lines.append("order_id,vehicle_id,seq,is_new")
    order_new = {o.order_id: o.is_new for o in scn.orders}
    for r in result.get("routes", []):
        for seq, oid in enumerate(r["order_ids"], 1):
            lines.append(",".join([csv_cell(oid), csv_cell(r["vehicle_id"]),
                                   str(seq), str(order_new.get(oid, False)).lower()]))
    lines += ["", "[timetable]",
              "vehicle_id,order_id,seq,arrive_s,wait_s,start_s,depart_s"]
    for s in result.get("schedule", []):
        lines.append(",".join(csv_cell(s[k]) for k in
                              ("vehicle_id", "order_id", "seq", "arrive_s",
                               "wait_s", "start_s", "depart_s")))
    return "\n".join(lines) + "\n"


# ================= 应用工厂 =================

def create_app(settings: Settings | None = None) -> FastAPI:
    root = Path(__file__).resolve().parents[2]
    settings = settings or Settings(project_root=root, llm=LLMConfig.from_env())
    if settings.llm.provider not in ("mock", "openai_compatible"):
        raise RuntimeError(
            f"E_LLM_CONFIG: LLM_PROVIDER 必须是 mock 或 openai_compatible，"
            f"当前为 {settings.llm.provider!r}")
    index = ScenarioIndex(settings)
    queue = JobQueue(settings.jobs_dir, max_queued=settings.max_queued_jobs)
    llm_service = LLMService(store=DraftStore(settings.drafts_dir),
                             config=settings.llm)

    app = FastAPI(title="城配调度副驾 API", version="5.0")
    app.state.settings = settings
    app.state.index = index
    app.state.queue = queue
    app.state.llm_service = llm_service

    # ---- 上传体积上限（Content-Length 前置拒绝） ----
    @app.middleware("http")
    async def _size_guard(request: Request, call_next):
        cl = request.headers.get("content-length")
        if cl and int(cl) > settings.max_upload_bytes:
            return JSONResponse(
                {"status": "error",
                 "warnings": [f"E_TOO_LARGE: 请求体超过上限 {settings.max_upload_bytes} 字节"]},
                status_code=413)
        return await call_next(request)

    # ---- 前端静态 ----
    frontend = root / "frontend"
    if frontend.is_dir():
        app.mount("/static", StaticFiles(directory=frontend), name="static")

        @app.get("/", include_in_schema=False)
        def _index():
            return FileResponse(frontend / "index.html")

    @app.on_event("startup")
    def _startup():
        log.info("LLM 配置（redacted）: %s", settings.llm.redacted())
        if settings.llm.demo_mode:
            log.info("[演示模式] %s", DEMO_MODE_NOTICE)
        queue.start()

    @app.on_event("shutdown")
    def _shutdown():
        queue.stop()

    from . import routes  # noqa: E402  （避免循环导入）
    routes.attach(app, settings, index, queue, llm_service)
    return app
