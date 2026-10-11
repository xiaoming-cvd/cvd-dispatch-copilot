"""第四阶段复审修复测试（复审《第四阶段复审与跨电脑AI改进建议》）。

执行顺序按复审要求：先写会在当前代码失败的最小反例，再修代码。
覆盖：
- P0-1 部分接受：显式 decisions 只接受所列条目，未列项拒绝；
- P0-2 原文依据：source_text 必须是原始需求子串，否则进 rejected；
- P0-3 无效确认：仅澄清/全拒绝/空草案不产生新版本；
- P1-1 确认原子性：二次写入失败不留半确认态；并发/重复确认只生效一次；
- P1-2 CLI 参数校验：非整数/越界/重复/互斥/裸确认；
- P1-3 25 条语料逐条校订接入（audit.status ∈ supported/clarify/deferred）；
- P2 草案 ID 含基线哈希且拒绝同 ID 异内容；get_draft 路径防护；HTTP 错误脱敏。
"""
from __future__ import annotations

import json
import re
import sys
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.domain.models import (
    Locks, Order, OriginalPlan, RoutePlan, Scenario, TravelMatrix, Vehicle,
)
from src.llm.base import LLMProviderError, ProviderReply
from src.llm.config import LLMConfig
from src.llm.extractor import (
    ProviderPayload, _PayloadItem, _PayloadNote, _PayloadPreference, map_payload,
)
from src.llm.openai_provider import OpenAICompatibleProvider
from src.llm.schema import DraftStatus, ItemKind
from src.llm.service import DraftStore, LLMService

NOW = datetime(2026, 10, 11, 12, 0, 0, tzinfo=timezone.utc)
TZ8 = timezone(timedelta(hours=8))

CORPUS_PATH = Path(__file__).resolve().parent.parent / "data" / "sample" / "nl_requirements_tests.json"


def make_scenario(locks: Locks | None = None) -> Scenario:
    orders = (
        Order(order_id="o1", node_id="A", demand=4.0, ready_s=0, due_s=10000, service_s=120),
        Order(order_id="o2", node_id="B", demand=3.0, ready_s=0, due_s=10000, service_s=120),
        Order(order_id="o3", node_id="C", demand=2.0, ready_s=0, due_s=10000, service_s=120),
        Order(order_id="o4", node_id="E", demand=1.0, ready_s=0, due_s=10000, service_s=120),
        Order(order_id="o5", node_id="B", demand=2.0, ready_s=2000, due_s=9000,
              service_s=120, is_new=True),
    )
    vehicles = (
        Vehicle(vehicle_id="V1", capacity=10.0, available_from_s=0, return_by_s=20000),
        Vehicle(vehicle_id="V2", capacity=10.0, available_from_s=0, return_by_s=20000),
    )
    matrix = TravelMatrix(
        node_ids=("depot", "A", "B", "C", "E"),
        time_s=((0, 300, 600, 500, 700), (300, 0, 200, 350, 450),
                (610, 210, 0, 250, 300), (500, 350, 260, 0, 200),
                (700, 450, 300, 210, 0)))
    plan = OriginalPlan(plan_id="plan-demo-1", version=1, routes=(
        RoutePlan(vehicle_id="V1", order_ids=("o1", "o2")),
        RoutePlan(vehicle_id="V2", order_ids=("o3", "o4")),
    ))
    return Scenario(
        scenario_id="sample-small-001", version=1,
        time_origin=datetime(2026, 10, 10, 8, 0, 0, tzinfo=TZ8),
        capacity_unit="load_units", depot_node_id="depot",
        orders=orders, vehicles=vehicles, travel_matrix=matrix,
        original_plan=plan, locks=locks or Locks())


def make_service(store: DraftStore | None = None) -> LLMService:
    cfg = LLMConfig(provider="mock", demo_mode=True)
    return LLMService(store=store or DraftStore(None), config=cfg)


