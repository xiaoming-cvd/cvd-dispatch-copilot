"""阶段 4 命令行入口：自然语言需求 → 待确认草案 →（人工确认后）新场景版本。

用法（Windows）：
    .venv\\Scripts\\python.exe scripts\\draft_cli.py --input <场景文件> --text "o1 不能换车，必须还是 V1 送"
    .venv\\Scripts\\python.exe scripts\\draft_cli.py --input <场景文件> --text "..." --store data\\processed\\drafts
    .venv\\Scripts\\python.exe scripts\\draft_cli.py --input <场景文件> --confirm <draft_id> --yes [--store ...]
    .venv\\Scripts\\python.exe scripts\\draft_cli.py --input <场景文件> --confirm <draft_id> --accept 0,2 [--store ...]

行为红线：
- 本 CLI 只产出草案与确认后的新场景版本，绝不调用求解器；
- 未确认不产生任何新场景版本；重复确认报冲突退出码 3；
- 演示模式在 stderr 明确标注"规则提取器，非真实大模型"。

确认语义（第四阶段复审 P0-1/P1-2）：
- --yes = 显式接受全部条目；--accept <列表> = 只接受所列序号，未列项拒绝；
  两者互斥，且确认时必须二选一（不接受裸 --confirm 的隐式全接受）；
- --accept 的序号必须是非负整数、在草案条目范围内、无重复，
  非法输入干净报错（退出码 2），绝不抛 traceback。

退出码：0 = 成功；1 = 用法/文件错误；2 = 输入非法；3 = 冲突（重复确认/基线不匹配）。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pydantic import ValidationError  # noqa: E402

from src.domain.models import Scenario  # noqa: E402
from src.llm.base import LLMProviderError  # noqa: E402
from src.llm.config import DEMO_MODE_NOTICE, LLMConfig  # noqa: E402
from src.llm.service import DraftStore, LLMService  # noqa: E402


def _load_scenario(path: Path) -> Scenario:
    data = json.loads(path.read_text(encoding="utf-8"))
    raw = data.get("scenario", data)
    return Scenario(**raw)


def main(argv: list[str] | None = None) -> int:
    # Windows 重定向默认 GBK：规范 JSON 一律 UTF-8 输出
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="城配调度副驾 · 阶段 4 约束草案 CLI（不调用求解器）")
    ap.add_argument("--input", required=True, type=Path, help="场景 JSON（含 scenario 顶层键或裸场景）")
    ap.add_argument("--text", help="调度员自然语言需求")
    ap.add_argument("--store", type=Path, default=ROOT / "data" / "processed" / "drafts",
                    help="草案/确认存储目录（默认 data\\processed\\drafts）")
    ap.add_argument("--confirm", metavar="DRAFT_ID", help="确认指定草案（需配合 --input 基线场景）")
    group = ap.add_mutually_exclusive_group()
    group.add_argument("--accept", help="只接受所列条目序号（items 在前 preferences 在后），如 0,2")
    group.add_argument("--yes", action="store_true", help="显式接受全部条目")
    args = ap.parse_args(argv)

    try:
        scenario = _load_scenario(args.input)
    except (OSError, json.JSONDecodeError, ValidationError, ValueError) as exc:
        print(json.dumps({"status": "error", "warnings": [f"{type(exc).__name__}: {exc}"]},
                         ensure_ascii=False, indent=2))
        return 1

    config = LLMConfig.from_env()
    if config.demo_mode:
        print(f"[演示模式] {DEMO_MODE_NOTICE}（provider=mock）", file=sys.stderr)
    service = LLMService(store=DraftStore(args.store), config=config)

    if args.confirm:
        if args.text:
            print(json.dumps({"status": "error", "warnings": ["--confirm 与 --text 不能混用"]},
                             ensure_ascii=False, indent=2))
            return 1
        if not args.yes and not args.accept:
            print(json.dumps({"status": "error",
                              "warnings": ["E_ACCEPT_REQUIRED: 确认必须显式选择 --yes（全部接受）"
                                           "或 --accept <序号列表>（只接受所列项）"]},
                             ensure_ascii=False, indent=2))
            return 2
        store = DraftStore(args.store)
        try:
            draft = store.get_draft(args.confirm)
        except KeyError as exc:
            print(json.dumps({"status": "error", "warnings": [str(exc)]},
                             ensure_ascii=False, indent=2))
            return 2
        total = len(draft.items) + len(draft.preferences)
        if args.yes:
            decisions = {i: True for i in range(total)}
        else:
            try:
                idxs = [int(x) for x in args.accept.split(",") if x.strip() != ""]
            except ValueError:
                print(json.dumps({"status": "error",
                                  "warnings": ["E_ACCEPT_INVALID: --accept 只接受整数序号列表"
                                               f"（ got {args.accept!r}）"]},
                                 ensure_ascii=False, indent=2))
                return 2
            if not idxs:
                print(json.dumps({"status": "error",
                                  "warnings": ["E_ACCEPT_INVALID: --accept 列表为空"]},
                                 ensure_ascii=False, indent=2))
                return 2
            if len(set(idxs)) != len(idxs):
                print(json.dumps({"status": "error",
                                  "warnings": ["E_ACCEPT_INVALID: --accept 存在重复序号"]},
                                 ensure_ascii=False, indent=2))
                return 2
            bad = [i for i in idxs if not 0 <= i < total]
            if bad:
                print(json.dumps({"status": "error",
                                  "warnings": [f"E_ACCEPT_INVALID: 序号 {bad} 越界"
                                               f"（草案共 {total} 个条目，合法范围 0..{total - 1}）"]},
                                 ensure_ascii=False, indent=2))
                return 2
            decisions = {i: True for i in idxs}  # 未列项拒绝（P0-1 语义）
        try:
            outcome = service.confirm(args.confirm, scenario, decisions)
        except KeyError as exc:
            print(json.dumps({"status": "error", "warnings": [str(exc)]},
                             ensure_ascii=False, indent=2))
            return 2
        except ValueError as exc:
            code = 3 if "E_DRAFT_ALREADY_CONFIRMED" in str(exc) or "E_DRAFT_BASE_MISMATCH" in str(exc) else 2
            print(json.dumps({"status": "error", "warnings": [str(exc)]},
                             ensure_ascii=False, indent=2))
            return code
        print(json.dumps({
            "status": "confirmed",
            "demo_mode": outcome.bundle.demo_mode,
            "bundle": outcome.bundle.model_dump(mode="json"),
            "scenario": outcome.scenario.model_dump(mode="json"),
        }, ensure_ascii=False, indent=2))
        return 0

    if not args.text:
        print(json.dumps({"status": "error", "warnings": ["缺少 --text 或 --confirm"]},
                         ensure_ascii=False, indent=2))
        return 1
    try:
        draft = service.create_draft(scenario, args.text)
    except ValueError as exc:
        print(json.dumps({"status": "error", "warnings": [str(exc)]},
                         ensure_ascii=False, indent=2))
        return 2
    except LLMProviderError as exc:
        print(json.dumps({"status": "error", "warnings": [str(exc)]},
                         ensure_ascii=False, indent=2))
        return 5
    print(json.dumps({"status": "draft", "draft": draft.model_dump(mode="json")},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
