"""求解编排（任务书阶段 2）：方法调度 + 状态语义 + 独立复核。

状态判定规则（红线）：
- invalid_input：场景交叉校验未过；
- feasible：方法给出方案，且方案通过独立校验器复核；
- no_feasible_found：求解器超时/未找到解，或求解器声称有解但
  未通过独立复核（不采信求解器标志）——超时绝不等同"证明无解"；
- proven_infeasible：仅当 OR-Tools 明确返回 INFEASIBLE；
- error：编排层自身异常（输入文件损坏、建模失败等）。
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from ..domain.models import (
    RouteResult,
    Scenario,
    SolveResult,
    SolveStatus,
    StopSchedule,
    input_hash,
)
from ..validation.checks import validate_scenario
from ..validation.timetable import (
    SolutionValidation,
    evaluate_route,
    validate_solution,
)
from . import diff as diff_mod
from . import insert as insert_mod
from . import low_disruption as low_mod
from . import rebuild as rebuild_mod
from .ortools_core import SOLVER_VERSION

METHODS = ("insert", "rebuild", "low_disruption")

# COPT 为可选后端（P1 F）：顶层不导入 coptpy，选择 rebuild 时才延迟导入；
# 无 COPT 环境插单/低扰动/OR-Tools 照常可用。


def _solver_version(method: str) -> str:
    if method == "rebuild":
        try:
            from .copt_core import SOLVER_VERSION_COPT
            return SOLVER_VERSION_COPT
        except ImportError:
            return "stage2-copt-unavailable"
    if method == "low_disruption":
        return "stage3-low-disruption/1.0"
    return "stage2-insert-enum/1.0"


@dataclass(frozen=True)
class SolveOutcome:
    result: SolveResult
    validation: SolutionValidation | None  # None = 未走到独立复核


def _metrics(scn: Scenario, routes: list[RouteResult]) -> dict:
    travel = 0
    wait = 0
    max_return = 0
    for r in routes:
        ev = evaluate_route(scn, r.vehicle_id, list(r.order_ids))
        travel += ev.travel_time_s
        wait += sum(s.wait_s for s in ev.stops)
        max_return = max(max_return, ev.return_s)
    return {
        "total_travel_time_s": travel,
        "total_wait_s": wait,
        "vehicles_used": len(routes),
        "orders_served": sum(len(r.order_ids) for r in routes),
        "orders_total": len(scn.orders),
        "max_return_s": max_return,
    }


def _assemble(
    scn: Scenario,
    method: str,
    time_limit_ms: int,
    started: float,
    status: SolveStatus,
    routes: list[RouteResult],
    validation: SolutionValidation | None,
    warnings: list[str],
    assumptions: list[str],
    extra_metrics: dict | None = None,
) -> SolveResult:
    schedule: list[StopSchedule] = []
    if validation is not None:
        for ev in validation.routes:
            schedule.extend(ev.stops)
    metrics = _metrics(scn, routes) if routes else {}
    if extra_metrics:
        metrics.update(extra_metrics)
    return SolveResult(
        input_hash=input_hash(scn),
        config={"method": method, "time_limit_ms": time_limit_ms},
        solver_version=_solver_version(method),
        elapsed_ms=int((time.monotonic() - started) * 1000),
        status=status,
        routes=routes,
        schedule=schedule,
        metrics=metrics,
        validation=validation.to_dict() if validation else {},
        warnings=warnings,
        assumptions=assumptions,
    )


def solve(
    scn: Scenario,
    method: str,
    time_limit_ms: int = 10_000,
    cost_cap_ratio: float | None = None,
    reference_results=None,
) -> SolveOutcome:
    """对一个完整场景跑一种基线方法，返回规范 SolveResult。

    cost_cap_ratio / reference_results 仅对 low_disruption 生效：
    业务成本上限 = cost_cap_ratio × reference_best_cost（跨方法已验证
    方案的最低成本）；reference_results 为 [(name, routes), ...]。
    """
    started = time.monotonic()
    if method not in METHODS:
        raise ValueError(f"E_METHOD: 未知方法 {method}，可选 {METHODS}")

    report = validate_scenario(scn)
    if not report.ok:
        result = _assemble(
            scn, method, time_limit_ms, started, SolveStatus.INVALID_INPUT,
            [], None, [], [],
            extra_metrics={"input_issues": [i.to_dict() for i in report.issues]},
        )
        return SolveOutcome(result=result, validation=None)

    assumptions = [
        "车辆从仓库固定时刻 available_from_s 出发，服务开始=max(到达,ready_s)，"
        "等待=服务开始-到达；返回不得晚于 return_by_s",
        "容量为配送语义：单路线装载=路线全部订单 demand 之和",
        "可行性由独立校验器依据输入矩阵重算复核，不采信求解器标志",
    ]

    warnings: list[str] = []
    solve_ms = 0  # 求解阶段耗时（建模+搜索），校验与总耗时分列（P1 G）

    try:
        if method == "insert":
            t_solve = time.monotonic()
            routes, diag = insert_mod.insert_new_orders(
                scn, time_limit_ms=time_limit_ms)  # 预算真实传递（P1 G）
            solve_ms = int((time.monotonic() - t_solve) * 1000)
            if not routes:
                reason = "time_budget_exhausted" if any(
                    d.get("reason") == "time_budget_exhausted"
                    for d in diag.get("inserted", [])) else "枚举未覆盖搬动旧任务"
                result = _assemble(
                    scn, method, time_limit_ms, started,
                    SolveStatus.NO_FEASIBLE_FOUND, [], None,
                    warnings=[f"插单基线未找到可行插入位置（{reason}，"
                              "不代表问题被证明无解，可尝试 rebuild 方法）"],
                    assumptions=assumptions,
                    extra_metrics={"diagnostics": diag,
                                   "elapsed_solve_ms": solve_ms},
                )
                return SolveOutcome(result=result, validation=None)
            extra = {"diagnostics": diag}
        elif method == "rebuild":
            try:
                from .copt_core import SOLVER_VERSION_COPT  # noqa: F401  # 延迟导入（P1 F）
            except ImportError:
                result = _assemble(
                    scn, method, time_limit_ms, started,
                    SolveStatus.ERROR, [], None,
                    warnings=["COPT 后端不可用（import coptpy 失败）；rebuild "
                              "需要 COPT 或显式改用 OR-Tools 后端，插单与"
                              "低扰动方法不受影响"],
                    assumptions=[],
                )
                return SolveOutcome(result=result, validation=None)
            t_solve = time.monotonic()
            outcome = rebuild_mod.rebuild_all(scn, time_limit_ms)
            solve_ms = int((time.monotonic() - t_solve) * 1000)
            served = [oid for seq in outcome.routes for oid in seq]
            expected = sorted(o.order_id for o in scn.orders)
            # 结构化状态优先（P1 H）；白名单仅为旧后端兼容回退
            term = outcome.termination
            accepted = (
                (term in ("optimal", "feasible", "timeout")
                 or outcome.raw_status in (
                     "ROUTING_SUCCESS", "ROUTING_OPTIMAL",
                     "ROUTING_PARTIAL_SUCCESS_LOCAL_OPTIMUM_NOT_REACHED",
                     "MIP_OPTIMAL", "MIP_FEASIBLE", "MIP_TIMEOUT_WITH_SOL"))
                and sorted(served) == expected)
            if outcome.raw_status in ("ROUTING_INFEASIBLE", "MIP_INFEASIBLE") \
                    or term == "infeasible":
                # 数学求解器只能证明"所建模型"无解：模型修正（大 M/目标）
                # 有误时不得把 INFEASIBLE 当业务无解（P0 A/B 的教训）
                result = _assemble(
                    scn, method, time_limit_ms, started,
                    SolveStatus.PROVEN_INFEASIBLE, [], None,
                    warnings=[f"求解器报告模型不可行（raw={outcome.raw_status}，"
                              f"backend={outcome.backend or 'unknown'}）；"
                              "该结论仅对当前建模有效"],
                    assumptions=assumptions,
                    extra_metrics={"solver_status": outcome.raw_status,
                                   "backend": outcome.backend,
                                   "backend_version": outcome.backend_version,
                                   "status_detail": outcome.status_detail,
                                   "elapsed_solve_ms": solve_ms},
                )
                return SolveOutcome(result=result, validation=None)
            if not accepted:
                reason = (
                    f"求解器状态 {outcome.raw_status}"
                    f"（termination={term or 'unknown'}）或方案未覆盖全部订单"
                    f"（served={len(served)}/{len(expected)}）；"
                    "未找到可行解 ≠ 证明无解"
                )
                result = _assemble(
                    scn, method, time_limit_ms, started,
                    SolveStatus.NO_FEASIBLE_FOUND, [], None,
                    warnings=[reason], assumptions=assumptions,
                    extra_metrics={"solver_status": outcome.raw_status,
                                   "backend": outcome.backend,
                                   "status_detail": outcome.status_detail,
                                   "elapsed_solve_ms": solve_ms},
                )
                return SolveOutcome(result=result, validation=None)
            routes = rebuild_mod.outcome_to_routes(scn, outcome)
            extra = {"solver_status": outcome.raw_status,
                     "backend": outcome.backend,
                     "backend_version": outcome.backend_version,
                     "status_detail": outcome.status_detail,
                     "elapsed_solve_ms": solve_ms}
        if method == "low_disruption":
            outcome = low_mod.solve_low_disruption(
                scn, time_limit_ms,
                cost_cap_ratio=(cost_cap_ratio
                                if cost_cap_ratio is not None
                                else low_mod.DEFAULT_COST_CAP),
                reference_results=reference_results)
            if outcome.recommended is None:
                result = _assemble(
                    scn, method, time_limit_ms, started,
                    SolveStatus.NO_FEASIBLE_FOUND, [], None,
                    warnings=["低扰动方法未找到任何可行候选（插单种子与"
                              "单任务搬动种子均失败），不代表问题被证明无解，"
                              "可尝试 rebuild 方法"],
                    assumptions=assumptions,
                    extra_metrics={"diagnostics": outcome.diagnostics},
                )
                return SolveOutcome(result=result, validation=None)
            routes = list(outcome.recommended.routes)
            low = outcome.recommended
            best = outcome.best_cost_candidate
            sel = outcome.selection
            extra = {
                "diagnostics": outcome.diagnostics,
                "selection": sel,
                # 成本口径（第三阶段复审 P0-1）：池内最优与跨方法参考分列
                "pool_best_cost_s": sel["pool_best_cost_s"],
                "reference_best_cost_s": sel["reference_best_cost_s"],
                "reference_source": sel["reference_source"],
                "cost_cap_ratio": sel["cost_cap_ratio"],
                "cost_cap_s": sel["cost_cap_s"],
                "within_reference_cap": sel["within_reference_cap"],
                "selection_status": sel["selection_status"],
                "recommended_candidate": low.summary(),
                "best_cost_candidate": best.summary() if best else None,
                # 对外审计候选：稳定 ID + 完整路线 + validation_ok（Top K）
                "audit_candidates": list(outcome.audit),
                "candidate_count": len(outcome.candidates),
                "search_stop_reason": outcome.diagnostics.get(
                    "local_search", {}).get("stop_reason"),
            }
            warnings = []
            if len(outcome.candidates) < 2:
                warnings.append(
                    "可行候选不足两个不同方案，仅展示唯一候选；"
                    "低扰动选择在单候选内退化为该候选本身")
            if sel["selection_status"] == "no_candidate_within_cost_cap":
                warnings.append(
                    f"低扰动候选池内无成本 ≤ {sel['cost_cap_ratio']:.2f} × "
                    f"reference_best_cost({sel['reference_best_cost_s']}s) = "
                    f"{sel['cost_cap_s']}s 的方案；推荐池内最低成本候选并如实"
                    "标注 within_reference_cap=false，不冒充达标")
            assumptions = assumptions + [
                f"低扰动候选成本上限 = {sel['cost_cap_ratio']:.2f} × "
                "reference_best_cost（同输入同约束下所有已通过独立校验方案的"
                "最低成本；未提供参考时回退为本方法候选池最优并标注）",
                "成本上限默认 1.10，可通过 cost_cap_ratio 调整（范围 "
                f"[{low_mod.RATIO_MIN}, {low_mod.RATIO_MAX}]）",
                "低扰动方法不调用 COPT/OR-Tools 子求解，"
                "本结果总耗时即该方法端到端耗时",
                "预算为绝对截止时刻，全程只扣一次；候选池/邻域/beam "
                "设有上限，停止原因见 search_stop_reason",
            ]
    except Exception as exc:  # noqa: BLE001 — 编排层兜底，状态语义要求
        result = _assemble(
            scn, method, time_limit_ms, started, SolveStatus.ERROR, [],
            None, warnings=[f"{type(exc).__name__}: {exc}"], assumptions=[],
        )
        return SolveOutcome(result=result, validation=None)

    # 独立复核（红线）：求解器结果一律重算校验
    t_val = time.monotonic()
    validation = validate_solution(scn, routes)
    elapsed_validate_ms = int((time.monotonic() - t_val) * 1000)
    extra["elapsed_validate_ms"] = elapsed_validate_ms
    if not validation.ok:
        # 与"搜索未找到"明确区分：这是候选被独立校验器拒绝（P1 H）
        extra["validator_rejected"] = True
        result = _assemble(
            scn, method, time_limit_ms, started,
            SolveStatus.NO_FEASIBLE_FOUND, [], validation,
            warnings=["求解器返回的方案未通过独立校验（validator_rejected），"
                      "按未找到可行解处理，详见 validation.issues"],
            assumptions=assumptions, extra_metrics=extra,
        )
        return SolveOutcome(result=result, validation=validation)

    result = _assemble(
        scn, method, time_limit_ms, started, SolveStatus.FEASIBLE,
        routes, validation, warnings, assumptions, extra_metrics=extra,
    )
    if method == "low_disruption":
        result = result.model_copy(
            update={"diff": diff_mod.diff_orders(scn, routes)})
    return SolveOutcome(result=result, validation=validation)


def compare_methods(
    scn: Scenario,
    time_limit_ms: int = 10_000,
    methods: tuple[str, ...] = ("insert", "rebuild", "low_disruption"),
    cost_cap_ratio: float | None = None,
) -> dict[str, SolveOutcome]:
    """同输入同约束同预算跑多方法（任务书阶段 3 对比 / 阶段 6 评测）。

    顺序执行；先跑方法中通过独立校验的方案自动作为 low_disruption 的
    reference_results 参与跨方法成本上限。返回 {method: SolveOutcome}。
    """
    results: dict[str, SolveOutcome] = {}
    references: list = []
    for m in methods:
        kwargs: dict = {}
        if m == "low_disruption":
            kwargs["cost_cap_ratio"] = cost_cap_ratio
            kwargs["reference_results"] = list(references)
        oc = solve(scn, m, time_limit_ms=time_limit_ms, **kwargs)
        results[m] = oc
        if oc.result.status == SolveStatus.FEASIBLE and oc.validation is not None:
            references.append((m, list(oc.result.routes)))
    return results
