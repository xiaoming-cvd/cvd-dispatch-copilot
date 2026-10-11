"""OpenAI 兼容真实 Provider（阶段 4 可替换 Provider 的默认实现）。

红线：
- 仅在本模块被显式导入时才有网络代码路径（config.build_provider 延迟
  导入）；密钥只进请求头，绝不写入消息体/日志/错误信息；
- 输出为原始文本，结构化收口在 extractor（与 mock 同一防护路径）；
- 超时与 HTTP 失败抛 LLMProviderError，由调用方决定呈现，不静默重试。
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from typing import Callable

from .base import LLMProviderError, ProviderReply
from .config import LLMConfig

Opener = Callable[..., object]

_KEY_LIKE_RE = re.compile(r"\b(sk-[A-Za-z0-9_-]{4,}|Bearer\s+\S+)\b")


def _sanitize(text: str, api_key: str) -> str:
    """异常服务端回显脱敏（第四阶段复审 P2）：密钥与疑似密钥串不进日志。"""
    if api_key:
        text = text.replace(api_key, "***")
    return _KEY_LIKE_RE.sub("***", text)


class OpenAICompatibleProvider:
    provider_name = "openai_compatible"

    def __init__(self, config: LLMConfig, opener: Opener | None = None):
        if not config.api_key:
            raise LLMProviderError("E_LLM_CONFIG: 真实 Provider 需要 LLM_API_KEY（后端环境变量）")
        if not config.model:
            raise LLMProviderError("E_LLM_CONFIG: 真实 Provider 需要 LLM_MODEL（后端环境变量）")
        self._config = config
        self._opener = opener or urllib.request.urlopen

    def complete(self, system_prompt: str, user_prompt: str) -> ProviderReply:
        url = self._config.base_url.rstrip("/") + "/chat/completions"
        body = json.dumps({
            "model": self._config.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0,
        }).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._config.api_key}",
            },
            method="POST",
        )
        try:
            with self._opener(req, timeout=self._config.timeout_s) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                body = exc.read().decode("utf-8", errors="replace")
                parsed = json.loads(body)
                detail = str(parsed.get("error", {}).get("message") or body)[:200]
            except Exception:
                detail = ""
            detail = _sanitize(detail, self._config.api_key)
            suffix = f"：{detail}" if detail else ""
            raise LLMProviderError(
                f"E_LLM_HTTP: 模型接口返回 HTTP {exc.code}{suffix}（请检查密钥/模型名/额度）"
            ) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise LLMProviderError(f"E_LLM_NETWORK: 模型接口不可达或超时（{type(exc).__name__}）") from exc
        except json.JSONDecodeError as exc:
            raise LLMProviderError("E_LLM_OUTPUT: 模型接口返回非 JSON") from exc

        try:
            text = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMProviderError("E_LLM_OUTPUT: 模型接口应答缺少 choices[0].message.content") from exc
        if not isinstance(text, str) or not text.strip():
            raise LLMProviderError("E_LLM_OUTPUT: 模型接口应答内容为空")
        return ProviderReply(
            text=text,
            provider=self.provider_name,
            model=self._config.model,
            demo_mode=False,
        )