class _Harness:
    def __init__(self, store: DraftStore | None = None):
        self.svc = make_service(store)

    def draft(self, text: str, scenario: Scenario | None = None):
        scn = scenario or make_scenario()
        return scn, self.svc.create_draft(scn, text, now=NOW)

    def confirm(self, draft_id: str, scenario: Scenario, decisions: dict | None = None):
        return self.svc.confirm(draft_id, scenario, decisions, now=NOW)


@pytest.fixture(autouse=True)
def _isolate_llm_env(monkeypatch):
    for key in ("LLM_PROVIDER", "LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "mock")  # 兜底：本机 .env 有真实密钥，测试禁止走真实 Provider


# ================= P0-1 部分接受语义 =================

class TestPartialAccept:
    def test_only_listed_items_accepted(self):
        """复审反例：decisions={0:True} 时 o2→V2 不得进入新场景。"""
        h = _Harness()
        scn, d = h.draft("o1 必须 V1 送；o2 必须 V2 送")
        assert len(d.items) == 2
        out = h.confirm(d.draft_id, scn, {0: True})
        assert dict(out.scenario.locks.assignment_locks) == {"o1": "V1"}
        assert [i.order_ids for i in out.bundle.accepted_items] == [("o1",)]

    def test_unlisted_preference_rejected(self):
        h = _Harness()
        scn, d = h.draft("o1 必须 V1 送，尽量别换车")
        out = h.confirm(d.draft_id, scn, {0: True})
        assert not out.bundle.accepted_preferences

    def test_explicit_all_accept_still_works(self):
        h = _Harness()
        scn, d = h.draft("o1 必须 V1 送；o2 必须 V2 送")
        total = len(d.items) + len(d.preferences)
        out = h.confirm(d.draft_id, scn, {i: True for i in range(total)})
        assert len(out.bundle.accepted_items) == 2

    def test_decisions_none_means_accept_all(self):
        """服务端缺省语义保持：未提供 decisions = 全部接受（CLI 侧另行显式化）。"""
        h = _Harness()
        scn, d = h.draft("o1 必须 V1 送；o2 必须 V2 送")
        out = h.confirm(d.draft_id, scn, None)
        assert len(out.bundle.accepted_items) == 2

    @pytest.mark.parametrize("bad", [{5: True}, {-1: True}, {0: True, "1": True},
                                     {True: True}, {0.5: True}])
    def test_invalid_decisions_rejected(self, bad):
        h = _Harness()
        scn, d = h.draft("o1 必须 V1 送；o2 必须 V2 送")
        with pytest.raises(ValueError, match="E_DECISION_INVALID"):
            h.confirm(d.draft_id, scn, bad)


# ================= P0-2 原文依据校验 =================

class TestSourceTextVerification:
    def test_fake_source_item_rejected(self):
        payload = ProviderPayload(items=(
            _PayloadItem(kind=ItemKind.ASSIGN_LOCK, order_ids=("o1",),
                         vehicle_id="V1", source_text="用户从未说过这句话"),))
        items, _, _, _, rejected, _ = map_payload(
            make_scenario(), payload, "o1 归 V1")
        assert not items and len(rejected) == 1
        assert "原文" in rejected[0].reason

    def test_fake_source_preference_rejected(self):
        payload = ProviderPayload(preferences=(
            _PayloadPreference(kind="avoid_vehicle_change", order_ids=(),
                               source_text="用户从未说过这句话"),))
        _, prefs, _, _, rejected, _ = map_payload(
            make_scenario(), payload, "尽量别换车")
        assert not prefs and rejected and rejected[0].kind == "preference"

    def test_fake_source_missing_questions_dropped(self):
        payload = ProviderPayload(
            missing=(_PayloadNote(text="缺字段", source_text="用户从未说过这句话"),),
            questions=(_PayloadNote(text="澄清", source_text="用户从未说过这句话"),))
        _, _, missing, questions, rejected, warnings = map_payload(
            make_scenario(), payload, "那批货还是老规矩")
        assert not missing and not questions
        assert {r.kind for r in rejected} == {"missing", "question"}
        assert any("原文" in w for w in warnings)

    def test_genuine_sources_pass(self):
        payload = ProviderPayload(
            items=(_PayloadItem(kind=ItemKind.ASSIGN_LOCK, order_ids=("o1",),
                                vehicle_id="V1", source_text="o1 归 V1"),),
            preferences=(_PayloadPreference(kind="avoid_vehicle_change", order_ids=(),
                                            source_text="尽量别换车"),))
        items, prefs, _, _, rejected, _ = map_payload(
            make_scenario(), payload, "o1 归 V1，尽量别换车")
        assert len(items) == 1 and len(prefs) == 1 and not rejected

    def test_service_end_to_end_fake_evidence(self, monkeypatch):
        """真实模型若编造依据：草案不得包含该硬约束。"""
        payload_text = json.dumps({
            "items": [{"kind": "assign_lock", "order_ids": ["o1"],
                       "vehicle_id": "V1",
                       "source_text": "用户从未说过这句话"}]}, ensure_ascii=False)

        class _FakeProvider:
            provider_name = "fake"

            def complete(self, system_prompt, user_prompt):
                return ProviderReply(text=payload_text, provider="fake",
                                     model="m", demo_mode=False)

        import src.llm.service as service_mod
        monkeypatch.setattr(service_mod, "build_provider",
                            lambda cfg: (_FakeProvider(), cfg))
        svc = LLMService(config=LLMConfig(provider="fake", demo_mode=False))
        scn = make_scenario()
        d = svc.create_draft(scn, "o1 归 V1", now=NOW)
        assert not d.items and len(d.rejected) == 1 and "原文" in d.rejected[0].reason


# ================= P0-3 无效确认不产生新版本 =================

class TestNoEffectConfirm:
    def test_questions_only_cannot_confirm(self):
        h = _Harness()
        scn, d = h.draft("那批货还是老规矩")
        assert d.questions and not d.items
        with pytest.raises(ValueError, match="E_CONFIRM_NEEDS_CLARIFICATION"):
            h.confirm(d.draft_id, scn)
        assert scn.version == 1
        assert h.svc._store.get_draft(d.draft_id).status is DraftStatus.PENDING

    def test_all_rejected_no_version_bump(self):
        h = _Harness()
        scn, d = h.draft("o1 必须 V1 送")
        with pytest.raises(ValueError, match="E_CONFIRM_NO_EFFECT"):
            h.confirm(d.draft_id, scn, {0: False})
        assert scn.version == 1
        assert h.svc._store.get_draft(d.draft_id).status is DraftStatus.PENDING

    def test_empty_draft_cannot_confirm(self, monkeypatch):
        """模型输出越约产生空草案：不得确认升级。"""
        class _EmptyProvider:
            provider_name = "fake"

            def complete(self, system_prompt, user_prompt):
                return ProviderReply(text='{"items": []}', provider="fake",
                                     model="m", demo_mode=False)

        import src.llm.service as service_mod
        monkeypatch.setattr(service_mod, "build_provider",
                            lambda cfg: (_EmptyProvider(), cfg))
        svc = LLMService(config=LLMConfig(provider="fake", demo_mode=False))
        scn = make_scenario()
        d = svc.create_draft(scn, "o1 归 V1", now=NOW)
        assert not d.items and not d.questions and not d.missing
        with pytest.raises(ValueError, match="E_CONFIRM_NO_EFFECT"):
            svc.confirm(d.draft_id, scn, now=NOW)
        assert scn.version == 1

    def test_no_confirmation_records_left(self, tmp_path: Path):
        h = _Harness(DraftStore(tmp_path))
        scn, d = h.draft("那批货还是老规矩")
        with pytest.raises(ValueError, match="E_CONFIRM_NEEDS_CLARIFICATION"):
            h.confirm(d.draft_id, scn)
        assert list((tmp_path / "confirmed").glob("*.json")) == []


# ================= P1-1 确认原子性 =================

class TestAtomicConfirm:
    def test_record_is_single_file(self, tmp_path: Path):
        h = _Harness(DraftStore(tmp_path))
        scn, d = h.draft("o1 必须 V1 送")
        out = h.confirm(d.draft_id, scn)
        records = list((tmp_path / "confirmed").glob("*.json"))
        assert len(records) == 1
        record = json.loads(records[0].read_text(encoding="utf-8"))
        assert record["bundle"]["bundle_id"] == out.bundle.bundle_id
        assert record["scenario"]["version"] == 2
        assert h.svc._store.get_draft(d.draft_id).status is DraftStatus.CONFIRMED

    def test_write_failure_leaves_no_half_confirmed_state(self, tmp_path: Path,
                                                          monkeypatch):
        h = _Harness(DraftStore(tmp_path))
        scn, d = h.draft("o1 必须 V1 送")
        real_write = Path.write_text

        def flaky(self, *a, **k):
            # 让确认记录（confirmed/ 下，含其临时文件）的写入失败
            if "confirmed" in str(self):
                raise OSError("E_DISK_FULL_SIMULATED")
            return real_write(self, *a, **k)

        monkeypatch.setattr(Path, "write_text", flaky)
        with pytest.raises(OSError, match="E_DISK_FULL_SIMULATED"):
            h.confirm(d.draft_id, scn)
        monkeypatch.undo()
        # 无半确认态：没有确认记录、草案仍 pending、可重试成功且只产生一份记录
        assert list((tmp_path / "confirmed").glob("*.json")) == []
        assert h.svc._store.get_draft(d.draft_id).status is DraftStatus.PENDING
        out = h.confirm(d.draft_id, scn)
        assert out.scenario.version == 2
        assert len(list((tmp_path / "confirmed").glob("*.json"))) == 1

    def test_two_services_one_winner(self, tmp_path: Path):
        store = DraftStore(tmp_path)
        h1, h2 = _Harness(store), _Harness(store)
        scn, d = h1.draft("o1 必须 V1 送")
        h1.confirm(d.draft_id, scn)
        with pytest.raises(ValueError, match="E_DRAFT_ALREADY_CONFIRMED"):
            h2.confirm(d.draft_id, scn)
        assert len(list((tmp_path / "confirmed").glob("*.json"))) == 1

    def test_stale_lock_rejected(self, tmp_path: Path):
        store = DraftStore(tmp_path)
        h = _Harness(store)
        scn, d = h.draft("o1 必须 V1 送")
        (tmp_path / "drafts" / f"{d.draft_id}.lock").write_text("", encoding="utf-8")
        with pytest.raises(ValueError, match="E_DRAFT_ALREADY_CONFIRMED"):
            h.confirm(d.draft_id, scn)
        assert len(list((tmp_path / "confirmed").glob("*.json"))) == 0


# ================= P1-2 CLI 参数校验 =================

class TestCliValidation:
    def _setup(self, tmp_path: Path):
        sys_path_write = tmp_path / "scn.json"
        scn = make_scenario()
        sys_path_write.write_text(
            json.dumps({"scenario": scn.model_dump(mode="json")}, ensure_ascii=False),
            encoding="utf-8")
        store = DraftStore(tmp_path / "store")
        svc = make_service(store)
        d = svc.create_draft(scn, "o1 必须 V1 送；o2 必须 V2 送", now=NOW)
        return sys_path_write, store, d

    def _run(self, argv):
        import scripts.draft_cli as cli
        return cli.main(argv)

    def test_accept_non_int_clean_error(self, tmp_path: Path, capsys):
        scn_file, _, d = self._setup(tmp_path)
        code = self._run(["--input", str(scn_file), "--confirm", d.draft_id,
                          "--accept", "a", "--store", str(tmp_path / "store")])
        assert code == 2
        out = capsys.readouterr().out
        assert "E_ACCEPT_INVALID" in out and "Traceback" not in out

    def test_accept_out_of_range(self, tmp_path: Path, capsys):
        scn_file, _, d = self._setup(tmp_path)
        code = self._run(["--input", str(scn_file), "--confirm", d.draft_id,
                          "--accept", "0,999", "--store", str(tmp_path / "store")])
        assert code == 2
        assert "E_ACCEPT_INVALID" in capsys.readouterr().out

    def test_accept_duplicates(self, tmp_path: Path, capsys):
        scn_file, _, d = self._setup(tmp_path)
        code = self._run(["--input", str(scn_file), "--confirm", d.draft_id,
                          "--accept", "0,0", "--store", str(tmp_path / "store")])
        assert code == 2
        assert "E_ACCEPT_INVALID" in capsys.readouterr().out

    def test_yes_and_accept_mutex(self, tmp_path: Path):
        scn_file, _, d = self._setup(tmp_path)
        with pytest.raises(SystemExit):
            self._run(["--input", str(scn_file), "--confirm", d.draft_id,
                       "--yes", "--accept", "0", "--store", str(tmp_path / "store")])

    def test_bare_confirm_requires_choice(self, tmp_path: Path, capsys):
        scn_file, _, d = self._setup(tmp_path)
        code = self._run(["--input", str(scn_file), "--confirm", d.draft_id,
                          "--store", str(tmp_path / "store")])
        assert code == 2
        assert "E_ACCEPT_REQUIRED" in capsys.readouterr().out

    def test_cli_partial_accept_only_listed(self, tmp_path: Path, capsys):
        scn_file, _, d = self._setup(tmp_path)
        code = self._run(["--input", str(scn_file), "--confirm", d.draft_id,
                          "--accept", "0", "--store", str(tmp_path / "store")])
        assert code == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["status"] == "confirmed"
        assert payload["scenario"]["locks"]["assignment_locks"] == {"o1": "V1"}

    def test_cli_yes_accepts_all(self, tmp_path: Path, capsys):
        scn_file, _, d = self._setup(tmp_path)
        code = self._run(["--input", str(scn_file), "--confirm", d.draft_id,
                          "--yes", "--store", str(tmp_path / "store")])
        assert code == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["scenario"]["locks"]["assignment_locks"] == {"o1": "V1",
                                                                    "o2": "V2"}


# ================= P1-3 语料接入（校订后） =================

def make_corpus_scenario() -> Scenario:
    """语料专用场景：o1–o30，新任务 = {o9, o21, o22, o30}，V1–V3。"""
    new_ids = {"o9", "o21", "o22", "o30"}
    orders = tuple(
        Order(order_id=f"o{i}", node_id=f"n{i}", demand=2.0, ready_s=0,
              due_s=20000, service_s=60, is_new=(f"o{i}" in new_ids))
        for i in range(1, 31))
    vehicles = tuple(
        Vehicle(vehicle_id=f"V{i}", capacity=50.0, available_from_s=0,
                return_by_s=40000)
        for i in range(1, 4))
    ids = ["depot"] + [f"n{i}" for i in range(1, 31)]
    rows = []
    for i in range(31):
        row = []
        for j in range(31):
            row.append(0 if i == j else (i * 7 + j * 13) % 400 + 50)
        rows.append(tuple(row))
    plan = OriginalPlan(plan_id="plan-corpus-1", version=1, routes=(
        RoutePlan(vehicle_id="V1", order_ids=tuple(f"o{i}" for i in range(1, 9))),
        RoutePlan(vehicle_id="V2", order_ids=tuple(f"o{i}" for i in range(10, 21))),
        RoutePlan(vehicle_id="V3", order_ids=tuple(f"o{i}" for i in range(23, 30))
                  ),
    ))
    return Scenario(
        scenario_id="corpus-demo-001", version=1,
        time_origin=datetime(2026, 10, 10, 8, 0, 0, tzinfo=TZ8),
        capacity_unit="load_units", depot_node_id="depot",
        orders=orders, vehicles=vehicles,
        travel_matrix=TravelMatrix(node_ids=tuple(ids), time_s=tuple(rows)),
        original_plan=plan, locks=Locks())


def _corpus():
    data = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))
    return data, data["cases"]


