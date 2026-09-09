"""Обёртки над OpenAI и Anthropic с нативным tool calling.

Провайдеры возвращают единый `LlmTurn`, поэтому агентный цикл в `react.py`
не знает, чей SDK под ним, и одинаково обрабатывает `tool_calls` OpenAI и
блоки `tool_use` Anthropic.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

from app.config import get_settings
from app.observability.tracing import TokenUsage

settings = get_settings()

# Модели с reasoning-контуром не принимают temperature/max_tokens в привычном виде.
_NO_SAMPLING_PREFIXES = ("o1", "o3", "o4", "gpt-5")


class ToolCallingUnsupported(RuntimeError):
    """Провайдер или модель не приняли native tools — нужен JSON-протокол."""


@dataclass
class ToolCall:
    id: str
    name: str
    args: dict = field(default_factory=dict)
    parse_error: str = ""


@dataclass
class LlmTurn:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: TokenUsage = field(default_factory=TokenUsage)
    stop_reason: str = ""
    raw_message: Any = None
    duration_ms: int = 0

    @property
    def wants_tools(self) -> bool:
        return bool(self.tool_calls)


def _is_tools_rejection(exc: Exception) -> bool:
    text = str(exc).lower()
    if "tool" not in text and "function" not in text:
        return False
    return any(marker in text for marker in ("not supported", "unsupported", "unrecognized", "invalid_request", "does not support"))


class OpenAIProvider:
    name = "openai"

    def __init__(self) -> None:
        from openai import OpenAI

        self.model = settings.openai_model
        self._client = OpenAI(api_key=settings.openai_api_key)

    def complete(self, messages: list[dict], tools: list[dict] | None = None) -> LlmTurn:
        payload: dict[str, Any] = {"model": self.model, "messages": messages}
        if not self.model.startswith(_NO_SAMPLING_PREFIXES):
            payload["temperature"] = settings.llm_temperature
            payload["max_tokens"] = settings.llm_max_tokens
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        started = time.perf_counter()
        try:
            resp = self._client.chat.completions.create(**payload)
        except Exception as exc:
            if tools and _is_tools_rejection(exc):
                raise ToolCallingUnsupported(str(exc)) from exc
            raise
        duration_ms = int((time.perf_counter() - started) * 1000)

        msg = resp.choices[0].message
        calls: list[ToolCall] = []
        for tc in msg.tool_calls or []:
            raw_args = tc.function.arguments or "{}"
            try:
                args = json.loads(raw_args)
                error = ""
            except json.JSONDecodeError as exc:
                args, error = {}, f"невалидный JSON в аргументах: {exc}"
            calls.append(ToolCall(id=tc.id, name=tc.function.name, args=args, parse_error=error))

        usage = TokenUsage(
            prompt_tokens=getattr(resp.usage, "prompt_tokens", 0) or 0,
            completion_tokens=getattr(resp.usage, "completion_tokens", 0) or 0,
        )
        return LlmTurn(
            text=msg.content or "",
            tool_calls=calls,
            usage=usage,
            stop_reason=resp.choices[0].finish_reason or "",
            raw_message=self._assistant_message(msg),
            duration_ms=duration_ms,
        )

    @staticmethod
    def _assistant_message(msg: Any) -> dict:
        out: dict[str, Any] = {"role": "assistant", "content": msg.content or ""}
        if msg.tool_calls:
            out["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments or "{}"},
                }
                for tc in msg.tool_calls
            ]
        return out

    def append_turn(self, messages: list[dict], turn: LlmTurn) -> None:
        messages.append(turn.raw_message)

    def append_tool_results(self, messages: list[dict], results: list[tuple[ToolCall, dict]]) -> None:
        for call, observation in results:
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": json.dumps(observation, ensure_ascii=False, default=str),
                }
            )


class AnthropicProvider:
    name = "anthropic"

    def __init__(self) -> None:
        from anthropic import Anthropic

        self.model = settings.anthropic_model
        self._client = Anthropic(api_key=settings.anthropic_api_key)

    def complete(self, messages: list[dict], tools: list[dict] | None = None) -> LlmTurn:
        system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
        convo = [m for m in messages if m["role"] != "system"]
        payload: dict[str, Any] = {
            "model": self.model,
            "max_tokens": settings.llm_max_tokens,
            "system": system,
            "messages": convo,
            "temperature": settings.llm_temperature,
        }
        if tools:
            payload["tools"] = tools

        started = time.perf_counter()
        try:
            resp = self._client.messages.create(**payload)
        except Exception as exc:
            if tools and _is_tools_rejection(exc):
                raise ToolCallingUnsupported(str(exc)) from exc
            raise
        duration_ms = int((time.perf_counter() - started) * 1000)

        text_parts: list[str] = []
        calls: list[ToolCall] = []
        blocks: list[dict] = []
        for block in resp.content:
            kind = getattr(block, "type", "")
            if kind == "text":
                text_parts.append(block.text)
                blocks.append({"type": "text", "text": block.text})
            elif kind == "tool_use":
                args = block.input if isinstance(block.input, dict) else {}
                calls.append(ToolCall(id=block.id, name=block.name, args=args))
                blocks.append({"type": "tool_use", "id": block.id, "name": block.name, "input": args})

        usage = TokenUsage(
            prompt_tokens=getattr(resp.usage, "input_tokens", 0) or 0,
            completion_tokens=getattr(resp.usage, "output_tokens", 0) or 0,
        )
        return LlmTurn(
            text="\n".join(text_parts),
            tool_calls=calls,
            usage=usage,
            stop_reason=resp.stop_reason or "",
            raw_message={"role": "assistant", "content": blocks},
            duration_ms=duration_ms,
        )

    def append_turn(self, messages: list[dict], turn: LlmTurn) -> None:
        messages.append(turn.raw_message)

    def append_tool_results(self, messages: list[dict], results: list[tuple[ToolCall, dict]]) -> None:
        messages.append(
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": call.id,
                        "content": json.dumps(observation, ensure_ascii=False, default=str),
                        "is_error": bool(observation.get("error")),
                    }
                    for call, observation in results
                ],
            }
        )


def build_provider(name: str):
    if name == "openai":
        return OpenAIProvider()
    if name == "anthropic":
        return AnthropicProvider()
    raise ValueError(f"unknown provider: {name}")
