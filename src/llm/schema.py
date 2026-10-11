"""阶段 4 契约：自然语言需求 → 待确认约束草案。

红线（任务书阶段 4，过程边界）：
- 本模块只描述"草案"这一数据形态，不含任何执行能力：不调用求解器、
  不构造路线、不发起网络请求；
- 草案一律冻结（frozen），确认前不得生效；确认只接受旧任务上的硬约束
  （换车锁 / 相对顺序锁），偏好项绝不写成锁；
- 草案必须自带溯源：基线场景版本与输入哈希、原文依据、provider 与
  演示模式标记，确保"哪句话产生了哪条约束"可回查。

语义区分（验收要点）：
- "不能换车" = 硬约束 → ASSIGN_LOCK（确认后写入 Locks.assignment_locks）；
- "尽量不换车" = 偏好 → PREFERENCE_AVOID_VEHICLE_CHANGE（确认后只进
  已确认约束包，供后续阶段接线求解配置，绝不写成锁）。
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ItemKind(str, Enum):
    """草案硬约束的种类（首版只支持任务书锁定语义内的两类）。"""

    ASSIGN_LOCK = "assign_lock"  # 换车锁：某旧任务固定到某车
    REL_ORDER_LOCK = "rel_order_lock"  # 相对顺序锁：某车若干旧任务先后不变


class PreferenceKind(str, Enum):
    """草案偏好种类（软约束，不写成锁）。"""

    AVOID_VEHICLE_CHANGE = "avoid_vehicle_change"  # 尽量不换车


class ItemStatus(str, Enum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class DraftStatus(str, Enum):
    PENDING = "pending"
    CONFIRMED = "confirmed"


class HardConstraintItem(BaseModel):
    """一条待确认的硬约束草案项。

    source_text 必须是原始需求文本的子串（原文依据），确认时可逐项
    接受/拒绝（status）。ASSIGN_LOCK 恰好 1 个订单；REL_ORDER_LOCK
    至少 2 个订单且保持给定先后顺序。
    """

    model_config = ConfigDict(frozen=True)

    kind: ItemKind
    order_ids: tuple[str, ...] = Field(min_length=1)
    vehicle_id: str = Field(min_length=1)  # 两类锁都固定车辆（RelativeOrderLock 语义）
    source_text: str = Field(min_length=1)
    status: ItemStatus = ItemStatus.PENDING


class PreferenceItem(BaseModel):
    """一条待确认的偏好（软约束）。order_ids 为空表示全局偏好。"""

    model_config = ConfigDict(frozen=True)

    kind: PreferenceKind
    order_ids: tuple[str, ...] = ()
    source_text: str = Field(min_length=1)
    status: ItemStatus = ItemStatus.PENDING
    note: str = ""


class MissingItem(BaseModel):
    """需求里缺少必要字段，如实记录、不补造数值。"""

    model_config = ConfigDict(frozen=True)

    description: str = Field(min_length=1)
    source_text: str = Field(min_length=1)


class ClarificationQuestion(BaseModel):
    """指代不清或无法理解时返回的澄清问题（不猜测、不硬猜）。"""

    model_config = ConfigDict(frozen=True)

    question: str = Field(min_length=1)
    source_text: str = Field(min_length=1)


class RejectedItem(BaseModel):
    """候选约束被安全收口拒绝，附原因（不得静默吞错）。"""

    model_config = ConfigDict(frozen=True)

    kind: str = Field(min_length=1)  # 候选时的种类（可能非法，故用 str）
    source_text: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class ConstraintDraft(BaseModel):
    """一份待人工确认的约束草案（冻结，确认前不产生任何场景变化）。"""

    model_config = ConfigDict(frozen=True)

    draft_id: str = Field(min_length=1)
    scenario_id: str = Field(min_length=1)
    base_version: int = Field(ge=1)  # 基线场景版本；确认后新版本 = base_version + 1
    base_input_hash: str = Field(min_length=1)  # 基线场景输入哈希（溯源）
    created_at: datetime
    demo_mode: bool  # 演示模式标记：响应与产物必须全程可识别
    provider: str = Field(min_length=1)  # 产生草案的 provider（mock / openai_compatible / ...）
    model: str | None = None  # 演示模式为 None（mock 不冒充真实模型）
    raw_text: str = Field(min_length=1)  # 原始自然语言需求
    items: tuple[HardConstraintItem, ...] = ()
    preferences: tuple[PreferenceItem, ...] = ()
    missing: tuple[MissingItem, ...] = ()
    questions: tuple[ClarificationQuestion, ...] = ()
    rejected: tuple[RejectedItem, ...] = ()
    status: DraftStatus = DraftStatus.PENDING
    warnings: tuple[str, ...] = ()

    @field_validator("created_at")
    @classmethod
    def _tz_required(cls, v: datetime) -> datetime:
        if v.tzinfo is None or v.utcoffset() is None:
            raise ValueError("E_TIME_ORIGIN_NO_TZ: created_at 必须带时区")
        return v


class ConfirmedConstraints(BaseModel):
    """确认结果：已接受约束 + 新场景版本指针（偏好随包走，不进 Locks）。"""

    model_config = ConfigDict(frozen=True)

    bundle_id: str = Field(min_length=1)
    draft_id: str = Field(min_length=1)
    scenario_id: str = Field(min_length=1)
    base_version: int = Field(ge=1)
    new_version: int = Field(ge=2)
    base_input_hash: str = Field(min_length=1)
    new_input_hash: str = Field(min_length=1)
    created_at: datetime
    demo_mode: bool
    provider: str = Field(min_length=1)
    model: str | None = None
    accepted_items: tuple[HardConstraintItem, ...] = ()
    accepted_preferences: tuple[PreferenceItem, ...] = ()
    warnings: tuple[str, ...] = ()

    @field_validator("created_at")
    @classmethod
    def _tz_required(cls, v: datetime) -> datetime:
        if v.tzinfo is None or v.utcoffset() is None:
            raise ValueError("E_TIME_ORIGIN_NO_TZ: created_at 必须带时区")
        return v
