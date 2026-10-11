"""D3：基础算例内容指纹检查（二次复审 P1：定义与计算对齐 + 源文件 SHA256 核对）。

目的：检查 11 个基础算例中是否存在客户内容高度重合的算例对，
为"是否按布局分组"提供证据；并独立校验源文件字节与登记 manifest 一致。
本脚本只读原始文件，不做任何修改。

双层指标（二次复审修正：原实现 sha256 只含 (x,y,demand)，与 docstring
声称的"坐标/需求/时间窗/服务"不符）：
- 布局重合度：沿用 (x, y, demand) 键集合的逐对 Jaccard（回答"布局是否分组"）；
- 完整内容指纹：每个算例对全部客户行的 (cust_no, x, y, demand, ready,
  due, service) 规范化序列取 sha256——客户 ID、时间窗、服务时长全部参与；
- 源文件完整性：逐个比对 11 个原始文件的字节 sha256 与
  data/manifests/solomon_manifest.json 的登记值（防"保留文件名替换内容"）。

退出码：0 = 11 个源文件齐全、manifest 一致且完成检查；
2 = 源文件缺失（显式跳过，调用方须显示"部分验证"而非 PASS）；
1 = 文件在但解析/manifest 不一致/数量异常（fail）。
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
MANIFEST = ROOT / "data" / "manifests" / "solomon_manifest.json"
OUT = ROOT / "reports" / "base_fingerprint.json"

# 本评测使用的全部 Solomon 基础算例（与 scenarios 草稿的 base 名一一对应）
EXPECTED_FILES = [
    "C101.txt", "C102.txt", "C201.txt", "C202.txt",
    "R101.txt", "R102.txt", "R201.txt", "R202.txt",
    "RC101.txt", "RC201.txt", "RC202.txt",
]


def layout_fingerprint(inst) -> tuple[str, list[tuple]]:
    """布局键（坐标+需求，去重排序）→ (sha256, 键集合)。用于重合度。"""
    keys = sorted({
        (round(c.x, 6), round(c.y, 6), round(c.demand, 6))
        for c in inst.customers
    })
    raw = json.dumps(keys, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest(), keys


def content_fingerprint(inst) -> str:
    """完整内容指纹：客户 ID + 坐标 + 需求 + ready/due/service 全部参与。"""
    rows = sorted([
        (c.cust_no, round(c.x, 6), round(c.y, 6), round(c.demand, 6),
         round(c.ready, 6), round(c.due, 6), round(c.service, 6))
        for c in inst.customers
    ], key=lambda r: r[0])
    raw = json.dumps(rows, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


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

    # 源文件字节完整性：与登记 manifest 比对（防保留文件名替换内容）
    manifest_sha: dict[str, str] = {}
    if not MANIFEST.is_file():
        print(f"FAIL: 源文件 manifest 不存在（{MANIFEST}），"
              "无法校验原始算例完整性；不得在无基线情况下声称已验证")
        return 1
    mdata = json.loads(MANIFEST.read_text(encoding="utf-8"))
    for e in mdata.get("files", []):
        manifest_sha[Path(e["file"]).name] = e.get("sha256", "")
    integrity = {}
    mismatches = []
    for f in sorted(RAW_DIR.glob("*.txt")):
        actual = sha256_file(f)
        expected = manifest_sha.get(f.name)
        ok = (expected is not None and actual == expected)
        integrity[f.stem] = {"sha256": actual, "manifest_match": ok}
        if not ok:
            mismatches.append(f.name)
    if mismatches:
        print(f"FAIL: {len(mismatches)} 个源文件与 manifest 登记 sha256 不符: "
              f"{mismatches}（可能为内容被替换/篡改，禁止静默继续）")
        return 1

    per = {}
    for f in sorted(RAW_DIR.glob("*.txt")):
        inst = parse_solomon(f.read_text(encoding="utf-8"))
        h_layout, keys = layout_fingerprint(inst)
        per[f.stem] = {
            "layout_sha256": h_layout,
            "content_sha256": content_fingerprint(inst),
            "n_customers": len(keys),
            "keys": keys,
        }

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
        "script": "check_base_fingerprint/2.0",
        "basis": {
            "layout_overlap": "(x, y, demand) 客户键集合的 Jaccard 重合度",
            "content_fingerprint": "(cust_no, x, y, demand, ready, due, service) "
                                   "规范化序列的 sha256",
            "source_integrity": "原始文件字节 sha256 对照 "
                                "data/manifests/solomon_manifest.json",
        },
        "threshold": 0.8,
        "instances": {k: {"layout_sha256": v["layout_sha256"],
                          "content_sha256": v["content_sha256"],
                          "n_customers": v["n_customers"],
                          "manifest_match": integrity[k]["manifest_match"]}
                      for k, v in per.items()},
        "pairs": pairs,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    flagged = [p for p in pairs if p["flag"] != "ok"]
    print(f"已写入 {OUT}")
    print(f"算例数 {len(per)}，高重合对 {len(flagged)}，"
          f"manifest 一致性 {len(per) - len(mismatches)}/{len(per)}")
    for p in pairs:
        print(f"  {p['pair'][0]:6s} vs {p['pair'][1]:6s}  J={p['jaccard_xy_demand']:.3f}  {p['flag']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
