"""低扰动重排（任务书阶段 3）：候选生成 + 选择规则 + 逐任务 diff。

算法（与执行清单口径一致）：
- 不调用 COPT/OR-Tools 子求解，总耗时即本方法端到端耗时；
- 候选来源：(a) 插单基线结果；(b) 插单失败时的单旧任务搬动枚举种子；
  (c) 尊重锁定的 relocate/swap 局部搜索（可行状态才进档案）；
- 可行性判定全部来自独立重算（src/validation/timetable.py），不采信任何
  求解器标志；扰动指标定义见 src/solver/perturbation.py。

候选选择规则（第三阶段复审 P0-1 修正版）：
- pool_best_cost = 本方法候选池最低成本（仅描述搜索池，不声称全局最优）；
- reference_best_cost = 同输入同约束下所有已找到且通过独立校验方案的
  最低成本（由调用方以 reference_results 传入；未传入时如实回退为池内
  最优并在输出中标注）；
- 业务成本上限 = cost_cap_ratio × reference_best_cost（默认 1.10，可调，
  范围校验，结果回显）；上限内无候选时 selection_status=
  "no_candidate_within_cost_cap"，推荐池内最低成本候选并如实标注
  within_reference_cap=false，不得把池内上限冒充跨方法上限；
- 上限内候选按 (换车数, 相邻破坏数, 成本) 字典序最小者推荐。

预算（第三阶段复审 P0-2 修正版）：全链路统一使用绝对截止时刻
（deadline = started + time_limit_ms/1000），任何一层不再自行换算
remaining，杜绝种子耗时被重复扣减；因预算/档案上限/邻域上限停止时
记录 stop_reason。

规模控制（P1/P2）：邻域惰性生成；max_archive_size / beam_width /
max_neighbors_per_candidate 可配，默认值见下；对外输出仅推荐、最低
成本与 Top K 审计候选（稳定 candidate_id + 完整路线 + validation_ok），
完整候选池保留在 outcome.candidates 供内部/debug 使用。
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass

from ..domain.models import RouteResult, Scenario
from ..validation.timetable import evaluate_route, validate_solution
from . import insert as insert_mod
from . import perturbation as pert_mod

DEFAULT_COST_CAP = 1.10       # 默认成本上限比例（相对 reference_best_cost）
COST_CAP = DEFAULT_COST_CAP   # 兼容旧引用
DEFAULT_MAX_ARCHIVE = 512
DEFAULT_BEAM_WIDTH = 64
DEFAULT_MAX_NEIGHBORS = 2_000   # 单候选单轮最多评估的邻居数
RATIO_MIN, RATIO_MAX = 0.5, 2.0


@dataclass(frozen=True)
class Candidate:
    key: tuple  # 规范化键：tuple(sorted((vid, tuple(seq))))
    routes: tuple[RouteResult, ...]
    total_travel_s: int
    perturbation: pert_mod.Perturbation

    @property
    def candidate_id(self) -> str:
        """稳定审计 ID：由规范化键推导，同候选同 ID，可凭 ID 找到完整路线。"""
        return "cand-" + hashlib.sha1(repr(self.key).encode()).hexdigest()[:12]

    def summary(self) -> dict:
        return {
            "candidate_id": self.candidate_id,
            "total_travel_time_s": self.total_travel_s,
            "vehicle_changes": self.perturbation.vehicle_changes,
            "adjacency_breaks": self.perturbation.adjacency_breaks,
        }


@dataclass(frozen=True)
class LowDisruptionOutcome:
    recommended: Candidate | None          # 推荐候选（上限内低扰动，或池内最低成本）
    candidates: tuple[Candidate, ...]      # 全部可行候选（内部/debug 用）
    best_cost_candidate: Candidate | None  # 池内成本最低候选
    audit: tuple[dict, ...]                # 对外审计输出：推荐/最低成本/Pareto/TopK
    selection: dict                        # 选择规则回显（含 reference 口径）
    diagnostics: dict


# ---- 基础工具 ----

def _initial_sequences(scn: Scenario) -> dict[str, list[str]]:
    seqs: dict[str, list[str]] = {
        r.vehicle_id: list(r.order_ids) for r in scn.original_plan.routes}
    for v in scn.vehicles:  # 原计划未用到的车也参与
        seqs.setdefault(v.vehicle_id, [])
    return seqs


def _key(seqs: dict[str, list[str]]) -> tuple:
    return tuple(sorted((vid, tuple(seq)) for vid, seq in seqs.items() if seq))


def _evaluate(
    scn: Scenario, seqs: dict[str, list[str]]
) -> tuple[tuple[RouteResult, ...], int] | None:
    """独立重算：逐路线 evaluate_route + 覆盖性（恰好全部订单各一次）。

    返回 (routes, total_travel_s)；任何路线不可行或覆盖不完整返回 None。
    """
    routes: list[RouteResult] = []
    total = 0
    for vid, seq in seqs.items():
        if not seq:
            continue
        ev = evaluate_route(scn, vid, list(seq))
        if not ev.ok:
            return None
        routes.append(RouteResult(
            vehicle_id=vid, order_ids=tuple(seq), return_s=ev.return_s))
        total += ev.travel_time_s
    served = sorted(o for r in routes for o in r.order_ids)
    expected = sorted(o.order_id for o in scn.orders)
    if served != expected:
        return None
    return tuple(routes), total


def _greedy_insert(
    scn: Scenario, seqs: dict[str, list[str]], new_order_ids: list[str],
    out_of_budget=lambda: False,
) -> dict[str, list[str]] | None:
    """在给定旧序列上贪心插入新任务（同插单基线规则），失败返回 None。

    out_of_budget：统一绝对截止检查（返回 True 即中止），由调用方注入。"""
    work = {vid: list(seq) for vid, seq in seqs.items()}
    for oid in new_order_ids:
        if out_of_budget():
            return None
        best: tuple[int, str, int] | None = None
        base = {}
        for vid, seq in work.items():
            if out_of_budget():
                return None
            if not seq:
                base[vid] = 0  # 空路线：只有新任务时的行驶时间
                ev = evaluate_route(scn, vid, [oid])
                if ev.ok:
                    best = (ev.travel_time_s, vid, 0) if best is None \
                        else min(best, (ev.travel_time_s, vid, 0))
                continue
            ev0 = evaluate_route(scn, vid, list(seq))
            if not ev0.ok:
                continue
            base[vid] = ev0.travel_time_s
            for pos in range(len(seq) + 1):
                if out_of_budget():
                    return None
                cand = seq[:pos] + [oid] + seq[pos:]
                ev = evaluate_route(scn, vid, cand)
                if not ev.ok:
                    continue
                delta = ev.travel_time_s - base[vid]
                if best is None or delta < best[0]:
                    best = (delta, vid, pos)
        if best is None:
            return None
        _, vid, pos = best
        work[vid] = work[vid][:pos] + [oid] + work[vid][pos:]
    return work


# ---- 锁定约束下的可移动性 ----

def _movable(scn: Scenario, oid: str) -> bool:
    """相对顺序锁成员一律禁止移动（保守规则：减少候选多样性但不违反硬锁；
    锁内平移/换位放松属 P2 改进项，另行评估）。"""
    for lock in scn.locks.relative_order_locks:
        if oid in lock.order_ids:
            return False
    return True


def _allowed_vehicles(scn: Scenario, oid: str) -> set[str]:
    """换车锁任务只允许在锁定车内移动；其余任务可去任何车（含未启用）。"""
    locked_to = scn.locks.assignment_locks.get(oid)
    if locked_to is not None:
        return {locked_to}
    return {v.vehicle_id for v in scn.vehicles}


# ---- 候选生成 ----

def _archive_candidate(
    scn: Scenario,
    seqs: dict[str, list[str]],
    archive: dict[tuple, Candidate],
    out_of_budget=lambda: False,
    max_archive_size: int = DEFAULT_MAX_ARCHIVE,
) -> Candidate | None:
    key = _key(seqs)
    if key in archive:
        return archive[key]
    if len(archive) >= max_archive_size:
        return None  # 调用方记录 archive_full
    if out_of_budget():
        return None
    evaled = _evaluate(scn, seqs)
    if evaled is None:
        return None
    routes, total = evaled
    cand = Candidate(
        key=key, routes=routes, total_travel_s=total,
        perturbation=pert_mod.measure(scn, routes),
    )
    archive[key] = cand
    return cand


def _seed_candidates(
    scn: Scenario, archive: dict[tuple, Candidate], diagnostics: dict,
    out_of_budget, deadline: float, max_archive_size: int,
) -> None:
    """种子：插单基线；失败则单旧任务搬动枚举后重试插单。共用绝对截止。"""
    new_ids = [o.order_id for o in scn.orders if o.is_new]

    # 叶子调用换算一次剩余毫秒（insert 自带独立时钟）；其余各处一律用绝对截止
    remaining_ms = max(int((deadline - time.monotonic()) * 1000), 1)
    routes, diag = insert_mod.insert_new_orders(scn, time_limit_ms=remaining_ms)
    diagnostics["insert_seed"] = diag
    if routes:
        seqs = _initial_sequences(scn)
        for r in routes:
            seqs[r.vehicle_id] = list(r.order_ids)
        before = len(archive)
        _archive_candidate(scn, seqs, archive,
                           max_archive_size=max_archive_size)
        if routes and len(archive) == before and _key(seqs) not in archive:
            diagnostics["insert_seed"]["archived"] = "archive_full"
        return

    # 插单失败：枚举"搬动一个旧任务"（换车或车内换位）后再插单
    diagnostics["relocate_seed"] = {"tried": 0, "seeds_found": 0}
    base = _initial_sequences(scn)
    for oid in [o.order_id for o in scn.orders if not o.is_new]:
        if out_of_budget():
            diagnostics["relocate_seed"]["stopped"] = "time_budget_exhausted"
            break
        if not _movable(scn, oid):
            continue
        home = next(vid for vid, seq in base.items() if oid in seq)
        if not new_ids:  # 无新任务时插单必"成功"，到不了这里；防御
            continue
        for vid in sorted(_allowed_vehicles(scn, oid)):
            for pos in range(len(base[vid]) + (0 if vid == home else 1)):
                if out_of_budget():
                    diagnostics["relocate_seed"]["stopped"] = "time_budget_exhausted"
                    return
                if len(archive) >= max_archive_size:
                    diagnostics["relocate_seed"]["stopped"] = "archive_full"
                    return
                seqs = {v: list(s) for v, s in base.items()}
                seqs[home].remove(oid)
                seqs[vid] = seqs[vid][:pos] + [oid] + seqs[vid][pos:]
                diagnostics["relocate_seed"]["tried"] += 1
                grown = _greedy_insert(scn, seqs, new_ids, out_of_budget=out_of_budget)
                if grown is None:
                    if out_of_budget():
                        diagnostics["relocate_seed"]["stopped"] = "time_budget_exhausted"
                        return
                    continue
                cand = _archive_candidate(
                    scn, grown, archive, max_archive_size=max_archive_size)
                if cand is not None:
                    diagnostics["relocate_seed"]["seeds_found"] += 1
                elif len(archive) >= max_archive_size:
                    diagnostics["relocate_seed"]["stopped"] = "archive_full"
                    return


def _iter_neighbors(
    scn: Scenario, seqs: dict[str, list[str]], out_of_budget=lambda: False
):
    """relocate + swap 邻域（惰性生成，尊重锁定，逐邻居检查截止）。

    P1 修正：新任务 relocate 的目标 = scn.vehicles 全部车辆（含未启用/
    空路线车辆），修复此前局部候选无法把新任务移入空闲车辆的缺口。
    """
    movable_old = [o.order_id for o in scn.orders
                   if not o.is_new and _movable(scn, o.order_id)]
    new_ids = [o.order_id for o in scn.orders if o.is_new]
    all_vehicles = [v.vehicle_id for v in scn.vehicles]

    # relocate：旧任务（换车/换位）与新任务（只影响成本，不违反"新任务无原车"）
    for oid in movable_old + new_ids:
        home = next(vid for vid, seq in seqs.items() if oid in seq)
        targets = (sorted(_allowed_vehicles(scn, oid))
                   if oid in movable_old else all_vehicles)
        for vid in targets:
            dst = seqs.get(vid, [])  # 未启用车辆视为空路线
            for pos in range(len(dst) + (0 if vid == home else 1)):
                if out_of_budget():
                    return
                src = seqs[home]
                cur = src.index(oid)
                if vid == home and (pos == cur or pos == cur + 1):
                    continue  # 原位或等效原位
                cand = {v: list(s) for v, s in seqs.items()}
                cand[home].remove(oid)
                cand[vid] = cand.get(vid, [])[:pos] + [oid] + cand.get(vid, [])[pos:]
                yield cand

    # swap：两个可移动旧任务互换（新任务不参与，避免无意义组合爆炸）
    for i, a in enumerate(movable_old):
        va = next(vid for vid, seq in seqs.items() if a in seq)
        ia = seqs[va].index(a)
        for b in movable_old[i + 1:]:
            if b not in _allowed_vehicles(scn, a) and \
                    scn.locks.assignment_locks.get(a) is not None:
                continue
            if a not in _allowed_vehicles(scn, b) and \
                    scn.locks.assignment_locks.get(b) is not None:
                continue
            vb = next(vid for vid, seq in seqs.items() if b in seq)
            ib = seqs[vb].index(b)
            cand = {v: list(s) for v, s in seqs.items()}
            cand[va][ia], cand[vb][ib] = b, a
            # swap 后检查锁定车约束仍然成立（互换可能把车锁任务带出锁定车）
            if scn.locks.assignment_locks.get(a, va) != va or \
                    scn.locks.assignment_locks.get(b, vb) != vb:
                continue
            yield cand


def _local_search(
    scn: Scenario,
    archive: dict[tuple, Candidate],
    out_of_budget,
    diagnostics: dict,
    beam_width: int,
    max_neighbors: int,
    max_archive_size: int,
) -> None:
    """从当前候选出发迭代扩展邻域（beam 限宽），直到截止/上限/邻域穷尽。

    stop_reason 记录停止原因：time_budget_exhausted / archive_full /
    neighborhood_budget / frontier_exhausted。
    """
    stats = {"iterations": 0, "evaluated": 0, "stop_reason": "frontier_exhausted"}
    diagnostics["local_search"] = stats
    frontier = sorted(archive.values(), key=lambda c: c.key)[:beam_width]
    seen = set(archive)
    while frontier and not out_of_budget():
        stats["iterations"] += 1
        next_frontier: list[Candidate] = []
        for cand in frontier:
            seqs = {r.vehicle_id: list(r.order_ids) for r in cand.routes}
            nb_count = 0
            for nb in _iter_neighbors(scn, seqs, out_of_budget):
                nb_count += 1
                if nb_count > max_neighbors:
                    stats["stop_reason"] = "neighborhood_budget"
                    return
                stats["evaluated"] += 1
                if stats["evaluated"] % 64 == 0 and out_of_budget():
                    stats["stop_reason"] = "time_budget_exhausted"
                    return
                key = _key(nb)
                if key in seen:
                    continue
                seen.add(key)
                if len(archive) >= max_archive_size:
                    stats["stop_reason"] = "archive_full"
                    return
                evaled = _evaluate(scn, nb)
                if evaled is None:
                    continue
                routes, total = evaled
                new_cand = Candidate(
                    key=key, routes=routes, total_travel_s=total,
                    perturbation=pert_mod.measure(scn, routes),
                )
                archive[key] = new_cand
                next_frontier.append(new_cand)
        if not next_frontier:
            break
        # beam：下一轮frontier按 (扰动, 成本) 择优限宽
        frontier = sorted(
            next_frontier,
            key=lambda c: (c.perturbation.vehicle_changes,
                           c.perturbation.adjacency_breaks,
                           c.total_travel_s, c.key))[:beam_width]
        stats["stop_reason"] = "time_budget_exhausted" if out_of_budget() \
            else "frontier_exhausted"


# ---- 选择规则 ----

def _select(
    candidates: list[Candidate], diagnostics: dict,
    cost_cap_ratio: float, reference_best_cost: float | None,
) -> tuple[Candidate | None, Candidate | None, dict]:
    """返回 (recommended, pool_best, selection 回显)。

    业务上限 = cost_cap_ratio × reference_best_cost；reference 未给出时
    如实回退为池内最优并标注。上限内无候选 → 推荐池内最低成本候选，
    selection_status="no_candidate_within_cost_cap"。
    """
    if not candidates:
        return None, None, {"selection_status": "no_candidates"}
    pool_best = min(candidates, key=lambda c: (c.total_travel_s, c.key))
    ref = reference_best_cost if reference_best_cost is not None \
        else pool_best.total_travel_s
    ref_source = "reference_results" if reference_best_cost is not None \
        else "pool_fallback(未提供跨方法参考，上限仅相对本方法候选池)"
    cap = cost_cap_ratio * ref
    pool = [c for c in candidates if c.total_travel_s <= cap + 1e-9]
    if pool:
        low = min(pool, key=lambda c: (
            c.perturbation.vehicle_changes,
            c.perturbation.adjacency_breaks,
            c.total_travel_s, c.key))
        status, within = "selected", True
    else:
        low = pool_best
        status, within = "no_candidate_within_cost_cap", False
    selection = {
        "selection_status": status,
        "within_reference_cap": within,
        "pool_best_cost_s": pool_best.total_travel_s,
        "reference_best_cost_s": ref,
        "reference_source": ref_source,
        "cost_cap_ratio": cost_cap_ratio,
        "cost_cap_s": round(cap, 3),
        "pool_size": len(pool),
        "candidates_total": len(candidates),
    }
    diagnostics["selection"] = selection
    return low, pool_best, selection


def _reference_best_cost(scn: Scenario, reference_results) -> float | None:
    """从调用方给的跨方法参考方案中取"已通过独立校验的最低成本"。

    reference_results：可迭代的 (name, routes) 或仅 routes；全部经
    validate_solution 复核后才参与，未通过者跳过并在 diagnostics 记录。
    """
    best = None
    details = []
    for item in reference_results or []:
        if isinstance(item, tuple):
            name, routes = item
        else:
            name, routes = "reference", item
        val = validate_solution(scn, list(routes))
        if not val.ok:
            details.append({"name": name, "used": False,
                            "issues": [i.code for i in val.issues]})
            continue
        travel = sum(
            evaluate_route(scn, r.vehicle_id, list(r.order_ids)).travel_time_s
            for r in routes)
        details.append({"name": name, "used": True, "total_travel_time_s": travel})
        if best is None or travel < best:
            best = travel
    return best, details


def _audit_output(
    scn: Scenario, recommended: Candidate | None,
    pool_best: Candidate | None, candidates: list[Candidate],
    selection: dict, top_k: int = 5,
) -> tuple[dict, ...]:
    """对外审计输出：推荐、池内最低成本、上限边界、Pareto 前沿、Top K。

    每条含稳定 candidate_id + 完整路线 + 指标 + validation_ok，
    可由 ID 在 outcome.candidates 中重建复核。
    """
    def audit(c: Candidate) -> dict:
        val = validate_solution(scn, list(c.routes))
        return {
            "candidate_id": c.candidate_id,
            "role": None,  # 由调用处填
            "routes": [{"vehicle_id": r.vehicle_id,
                        "order_ids": list(r.order_ids),
                        "return_s": r.return_s} for r in c.routes],
            "total_travel_time_s": c.total_travel_s,
            "vehicle_changes": c.perturbation.vehicle_changes,
            "adjacency_breaks": c.perturbation.adjacency_breaks,
            "validation_ok": val.ok,
            "validation_issues": [i.code for i in val.issues],
        }

    out: list[dict] = []
    seen_ids: set[str] = set()

    def push(c: Candidate | None, role: str) -> None:
        if c is None or c.candidate_id in seen_ids:
            return
        seen_ids.add(c.candidate_id)
        d = audit(c)
        d["role"] = role
        out.append(d)

    push(recommended, "recommended")
    push(pool_best, "pool_best_cost")
    cap = selection.get("cost_cap_s")
    if cap is not None:
        within = [c for c in candidates if c.total_travel_s <= cap + 1e-9]
        if within:
            boundary = max(within, key=lambda c: c.total_travel_s)
            push(boundary, "cap_boundary")
    # Pareto 前沿：(换车, 相邻破坏, 成本) 互不支配
    pareto: list[Candidate] = []
    for c in sorted(candidates, key=lambda c: (c.total_travel_s, c.key)):
        dominated = any(
            (p.perturbation.vehicle_changes <= c.perturbation.vehicle_changes
             and p.perturbation.adjacency_breaks <= c.perturbation.adjacency_breaks
             and p.total_travel_s <= c.total_travel_s and p is not c)
            for p in pareto)
        if not dominated:
            pareto = [p for p in pareto if not (
                c.perturbation.vehicle_changes <= p.perturbation.vehicle_changes
                and c.perturbation.adjacency_breaks <= p.perturbation.adjacency_breaks
                and c.total_travel_s <= p.total_travel_s)]
            pareto.append(c)
    for c in sorted(pareto, key=lambda c: (c.perturbation.vehicle_changes,
                                           c.perturbation.adjacency_breaks,
                                           c.total_travel_s))[:top_k]:
        push(c, "pareto")
    # Top K（按选择规则排序）
    rest = sorted(candidates, key=lambda c: (
        c.perturbation.vehicle_changes, c.perturbation.adjacency_breaks,
        c.total_travel_s, c.key))
    for c in rest:
        if len(out) >= top_k + 3:
            break
        push(c, "top_k")
    return tuple(out)


def solve_low_disruption(
    scn: Scenario, time_limit_ms: int = 10_000,
    *,
    cost_cap_ratio: float = DEFAULT_COST_CAP,
    reference_results=None,
    max_archive_size: int = DEFAULT_MAX_ARCHIVE,
    beam_width: int = DEFAULT_BEAM_WIDTH,
    max_neighbors_per_candidate: int = DEFAULT_MAX_NEIGHBORS,
) -> LowDisruptionOutcome:
    """入口：生成候选、局部搜索、按选择规则推荐。全程独立重算。

    cost_cap_ratio 范围 [0.5, 2.0]；reference_results 为跨方法已验证方案
    （见 _reference_best_cost）。预算为绝对截止时刻，只扣一次。
    """
    if not (RATIO_MIN <= cost_cap_ratio <= RATIO_MAX):
        raise ValueError(
            f"E_COST_CAP_RATIO: {cost_cap_ratio} 超出范围 [{RATIO_MIN}, {RATIO_MAX}]")
    started = time.monotonic()
    deadline = started + time_limit_ms / 1000.0

    def out_of_budget() -> bool:
        return time.monotonic() >= deadline

    archive: dict[tuple, Candidate] = {}
    diagnostics: dict = {"time_limit_ms": time_limit_ms,
                         "absolute_deadline_s": round(deadline, 6)}
    _seed_candidates(scn, archive, diagnostics, out_of_budget, deadline,
                     max_archive_size)
    if archive:
        _local_search(scn, archive, out_of_budget, diagnostics,
                      beam_width, max_neighbors_per_candidate, max_archive_size)
    candidates = sorted(archive.values(), key=lambda c: c.key)

    reference_best, ref_details = _reference_best_cost(scn, reference_results)
    diagnostics["reference_details"] = ref_details
    low, best, selection = _select(
        list(candidates), diagnostics, cost_cap_ratio, reference_best)
    audit = _audit_output(scn, low, best, list(candidates), selection)
    diagnostics["elapsed_ms"] = int((time.monotonic() - started) * 1000)
    return LowDisruptionOutcome(
        recommended=low, candidates=tuple(candidates),
        best_cost_candidate=best, audit=audit, selection=selection,
        diagnostics=diagnostics,
    )
