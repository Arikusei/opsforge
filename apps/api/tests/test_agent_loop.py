"""Агентный цикл: многошаговость, самокоррекция, лимит итераций, фолбэки, трейсинг."""

import json

import pytest

from app.agents import react
from app.agents.providers import LlmTurn, ToolCall, ToolCallingUnsupported
from app.db import SessionLocal
from app.main import init_db
from app.models import AgentRun, LlmCall
from app.observability.tracing import TokenUsage, estimate_cost_usd

init_db()


class FakeProvider:
    """Возвращает заранее заданные ходы и копит отправленные сообщения."""

    name = "openai"

    def __init__(self, turns, model="gpt-4.1-mini"):
        self.model = model
        self._turns = list(turns)
        self.sent_tools = []
        self.messages_snapshots = []

    def complete(self, messages, tools=None):
        self.sent_tools.append(tools)
        self.messages_snapshots.append(list(messages))
        if not self._turns:
            return LlmTurn(text="Финальный ответ по собранным данным.", usage=TokenUsage(10, 5))
        turn = self._turns.pop(0)
        if isinstance(turn, Exception):
            raise turn
        return turn

    def append_turn(self, messages, turn):
        messages.append({"role": "assistant", "content": turn.text or ""})

    def append_tool_results(self, messages, results):
        for call, observation in results:
            messages.append(
                {"role": "tool", "tool_call_id": call.id, "content": json.dumps(observation, ensure_ascii=False)}
            )


def tool_turn(name, args, call_id="c1", usage=(100, 20)):
    return LlmTurn(
        tool_calls=[ToolCall(id=call_id, name=name, args=args)],
        usage=TokenUsage(*usage),
        stop_reason="tool_calls",
    )


def text_turn(text, usage=(50, 30)):
    return LlmTurn(text=text, usage=TokenUsage(*usage), stop_reason="stop")


@pytest.fixture
def db():
    session = SessionLocal()
    yield session
    session.close()


@pytest.fixture
def openai_mode(monkeypatch):
    monkeypatch.setattr(react.settings, "openai_api_key", "test-key")
    monkeypatch.setattr(react.settings, "llm_provider", "openai")
    monkeypatch.setattr(react.settings, "agent_native_tools", True)


def use_provider(monkeypatch, provider):
    monkeypatch.setattr(react, "build_provider", lambda name: provider)
    return provider


def test_native_loop_runs_tool_then_answers(db, monkeypatch, openai_mode):
    provider = use_provider(
        monkeypatch,
        FakeProvider(
            [
                tool_turn("search_catalog", {"query": "NSF H1 фасовка"}),
                text_turn("Подходит ForgeFood 397 с допуском NSF H1."),
            ]
        ),
    )

    result = react.run_agent(db, "Смазка для пищевой фасовки?")

    assert result["mode"] == "native_tools"
    assert result["provider"] == "openai"
    assert result["iterations"] == 2
    assert "ForgeFood 397" in result["answer"]

    tool_steps = [s for s in result["trace"] if s["type"] == "tool"]
    assert [s["action"] for s in tool_steps] == ["search_catalog"]
    assert tool_steps[0]["ok"] is True
    assert tool_steps[0]["call_id"] == "c1"

    # Схемы инструментов уходят в API на первом ходе.
    assert provider.sent_tools[0] is not None
    assert result["usage"]["prompt_tokens"] == 150
    assert result["usage"]["completion_tokens"] == 50
    assert result["usage"]["llm_calls"] == 2


def test_multi_step_chain_search_then_lead(db, monkeypatch, openai_mode):
    use_provider(
        monkeypatch,
        FakeProvider(
            [
                tool_turn("search_catalog", {"query": "NSF H1"}, call_id="a"),
                tool_turn(
                    "create_lead",
                    {"company": "ООО «ПищеЛиния»", "need": "смазка NSF H1 на фасовку", "tags": "food"},
                    call_id="b",
                ),
                text_turn("Создал лид и подобрал ForgeFood 397."),
            ]
        ),
    )

    result = react.run_agent(db, "Подбери NSF смазку и создай лид на ООО «ПищеЛиния»")

    actions = [s["action"] for s in result["trace"] if s["type"] == "tool"]
    assert actions == ["search_catalog", "create_lead"]
    lead_step = next(s for s in result["trace"] if s["action"] == "create_lead")
    assert lead_step["observation"]["crm_id"]
    assert lead_step["observation"]["company"] == "ООО «ПищеЛиния»"


