"""阶段 5 补充数据导入：Kilby 动态 VRP → 情景草稿（走阶段 0 管线）。

产物：
- data/processed/scenarios/<dev|test>/kilby-<name>-s<seed>.json（ScenarioDraft + _meta）
- data/processed/scenarios/registry.json（追加，不改动已有条目）
- data/manifests/kilby_manifest.json（溯源 + 转换规则声明）
- reports/kilby_data_quality.md（程序生成）

用法：.venv\\Scripts\\python.exe scripts\\import_kilby.py
"""
from __future__ import annotations

import hashlib
import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.adapters.generator import split_base_instances  # noqa: E402
from src.adapters.kilby import (  # noqa: E402
    CUTOFF_DEFAULT,
    build_draft,
    parse_vrptest,
    scenario_meta,
)
from src.domain.models import input_hash  # noqa: E402
from src.validation.checks import validate_draft  # noqa: E402

RAW_ZIP = ROOT / "data" / "raw" / "kilby" / "3fwc3twwn6.zip"
SPLIT_SEED = 20261010  # 与 Solomon 集同种子，划分独立进行（布局组不同）
DEV_FRACTION = 0.5
DEV_LOCK_RATIO = 0.3
TEST_LOCK_RATIO = 0.2
DEV_SEED = 101
TEST_SEED = 201
DOI = "10.17632/3fwc3twwn6.1"
SRC_URL = "https://data.mendeley.com/public-api/zip/3fwc3twwn6/download/1"
LICENSE = "CC BY 4.0"


def layout_groups(instances) -> dict[str, str]:
    """坐标集合相同的算例整组同名（Kilby 各算例布局均不同，仍走统一分组逻辑）。"""
    groups: dict[tuple, list[str]] = {}
    for inst in instances:
        sig = tuple(sorted(inst.coords))
        groups.setdefault(sig, []).append(inst.name)
    out: dict[str, str] = {}
    for names in groups.values():
        for n in names:
            out[n] = min(names)
    return out


