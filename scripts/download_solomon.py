"""D1/D2：Solomon 原始算例下载脚本（许可状态为"待确认"，原始文件不随审查包转发）。

用法：
    .venv\\Scripts\\python.exe scripts\\download_solomon.py
        [--manifest data/manifests/solomon_manifest.json]
        [--out data/raw/solomon]

按 manifest 中的 source_url 下载 11 个算例并逐文件核对 sha256；
哈希不一致即报错退出（不静默使用可疑文件）。下载失败给出可手工
访问的 URL 列表。原始文件只写入 --out 目录，不修改项目其他部分。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser(description="Solomon 原始算例下载与哈希核对")
    ap.add_argument("--manifest", type=Path,
                    default=ROOT / "data" / "manifests" / "solomon_manifest.json")
    ap.add_argument("--out", type=Path, default=ROOT / "data" / "raw" / "solomon")
    args = ap.parse_args()

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    args.out.mkdir(parents=True, exist_ok=True)
    failures = []
    for f in manifest["files"]:
        name = Path(f["file"]).name
        dest = args.out / name
        url = f["source_url"]
        if not dest.exists():
            print(f"下载 {name} <- {url}")
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "curl/8"})
                dest.write_bytes(urllib.request.urlopen(req, timeout=60).read())
            except Exception as exc:  # noqa: BLE001 — 下载失败要汇总报告
                failures.append({"file": name, "url": url, "error": str(exc)})
                continue
        actual = sha256(dest)
        if actual != f["sha256"]:
            failures.append({"file": name, "url": url,
                             "error": f"sha256 不符：{actual[:16]}… ≠ {f['sha256'][:16]}…"})
        else:
            print(f"  ok {name}  sha256={actual[:16]}…")

    if failures:
        print("\n失败项（请手工下载并核对 manifest 中的 sha256）：")
        for x in failures:
            print(f"  {x['file']}: {x['error']}\n    {x['url']}")
        return 1
    print(f"\n全部 {len(manifest['files'])} 个算例哈希核对通过 -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
