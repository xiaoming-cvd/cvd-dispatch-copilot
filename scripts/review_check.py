"""一键审查入口（跨电脑审查包配套，任务书审查意见第 8 章）。

用法（项目根目录）：
    .venv\\Scripts\\python.exe scripts\\review_check.py [--skip-copt]

依次执行并输出 reports/review_summary.json：
1. 依赖检查（pydantic/pytest/ortools 必需；COPT 可选，--skip-copt 时跳过）
2. manifest 与 registry 登记一致性（哈希、split、文件存在性）
3. 基础算例内容指纹（调用 check_base_fingerprint 的逻辑结果）
4. 全量 pytest
5. 小样本三方法 smoke（insert / rebuild[可选] / low_disruption）
6. 审查反例回归（R01/R02 穷举核对 + P0 校验反例）

不自动安装全局软件；失败不修改测试；所有数字来自程序输出。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable
OUT = ROOT / "reports" / "review_summary.json"


def run(cmd: list[str]) -> dict:
    t0 = time.monotonic()
    p = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return {"cmd": " ".join(cmd), "returncode": p.returncode,
            "elapsed_s": round(time.monotonic() - t0, 2),
            "stdout_tail": p.stdout[-2000:], "stderr_tail": p.stderr[-1000:]}


def main() -> int:
    ap = argparse.ArgumentParser(description="城配调度副驾 · 一键审查")
    ap.add_argument("--skip-copt", action="store_true",
                    help="无 COPT 的基础模式：跳过 rebuild 相关检查")
    args = ap.parse_args()

    summary: dict = {"started": time.strftime("%Y-%m-%d %H:%M:%S"),
                     "skip_copt": args.skip_copt, "steps": {}}

    # 1. 依赖
    deps = {}
    for mod in ["pydantic", "pytest", "ortools"]:
        try:
            m = __import__(mod)
            deps[mod] = getattr(m, "__version__", "installed")
        except ImportError:
            deps[mod] = "MISSING"
    try:
        import coptpy
        deps["coptpy"] = getattr(coptpy, "__version__", "installed")
    except ImportError:
        deps["coptpy"] = "MISSING(optional)"
    summary["steps"]["dependencies"] = deps
    if any(v == "MISSING" for v in deps.values()):
        summary["overall"] = "fail: 必需依赖缺失"
        OUT.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        return 1

    # 2. 登记一致性
    summary["steps"]["manifest_registry_check"] = run(
        [PY, "-c", (
            "import json,sys;from pathlib import Path;sys.path.insert(0,'.');"
            "from src.domain.models import input_hash, ScenarioDraft;"
            "r=json.loads(Path('data/processed/scenarios/registry.json').read_text(encoding='utf-8'));"
            "base=Path('data/processed/scenarios');bad=[];"
            "[bad.append(s['scenario_id']) for s in r['scenarios'] if not (base/s['file'].replace('scenarios/','',1)).exists() or input_hash(ScenarioDraft(**json.loads((base/s['file'].replace('scenarios/','',1)).read_text(encoding='utf-8'))['draft']))!=s['draft_input_hash']];"
            "print('checked',len(r['scenarios']),'mismatch',len(bad),bad);"
            "sys.exit(1 if bad else 0)"
        )])

    # 3. 指纹检查
    summary["steps"]["fingerprint"] = run([PY, "scripts/check_base_fingerprint.py"])

    # 4. 全量测试
    summary["steps"]["pytest"] = run([PY, "-m", "pytest", "-q"])

    # 5. 小样本 smoke（含 COPT 与否两个层面）
    smoke = {}
    smoke["insert"] = run([PY, "scripts/solve_cli.py",
                           "--input", "data/sample/sample_small.json",
                           "--method", "insert", "--time-limit-ms", "5000"])
    smoke["low_disruption"] = run([PY, "scripts/solve_cli.py",
                                   "--input", "data/sample/sample_small.json",
                                   "--method", "low_disruption",
                                   "--time-limit-ms", "5000"])
    if not args.skip_copt:
        smoke["rebuild_copt"] = run([PY, "scripts/solve_cli.py",
                                     "--input", "data/sample/sample_small.json",
                                     "--method", "rebuild", "--time-limit-ms", "5000"])
    summary["steps"]["smoke"] = smoke

    # 6. 反例穷举核对（R01/R02）
    if not args.skip_copt:
        summary["steps"]["exhaustive_counterexamples"] = run(
            [PY, "scripts/exhaustive_check.py"])

    ok = all(s["returncode"] == 0 for s in summary["steps"].values()
             if isinstance(s, dict) and "returncode" in s) and \
         all(s["returncode"] == 0 for s in smoke.values())
    summary["overall"] = "pass" if ok else "fail: 见各步骤 returncode"
    OUT.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"review_summary -> {OUT}")
    print(f"overall: {summary['overall']}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
