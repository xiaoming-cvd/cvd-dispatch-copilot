"""MockProvider：演示模式下的确定性规则提取器（非真实大模型）。

红线：
- 它明确标注为演示模式（model=None，demo_mode=True），绝不冒充真实
  模型；固定规则不覆盖的需求一律返回澄清问题，不编造"已理解"；
- 输出走与真实模型相同的 ProviderPayload JSON 文本契约，由 extractor
  统一收口（同一注入防护路径）；
- 只支持任务书锁定语义内的小型中文词表：换车锁 / 相对顺序锁 /
  "尽量不换车"偏好；时间类调整缺字段时如实进 missing；
- "o1 不能换车"这类未点名车辆的硬约束，从 prompt 里的原计划摘要解析
  原车落实换车锁（Mock 只认识 service._user_prompt 的固定格式）。
"""
from __future__ import annotations

import json
import re

from .base import ProviderReply

_ORDER_ID_RE = re.compile(r"\b[oO](\d+)\b")
_VEHICLE_ID_RE = re.compile(r"\b[vV](\d+)\b")
_ORDER_NUM_RE = re.compile(r"(\d+)\s*号(订单|客户)")
_VEHICLE_NUM_RE = re.compile(r"(\d+)\s*号车")
_CLAUSE_SPLIT_RE = re.compile(r"[,，。；;！!？?\n]+")
_PLAN_LINE_RE = re.compile(r"^原计划：(.*)$", re.MULTILINE)
_PLAN_PAIR_RE = re.compile(r"([A-Za-z0-9_-]+):([A-Za-z0-9_,]+)")
_ASSIGN_CUES = ("必须", "固定", "指定", "安排给", "留给", "不能换", "不换", "别换", "还是")
_AMBIGUOUS_CUES = ("老规矩", "那批", "这批", "第二个", "第三个", "它", "他", "她", "他们", "原来那")
_TIME_CUES = ("提前", "推迟", "延后", "改到", "时间窗", "加窗", "晚点")


def _order_refs(clause: str) -> list[str]:
    refs = [f"o{m.group(1)}" for m in _ORDER_ID_RE.finditer(clause)]
    refs += [f"o{m.group(1)}" for m in _ORDER_NUM_RE.finditer(clause)]
    return _dedupe(refs)


def _vehicle_refs(clause: str) -> list[str]:
    refs = [f"V{m.group(1)}" for m in _VEHICLE_ID_RE.finditer(clause)]
    refs += [f"V{m.group(1)}" for m in _VEHICLE_NUM_RE.finditer(clause)]
    return _dedupe(refs)


def _dedupe(refs: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for r in refs:
        if r not in seen:
            seen.add(r)
            out.append(r)
    return out


class MockProvider:
    """确定性规则提取器（演示模式专用）。同输入输出恒定。"""

    provider_name = "mock"

    def complete(self, system_prompt: str, user_prompt: str) -> ProviderReply:
        text = user_prompt.split("\n\n【调度员需求】", 1)[-1].strip()
        payload = self._extract(text, self._parse_plan(user_prompt))
        return ProviderReply(
            text=json.dumps(payload, ensure_ascii=False),
            provider=self.provider_name,
            model=None,
            demo_mode=True,
        )

    @staticmethod
    def _parse_plan(user_prompt: str) -> dict[str, str]:
        """从 prompt 的"原计划：V1:o1,o2"摘要解析 order -> vehicle。"""
        m = _PLAN_LINE_RE.search(user_prompt)
        if not m:
            return {}
        plan: dict[str, str] = {}
        for pair in _PLAN_PAIR_RE.finditer(m.group(1)):
            vehicle, orders = pair.group(1), pair.group(2).split(",")
            for oid in orders:
                plan[oid] = vehicle
        return plan

    def _extract(self, text: str, plan: dict[str, str]) -> dict:
        items: list[dict] = []
        preferences: list[dict] = []
        questions: list[dict] = []
        missing: list[dict] = []
        seen: set[tuple] = set()

        clauses = [c.strip() for c in _CLAUSE_SPLIT_RE.split(text) if c.strip()]
        for clause in clauses:
            orders = _order_refs(clause)
            vehicles = _vehicle_refs(clause)

            if ("尽量" in clause or "最好" in clause or "尽可能" in clause) and "换车" in clause:
                key = ("pref", tuple(orders))
                if key not in seen:
                    seen.add(key)
                    preferences.append({
                        "kind": "avoid_vehicle_change",
                        "order_ids": orders,
                        "source_text": clause,
                        "note": "尽量不换车（软约束，不写成锁）",
                    })
                continue

            if len(orders) >= 2 and vehicles and (
                    "顺序" in clause or "先后" in clause or "之前" in clause
                    or "之后" in clause or "先于" in clause or "晚于" in clause):
                key = ("rel", tuple(orders), vehicles[0])
                if key not in seen:
                    seen.add(key)
                    items.append({
                        "kind": "rel_order_lock",
                        "order_ids": orders,
                        "vehicle_id": vehicles[0],
                        "source_text": clause,
                    })
                continue

            if orders and vehicles and any(cue in clause for cue in _ASSIGN_CUES):
                key = ("assign", orders[0], vehicles[0])
                if key not in seen:
                    seen.add(key)
                    items.append({
                        "kind": "assign_lock",
                        "order_ids": [orders[0]],
                        "vehicle_id": vehicles[0],
                        "source_text": clause,
                    })
                continue

            if len(orders) == 1 and not vehicles and any(cue in clause for cue in _ASSIGN_CUES):
                # "不能换车"未点名车辆：从原计划落实原车；找不到则澄清。
                original = plan.get(orders[0])
                if original:
                    key = ("assign", orders[0], original)
                    if key not in seen:
                        seen.add(key)
                        items.append({
                            "kind": "assign_lock",
                            "order_ids": [orders[0]],
                            "vehicle_id": original,
                            "source_text": clause,
                        })
                else:
                    questions.append({
                        "text": f"无法确定订单 {orders[0]} 的原车，请明确车辆号",
                        "source_text": clause,
                    })
                continue

            if any(cue in clause for cue in _TIME_CUES):
                missing.append({
                    "text": "时间/窗口类调整缺少明确目标值（需要具体到秒或明确时间点），未生成约束",
                    "source_text": clause,
                })
                continue

            if any(cue in clause for cue in _AMBIGUOUS_CUES):
                questions.append({
                    "text": "指代不清，请明确到订单号/车辆号后再确认",
                    "source_text": clause,
                })
                continue

            if orders or vehicles:
                questions.append({
                    "text": "该句信息不完整（缺少订单或车辆引用），请补充明确",
                    "source_text": clause,
                })
            else:
                questions.append({
                    "text": "未能理解该需求，请用订单号/车辆号明确表述约束",
                    "source_text": clause,
                })

        if not clauses:
            questions.append({"text": "需求为空或无法断句，请重新表述", "source_text": text[:50]})

        return {"items": items, "preferences": preferences,
                "questions": questions, "missing": missing}