def main() -> int:
    sha = hashlib.sha256(RAW_ZIP.read_bytes()).hexdigest()
    with zipfile.ZipFile(RAW_ZIP) as z:
        names = sorted(n for n in z.namelist() if n.endswith(".vrp"))
        instances = [parse_vrptest(z.read(n).decode("utf-8")) for n in names]

    groups = layout_groups(instances)
    group_names = sorted(set(groups.values()))
    group_split = split_base_instances(group_names, DEV_FRACTION, SPLIT_SEED)

    scn_dir = ROOT / "data" / "processed" / "scenarios"
    registry_path = scn_dir / "registry.json"
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    existing = {e["scenario_id"] for e in registry["scenarios"]}

    report_rows = []
    added = []
    for inst in instances:
        split = group_split[groups[inst.name]]
        seed = DEV_SEED if split == "dev" else TEST_SEED
        lock = DEV_LOCK_RATIO if split == "dev" else TEST_LOCK_RATIO
        sid = f"kilby-{inst.name.lower()}-s{seed}"
        if sid in existing:
            continue
        draft = build_draft(inst, scenario_id=sid, cutoff=CUTOFF_DEFAULT,
                            lock_ratio_target=lock, seed=seed)
        vrep = validate_draft(draft)
        if not vrep.ok:
            raise SystemExit(f"E_VALIDATE: {sid} 校验未过: {vrep.to_dict()}")
        n_err = sum(1 for i in vrep.issues if i.severity == "error")
        n_warn = sum(1 for i in vrep.issues if i.severity != "error")
        meta = scenario_meta(inst, cutoff=CUTOFF_DEFAULT, lock_ratio_target=lock,
                             seed=seed, split=split)
        payload = {"_meta": meta, "draft": json.loads(draft.model_dump_json())}
        rel = f"scenarios/{split}/{sid}.json"
        out = ROOT / "data" / "processed" / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                       encoding="utf-8")
        known = meta["event_log"][0]["known"]
        new = meta["event_log"][0]["new"]
        registry["scenarios"].append({
            "scenario_id": sid,
            "base_instance": f"kilby-{inst.name}",
            "split": split,
            "config": {
                "source": "kilby",
                "cutoff": CUTOFF_DEFAULT,
                "n_known": len(known),
                "n_new": len(new),
                "lock_ratio": lock,
                "seed": seed,
            },
            "file": rel,
            "draft_input_hash": input_hash(draft),
        })
        added.append((sid, split))
        report_rows.append({
            "scenario_id": sid, "split": split, "n_visits": inst.n_visits,
            "n_known": len(known), "n_new": len(new),
            "capacity": inst.capacity, "horizon_s": inst.depot_window[1],
            "best_known": inst.best_known, "timestep": inst.timestep,
            "warnings": n_warn,
            "input_hash": input_hash(draft)[:16],
        })

    registry_path.write_text(json.dumps(registry, ensure_ascii=False, indent=1),
                             encoding="utf-8")

    manifest = {
        "source": {
            "name": "Kilby's instances for Dynamic Vehicle Routing Problem",
            "contributor": "Michał Okulewicz (Mendeley Data)",
            "doi": DOI,
            "url": SRC_URL,
            "downloaded": "2026-10-11",
            "license": LICENSE,
            "license_url": "https://creativecommons.org/licenses/by/4.0/",
            "license_status": "confirmed_via_api_metadata",
            "attribution_required": True,
            "modified": "converted (abs demand, unit-speed time, declared window default)",
            "redistributable": True,
            "raw_file": "data/raw/kilby/3fwc3twwn6.zip",
            "raw_sha256": sha,
            "version_note": "pinned Version 1 (2018-11-08); Version 5 exists, not used",
        },
        "conversion_rules": [
            "demand = abs(raw)（原始文件为负值，配送方向）",
            "time_s = round(euclidean_distance)（单位速度假设，模拟尺度；非真实分钟）",
            "无逐客时间窗 → ready_s=0, due_s=仓窗末端（声明缺省）",
            f"cutoff={CUTOFF_DEFAULT}: TIME_AVAIL ≤ cutoff×horizon 为旧任务，其余为新任务",
            "车队 = ceil(全部任务总量/容量)+1 余量；矩阵缺边拒绝（null 表达）",
        ],
        "split": {"seed": SPLIT_SEED, "dev_fraction": DEV_FRACTION,
                  "layout_grouped": True},
        "instances": report_rows,
    }
    (ROOT / "data" / "manifests" / "kilby_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")

    lines = ["# Kilby 补充数据集质量报告（程序生成）", "",
             f"- 原始文件: `data/raw/kilby/3fwc3twwn6.zip` sha256 `{sha}`",
             f"- 许可: {LICENSE}（DOI {DOI}），需署名与修改声明",
             f"- 划分: split_seed={SPLIT_SEED}, dev_fraction={DEV_FRACTION}，布局整组划分"
             f"（本数据集各算例布局唯一，组=算例本身）",
             f"- cutoff={CUTOFF_DEFAULT}（文献惯用值，记录于 manifest）", "",
             "| 场景 | 集合 | 访客 | 旧 | 新 | 容量 | 仓窗(s) | 已知最优 | 校验告警 |",
             "|---|---|---|---|---|---|---|---|---|"]
    for r in report_rows:
        bk = r["best_known"] if r["best_known"] is not None else "-"
        lines.append(
            f"| {r['scenario_id']} | {r['split']} | {r['n_visits']} "
            f"| {r['n_known']} | {r['n_new']} | {r['capacity']:g} "
            f"| {r['horizon_s']} | {bk} | {r['warnings']} |")
    dev_n = sum(1 for r in report_rows if r["split"] == "dev")
    lines += ["", f"- 新增草稿 {len(report_rows)} 个（dev {dev_n} / test "
              f"{len(report_rows) - dev_n}），全部通过 validate_draft（0 errors）",
              "- 新任务为数据集原始 appearing time，非合成事件",
              "- 行驶时间=欧氏距离取整（单位速度假设），×60 规则不适用本数据集"]
    (ROOT / "reports" / "kilby_data_quality.md").write_text(
        "\n".join(lines), encoding="utf-8")

    print(json.dumps({"imported": len(report_rows),
                      "added": [a[0] for a in added],
                      "skipped_existing": len(existing) and sorted(
                          existing & {a[0] for a in added})},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