def test_invalid_tool_args_return_error_and_allow_self_correction(db, monkeypatch, openai_mode):
    provider = use_provider(
        monkeypatch,
        FakeProvider(
            [
                # Обязательное поле company пустое — инструмент не должен создать лид.
                tool_turn("create_lead", {"company": "", "need": "смазка"}, call_id="bad"),
                tool_turn("create_lead", {"company": "ООО «Ромашка»", "need": "смазка"}, call_id="good"),
                text_turn("Лид создан."),
            ]
        ),
    )

    result = react.run_agent(db, "Создай лид")

    steps = [s for s in result["trace"] if s["type"] == "tool"]
    assert steps[0]["ok"] is False
    assert steps[0]["observation"]["error"] == "invalid arguments"
    assert steps[1]["ok"] is True

    # Ошибка инструмента вернулась в модель как наблюдение — на этом строится самокоррекция.
    last_messages = provider.messages_snapshots[-1]
    assert any(m.get("role") == "tool" and "invalid arguments" in m["content"] for m in last_messages)


def test_unknown_tool_is_reported_not_raised(db, monkeypatch, openai_mode):
    use_provider(
        monkeypatch,
        FakeProvider([tool_turn("delete_crm", {}, call_id="x"), text_turn("Такого инструмента нет.")]),
    )
    result = react.run_agent(db, "Удали всё из CRM")
    step = next(s for s in result["trace"] if s["type"] == "tool")
    assert step["ok"] is False
    assert "unknown tool" in step["observation"]["error"]


def test_iteration_limit_forces_final_answer(db, monkeypatch, openai_mode):
    monkeypatch.setattr(react.settings, "agent_max_iterations", 3)
    provider = use_provider(
        monkeypatch,
        FakeProvider([tool_turn("search_catalog", {"query": f"q{i}"}, call_id=f"c{i}") for i in range(3)]),
    )

    result = react.run_agent(db, "Зацикленный запрос")

    assert result["iterations"] == 3
    assert result["warning"] == "Достигнут лимит итераций агента."
    assert result["answer"]
    # Финальный добивающий вызов идёт без инструментов, чтобы модель не звала их снова.
    assert provider.sent_tools[-1] is None


def test_tool_calling_unsupported_switches_to_json_protocol(db, monkeypatch, openai_mode):
    calls = {"n": 0}

    def build(name):
        calls["n"] += 1
        if calls["n"] == 1:
            return FakeProvider([ToolCallingUnsupported("tools are not supported")])
        return FakeProvider([text_turn('{"answer": "Ответ через JSON-протокол", "citations": ["processes/logistics.md"]}')])

    monkeypatch.setattr(react, "build_provider", build)

    result = react.run_agent(db, "Режим работы склада?")

    assert result["mode"] == "json_protocol"
    assert result["answer"] == "Ответ через JSON-протокол"
    assert result["citations"] == ["processes/logistics.md"]
    assert "native tools" in result["warning"]


def test_json_protocol_parses_tool_directive(db, monkeypatch, openai_mode):
    monkeypatch.setattr(react.settings, "agent_native_tools", False)
    provider = use_provider(
        monkeypatch,
        FakeProvider(
            [
                text_turn('{"tool": "company_policy", "args": {"topic": "логистика"}}'),
                text_turn('{"answer": "Склад в демо-склада, 09:00-18:00."}'),
            ]
        ),
    )

    result = react.run_agent(db, "Где склад?")

    assert result["mode"] == "json_protocol"
    assert provider.sent_tools[0] is None
    assert [s["action"] for s in result["trace"] if s["type"] == "tool"] == ["company_policy"]
    assert "демо-склада" in result["answer"]