class TestCorpusAudit:
    def test_annotation_complete(self):
        _, cases = _corpus()
        assert len(cases) == 25
        for c in cases:
            assert c.get("audit", {}).get("status") in {
                "supported", "clarify", "deferred"}, c["id"]
            assert c["audit"].get("note"), c["id"]

    def _check_universal(self, draft, case):
        blob_items = draft.items + draft.preferences
        for it in blob_items:
            assert it.source_text in draft.raw_text, case["id"]
        for m in draft.missing:
            assert m.source_text in draft.raw_text, case["id"]
        for q in draft.questions:
            assert q.source_text in draft.raw_text, case["id"]

    @pytest.mark.parametrize("case_id", [f"NL-{i:02d}" for i in range(1, 26)])
    def test_corpus_case_behavior(self, case_id):
        data, cases = _corpus()
        case = next(c for c in cases if c["id"] == case_id)
        status = case["audit"]["status"]
        svc = make_service()
        scn = make_corpus_scenario()
        d = svc.create_draft(scn, case["text"], now=NOW)
        self._check_universal(d, case)

        if status == "deferred":
            # 未支持的能力：绝不允许产出硬约束条目（以问题/缺字段/拒绝收口）
            assert not d.items, (
                f"{case['id']} deferred 但产出硬约束："
                f"{[(i.kind, i.order_ids) for i in d.items]}")
        elif status == "clarify":
            assert d.questions or d.missing, f"{case['id']} 应澄清"
            assert not d.items
        else:  # supported
            exp = case.get("expected", {})
            for hc in exp.get("hard_constraints", []):
                if hc["type"] == "assignment_lock":
                    assert any(i.kind is ItemKind.ASSIGN_LOCK
                               and i.order_ids == (hc["order_id"],)
                               and i.vehicle_id == hc["vehicle_id"]
                               for i in d.items), case["id"]
                elif hc["type"] == "relative_order_lock":
                    assert any(i.kind is ItemKind.REL_ORDER_LOCK
                               and set(i.order_ids) == set(hc["order_ids"])
                               for i in d.items), case["id"]
            if case.get("expect") == "reject":
                assert not d.items, case["id"]
            # 支持类用例允许问题共存（如 NL-17 第二分句），但不得静默


