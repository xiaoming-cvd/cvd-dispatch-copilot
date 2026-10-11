"""源数据在位检查（三次复审 P0）：缺原件时显式报告并退出 2（partial）。

不下载、不修改任何文件。退出码：0 = 全部在位；2 = 有缺失（调用方应显示
partial 并附本脚本的缺失清单与获取方式）。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

SOLOMON_FILES = [
    "C101.txt", "C102.txt", "C201.txt", "C202.txt",
    "R101.txt", "R102.txt", "R201.txt", "R202.txt",
    "RC101.txt", "RC201.txt", "RC202.txt",
]
KILBY_FILES = ["3fwc3twwn6.zip", "collection_notes.md"]

HOW_TO_RESTORE = {
    "solomon": (
        "获取：.venv\\Scripts\\python.exe scripts\\download_solomon.py\n"
        "  （来源与许可见 data/manifests/solomon_manifest.json；\n"
        "    获取后运行 scripts\\check_base_fingerprint.py 校验 sha256）"),
    "kilby": (
        "获取：Mendeley Data DOI 10.17632/3fwc3twwn6.1（CC BY 4.0，署名要求\n"
        "  见 docs/data_licenses.md），下载 3fwc3twwn6.zip 放到 data/raw/kilby/；\n"
        "  文件名与 sha256 登记见 data/manifests/kilby_manifest.json"),
}


def main() -> int:
    missing_solomon = [f for f in SOLOMON_FILES
                       if not (ROOT / "data" / "raw" / "solomon" / f).is_file()]
    missing_kilby = [f for f in KILBY_FILES
                     if not (ROOT / "data" / "raw" / "kilby" / f).is_file()]
    if not missing_solomon and not missing_kilby:
        print("source_data: 齐全（Solomon 11 + Kilby zip/notes）")
        return 0
    if missing_solomon:
        print(f"MISSING_SOLOMON: {missing_solomon}")
        print(HOW_TO_RESTORE["solomon"])
    if missing_kilby:
        print(f"MISSING_KILBY: {missing_kilby}")
        print(HOW_TO_RESTORE["kilby"])
    return 2


if __name__ == "__main__":
    sys.exit(main())
