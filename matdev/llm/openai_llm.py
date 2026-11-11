"""OpenAI 形式の Chat Completions (OpenAI・Azure OpenAI・xAI・ローカルの vLLM・Ollama・LM Studio など)。

設定: {"provider": "openai", "model": "...", "base_url": "http://<マシン>:8000/v1", "api_key_env": "LOCAL_LLM_KEY",
       "price_per_mtok": [0, 0]}
共通の形 (Anthropic 形式のブロック) との変換:
- assistant の text と tool_use → content と tool_calls
- user の tool_result → role "tool" のメッセージ (道具ごとに 1 つ)
- Claude 固有のブロック (thinking など) は送らない
"""
from __future__ import annotations

import json

import openai

from .base import LLM, LLMAuthError, LLMConnectionError, LLMError, LLMRateLimit, OnText, Turn, Usage

STOPS = {"stop": "end", "tool_calls": "tool_use", "function_call": "tool_use", "length": "max_tokens",
         "content_filter": "refusal"}


def to_openai(system: str, messages: list[dict]) -> list[dict]:
    out = [{"role": "system", "content": system}]
    for message in messages:
        blocks = message["content"]
        if message["role"] == "assistant":
            text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
            calls = [{"id": b["id"], "type": "function",
                      "function": {"name": b["name"], "arguments": json.dumps(b.get("input", {}), ensure_ascii=False)}}
                     for b in blocks if b.get("type") == "tool_use"]
            item = {"role": "assistant", "content": text or None}
            if calls:
                item["tool_calls"] = calls
            out.append(item)
            continue
        texts = [b.get("text", "") for b in blocks if b.get("type") == "text"]
        for b in blocks:
            if b.get("type") == "tool_result":
                content = b.get("content", "")
                if not isinstance(content, str):
                    content = json.dumps(content, ensure_ascii=False)
                out.append({"role": "tool", "tool_call_id": b["tool_use_id"],
                            "content": ("[失敗] " if b.get("is_error") else "") + content})
        if texts:
            out.append({"role": "user", "content": "\n".join(texts)})
    return out


def to_openai_tools(tools: list[dict]) -> list[dict]:
    return [{"type": "function", "function": {"name": t["name"], "description": t.get("description", ""),
                                              "parameters": t["input_schema"]}} for t in tools]


class OpenAILLM(LLM):
    def ready(self) -> bool:
        # ローカルの LLM など、鍵の要らない接続先もある (api_key_env が空なら鍵なしで呼ぶ)
        return bool(self.key()) or not self.config.api_key_env

    def new_client(self):
        return openai.AsyncOpenAI(base_url=self.config.base_url, api_key=self.key() or "not-needed")

    async def turn(self, system: str, tools: list[dict], messages: list[dict], on_text: OnText) -> Turn:
        try:
            stream = await self.client().chat.completions.create(
                model=self.config.model, messages=to_openai(system, messages), tools=to_openai_tools(tools),
                max_tokens=self.config.max_tokens, stream=True, stream_options={"include_usage": True})
            text, calls, finish, usage = [], {}, "stop", None
            async for chunk in stream:
                if getattr(chunk, "usage", None):
                    usage = chunk.usage
                if not chunk.choices:
                    continue
                choice = chunk.choices[0]
                delta = choice.delta
                if getattr(delta, "content", None):
                    text.append(delta.content)
                    on_text(delta.content)
                for call in getattr(delta, "tool_calls", None) or []:
                    slot = calls.setdefault(call.index, {"id": None, "name": "", "arguments": ""})
                    if call.id:
                        slot["id"] = call.id
                    if call.function and call.function.name:
                        slot["name"] += call.function.name
                    if call.function and call.function.arguments:
                        slot["arguments"] += call.function.arguments
                if choice.finish_reason:
                    finish = choice.finish_reason
        except openai.AuthenticationError as exc:
            raise LLMAuthError(f"LLM の鍵が使えません。.env の {self.config.api_key_env} を確認してください") from exc
        except openai.RateLimitError as exc:
            raise LLMRateLimit("混雑か利用の上限です。少し待ってからもう一度送ってください") from exc
        except openai.APIConnectionError as exc:
            raise LLMConnectionError(f"LLM ({self.config.base_url or 'OpenAI'}) に接続できません") from exc
        except openai.APIStatusError as exc:
            raise LLMError(f"LLM がエラーを返しました ({exc.status_code})") from exc
        blocks = [{"type": "text", "text": "".join(text)}] if text else []
        for index in sorted(calls):
            slot = calls[index]
            try:
                arguments = json.loads(slot["arguments"] or "{}")
            except json.JSONDecodeError:
                # 読めない入力は、そのまま道具の検査で弾いて LLM に直してもらう
                arguments = {"_unparsed": slot["arguments"]}
            blocks.append({"type": "tool_use", "id": slot["id"] or f"call_{index}", "name": slot["name"],
                           "input": arguments})
        stop = "tool_use" if any(b["type"] == "tool_use" for b in blocks) else STOPS.get(finish, "end")
        return Turn(blocks=blocks, stop=stop,
                    usage=Usage(getattr(usage, "prompt_tokens", 0) or 0, getattr(usage, "completion_tokens", 0) or 0),
                    model=self.config.model)
