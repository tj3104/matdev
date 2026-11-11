"""LLM の部品が守る約束と、共通の結果の形。"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Callable

from ..config import LLMConfig

OnText = Callable[[str], None]


class LLMError(Exception):
    """LLM の呼び出しの失敗。文面は画面に出す。"""


class MissingKey(LLMError):
    """鍵が設定されていない。"""


class LLMAuthError(LLMError):
    """鍵が使えない。"""


class LLMRateLimit(LLMError):
    """混雑・利用の上限。"""


class LLMConnectionError(LLMError):
    """接続できない。"""


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0


@dataclass
class Turn:
    """LLM の 1 回の応答。blocks は共通の形 (text / tool_use と、LLM 固有のブロック)。

    stop: end (返事が終わった) / tool_use (道具を呼ぶ) / pause (続きがある) / max_tokens (長すぎて切れた) / refusal (断った)
    """
    blocks: list[dict]
    stop: str
    usage: Usage = field(default_factory=Usage)
    model: str = ""

    @property
    def text(self) -> str:
        return "".join(b.get("text", "") for b in self.blocks if b.get("type") == "text")

    @property
    def tool_uses(self) -> list[dict]:
        return [b for b in self.blocks if b.get("type") == "tool_use"]


class LLM:
    """部品が持つもの: ready・describe・turn。"""

    def __init__(self, config: LLMConfig, client_factory=None) -> None:
        self.config = config
        self._client_factory = client_factory
        self._client = None

    def key(self) -> str | None:
        return os.environ.get(self.config.api_key_env) if self.config.api_key_env else None

    def ready(self) -> bool:
        """鍵が要る LLM で、鍵があるか。"""
        return True

    def describe(self) -> str:
        return f"{self.config.provider} / {self.config.model}"

    def client(self):
        if self._client is None:
            if self._client_factory is not None:
                self._client = self._client_factory()
            else:
                if not self.ready():
                    raise MissingKey(f"LLM の鍵がありません。.env の {self.config.api_key_env} を確認して起動し直してください")
                self._client = self.new_client()
        return self._client

    def new_client(self):
        raise NotImplementedError

    async def turn(self, system: str, tools: list[dict], messages: list[dict], on_text: OnText) -> Turn:
        """1 回呼ぶ。tools は {name, description, input_schema}。messages は共通の形。"""
        raise NotImplementedError


