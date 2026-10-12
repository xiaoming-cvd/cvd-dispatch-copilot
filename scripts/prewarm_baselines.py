"""批量预热：为注册表内全部场景生成 baseline 原计划（幂等，可反复执行）。

用途（2026-10-12 队长决策）：场景列表"事前参考"列要在进入页面时就能看到
每个场景的原计划成本 ms，因此交付/演示前先把所有场景的 baseline 跑完。

行为：
- 跳过已物化场景（除非 --force；force 会按 handle_baseline 同款语义
  版本单调递增，但不保留已确认锁——本脚本不用于已确认过的场景）；
- 与 Web 链路同一套代码（materialize_scenario + ScenarioIndex 原子写），
  逐路线独立重算成本并打印，便于核对；
- 单个场景超时/失败不中断整体，最后汇总成功/失败/跳过。

用法：
    .venv\\Scripts\\python.exe scripts\\prewarm_baselines.py [--force]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.api.app import ScenarioIndex, Settings          # noqa: E402
from src.domain.models import Scenario, ScenarioDraft, input_hash  # noqa: E402
from src.llm.config import LLMConfig                     # noqa: E402
from src.solver.original import materialize_scenario     # noqa: E402
from src.validation.checks import validate_draft         # noqa: E402
from src.validation.timetable import evaluate_original_plan  # noqa: E402

BUDGET_MS = 60_000


def main() -> int:
    ap = argparse.ArgumentParser(description="批量生成场景 baseline 原计划")
    ap.add_argument("--force", action="store_true",
                    help="重建已物化场景（版本+1，不保留已确认锁）")
    args = ap.parse_args()

    settings = Settings(project_root=ROOT, llm=LLMConfig(provider="mock"))
    index = ScenarioIndex(settings)
    rows = index.list()
    print(f"注册场景 {len(rows)} 个（含上传），预算 {BUDGET_MS}ms/个")

    ok, failed, skipped = [], [], []
    for row in rows:
        sid = row["scenario_id"]
        if row.get("materialized") and not args.force:
            skipped.append(sid)
            continue
        try:
            doc = index.load_doc(sid)
            draft = ScenarioDraft(**doc["draft"])
            report = validate_draft(draft)
            if not report.ok:
                failed.append((sid, "草稿校验未通过："
                               + "; ".join(i.code for i in report.issues)))
                continue
            meta = doc.get("_meta") or {}
            t0 = time.monotonic()
            scn = materialize_scenario(
                draft, lock_ratio=float(meta.get("lock_ratio_target") or 0.0),
                lock_seed=int(meta.get("seed") or 0),
                time_limit_ms=BUDGET_MS)
            elapsed = int((time.monotonic() - t0) * 1000)
            if elapsed > BUDGET_MS:
                failed.append((sid, f"生成超时 {elapsed}ms（超时≠无解）"))
                continue
            prev = 0
            mat_path = settings.materialized_dir / f"{sid}.json"
            if mat_path.is_file() and args.force:
                try:
                    prev = Scenario(**json.loads(
                        mat_path.read_text(encoding="utf-8"))).version
                except Exception:
                    prev = 0
                scn = scn.model_copy(update={"version": prev + 1})
            index.save_materialized(scn)
            cost, details = evaluate_original_plan(scn)
            bad = [d for d in details if not d["used"]]
            status = "OK" if cost is not None else "原计划自身违规"
            print(f"  {sid}: v{scn.version} 成本={cost} 模型秒 "
                  f"({elapsed}ms, {len(details)} 条路线, {status})")
            ok.append(sid)
        except Exception as exc:  # 单场景失败不中断
            failed.append((sid, f"{type(exc).__name__}: {exc}"))
            print(f"  {sid}: 失败 {type(exc).__name__}: {exc}")

    print(f"\n汇总：成功 {len(ok)}，跳过(已物化) {len(skipped)}，"
          f"失败 {len(failed)}")
    for sid, why in failed:
        print(f"  失败 {sid}: {why}")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
