"""生成 data/manifests/solomon_manifest.json：来源、哈希、许可、字段映射、转换规则。

原始文件位于 data/raw/solomon/（只读，默认不提交 Git）。
本脚本只读取并计算哈希，绝不修改原始文件。
"""
from __future__ import annotations

import hashlib
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "raw" / "solomon"
OUT = ROOT / "data" / "manifests" / "solomon_manifest.json"

MIRROR_BASE = "https://raw.githubusercontent.com/jonzhaocn/VRPTW-ACO-python/master/solomon-100/"
# D2 修正：canonical_source 只存规范 URL；访问失败与替代来源独立成字段；
# 镜像为浮动 master 分支，未固定 commit，可复现性以文件 sha256 为准。
CANONICAL_SOURCE = "https://www.sintef.no/projectweb/top/vrptw/100-customers/"
CANONICAL_ACCESS_NOTE = (
    "官方页受 Cloudflare 防护，本机无法直接抓取；经 GitHub 公开镜像"
    "（jonzhaocn/VRPTW-ACO-python）获取同内容文件"
)
MIRROR_REF = "master（浮动分支，未固定 commit；完整性以 sha256 核对为准）"
SCRIPT_VERSION = "solomon-manifest/1.1"

FIELD_MAPPING = {
    "VEHICLE NUMBER": "车辆数上限（原始文件，仅记录；情景生成器自行决定启用车辆数）",
    "VEHICLE CAPACITY": "Vehicle.capacity（容量单位 load_units，原始值不换算）",
    "CUST NO.=0": "仓库节点 depot_node_id（XCOORD/YCOORD 为坐标，不直接入 schema）",
    "CUST NO.>0": "候选订单节点（坐标经欧氏距离换算为行驶时间矩阵）",
    "DEMAND": "Order.demand（原始载荷单位，不换算）",
    "READY TIME": "Order.ready_s（换算规则见 conversion_rules）",
    "DUE DATE": "Order.due_s（同上；仓库行的 DUE DATE 视为整体返回时限参考）",
    "SERVICE TIME": "Order.service_s（同上）",
    "XCOORD/YCOORD": "仅用于计算欧氏距离；坐标本身不进入 schema",
}

CONVERSION_RULES = {
    "time_unit_source": "基准时间单位（算例原始值，不声称是真实分钟）；"
                        "本项目每单位映射为 60 模拟秒，保留原始值于 raw",
    "time_conversion": "ready_s/due_s/service_s = 原始值 * 60（整数），"
                       "规则记录于此，不得静默改变；此为模拟尺度转换，"
                       "不得由该转换推导真实工时或现金节约",
    "travel_time": "time_s[i][j] = int(round(euclid_distance(coords_i, coords_y) * 60))；"
                   "欧氏距离按双精度计算后缩放取整（整数缩放与舍入规则），"
                   "原始坐标与双精度距离值不进入 schema",
    "travel_time_note": "坐标与载荷为基准单位，页面与报告不得直接称为公里、公斤",
    "capacity": "容量与需求量保留原始载荷单位（load_units），不换算",
}

# D2 修正：许可必须落到 URL 与状态字段；"公开学术基准"不等于已确认再分发权。
LICENSE_NOTE = (
    "Solomon (1987) 学术基准算例，学术界惯常公开用于研究；原始出处 SINTEF "
    "TOP 页面。经第三方公开镜像分发，本仓库仅保存小体积文本算例并记录来源。"
    "不得声称拥有数据权利；未见明示许可文本，商用/比赛再分发前须自行核对。"
)
LICENSE_URL = "https://www.sintef.no/projectweb/top/vrptw/100-customers/"
TERMS_URL = None  # SINTEF 页面未附单独条款文本；null 表示未见明示条款
LICENSE_STATUS = "待确认（学术基准惯常公开用于研究，但未见明示许可文本）"
REDISTRIBUTABLE = "待确认（当前仅按研究用途保存与转换）"

# D2 修正：如实记录每项实际核对范围，不用"核对 C101 首行"冒充全量核对。
VERIFICATION_SCOPE = (
    "行数与列结构核对（仓库行 + 100 个客户行）；"
    "仅 C101 首行与公开文献记载数值交叉核对"
)
VERIFICATION_STATUS = "部分核对（除 C101 首行外，未与官方文件逐值比对）"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    files = sorted(RAW_DIR.glob("*.txt"))
    if not files:
        raise SystemExit(f"未找到原始算例：{RAW_DIR}（请先运行下载流程）")

    entries = []
    for f in files:
        entries.append({
            "file": f"solomon/{f.name}",
            "source_url": MIRROR_BASE + f.name.lower(),
            "source_ref": MIRROR_REF,
            "canonical_source": CANONICAL_SOURCE,
            "canonical_access_note": CANONICAL_ACCESS_NOTE,
            "download_date": str(date.today()),
            "sha256": sha256(f),
            "size_bytes": f.stat().st_size,
            "license_note": LICENSE_NOTE,
            "license_url": LICENSE_URL,
            "terms_url": TERMS_URL,
            "license_status": LICENSE_STATUS,
            "redistributable": REDISTRIBUTABLE,
            "verification_scope": VERIFICATION_SCOPE,
            "verification_status": VERIFICATION_STATUS,
            "field_mapping": FIELD_MAPPING,
            "conversion_rules": CONVERSION_RULES,
            "filter_rules": "无过滤；原样保存原始文件",
            "missing_rate": "0（已核对每个文件含仓库行 + 100 个客户行）",
            "script_version": SCRIPT_VERSION,
        })

    manifest = {
        "dataset": "Solomon VRPTW 100-customers（选取 11 个基础算例）",
        "script_version": SCRIPT_VERSION,
        "generated": str(date.today()),
        "files": entries,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"manifest 已写入 {OUT}，共 {len(entries)} 个文件")
    for e in entries:
        print(f"  {e['file']}  sha256={e['sha256'][:12]}…  {e['size_bytes']}B")


if __name__ == "__main__":
    sys.exit(main())
