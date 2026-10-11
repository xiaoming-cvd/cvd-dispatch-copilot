"""阶段 5 任务队列：限量单工作进程、持久化、重启标记 interrupted。

红线（任务书阶段 5）：
- 单工作进程、限并发（=1）、限排队长度；超时由求解预算控制，任务状态如实；
- 任务落盘（jobs/<id>.json + <id>.result.json）：页面刷新可找回已完成结果；
- 重启后 stale 的 queued/running 一律标记 interrupted（不假装还在跑）；
- 版本冲突由路由层返回 409，本模块不管业务。
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

Handler = Callable[[dict], dict]


def _now() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


@dataclass
class Job:
    job_id: str
    kind: str
    payload: dict
    status: str = "queued"  # queued/running/succeeded/failed/interrupted
    created_at: str = field(default_factory=_now)
    started_at: str | None = None
    finished_at: str | None = None
    error: str | None = None
    result_path: str | None = None

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if k != "payload_handler"}


class JobQueue:
    """单工作进程队列。handler 注册于 create_app，Job 元数据持久化。"""

    def __init__(self, jobs_dir: Path, max_queued: int = 8):
        self.jobs_dir = jobs_dir
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        self.max_queued = max_queued
        self._pending: deque[str] = deque()
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._stop = False
        self._handlers: dict[str, Handler] = {}
        self._mark_stale_interrupted()

    # ---- 生命周期 ----

    def register(self, kind: str, handler: Handler) -> None:
        self._handlers[kind] = handler

    def start(self) -> None:
        # 防重入：旧 worker 未退出时禁止启动第二个线程（阶段5审查 P1-4）
        if self._thread is not None and self._thread.is_alive():
            raise RuntimeError(
                "E_QUEUE_RUNNING: 队列工作线程仍在运行，禁止重复启动")
        self._stop = False
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="api-job-worker")
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> bool:
        """优雅停止。返回 True=已退出；False=超时未退出（此时保留 _thread
        引用，start() 会拒绝重入，避免两线程并行执行同一队列）。"""
        self._stop = True
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            if self._thread.is_alive():
                return False
            self._thread = None
        return True

    def _mark_stale_interrupted(self) -> None:
        for p in self.jobs_dir.glob("*.json"):
            if p.name.endswith(".result.json"):
                continue
            try:
                meta = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if meta.get("status") in ("queued", "running"):
                meta["status"] = "interrupted"
                meta["error"] = "服务重启：任务未完成，标记为 interrupted"
                meta["finished_at"] = _now()
                p.write_text(json.dumps(meta, ensure_ascii=False, indent=1),
                             encoding="utf-8")

    # ---- 提交与查询 ----

    def submit(self, kind: str, payload: dict) -> Job:
        with self._lock:
            queued = sum(1 for j in self._all_jobs()
                         if j.get("status") == "queued")
            if queued >= self.max_queued:
                raise OverflowError(
                    f"E_QUEUE_FULL: 排队任务已满（上限 {self.max_queued}），请稍后重试")
            job = Job(job_id=f"job-{int(time.time())}-{uuid.uuid4().hex[:6]}",
                      kind=kind, payload=payload)
            self._save_meta(job)
            self._pending.append(job.job_id)
        self._wake.set()
        return job

    def get(self, job_id: str) -> dict | None:
        meta = self._read_meta(job_id)
        return meta

    def list(self, limit: int = 50) -> list[dict]:
        metas = [m for m in (self._read_meta(p.stem) for p in
                             sorted(self.jobs_dir.glob("job-*.json"),
                                    key=lambda p: p.stat().st_mtime,
                                    reverse=True)) if m]
        return metas[:limit]

    def result(self, job_id: str) -> dict | None:
        p = self.jobs_dir / f"{job_id}.result.json"
        if not p.is_file():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def wait_idle(self, timeout: float = 60.0) -> bool:
        """测试辅助：等待队列清空（全部进入终态）。"""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                active = any(j.get("status") in ("queued", "running")
                             for j in self._all_jobs())
                if not self._pending and not active:
                    return True
            time.sleep(0.05)
        return False

    # ---- 内部 ----

    def _loop(self) -> None:
        while not self._stop:
            self._wake.wait(timeout=0.5)
            self._wake.clear()
            while True:
                with self._lock:
                    if not self._pending or self._stop:
                        break
                    job_id = self._pending.popleft()
                self._run(job_id)

    def _run(self, job_id: str) -> None:
        meta = self._read_meta(job_id)
        if meta is None or meta["status"] != "queued":
            return
        meta["status"] = "running"
        meta["started_at"] = _now()
        self._write_meta(meta)
        handler = self._handlers.get(meta["kind"])
        try:
            if handler is None:
                raise ValueError(f"E_JOB_KIND: 未知任务类型 {meta['kind']}")
            payload = json.loads((self.jobs_dir / f"{job_id}.payload.json")
                                 .read_text(encoding="utf-8"))
            result = handler(payload)
            result_path = f"{job_id}.result.json"
            (self.jobs_dir / result_path).write_text(
                json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
            meta["status"] = "succeeded"
            meta["result_path"] = result_path
        except Exception as exc:  # 任务失败如实记录，不影响队列
            meta["status"] = "failed"
            meta["error"] = f"{type(exc).__name__}: {exc}"[:500]
        meta["finished_at"] = _now()
        self._write_meta(meta)

    def _save_meta(self, job: Job) -> None:
        meta = job.to_dict()
        self._write_meta(meta)
        (self.jobs_dir / f"{job.job_id}.payload.json").write_text(
            json.dumps(job.payload, ensure_ascii=False), encoding="utf-8")

    def _read_meta(self, job_id: str) -> dict | None:
        p = self.jobs_dir / f"{job_id}.json"
        if not p.is_file():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def _write_meta(self, meta: dict) -> None:
        tmp = self.jobs_dir / f"{meta['job_id']}.json.tmp-{os.getpid()}"
        tmp.write_text(json.dumps(meta, ensure_ascii=False, indent=1),
                       encoding="utf-8")
        # Windows 下杀软/索引器可能短暂占用目标文件导致 replace 拒绝访问：
        # 短暂重试而非直接失败（worker 循环崩掉会让队列卡死）
        for attempt in range(5):
            try:
                os.replace(tmp, self.jobs_dir / f"{meta['job_id']}.json")
                return
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.1 * (attempt + 1))

    def _all_jobs(self) -> list[dict]:
        out = []
        for p in self.jobs_dir.glob("job-*.json"):
            if p.name.endswith((".result.json", ".payload.json")):
                continue
            m = self._read_meta(p.stem)
            if m:
                out.append(m)
        return out
