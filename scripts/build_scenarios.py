"""阶段 1 批处理：划分开发/测试集 → 生成情景草稿 → 登记注册表 → 数据质量报告。

产物：
- data/processed/scenarios/<dev|test>/<scenario_id>.json（ScenarioDraft，含事件日志）
- data/processed/scenarios/registry.json
- reports/stage1_data_quality.md（程序生成）
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.adapters.generator import GenConfig, generate_draft, split_base_instances  # noqa: E402
from src.adapters.solomon import load_instance  # noqa: E402
from src.domain.models import input_hash  # noqa: E402
from src.validation.checks import validate_draft  # noqa: E402

SPLIT_SEED = 20261010
DEV_FRACTION = 0.5
TZ = timezone(timedelta(hours=8))
T0 = datetime(2026, 10, 10, 8, 0, 0, tzinfo=TZ)

# 开发集配置（可在此调参；测试集配置冻结后不得改）
DEV_CONFIGS = [
    GenConfig(n_known=15, n_new=3, tw_tightness="normal", vehicle_slack="normal", lock_ratio=0.2, seed=101),
    GenConfig(n_known=15, n_new=2, tw_tightness="tight", vehicle_slack="tight", lock_ratio=0.5, seed=102),
]
TEST_CONFIGS = [
    GenConfig(n_known=15, n_new=3, tw_tightness="normal", vehicle_slack="normal", lock_ratio=0.2, seed=201),
]


def split_by_layout(raw: list[Path], dev_fraction: float, seed: int) -> dict[str, str]:
    """按客户布局分组划分开发/测试集（总工程师审查修正）。

    坐标集合完全相同的算例（如 C201/C202、R101–R202）视为同一泛化单元，
    整组只能属于一个集合；同一组内各算例仅时间窗/服务时长不同，
    分开划入开发/测试等于用高度相似数据做"独立泛化测试"。
    """
    groups: dict[tuple, list[str]] = {}
    for p in raw:
        inst = load_instance(p)
        sig = tuple(sorted((c.x, c.y) for c in inst.customers))
        groups.setdefault(sig, []).append(p.stem)
    # 以组内字典序最小算例名作为组名，复用 split_base_instances 的确定性划分
    group_names = sorted(min(names) for names in groups.values())
    group_split = split_base_instances(group_names, dev_fraction, seed)
    inst_split: dict[str, str] = {}
    for sig, names in groups.items():
        gname = min(names)
        for n in names:
            inst_split[n] = group_split[gname]
    return inst_split


def main() -> None:
    raw = sorted((ROOT / "data" / "raw" / "solomon").glob("*.txt"))
    names = [p.stem for p in raw]
    split = split_by_layout(raw, DEV_FRACTION, SPLIT_SEED)

    out_dir = ROOT / "data" / "processed" / "scenarios"
    registry = {"split_seed": SPLIT_SEED, "dev_fraction": DEV_FRACTION, "scenarios": []}

    for path in raw:
        inst = load_instance(path)
        group = split[inst.name]
        configs = DEV_CONFIGS if group == "dev" else TEST_CONFIGS
        for cfg in configs:
            sid = f"{inst.name.lower()}-s{cfg.seed}"
            gen = generate_draft(inst, cfg, sid, T0)
            report = validate_draft(gen.draft)
            if not report.ok:
                raise SystemExit(f"草稿 {sid} 未通过校验：{report.to_dict()}")
            d = out_dir / group
            d.mkdir(parents=True, exist_ok=True)
            fpath = d / f"{sid}.json"
            fpath.write_text(json.dumps(gen.to_dict(), ensure_ascii=False, indent=2),
                             encoding="utf-8")
            registry["scenarios"].append({
                "scenario_id": sid,
                "base_instance": inst.name,
                "split": group,
                "config": cfg.__dict__,
                "file": f"scenarios/{group}/{sid}.json",
                "draft_input_hash": input_hash(gen.draft),
            })
            print(f"{group:4s} {sid:16s} hash={input_hash(gen.draft)[:12]}…")

    (out_dir / "registry.json").write_text(
        json.dumps(registry, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n注册表：{out_dir / 'registry.json'}，共 {len(registry['scenarios'])} 个情景")

    write_quality_report(raw, split)


def write_quality_report(raw: list[Path], split: dict[str, str]) -> None:
    lines = ["# 阶段 1 数据质量报告（程序生成）\n",
             f"- 划分种子：{SPLIT_SEED}，开发集比例：{DEV_FRACTION}",
             "- 转换规则：时间×60 得『模拟秒』；行驶时间=欧氏距离×60 四舍五入取整（见 solomon_manifest.json）",
             "- **尺度声明：×60 为模拟尺度转换（1 个原始时间单位 = 60 个模拟秒），"
             "仅用于算法开发与评测的一致性，不代表真实配送业务中的分钟换算**；"
             "坐标与载荷同为基准单位，不直接称为公里、公斤\n",
             "| 算例 | 集合 | 客户行 | 车辆上限 | 容量 | 需求范围 | 原始窗范围 | 行哈希(前12) |",
             "|---|---|---|---|---|---|---|---|"]
    import hashlib
    layouts: dict[tuple, list[str]] = {}
    for p in raw:
        inst = load_instance(p)
        sig = tuple(sorted((c.x, c.y) for c in inst.customers))
        layouts.setdefault(sig, []).append(inst.name)
        demands = [c.demand for c in inst.customers]
        readys = [c.ready for c in inst.customers]
        dues = [c.due for c in inst.customers]
        h = hashlib.sha256(p.read_bytes()).hexdigest()[:12]
        lines.append(
            f"| {inst.name} | {split[inst.name]} | {len(inst.customers)} | {inst.vehicle_number} "
            f"| {inst.capacity:g} | {min(demands):g}–{max(demands):g} "
            f"| {min(readys):g}–{max(dues):g} | {h} |")
    lines.append("\n缺失率：0（所有文件均为仓库行 + 100 客户行，解析无缺字段）。")
    lines.append("原始文件校验：与 data/manifests/solomon_manifest.json 中 sha256 逐一比对，"
                 "由测试 test_raw_files_unmodified 执行。")

    # ---- 客户布局相似性检查（总工程师审查补充）----
    lines += ["\n## 客户布局分组（坐标集合完全相同的算例视为同一布局）\n",
              "| 布局组 | 算例 | 集合 | 跨开发/测试? |",
              "|---|---|---|---|"]
    warned = False
    for sig, names in sorted(layouts.items(), key=lambda kv: kv[1][0]):
        groups = {split[n] for n in names}
        cross = "是 ⚠️" if len(groups) > 1 else "否"
        if len(groups) > 1:
            warned = True
        lines.append(f"| {names[0]}系列布局 | {'、'.join(names)} "
                     f"| {'、'.join(sorted(groups))} | {cross} |")
    lines.append("")
    if warned:
        lines.append("⚠️ 同一坐标布局横跨开发/测试集合：这些算例仅时间窗/服务时长不同，"
                     "客户位置完全一致。**阶段 6 冻结评测协议时必须把『同一布局组』"
                     "当作一个泛化单元对待**（同一布局的算例不得分别出现在开发集与测试集），"
                     "否则是把高度相似的数据当作独立泛化测试。")
    else:
        lines.append("布局组均未跨集合，划分在布局层面无泄漏。")
    out = ROOT / "reports" / "stage1_data_quality.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"质量报告：{out}")


if __name__ == "__main__":
    main()