# ================= P2 =================

class TestP2:
    def test_draft_id_includes_base_hash(self):
        svc = make_service()
        scn1 = make_scenario()
        d1 = svc.create_draft(scn1, "o1 必须 V1 送", now=NOW)
        tampered = scn1.model_copy(update={"orders": tuple(
            o.model_copy(update={"demand": 99.0}) if o.order_id == "o1" else o
            for o in scn1.orders)})
        d2 = svc.create_draft(tampered, "o1 必须 V1 送", now=NOW)
        assert d1.draft_id != d2.draft_id

    def test_same_id_different_content_refused(self, tmp_path: Path):
        store = DraftStore(tmp_path)
        svc = make_service(store)
        scn = make_scenario()
        d = svc.create_draft(scn, "o1 必须 V1 送", now=NOW)
        tampered = d.model_copy(update={"raw_text": "完全不同的一句需求原文"})
        with pytest.raises(ValueError, match="E_DRAFT_ID_COLLISION"):
            store.save_draft(tampered)

    @pytest.mark.parametrize("bad_id", ["../escape", "..\\escape", "a/b",
                                        "draft-../../x", "x" * 100])
    def test_get_draft_path_guard(self, bad_id):
        store = DraftStore(Path(__file__).parent.parent / "data" / "processed" / "drafts")
        with pytest.raises(ValueError, match="E_DRAFT_ID_INVALID"):
            store.get_draft(bad_id)

    def test_http_error_detail_sanitized(self):
        key = "sk-live-key-abcdef1234567890"
        cfg = LLMConfig(provider="openai_compatible", api_key=key,
                        base_url="https://example.test/v1", model="m1",
                        demo_mode=False)

        class _Boom:
            def read(self):
                return json.dumps({"error": {
                    "message": f"invalid key {key} please check"}}).encode("utf-8")

            def close(self):
                pass

        def failing_opener(req, timeout=None):
            raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized",
                                         {}, _Boom())

        provider = OpenAICompatibleProvider(cfg, opener=failing_opener)
        with pytest.raises(LLMProviderError) as exc_info:
            provider.complete("sys", "user")
        msg = str(exc_info.value)
        assert "401" in msg and key not in msg
