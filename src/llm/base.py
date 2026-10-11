"""阶段 4 LLM 适配层：Provider 协议与响应信封。

设计要点：
- Provider 是纯文本进出的薄适配器（system/user prompt → 文本回复），
  不接触场景对象、不做任何执行；所有结构化收口在 extractor；
- 响应信封必须带 provider 名与演示模式标记，确保产物全程可识别来源，
   mock 不得冒充真实模型。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class LLMProviderError(RuntimeError):
    """Provider 调用失败（网络/HTTP/超时/应答格式）。消息不得含密钥。"""


@dataclass(frozen=True)
class ProviderReply:
    """Provider 原始文本回复 + 来源标记。

    demo_mode=True 表示演示模式（规则提取器），产物与页面必须明确展示。
    model 为 None 表示无真实模型名可声明（mock 恒为 None）。
    """

    text: str
    provider: str
    model: str | None
    demo_mode: bool


class LLMTextProvider(Protocol):
    """文本补全协议。实现只允许做一件事：把 prompt 变成文本。"""

    provider_name: str

    def complete(self, system_prompt: str, user_prompt: str) -> ProviderReply: ...
