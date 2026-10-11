"""逐任务 diff 与变更原因（任务书阶段 3）：所有字段程序生成，不得手写。

对每个订单输出：
- 原车辆 / 新车辆、原原始索引 / 新原始索引、原旧任务内索引 / 新旧任务内索引
  （旧任务内索引 = 滤去新任务后的位置，是相邻关系的判定基准）；
- change_reason：程序可解释的原因，取值规则固定：
  * 新任务                      → "新增任务（原计划不存在）"
  * 换车                        → "从 {原车辆} 换到 {新车辆}"
  * 同车、旧任务内索引不变      → "位置因新任务插入顺延（不计扰动）"
    （原始索引未变时同义表述为 "车辆与相对位置未变"）
  * 同车、旧任务内索引改变      → "同车旧任务顺序改变"
"""
from __future__ import annotations

from ..domain.models import RouteResult, Scenario
from . import perturbation as pert_mod


def _positions(scn: Scenario, routes) -> dict[str, dict]:
    """每个订单在新方案中的：车辆、原始索引、旧任务内索引。"""
    out: dict[str, dict] = {}
    for r in routes:
        old_rank = 0
        for raw_idx, oid in enumerate(r.order_ids):
            is_new = next((o.is_new for o in scn.orders if o.order_id == oid), False)
            entry = {
                "vehicle_id": r.vehicle_id,
                "raw_index": raw_idx,
                "old_index": None if is_new else old_rank,
            }
            out[oid] = entry
            if not is_new:
                old_rank += 1
    return out


def diff_orders(scn: Scenario, routes) -> dict:
    """逐任务 diff + 扰动汇总。routes 必须已覆盖全部订单。"""
    orig_pos: dict[str, dict] = {}
    for r in scn.original_plan.routes:
        for idx, oid in enumerate(r.order_ids):
            orig_pos[oid] = {"vehicle_id": r.vehicle_id, "raw_index": idx,
                             "old_index": idx}  # 原计划只含旧任务，raw==old

    new_pos = _positions(scn, routes)
    orders: dict[str, dict] = {}
    for o in scn.orders:
        oid = o.order_id
        before = orig_pos.get(oid)
        after = new_pos.get(oid)
        if after is None:
            orders[oid] = {"change_reason": "未出现在方案中（不应发生，独立校验会拦截）"}
            continue
        if o.is_new:
            orders[oid] = {
                "is_new": True,
                "vehicle_id": after["vehicle_id"],
                "raw_index": after["raw_index"],
                "change_reason": "新增任务（原计划不存在）",
            }
            continue
        ov, nv = before["vehicle_id"], after["vehicle_id"]
        if ov != nv:
            reason = f"从 {ov} 换到 {nv}"
        elif before["old_index"] != after["old_index"]:
            reason = "同车旧任务顺序改变"
        elif before["raw_index"] != after["raw_index"]:
            reason = "位置因新任务插入顺延（不计扰动）"
        else:
            reason = "车辆与相对位置未变"
        orders[oid] = {
            "is_new": False,
            "original_vehicle_id": ov,
            "vehicle_id": nv,
            "original_raw_index": before["raw_index"],
            "raw_index": after["raw_index"],
            "original_old_index": before["old_index"],
            "old_index": after["old_index"],
            "change_reason": reason,
        }

    return {
        "orders": orders,
        "perturbation": pert_mod.measure(scn, routes).to_dict(),
    }
