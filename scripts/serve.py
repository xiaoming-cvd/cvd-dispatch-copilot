"""启动 Web 工作台（阶段 5 交付入口）。

用法（Windows，项目根目录）：

    .venv\\Scripts\\python.exe scripts\\serve.py            # 默认 127.0.0.1:8000
    .venv\\Scripts\\python.exe scripts\\serve.py --port 8080

说明：
- 默认仅监听本机（任务书边界）；密钥只从后端 .env 读取，任何响应不回显。
- LLM_PROVIDER 必须是 mock 或 openai_compatible，启动即校验，失败直接报错。
- 无 COPT 许可也能运行：rebuild 默认 OR-Tools；显式选 COPT 而无许可会如实报错。
"""
from __future__ import annotations

import argparse

import uvicorn


def main() -> None:
    p = argparse.ArgumentParser(description="城配调度副驾 Web 工作台")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    args = p.parse_args()
    uvicorn.run("src.api.app:create_app", factory=True,
                host=args.host, port=args.port)


if __name__ == "__main__":
    main()
