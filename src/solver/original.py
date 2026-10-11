"""原计划生成（任务书阶段 2）：只对"当时已知任务"求解并冻结。

语义红线：
- 输入必须是不含 original_plan 的 ScenarioDraft；生成时只看 is_new=False
  的旧任务，新增任务绝不参与原计划。
- 生成的 OriginalPlan 只覆盖旧任务，经 validate_scenario 通过后方可冻结。
- 锁定（locks）在原计划存在之后才能落实：换车锁需要知道任务所属车辆，
  相对顺序锁需要知道任务在车上的顺序。lock_ratio 来自生成器 _meta。
"""
from __future__ import annotations

import random

from ..domain.models import Locks, OriginalPlan, RelativeOrderLock, Scenario, ScenarioDraft
from ..validation.checks import validate_scenario
from .ortools_core import solve_vrptw

DEFAULT_TIME_LIMIT_MS = 10_000  # 任务书：10 秒预算作为待测目标


def generate_original_plan(
    draft: ScenarioDraft,
    time_limit_ms: int = DEFAULT_TIME_LIMIT_MS,
) -> OriginalPlan:
    """用 OR-Tools 对旧任务集合生成基准计划（完全重排，无锁定）。"""
    old_ids = [o.order_id for o in draft.orders if not o.is_new]
    if not old_ids:
        raise ValueError("E_PLAN_NO_OLD: 场景没有旧任务，无法生成原计划")

    # 借用 Scenario 壳以复用建模层：original_plan 只参与展示、不进模型
    tmp = draft.to_scenario(OriginalPlan(plan_id="tmp", version=1, routes=[
        {"vehicle_id": draft.vehicles[0].vehicle_id, "order_ids": [old_ids[0]]}
    ]))
    outcome = solve_vrptw(tmp, old_ids, time_limit_ms, respect_locks=False)

    if outcome.raw_status == "ROUTING_INFEASIBLE":
        raise ValueError("E_PLAN_OLD_INFEASIBLE: 已知任务集合本身无可行计划（proven_infeasible）")
    served = [oid for seq in outcome.routes for oid in seq]
    feasible_statuses = (
        "ROUTING_SUCCESS", "ROUTING_OPTIMAL",
        "ROUTING_PARTIAL_SUCCESS_LOCAL_OPTIMUM_NOT_REACHED")
    if outcome.raw_status not in feasible_statuses or sorted(served) != sorted(old_ids):
        raise ValueError(
            f"E_PLAN_NOT_FOUND: 预算 {time_limit_ms}ms 内未找到覆盖全部旧任务的原计划"
            f"（{outcome.raw_status}，禁止把超时写成无解；可加大预算重试）")

    routes = [
        {"vehicle_id": draft.vehicles[v].vehicle_id, "order_ids": list(seq)}
        for v, seq in enumerate(outcome.routes)
        if seq
    ]
    plan = OriginalPlan(plan_id=f"{draft.scenario_id}-original", version=1, routes=routes)

    scn = draft.to_scenario(plan)
    report = validate_scenario(scn)
    if not report.ok:
        raise ValueError(f"E_PLAN_INVALID: 生成的原计划未过场景校验：{report.to_dict()}")
    return plan


def materialize_locks(
    draft: ScenarioDraft,
    plan: OriginalPlan,
    lock_ratio: float = 0.0,
    seed: int = 0,
) -> Locks:
    """按 lock_ratio 把生成器登记的目标比例落实为真实锁定。

    实现规则（声明并留痕）：
    - 换车锁：随机选 floor(ratio * 旧任务数) 个旧任务，锁定到原计划车辆；
    - 相对顺序锁：在已锁定任务中，对同车的相邻对按顺序连成锁。
    - 锁定只作用于旧任务；种子固定可复现。
    """
    if not 0.0 <= lock_ratio <= 1.0:
        raise ValueError("E_LOCK_RATIO: lock_ratio 必须在 [0,1]")
    rng = random.Random(f"locks:{draft.scenario_id}:{seed}")
    vehicle_of = {oid: r.vehicle_id for r in plan.routes for oid in r.order_ids}
    old_ids = [o.order_id for o in draft.orders if not o.is_new]
    n_lock = int(lock_ratio * len(old_ids))
    chosen = sorted(rng.sample(old_ids, n_lock)) if n_lock else []

    assignment = {oid: vehicle_of[oid] for oid in chosen}

    rel_locks: list[RelativeOrderLock] = []
    if len(chosen) >= 2:
        seqs = {
            r.vehicle_id: [oid for oid in r.order_ids if oid in set(chosen)]
            for r in plan.routes
        }
        for vid, seq in seqs.items():
            if len(seq) >= 2:
                rel_locks.append(RelativeOrderLock(vehicle_id=vid, order_ids=seq))
    return Locks(assignment_locks=assignment, relative_order_locks=rel_locks)


def materialize_scenario(
    draft: ScenarioDraft,
    lock_ratio: float = 0.0,
    lock_seed: int = 0,
    time_limit_ms: int = DEFAULT_TIME_LIMIT_MS,
) -> Scenario:
    """Draft → 完整 Scenario：生成原计划 → 落实锁定 → 交叉校验 → 返回。"""
    plan = generate_original_plan(draft, time_limit_ms)
    locks = materialize_locks(draft, plan, lock_ratio=lock_ratio, seed=lock_seed)
    scn = draft.to_scenario(plan).model_copy(update={"locks": locks})
    report = validate_scenario(scn)
    if not report.ok:
        raise ValueError(f"E_SCENARIO_INVALID: 升格后的场景未过校验：{report.to_dict()}")
    return scn
