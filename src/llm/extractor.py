"""模型输出收口：文本 → ProviderPayload → 场景核对后的草案组成件。

这是注入防护的核心边界（任务书阶段 4：注入无副作用）：
- Provider（mock 或真实模型）只准返回 JSON 文本，且必须符合
  ProviderPayload 契约（extra=forbid，未知字段直接拒绝）；
- 结构化之后做场景成员核对：订单/车辆必须存在于当前场景，锁只能
  作用于旧任务（新任务锁在 checks 层本来就会拒绝，这里提前给原因）；
- 任何不合法的候选项不静默丢弃，而是进 rejected 并附原因；
- 本模块不执行任何代码、不发起网络请求，数值/枚举全部收口在
  schema 定义内（真实模型也无法越出这个面）。
"""
from __future__ import annotations

import json

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..domain.models import Scenario
from .schema import (
    ClarificationQuestion,
    HardConstraintItem,
    ItemKind,
    MissingItem,
    PreferenceItem,
    PreferenceKind,
    RejectedItem,
)

_MAX_ENTRIES = 100  # 单草案各类条目上限（资源防护）


class LLMOutputError(ValueError):
    """Provider 输出无法解析或越出载荷契约。"""


class _PayloadPreference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: PreferenceKind
    order_ids: tuple[str, ...] = ()
    source_text: str = Field(min_length=1)
    note: str = ""


class _PayloadItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: ItemKind
    order_ids: tuple[str, ...] = Field(min_length=1)
    vehicle_id: str | None = None
    source_text: str = Field(min_length=1)


class _PayloadNote(BaseModel):
    """missing/questions 的结构化条目：说明/问题 + 原文依据。"""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1)
    source_text: str = Field(min_length=1)


class ProviderPayload(BaseModel):
    """Provider 应答载荷契约（mock 与真实模型共用同一形态）。"""

    model_config = ConfigDict(extra="forbid")

    items: tuple[_PayloadItem, ...] = Field(default_factory=tuple, max_length=_MAX_ENTRIES)
    preferences: tuple[_PayloadPreference, ...] = Field(default_factory=tuple, max_length=_MAX_ENTRIES)
    questions: tuple[_PayloadNote, ...] = Field(default_factory=tuple, max_length=_MAX_ENTRIES)
    missing: tuple[_PayloadNote, ...] = Field(default_factory=tuple, max_length=_MAX_ENTRIES)


def payload_from_text(text: str) -> ProviderPayload:
    """解析 Provider 输出的 JSON 文本；失败抛 LLMOutputError（不静默）。"""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise LLMOutputError(f"E_LLM_OUTPUT: 模型输出不是合法 JSON（{exc.msg}）") from exc
    if not isinstance(data, dict):
        raise LLMOutputError("E_LLM_OUTPUT: 模型输出必须是 JSON 对象")
    try:
        return ProviderPayload(**data)
    except ValidationError as exc:
        raise LLMOutputError(f"E_LLM_OUTPUT: 模型输出越出载荷契约（{exc.errors()[0]['type']}: "
                             f"{exc.errors()[0].get('loc', ())}）") from exc


def map_payload(
    scenario: Scenario, payload: ProviderPayload, raw_text: str,
) -> tuple[
    tuple[HardConstraintItem, ...],
    tuple[PreferenceItem, ...],
    tuple[MissingItem, ...],
    tuple[ClarificationQuestion, ...],
    tuple[RejectedItem, ...],
    tuple[str, ...],
]:
    """把载荷落到当前场景：成员核对、旧任务核对、原文依据核对、去重。

    raw_text 为调度员原始需求。所有结构化条目的 source_text 必须是
    raw_text 的子串（原文依据）；不匹配的条目进 rejected 并附原因，
    绝不后台伪造依据（第四阶段复审 P0-2）。

    返回 (items, preferences, missing, questions, rejected, warnings)。
    """
    order_by_id = {o.order_id: o for o in scenario.orders}
    vehicle_ids = {v.vehicle_id for v in scenario.vehicles}
    items: list[HardConstraintItem] = []
    rejected: list[RejectedItem] = []
    seen: set[tuple] = set()
    warnings: list[str] = []
    dropped_notes = 0

    for cand in payload.items:
        key = (cand.kind.value, cand.order_ids, cand.vehicle_id)
        if key in seen:
            continue
        seen.add(key)
        reasons: list[str] = []
        if cand.source_text not in raw_text:
            reasons.append("原文依据在原始需求中不存在（拒绝伪造依据）")
        if cand.kind is ItemKind.ASSIGN_LOCK and len(cand.order_ids) != 1:
            reasons.append("换车锁必须恰好 1 个订单")
        if cand.kind is ItemKind.REL_ORDER_LOCK and len(set(cand.order_ids)) < 2:
            reasons.append("相对顺序锁至少需要 2 个不同订单")
        if len(set(cand.order_ids)) != len(cand.order_ids):
            reasons.append("订单引用重复")
        if not cand.vehicle_id:
            reasons.append("缺少车辆")
        else:
            if cand.vehicle_id not in vehicle_ids:
                reasons.append(f"车辆 {cand.vehicle_id} 不在当前场景")
        for oid in cand.order_ids:
            order = order_by_id.get(oid)
            if order is None:
                reasons.append(f"订单 {oid} 不在当前场景")
            elif order.is_new:
                reasons.append(f"订单 {oid} 是新增任务，锁只能作用于旧任务")
        if reasons:
            rejected.append(RejectedItem(
                kind=cand.kind.value,
                source_text=cand.source_text,
                reason="；".join(reasons),
            ))
            continue
        items.append(HardConstraintItem(
            kind=cand.kind,
            order_ids=cand.order_ids,
            vehicle_id=cand.vehicle_id,  # 无 reasons 时已确保非 None
            source_text=cand.source_text,
        ))

    preferences: list[PreferenceItem] = []
    for cand in payload.preferences:
        if cand.source_text not in raw_text:
            rejected.append(RejectedItem(
                kind="preference", source_text=cand.source_text,
                reason="原文依据在原始需求中不存在（拒绝伪造依据）"))
            continue
        known = tuple(oid for oid in cand.order_ids if oid in order_by_id)
        dropped = sorted(set(cand.order_ids) - set(known))
        if dropped:
            warnings.append(f"偏好项引用了不存在的订单，已忽略：{dropped}")
        preferences.append(PreferenceItem(
            kind=cand.kind,
            order_ids=known,
            source_text=cand.source_text,
            note=cand.note,
        ))

    missing: list[MissingItem] = []
    for n in payload.missing:
        if n.source_text not in raw_text:
            rejected.append(RejectedItem(
                kind="missing", source_text=n.source_text,
                reason="原文依据在原始需求中不存在（拒绝伪造依据）"))
            dropped_notes += 1
            continue
        missing.append(MissingItem(description=n.text, source_text=n.source_text))

    questions: list[ClarificationQuestion] = []
    for n in payload.questions:
        if n.source_text not in raw_text:
            rejected.append(RejectedItem(
                kind="question", source_text=n.source_text,
                reason="原文依据在原始需求中不存在（拒绝伪造依据）"))
            dropped_notes += 1
            continue
        questions.append(ClarificationQuestion(question=n.text,
                                               source_text=n.source_text))

    if dropped_notes:
        warnings.append(f"{dropped_notes} 条 missing/questions 因原文依据不匹配被丢弃")
    return (tuple(items), tuple(preferences), tuple(missing), tuple(questions),
            tuple(rejected), tuple(warnings))
