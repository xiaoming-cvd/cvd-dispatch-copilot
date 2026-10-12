"""阶段 7 交付包打包器（二次复审 P1：白名单制，.gitignore 管不到 ZIP）。

用法（项目根）：
    .venv\\Scripts\\python.exe scripts\\build_delivery_zip.py [--out <zip路径>]

原则：
- 只打白名单内的文件/目录；运行态草稿（data/processed/drafts）、临时结果
  （reports/_tmp_*）、真实模型调用痕迹（stage4_real_case*）默认排除；
  确需保留的审查证据在 EVIDENCE 中白名单化并在 delivery_checklist 单列；
- 敏感字段扫描（高置信模式：sk- key、Bearer token）命中即拒绝打包；
- 打包后生成包内 manifest（逐文件 sha256），随即重开 ZIP 自动核对
  "manifest 声明的集合 == ZIP 实际条目集合、逐条 sha256 一致"，不一致则
  删除产物并以非零退出；
- 不删除、不改动开发机任何原件。

默认输出：C:\\Users\\tianj\\Desktop\\阶段审查包\\阶段7_交付包_<日期>.zip，
并同步更新同目录 校验和清单.txt 中对应行。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT_DIR = Path(r"C:\Users\tianj\Desktop\阶段审查包")

# ---------- 白名单 ----------
ROOT_FILES = [
    "README.md", "requirements.txt", ".env.example", ".gitignore",
    "pyproject.toml",
    "城配调度副驾.bundle",
]
DIRS = [
    "docs", "src", "frontend", "scripts", "tests",
    "data/sample", "data/manifests", "data/processed/scenarios",
    "reports/evaluation6",
]
REPORTS_FILES = [
    "reports/review_closure.md",
    "reports/review_closure_stage3.md",
    "reports/review_closure_stage4.md",
    "reports/review_closure_stage5.md",
    "reports/review_closure_stage6.md",
    "reports/kilby_data_quality.md",
    "reports/base_fingerprint.json",
    "reports/exhaustive_check.json",
    "reports/review_summary.json",
    "reports/stage7_review_full_summary.json",
    "reports/stage7_review_skip_summary.json",
    "reports/stage7_cleanenv_skip_summary.json",
    "reports/stage7_clean_env_log.txt",
    "reports/stage7_cmd_smoke.json",
    "reports/verify_empty_dir_log.txt",
    # baseline 300 秒诊断（独立成表）：修正后评测报告〇节引用其为
    # "初解构造失败（时间窗过紧）"表述的证据，必须随包可溯源
    "reports/baseline300/diagnostic_report.md",
    "reports/baseline300/results.json",
    "reports/baseline300/strategy_sweep.json",
    "reports/baseline300/summary.csv",
    # 时间窗放宽演示（8 个 -loose 场景）：证实根因与系统可用性，独立成表
    "reports/tw_relax_demo/README.md",
    "reports/tw_relax_demo/results.json",
    "reports/tw_relax_demo/summary.csv",
]

# 高置信敏感模式：命中即拒绝打包（允许文档中出现 "api_key" 等单词，
# 只抓形似真实凭证的值）
SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"Bearer\s+[A-Za-z0-9._\-]{20,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
]
SCAN_SUFFIX = {".json", ".txt", ".md", ".csv", ".py", ".js", ".html", ".example"}


def collect() -> list[Path]:
    files: set[Path] = set()
    for name in ROOT_FILES:
        p = ROOT / name
        if not p.is_file():
            sys.exit(f"FAIL: 白名单根文件缺失: {name}")
        files.add(p)
    for d in DIRS:
        base = ROOT / d
        if not base.is_dir():
            sys.exit(f"FAIL: 白名单目录缺失: {d}")
        files.update(p for p in base.rglob("*") if p.is_file())
    for name in REPORTS_FILES:
        p = ROOT / name
        if not p.is_file():
            sys.exit(f"FAIL: 白名单审查证据缺失: {name}")
        files.add(p)
    # 子目录排除（即使在白名单目录内）
    DROP_DIR = {"__pycache__", ".pytest_cache", "node_modules"}
    files = {p for p in files
             if not (set(p.relative_to(ROOT).parts[:-1]) & DROP_DIR)
             and not p.match("review_portable_*.zip")
             and not p.match("snapshot_*.zip")}
    return sorted(files)


def scan(files: list[Path]) -> None:
    hits = []
    for p in files:
        if p.suffix.lower() not in SCAN_SUFFIX:
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for pat in SECRET_PATTERNS:
            if pat.search(text):
                hits.append(str(p.relative_to(ROOT)))
                break
    if hits:
        sys.exit("FAIL: 敏感字段扫描命中，拒绝打包: " + ", ".join(hits))


def sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser(description="城配调度副驾 · 白名单交付打包")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()
    stamp = time.strftime("%Y%m%d")
    out = args.out or (DEFAULT_OUT_DIR / f"阶段7_交付包_{stamp}.zip")

    files = collect()
    scan(files)
    entries = []
    for p in files:
        b = p.read_bytes()
        entries.append({"path": p.relative_to(ROOT).as_posix(),
                        "sha256": sha256(b), "size": len(b)})
    manifest = {"packager": "build_delivery_zip/1.0",
                "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
                "whitelist_root": ROOT_FILES, "whitelist_dirs": DIRS,
                "evidence_files": REPORTS_FILES,
                "file_count": len(entries), "files": entries}

    if out.exists():
        out.unlink()
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("交付包_manifest.json",
                   json.dumps(manifest, ensure_ascii=False, indent=2))
        for p in files:
            z.write(p, p.relative_to(ROOT).as_posix())

    # ---- 自动核对：重开 ZIP，manifest 声明集合 == 实际条目、逐条 sha256 ----
    with zipfile.ZipFile(out) as z:
        actual = {i.filename: z.read(i.filename) for i in z.infolist()}
    declared = {e["path"]: e for e in entries}
    problems = []
    for name, blob in actual.items():
        if name == "交付包_manifest.json":
            continue
        if name not in declared:
            problems.append(f"ZIP 内多出的文件: {name}")
        elif sha256(blob) != declared[name]["sha256"]:
            problems.append(f"sha256 不一致: {name}")
    for name in declared:
        if name not in actual:
            problems.append(f"ZIP 内缺失: {name}")
    if problems:
        out.unlink()
        sys.exit("FAIL: 包内 manifest 自核未通过:\n  " + "\n  ".join(problems))

    final_sha = sha256(out.read_bytes())
    size = out.stat().st_size
    print(f"打包 {len(entries)} 个文件 -> {out}")
    print(f"manifest 自核通过；sha256 {final_sha}")
    print(f"size {size} bytes")

    # ---- 更新桌面校验和清单 ----
    listing = out.parent / "校验和清单.txt"
    if listing.is_file():
        lines = [l for l in listing.read_text(encoding="utf-8").splitlines()
                 if out.name not in l]
        lines.append(f"{final_sha}  {out.name}  ({size} bytes)")
        listing.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"校验和清单已更新: {listing}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
