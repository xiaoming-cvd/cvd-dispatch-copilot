"""阶段 2 命令行入口：输入场景（完整 Scenario 或 ScenarioDraft）→ 规范 JSON 结果。

用法（Windows）：
    .venv\\Scripts\\python.exe scripts\\solve_cli.py --input <文件> --method insert|rebuild|low_disruption
        [--time-limit-ms 10000] [--out <结果文件>] [--lock-ratio 0.2] [--lock-seed 0]
        [--write-scenario <场景文件>]

输入文件两种形态（由 JSON 顶层键自动识别）：
- "scenario"：完整场景，直接求解；
- "draft"：待生成原计划的草稿（阶段 1 产物），先经 materialize_scenario
  生成并冻结原计划、落实锁定，再求解；可用 --lock-ratio 覆盖 _meta 目标。

输出（stdout 或 --out 文件）：SolveResult 规范 JSON，含状态、逐站时刻表
(schedule)、独立校验报告(validation)、指标(metrics)、警告与假设。
退出码：0 = feasible；2 = invalid_input；3 = no_feasible_found；
4 = proven_infeasible；5 = error；1 = 用法/文件错误。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pydantic import ValidationError  # noqa: E402

from src.domain.models import Scenario, ScenarioDraft  # noqa: E402
from src.solver.original import materialize_scenario  # noqa: E402
from src.solver.pipeline import solve  # noqa: E402

EXIT_CODES = {
    "feasible": 0,
    "invalid_input": 2,
    "no_feasible_found": 3,
    "proven_infeasible": 4,
    "error": 5,
}


def _load(path: Path) -> tuple[Scenario | None, ScenarioDraft | None, dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if "scenario" in data:
        return Scenario(**data["scenario"]), None, data.get("_meta", {})
    if "draft" in data:
        return None, ScenarioDraft(**data["draft"]), data.get("_meta", {})
    raise ValueError("E_INPUT: 文件既无 'scenario' 也无 'draft' 顶层键")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="城配调度副驾 · 阶段 2 求解 CLI")
    ap.add_argument("--input", required=True, type=Path)
    ap.add_argument("--method", required=True,
                    choices=["insert", "rebuild", "low_disruption"])
    ap.add_argument("--time-limit-ms", type=int, default=10_000)
    ap.add_argument("--cost-cap-ratio", type=float, default=None,
                    help="low_disruption 成本上限比例（0.5–2.0，默认 1.10）")
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--lock-ratio", type=float, default=None,
                    help="覆盖草稿 _meta.lock_ratio_target")
    ap.add_argument("--lock-seed", type=int, default=None,
                    help="覆盖草稿 _meta.seed（锁定落实种子）")
    ap.add_argument("--write-scenario", type=Path, default=None,
                    help="草稿求解时把升格后的完整场景写到该文件")
    args = ap.parse_args(argv)

    try:
        scn, draft, meta = _load(args.input)
    except (OSError, json.JSONDecodeError, ValueError, ValidationError) as exc:
        print(json.dumps({"status": "error", "warnings": [f"{type(exc).__name__}: {exc}"]},
                         ensure_ascii=False, indent=2))
        return 1

    if draft is not None:
        lock_ratio = args.lock_ratio
        if lock_ratio is None:
            lock_ratio = float(meta.get("lock_ratio_target") or 0.0)
        lock_seed = args.lock_seed
        if lock_seed is None:
            lock_seed = int(meta.get("seed") or 0)
        try:
            scn = materialize_scenario(
                draft, lock_ratio=lock_ratio, lock_seed=lock_seed,
                time_limit_ms=args.time_limit_ms)
        except ValueError as exc:
            print(json.dumps({"status": "error", "warnings": [str(exc)]},
                             ensure_ascii=False, indent=2))
            return 1
        if args.write_scenario:
            args.write_scenario.parent.mkdir(parents=True, exist_ok=True)
            args.write_scenario.write_text(json.dumps(
                {"_meta": {**meta, "materialized": True,
                           "pending_original_plan": False,  # D4：状态不再冲突
                           "original_plan_generated": True,
                           "note": (meta.get("note", "") + "；已由 materialize_scenario "
                                    "生成并冻结原计划（计划内仅含旧任务）").strip("；")},
                 "scenario": scn.model_dump(mode="json")},
                ensure_ascii=False, indent=2), encoding="utf-8")

    outcome = solve(scn, args.method, time_limit_ms=args.time_limit_ms,
                    cost_cap_ratio=args.cost_cap_ratio)
    payload = outcome.result.model_dump(mode="json")
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
    print(text)
    return EXIT_CODES[payload["status"]]


if __name__ == "__main__":
    raise SystemExit(main())
