"""LLM 配置：密钥只从后端环境变量读取，无密钥自动进入明确标示的演示模式。

红线（任务书阶段 4 与全程红线"密钥不进仓库"）：
- API 密钥只允许从后端进程环境变量（LLM_API_KEY）读取，绝不从请求体、
  前端、草稿内容、场景文件读取；
- 未配置密钥或 LLM_PROVIDER=mock → demo_mode=True（演示模式），
  此时构建 provider 只会得到规则提取器 MockProvider；
- 配置对象可携带密钥用于请求头，但 redacted() 是唯一允许对外展示的
  形态（只暴露"是否已配置"，永不回显密钥原文）。
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

DEFAULT_BASE_URL = "https://api.openai.com/v1"

DEMO_MODE_NOTICE = "演示模式：规则提取器输出，非真实大模型"

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_ENV_LINE_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$")


def load_dotenv(path: Path) -> int:
    """把 .env 的 KEY=VALUE 载入 os.environ（已存在的键不覆盖）。

    仅本机后端进程使用；.env 被 .gitignore 排除，禁止提交。
    返回载入的键数。值不支持多行。
    """
    if not path.is_file():
        return 0
    loaded = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = _ENV_LINE_RE.match(line)
        if not m:
            continue
        key, value = m.group(1), m.group(2).strip().strip('"').strip("'")
        if key not in os.environ:
            os.environ[key] = value
            loaded += 1
    return loaded


@dataclass(frozen=True)
class LLMConfig:
    provider: str = "mock"  # mock | openai_compatible（可替换真实 Provider 的挂载点）
    api_key: str = ""
    base_url: str = DEFAULT_BASE_URL
    model: str = ""
    timeout_s: float = 30.0
    max_text_chars: int = 4000  # 单次需求文本长度上限（注入/资源防护）
    demo_mode: bool = False
    warnings: tuple[str, ...] = ()

    @classmethod
    def from_env(cls, env: dict | None = None, dotenv_path: Path | None | object = ...) -> "LLMConfig":
        """从后端环境变量构造配置。env 可注入便于测试。

        默认先载入项目根 .env（不覆盖已存在的环境变量）；dotenv_path=None
        表示跳过 .env（测试隔离用）。
        """
        if dotenv_path is ...:
            load_dotenv(_PROJECT_ROOT / ".env")
        elif dotenv_path is not None:
            load_dotenv(dotenv_path)  # type: ignore[arg-type]
        e = os.environ if env is None else env
        provider = (e.get("LLM_PROVIDER") or "mock").strip().lower()
        api_key = (e.get("LLM_API_KEY") or "").strip()
        base_url = (e.get("LLM_BASE_URL") or DEFAULT_BASE_URL).strip()
        model = (e.get("LLM_MODEL") or "").strip()
        warnings: list[str] = []
        demo_mode = False
        if provider == "mock":
            demo_mode = True
        elif not api_key:
            # 显式选了真实 Provider 但无密钥：不得静默失败或报错停摆，
            # 按红线进入明确标示的演示模式。
            demo_mode = True
            provider = "mock"
            warnings.append(
                f"LLM_PROVIDER 已指定真实 Provider 但未配置 LLM_API_KEY，"
                f"已切换演示模式（{DEMO_MODE_NOTICE}）"
            )
        return cls(
            provider=provider,
            api_key=api_key,
            base_url=base_url,
            model=model,
            demo_mode=demo_mode,
            warnings=tuple(warnings),
        )

    def redacted(self) -> dict:
        """允许对外展示的形态：只暴露密钥是否已配置，永不回显原文。"""
        return {
            "provider": self.provider,
            "model": self.model or None,
            "base_url": self.base_url,
            "api_key_configured": bool(self.api_key),
            "demo_mode": self.demo_mode,
        }


def build_provider(config: LLMConfig):
    """按配置构建 (provider, config)。演示模式只产出 MockProvider。

    真实 Provider 在本函数内按需延迟导入：无密钥环境下
    openai_provider 模块（含网络代码路径）不会被加载。
    """
    if config.demo_mode:
        from .mock_provider import MockProvider

        return MockProvider(), config
    from .openai_provider import OpenAICompatibleProvider

    return OpenAICompatibleProvider(config), config
