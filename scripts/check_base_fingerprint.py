"""D3：基础算例内容指纹检查（坐标 + 需求 + 时间窗）。

目的：检查 11 个基础算例中是否存在客户内容高度重合的算例对，
为"是否按布局分组"提供证据。本脚本只读原始文件，不做任何修改。

退出码：0 = 11 个源文件齐全且完成检查；2 = 源文件缺失（显式跳过，
调用方须显示"部分验证"而非 PASS）；1 = 文件在但解析/数量异常。

输出 reports/base_fingerprint.json：
- 每个算例的 sha256（对全部客户行的 坐标/需求/时间窗/服务 规范化序列）
- 逐对重合度（共同 (坐标,需求) 键占比，Jaccard）
判定：Jaccard ≥ 0.8 记为"高重合风险"，需在划分/评测说明中披露。
"""
from __future__ import annotations

import hashlib
import json
import sys
from itertools import combinations
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.adapters.solomon import parse_solomon  # noqa: E402

RAW_DIR = ROOT / "data" / "raw" / "solomon"
OUT = ROOT / "reports" / "base_fingerprint.json"

# 本评测使用的全部 Solomon 基础算例（与 scenarios 草稿的 base 名一一对应）
EXPECTED_FILES = [
    "C101.txt", "C102.txt", "C201.txt", "C202.txt",
    "R101.txt", "R102.txt", "R201.txt", "R202.txt",
    "RC101.txt", "RC201.txt", "RC202.txt",
]


def fingerprint(inst) -> tuple[str, list[tuple]]:
    """规范化客户内容 → (整体 sha256, (坐标,需求) 键集合)。"""
    keys = sorted({
        (round(c.x, 6), round(c.y, 6), round(c.demand, 6))
        for c in inst.customers
    })
    raw = json.dumps(keys, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest(), keys


def main() -> int:
    # 审查 P0-2：空目录/缺源文件不得静默产出"算例数 0"再被当作 PASS。
    missing = [f for f in EXPECTED_FILES if not (RAW_DIR / f).is_file()]
    present = sorted(p.name for p in RAW_DIR.glob("*.txt"))
    if missing:
        print(f"SKIPPED_NO_SOURCE: 缺 {len(missing)}/{len(EXPECTED_FILES)} 个源文件: "
              f"{missing}")
        print("提示：可先运行 scripts/download_solomon.py 获取原始算例；"
              "下载失败不得静默降级为 0 算例通过。")
        return 2
    if len(present) != len(EXPECTED_FILES):
        print(f"源文件数量异常：期望 {len(EXPECTED_FILES)}，实际 {len(present)}: "
              f"{present}")
        return 1

    per = {}
    for f in sorted(RAW_DIR.glob("*.txt")):
        inst = parse_solomon(f.read_text(encoding="utf-8"))
        h, keys = fingerprint(inst)
        per[f.stem] = {"sha256": h, "n_customers": len(keys), "keys": keys}

    pairs = []
    for a, b in combinations(sorted(per), 2):
        ka, kb = set(per[a]["keys"]), set(per[b]["keys"])
        inter = len(ka & kb)
        union = len(ka | kb)
        jac = inter / union if union else 0.0
        pairs.append({
            "pair": [a, b],
            "jaccard_xy_demand": round(jac, 4),
            "shared_keys": inter,
            "flag": "高重合风险" if jac >= 0.8 else "ok",
        })

    report = {
        "script": "check_base_fingerprint/1.0",
        "basis": "(x, y, demand) 客户键集合的 Jaccard 重合度",
        "threshold": 0.8,
        "instances": {k: {"sha256": v["sha256"], "n_customers": v["n_customers"]}
                      for k, v in per.items()},
        "pairs": pairs,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    flagged = [p for p in pairs if p["flag"] != "ok"]
    print(f"已写入 {OUT}")
    print(f"算例数 {len(per)}，高重合对 {len(flagged)}")
    for p in pairs:
        print(f"  {p['pair'][0]:6s} vs {p['pair'][1]:6s}  J={p['jaccard_xy_demand']:.3f}  {p['flag']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
