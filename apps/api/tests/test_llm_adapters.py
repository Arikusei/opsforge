"""Проверка формата запросов и разбора ответов для native tool calling.

Живые ключи не нужны: SDK-клиент подменяется заглушкой, которая повторяет
форму ответов OpenAI и Anthropic. Это ловит расхождения в проводном формате —
самое хрупкое место при переходе с JSON-протокола на нативные инструменты.
"""

import json
from types import SimpleNamespace

import pytest

from app.agents.providers import (
    AnthropicProvider,
    OpenAIProvider,
    ToolCall,
    ToolCallingUnsupported,
)
from app.agents.schemas import anthropic_tools, openai_tools


class StubOpenAIClient:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.response


def openai_response(content="", tool_calls=(), finish_reason="stop", prompt=100, completion=20):
    message = SimpleNamespace(
        content=content,
        tool_calls=[
            SimpleNamespace(
                id=tc["id"],
                type="function",
                function=SimpleNamespace(name=tc["name"], arguments=tc["arguments"]),
            )
            for tc in tool_calls
        ]
        or None,
    )
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message, finish_reason=finish_reason)],
        usage=SimpleNamespace(prompt_tokens=prompt, completion_tokens=completion),
    )


def build_openai(client) -> OpenAIProvider:
    provider = OpenAIProvider.__new__(OpenAIProvider)
    provider.model = "gpt-4.1-mini"
    provider._client = client
    return provider


def anthropic_response(blocks, stop_reason="end_turn", prompt=120, completion=30):
    content = []
    for block in blocks:
        if block["type"] == "text":
            content.append(SimpleNamespace(type="text", text=block["text"]))
        else:
            content.append(
                SimpleNamespace(type="tool_use", id=block["id"], name=block["name"], input=block["input"])
            )
    return SimpleNamespace(
        content=content,
        stop_reason=stop_reason,
        usage=SimpleNamespace(input_tokens=prompt, output_tokens=completion),
    )


class StubAnthropicClient:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.response


def build_anthropic(client) -> AnthropicProvider:
    provider = AnthropicProvider.__new__(AnthropicProvider)
    provider.model = "claude-sonnet-4-5"
    provider._client = client
    return provider


def test_openai_sends_tools_and_parses_tool_call():
    client = StubOpenAIClient(
        openai_response(
            tool_calls=[{"id": "call_1", "name": "search_catalog", "arguments": '{"query": "NSF H1"}'}],
            finish_reason="tool_calls",
        )
    )
    provider = build_openai(client)
    turn = provider.complete([{"role": "user", "content": "смазка"}], openai_tools())

    sent = client.calls[0]
    assert sent["tool_choice"] == "auto"
    assert {t["function"]["name"] for t in sent["tools"]} == {
        "search_catalog",
        "create_lead",
        "draft_quote",
        "notify_sales",
        "company_policy",
    }
    assert sent["temperature"] == pytest.approx(0.2)

    assert turn.wants_tools
    assert turn.tool_calls[0].name == "search_catalog"
    assert turn.tool_calls[0].args == {"query": "NSF H1"}
    assert turn.usage.prompt_tokens == 100
    assert turn.usage.completion_tokens == 20
    assert turn.stop_reason == "tool_calls"


def test_openai_marks_broken_tool_arguments():
    client = StubOpenAIClient(
        openai_response(tool_calls=[{"id": "call_2", "name": "create_lead", "arguments": "{не json"}])
    )
    turn = build_openai(client).complete([], openai_tools())
    assert turn.tool_calls[0].parse_error
    assert turn.tool_calls[0].args == {}


def test_openai_roundtrip_message_shape():
    client = StubOpenAIClient(
        openai_response(
            content="",
            tool_calls=[{"id": "call_3", "name": "notify_sales", "arguments": '{"text": "hi"}'}],
        )
    )
    provider = build_openai(client)
    turn = provider.complete([], openai_tools())

    messages: list[dict] = []
    provider.append_turn(messages, turn)
    provider.append_tool_results(messages, [(turn.tool_calls[0], {"ok": True})])

    assert messages[0]["role"] == "assistant"
    assert messages[0]["tool_calls"][0]["id"] == "call_3"
    assert messages[0]["tool_calls"][0]["function"]["name"] == "notify_sales"
    assert messages[1]["role"] == "tool"
    assert messages[1]["tool_call_id"] == "call_3"
    assert json.loads(messages[1]["content"]) == {"ok": True}


def test_openai_skips_sampling_params_for_reasoning_models():
    client = StubOpenAIClient(openai_response(content="ok"))
    provider = build_openai(client)
    provider.model = "gpt-5-mini"
    provider.complete([], None)
    assert "temperature" not in client.calls[0]
    assert "max_tokens" not in client.calls[0]


def test_openai_tools_rejection_raises_dedicated_error():
    client = StubOpenAIClient(error=RuntimeError("invalid_request_error: tools are not supported"))
    with pytest.raises(ToolCallingUnsupported):
        build_openai(client).complete([], openai_tools())


def test_openai_other_errors_propagate():
    client = StubOpenAIClient(error=RuntimeError("rate limit exceeded"))
    with pytest.raises(RuntimeError, match="rate limit"):
        build_openai(client).complete([], openai_tools())


def test_anthropic_splits_system_and_parses_tool_use():
    client = StubAnthropicClient(
        anthropic_response(
            [
                {"type": "text", "text": "Ищу в каталоге"},
                {"type": "tool_use", "id": "toolu_1", "name": "company_policy", "input": {"topic": "логистика"}},
            ],
            stop_reason="tool_use",
        )
    )
    provider = build_anthropic(client)
    turn = provider.complete(
        [{"role": "system", "content": "SYS"}, {"role": "user", "content": "вопрос"}],
        anthropic_tools(),
    )

    sent = client.calls[0]
    assert sent["system"] == "SYS"
    assert [m["role"] for m in sent["messages"]] == ["user"]
    assert {t["name"] for t in sent["tools"]} == {
        "search_catalog",
        "create_lead",
        "draft_quote",
        "notify_sales",
        "company_policy",
    }

    assert turn.text == "Ищу в каталоге"
    assert turn.tool_calls[0].id == "toolu_1"
    assert turn.tool_calls[0].args == {"topic": "логистика"}
    assert turn.usage.prompt_tokens == 120
    assert turn.usage.completion_tokens == 30


def test_anthropic_tool_result_block_shape():
    provider = build_anthropic(StubAnthropicClient())
    messages: list[dict] = []
    call = ToolCall(id="toolu_9", name="create_lead")
    provider.append_tool_results(messages, [(call, {"error": "invalid arguments"})])

    block = messages[0]["content"][0]
    assert messages[0]["role"] == "user"
    assert block["type"] == "tool_result"
    assert block["tool_use_id"] == "toolu_9"
    assert block["is_error"] is True


def test_anthropic_assistant_turn_is_serialisable():
    client = StubAnthropicClient(
        anthropic_response(
            [{"type": "tool_use", "id": "toolu_2", "name": "search_catalog", "input": {"query": "СОЖ"}}],
            stop_reason="tool_use",
        )
    )
    provider = build_anthropic(client)
    turn = provider.complete([{"role": "user", "content": "q"}], anthropic_tools())
    messages: list[dict] = []
    provider.append_turn(messages, turn)
    # Ответ ассистента уходит обратно в API как чистый JSON, без объектов SDK.
    assert json.loads(json.dumps(messages[0])) == messages[0]
