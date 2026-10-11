"""交付包空目录无原件模拟回归（三次复审 P0）。

运行 scripts/verify_empty_dir.py：白名单打包 → 解压到无 data/raw 的空目录 →
pytest 收集无错误且源数据用例显式 skip → review_check 基础 smoke 通过、
overall=partial。全量日志见 reports/verify_empty_dir_log.txt。

单独运行：.venv\\Scripts\\python.exe -m pytest -m delivery_sim -q
（耗时约 1-3 分钟；批量审查脚本 review_check.py 已用 -m "not delivery_sim"
排除本用例，避免套娃。）
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.delivery_sim
def test_empty_dir_delivery_sim():
    if os.environ.get("SKIP_DELIVERY_SIM"):
        pytest.skip("SKIP_DELIVERY_SIM 已设置（快速开发循环；CI/交付前请去掉）")
    p = subprocess.run(
        [sys.executable, "scripts/verify_empty_dir.py"],
        cwd=ROOT, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=1800)
    assert p.returncode == 0, (
        "空目录模拟未通过（完整日志 reports/verify_empty_dir_log.txt）：\n"
        + p.stdout[-3000:])
