"""Claude (Anthropic の Messages API)。

思考の記録 (thinking) を含む応答のブロックは、そのまま記録して次の依頼で返す (書き換えると無効になる)。
Opus が断ったときの自動の切り替え (fallbacks) と、キャッシュを使う。
"""
from __future__ import annotations

import anthropic

from .base import LLM, LLMAuthError, LLMConnectionError, LLMError, LLMRateLimit, OnText, Turn, Usage

JSON_RETRIES = 2
STOPS = {"end_turn": "end", "tool_use": "tool_use", "pause_turn": "pause", "max_tokens": "max_tokens",
         "refusal": "refusal", "stop_sequence": "end"}


def _dump(blocks) -> list[dict]:
    """応答のブロックを、次の依頼でそのまま送れる形にする。"""
    dumped = []
    for block in blocks:
        data = block.model_dump(mode="json", exclude_none=True)
        data.pop("parsed_output", None)
        dumped.append(data)
    return dumped


class AnthropicLLM(LLM):
    def ready(self) -> bool:
        import os
        return bool(self.key() or os.environ.get("ANTHROPIC_AUTH_TOKEN"))

    def new_client(self):
        return anthropic.AsyncAnthropic()

    async def turn(self, system: str, tools: list[dict], messages: list[dict], on_text: OnText) -> Turn:
        # 共通の形の中の、ほかの LLM 固有のブロックは送らない (Claude は自分のブロックだけ読める)
        messages = [{**m, "content": [b for b in m["content"] if not b.get("type", "").startswith("x_")]} for m in messages]
        tools = [{**tool, "eager_input_streaming": True} for tool in tools]
        try:
            for attempt in range(JSON_RETRIES + 1):
                try:
                    async with self.client().beta.messages.stream(
                        model=self.config.model, max_tokens=self.config.max_tokens,
                        system=[{"type": "text", "text": system}], tools=tools, messages=messages,
                        thinking={"type": "adaptive"}, output_config={"effort": self.config.effort},
                        cache_control={"type": "ephemeral"}, fallbacks="default",
                        betas=["server-side-fallback-2026-07-01"],
                    ) as stream:
                        async for event in stream:
                            if event.type == "text":
                                on_text(event.text)
                        response = await stream.get_final_message()
                    break
                except ValueError:   # 読めない道具の入力が来たら呼び直す
                    if attempt == JSON_RETRIES:
                        raise
        except anthropic.AuthenticationError as exc:
            raise LLMAuthError(f"Claude の鍵が使えません。.env の {self.config.api_key_env} を確認してください") from exc
        except anthropic.RateLimitError as exc:
            raise LLMRateLimit("利用の上限に達しました。少し待ってからもう一度送ってください") from exc
        except anthropic.APIConnectionError as exc:
            raise LLMConnectionError("Claude に接続できません。通信を確認してください") from exc
        except anthropic.APIStatusError as exc:
            raise LLMError(f"Claude がエラーを返しました ({exc.status_code})") from exc
        u = response.usage
        return Turn(blocks=_dump(response.content), stop=STOPS.get(response.stop_reason, "end"),
                    usage=Usage(u.input_tokens, u.output_tokens, u.cache_read_input_tokens or 0,
                                u.cache_creation_input_tokens or 0),
                    model=getattr(response, "model", None) or self.config.model)
