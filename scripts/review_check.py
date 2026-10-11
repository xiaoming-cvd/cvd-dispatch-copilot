"""一键审查入口（跨电脑审查包配套，任务书审查意见第 8 章）。

用法（项目根目录）：
    .venv\\Scripts\\python.exe scripts\\review_check.py [--skip-copt]

依次执行并输出 reports/review_summary.json：
1. 依赖检查（pydantic/pytest/ortools 必需；COPT 可选，--skip-copt 时跳过）
2. manifest 与 registry 登记一致性（哈希、split、文件存在性）
3. 基础算例内容指纹（调用 check_base_fingerprint 的逻辑结果）
4. 全量 pytest
5. 小样本 smoke（insert / rebuild / low_disruption；rebuild 始终验证默认
   OR-Tools 后端，有 COPT 时另跑显式 --backend copt 并校验结果中的后端）
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


def assert_backend(result_file: Path, expected: str) -> dict:
    """读取 smoke 结果 JSON，校验实际后端与期望一致（防"命名 copt 实为 ortools"）。

    返回与 run() 同形的 dict，returncode 0 = 后端匹配，1 = 不匹配/文件缺失。
    """
    try:
        data = json.loads(result_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"cmd": f"assert_backend({result_file.name}, {expected})",
                "returncode": 1, "elapsed_s": 0.0,
                "stdout_tail": f"结果文件不可读: {exc}", "stderr_tail": ""}
    backend = (data.get("metrics") or {}).get("backend")
    requested = (data.get("metrics") or {}).get("backend_requested")
    solver_version = data.get("solver_version", "")
    ok = (backend == expected
          or (expected == "copt" and "copt" in solver_version.lower()))
    result_file.unlink(missing_ok=True)
    detail = (f"expected={expected} backend={backend} "
              f"backend_requested={requested} solver_version={solver_version}")
    return {"cmd": f"assert_backend({expected})", "returncode": 0 if ok else 1,
            "elapsed_s": 0.0, "stdout_tail": detail, "stderr_tail": ""}


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

    # 5. 小样本 smoke
    # rebuild 默认 OR-Tools 冒烟在两种模式下都必须跑（审查 P0-1：--skip-copt
    # 不等于可以不验证 rebuild 的默认后端）；有 COPT 时另跑显式 copt 冒烟，
    # 两种模式都校验结果中的实际后端，防止"命名 copt 实为 ortools"假阳性。
    smoke = {}
    smoke["insert"] = run([PY, "scripts/solve_cli.py",
                           "--input", "data/sample/sample_small.json",
                           "--method", "insert", "--time-limit-ms", "5000"])
    smoke["low_disruption"] = run([PY, "scripts/solve_cli.py",
                                   "--input", "data/sample/sample_small.json",
                                   "--method", "low_disruption",
                                   "--time-limit-ms", "5000"])
    ortools_out = ROOT / "reports" / "_smoke_rebuild_ortools.json"
    smoke["rebuild_ortools"] = run([PY, "scripts/solve_cli.py",
                                    "--input", "data/sample/sample_small.json",
                                    "--method", "rebuild", "--time-limit-ms", "5000",
                                    "--out", str(ortools_out)])
    smoke["rebuild_ortools_backend"] = assert_backend(ortools_out, "ortools")
    if not args.skip_copt:
        copt_out = ROOT / "reports" / "_smoke_rebuild_copt.json"
        smoke["rebuild_copt"] = run([PY, "scripts/solve_cli.py",
                                     "--input", "data/sample/sample_small.json",
                                     "--method", "rebuild", "--time-limit-ms", "5000",
                                     "--backend", "copt",
                                     "--out", str(copt_out)])
        smoke["rebuild_copt_backend"] = assert_backend(copt_out, "copt")
    summary["steps"]["smoke"] = smoke

    # 6. 反例穷举核对（R01/R02，显式 COPT 后端）
    if not args.skip_copt:
        summary["steps"]["exhaustive_counterexamples"] = run(
            [PY, "scripts/exhaustive_check.py"])

    rc_steps = [s for s in summary["steps"].values()
                if isinstance(s, dict) and "returncode" in s]
    rc_smoke = list(smoke.values())
    all_rc = [s["returncode"] for s in rc_steps + rc_smoke]
    if any(rc not in (0, 2) for rc in all_rc):
        summary["overall"] = "fail: 见各步骤 returncode"
    elif any(rc == 2 for rc in all_rc):
        # 退出码 2 = 该步显式跳过/无源（如缺原始算例、无 COPT），
        # 不算 fail，但也不能报全量 pass（防"空目录假通过"）
        skipped = [s["cmd"] for s in rc_steps + rc_smoke if s["returncode"] == 2]
        summary["overall"] = ("partial: 以下步骤显式跳过/无源，未完全验证 "
                              + "; ".join(skipped))
    else:
        summary["overall"] = "pass"
    OUT.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"review_summary -> {OUT}")
    print(f"overall: {summary['overall']}")
    return 0 if summary["overall"].startswith("pass") else 1


if __name__ == "__main__":
    sys.exit(main())
