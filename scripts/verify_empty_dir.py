"""空目录无原件验收模拟（三次复审 P0 要求的自动化回归）。

流程：
1. scripts/build_delivery_zip.py 按白名单打出交付 ZIP（与交付同一条路径）；
2. 解压到全新临时目录——该目录**没有 data/raw**，等同未恢复原始数据的接收机；
3. 在解压目录运行 `pytest -m "not delivery_sim" -q`：
   断言收集阶段无错误、源数据用例显式 skip（不是 fail）；
4. 在解压目录运行 `review_check.py --skip-copt`：
   断言基础 smoke 全通过、fingerprint/source_data 退出码 2（partial），
   overall 为 partial 而非 pass/fail；
5. 全量日志写入 reports/verify_empty_dir_log.txt。

退出码：0 = 模拟验收通过；1 = 任一断言失败（日志见 reports/verify_empty_dir_log.txt
或 stdout）。本脚本不改动开发机任何文件（ZIP 与解压目录均在系统临时区）。
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "reports" / "verify_empty_dir_log.txt"
PY = sys.executable


def run(cmd: list[str], cwd: Path) -> tuple[int, str]:
    t0 = time.monotonic()
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=1800)
    return p.returncode, (
        f"$ {' '.join(cmd)}  (cwd={cwd}, {time.monotonic() - t0:.1f}s)\n"
        f"exit={p.returncode}\n--- stdout tail ---\n{p.stdout[-4000:]}\n"
        f"--- stderr tail ---\n{p.stderr[-2000:]}\n")


def main() -> int:
    lines: list[str] = [
        f"=== verify_empty_dir（交付包空目录无原件模拟）{time.strftime('%Y-%m-%d %H:%M:%S')} ==="]
    failures: list[str] = []

    with tempfile.TemporaryDirectory(prefix="cvd_empty_") as td:
        td = Path(td)
        zpath = td / "delivery.zip"
        xdir = td / "extracted"

        rc, log = run([PY, "scripts/build_delivery_zip.py", "--out", str(zpath)], ROOT)
        lines.append(log)
        if rc != 0 or not zpath.is_file():
            failures.append("白名单打包失败")

        if not failures:
            xdir.mkdir()
            with zipfile.ZipFile(zpath) as z:
                z.extractall(xdir)
            (xdir / "data" / "raw").mkdir(parents=True, exist_ok=True)
            lines.append("已解压交付 ZIP 到空目录（data/raw 为空，等同未恢复原件的接收机）")

            # --- 3. pytest：收集无错误、源数据用例显式 skip ---
            rc, log = run([PY, "-m", "pytest", "-m", "not delivery_sim", "-q",
                           "tests"], xdir)
            lines.append(log)
            if rc != 0:
                failures.append("空目录 pytest 未全过（收集错误或基础用例失败）")
            if "skipped" not in log:
                failures.append("未见源数据用例 skip——缺原件时应显式跳过")

            # --- 4. review_check --skip-copt：partial + 基础 smoke 通过 ---
            rc, log = run([PY, "scripts/review_check.py", "--skip-copt"], xdir)
            lines.append(log)
            summary = xdir / "reports" / "review_summary.json"
            if not summary.is_file():
                failures.append("review_check 未产出 review_summary.json")
            else:
                s = json.loads(summary.read_text(encoding="utf-8"))
                if not str(s.get("overall", "")).startswith("partial"):
                    failures.append(f"overall 应为 partial，实际 {s.get('overall')}")
                for name, step in s["steps"]["smoke"].items():
                    if step["returncode"] != 0:
                        failures.append(f"smoke.{name} rc={step['returncode']} != 0")
                for key in ("fingerprint", "source_data"):
                    st = s["steps"].get(key, {})
                    if st.get("returncode") != 2:
                        failures.append(f"{key} 缺原件时应 rc=2，实际 {st.get('returncode')}")
                if rc == 0:
                    failures.append("partial 状态下 review_check 退出码应为 1")

    LOG.parent.mkdir(exist_ok=True)
    text = "\n".join(lines) + "\n\n结论: " + (
        "PASS（空目录无原件可收集、基础审查通过、总状态 partial）"
        if not failures else "FAIL: " + "；".join(failures))
    LOG.write_text(text, encoding="utf-8")
    print(text[-3000:])
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
