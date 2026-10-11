"""把 Scenario 的 JSON Schema 导出为文件，供前端/文档引用（先写 schema，再写适配器和页面）。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.domain.models import scenario_json_schema


def main() -> None:
    schema = scenario_json_schema()
    out = Path(__file__).resolve().parent.parent / "docs" / "schema" / "scenario.schema.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(schema, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"JSON Schema 已导出到 {out}")


if __name__ == "__main__":
    main()