def test_llm_failure_falls_back_to_demo(db, monkeypatch, openai_mode):
    use_provider(monkeypatch, FakeProvider([RuntimeError("502 upstream error")]))

    result = react.run_agent(db, "Какая смазка с NSF H1 для пищевого производства?")

    assert result["provider"] == "demo"
    assert result["mode"] == "demo"
    assert "502 upstream error" in result["warning"]
    assert result["answer"]
    assert result["citations"]


def test_run_is_persisted_with_usage_and_steps(db, monkeypatch, openai_mode):
    use_provider(
        monkeypatch,
        FakeProvider([tool_turn("search_catalog", {"query": "СОЖ"}), text_turn("Подходит ForgeSozh 632.")]),
    )

    result = react.run_agent(db, "СОЖ для легированной стали", channel="eval")

    run = db.query(AgentRun).filter(AgentRun.run_id == result["run_id"]).one()
    assert run.channel == "eval"
    assert run.mode == "native_tools"
    assert run.outcome == "answered"
    assert run.iterations == 2
    assert run.tool_calls == 1
    assert run.prompt_tokens == 150
    assert run.cost_usd > 0
    assert json.loads(run.trace)

    calls = db.query(LlmCall).filter(LlmCall.run_id == result["run_id"]).order_by(LlmCall.step).all()
    assert [c.step for c in calls] == [1, 2]
    assert calls[0].requested_tools == "search_catalog"
    assert calls[1].stop_reason == "stop"


def test_failed_llm_call_is_traced_with_error(db, monkeypatch, openai_mode):
    use_provider(monkeypatch, FakeProvider([RuntimeError("timeout")]))
    result = react.run_agent(db, "Вопрос")

    calls = db.query(LlmCall).filter(LlmCall.run_id == result["run_id"]).all()
    assert len(calls) == 1
    assert "timeout" in calls[0].error

    run = db.query(AgentRun).filter(AgentRun.run_id == result["run_id"]).one()
    assert run.outcome == "fallback"


def test_history_is_passed_to_model(db, monkeypatch, openai_mode):
    provider = use_provider(monkeypatch, FakeProvider([text_turn("Ответ")]))
    history = [
        {"role": "user", "content": "Первый вопрос"},
        {"role": "assistant", "content": "Первый ответ"},
    ]
    react.run_agent(db, "Второй вопрос", history=history)

    roles = [(m["role"], m["content"]) for m in provider.messages_snapshots[0]]
    assert ("user", "Первый вопрос") in roles
    assert ("assistant", "Первый ответ") in roles


def test_observations_are_compacted_before_returning_to_model(db, monkeypatch, openai_mode):
    provider = use_provider(
        monkeypatch, FakeProvider([tool_turn("search_catalog", {"query": "СОЖ"}), text_turn("ок")])
    )
    react.run_agent(db, "СОЖ")

    tool_message = next(
        m for m in provider.messages_snapshots[-1] if m.get("role") == "tool"
    )
    payload = json.loads(tool_message["content"])
    for hit in payload.get("knowledge", []):
        assert len(hit["content"]) <= react.MAX_OBSERVATION_CHARS + 1


def test_cost_estimation_uses_longest_matching_prefix():
    usage = TokenUsage(1_000_000, 1_000_000)
    assert estimate_cost_usd("gpt-4.1-mini", usage) == pytest.approx(2.0)
    assert estimate_cost_usd("gpt-4.1", usage) == pytest.approx(10.0)
    assert estimate_cost_usd("claude-sonnet-4-5", usage) == pytest.approx(18.0)
    # Неизвестная модель не должна давать выдуманную цифру.
    assert estimate_cost_usd("some-local-llama", usage) == 0.0


def test_capabilities_expose_tools_and_limits(monkeypatch, openai_mode):
    caps = react.agent_capabilities()
    assert caps["provider"] == "openai"
    assert caps["mode"] == "native_tools"
    assert {t["name"] for t in caps["tools"]} == {
        "search_catalog",
        "create_lead",
        "draft_quote",
        "notify_sales",
        "company_policy",
    }
