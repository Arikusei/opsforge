from __future__ import annotations

import json
import re
import time
from typing import Any

from sqlalchemy.orm import Session

from app.agents.providers import ToolCallingUnsupported, build_provider
from app.agents.schemas import anthropic_tools, openai_tools, tool_catalog_text
from app.agents.tools import TOOLS, run_tool
from app.config import get_settings
from app.observability.tracing import AgentTracer, TokenUsage
from app.rag.retrieve import retrieve

settings = get_settings()

SYSTEM_PROMPT = """Ты — AI-инженерный ассистент OpsForge, демо-дистрибьютора промышленных смазок и покрытий.
Отвечай по-русски, кратко и по делу. Опирайся на найденные фрагменты базы знаний и на результаты инструментов.

Правила:
- Не выдумывай NSF-допуски, сроки поставки и цены. Если данных нет — скажи, что нужна эскалация технологу.
- Точное окно доставки не обещай: слот подтверждает логист.
- Перед подбором продукта вызывай search_catalog, а не отвечай по памяти.
- create_lead вызывай только когда известны и компания, и задача клиента. Не хватает данных — спроси.
- В финальном ответе перечисли, что ты сделал (созданный лид, отправленное уведомление) с идентификаторами.
"""

JSON_PROTOCOL_PROMPT = f"""Инструменты вызывать нативно нельзя, используй JSON-протокол.
Доступные инструменты:
{tool_catalog_text()}

Если нужен инструмент, верни строго JSON:
{{"tool": "название", "args": {{...}}}}
Иначе верни строго JSON:
{{"answer": "текст ответа", "citations": ["источник"]}}
"""

MAX_OBSERVATION_CHARS = 800


def available_provider() -> str:
    mode = (settings.llm_provider or "auto").lower()
    if mode == "demo":
        return "demo"
    if mode in {"openai", "auto"} and settings.openai_api_key:
        return "openai"
    if mode in {"anthropic", "auto"} and settings.anthropic_api_key:
        return "anthropic"
    return "demo"


def agent_capabilities() -> dict:
    provider = available_provider()
    return {
        "provider": provider,
        "model": _model_for(provider),
        "mode": _mode_for(provider),
        "native_tools_enabled": settings.agent_native_tools,
        "max_iterations": settings.agent_max_iterations,
        "tools": [{"name": name, "description": spec["description"]} for name, spec in TOOLS.items()],
    }


def run_agent(
    db: Session,
    question: str,
    history: list[dict] | None = None,
    channel: str = "web",
) -> dict[str, Any]:
    provider = available_provider()
    tracer = AgentTracer(
        provider=provider,
        model=_model_for(provider),
        mode=_mode_for(provider),
        channel=channel,
        question=question,
    )

    started = time.perf_counter()
    context = retrieve(db, question, limit=5)
    tracer.record_retrieval(question, context, int((time.perf_counter() - started) * 1000))

    if provider == "demo":
        result = _demo_agent(db, question, context, tracer)
        tracer.persist(db)
        return result

    try:
        result = _llm_loop(db, question, context, history or [], provider, tracer)
    except ToolCallingUnsupported as exc:
        tracer.mode = "json_protocol"
        try:
            result = _llm_loop(db, question, context, history or [], provider, tracer, native_tools=False)
            result["warning"] = f"Модель не поддерживает native tools ({exc}). Использован JSON-протокол."
        except Exception as inner:
            result = _fallback_to_demo(db, question, context, tracer, inner)
    except Exception as exc:
        result = _fallback_to_demo(db, question, context, tracer, exc)

    tracer.persist(db)
    return result


def _fallback_to_demo(
    db: Session, question: str, context: list[dict], tracer: AgentTracer, exc: Exception
) -> dict[str, Any]:
    tracer.provider = "demo"
    tracer.mode = "demo"
    result = _demo_agent(db, question, context, tracer, outcome="fallback")
    result["warning"] = f"LLM недоступен ({exc}). Использован demo-контур."
    result["provider"] = "demo"
    tracer.warning = result["warning"]
    return result


def _llm_loop(
    db: Session,
    question: str,
    context: list[dict],
    history: list[dict],
    provider: str,
    tracer: AgentTracer,
    native_tools: bool = True,
) -> dict[str, Any]:
    use_native = native_tools and settings.agent_native_tools
    client = build_provider(provider)
    tracer.model = client.model
    tracer.mode = "native_tools" if use_native else "json_protocol"

    tools = None
    if use_native:
        tools = openai_tools() if provider == "openai" else anthropic_tools()

    messages = _build_messages(question, context, history, use_native)
    citations = [c["source"] for c in context[:3]]

    for step in range(settings.agent_max_iterations):
        tracer.iterations = step + 1
        turn = _complete(client, messages, tools, tracer)

        calls = turn.tool_calls if use_native else _json_tool_calls(turn.text)
        if not calls:
            answer, extra_citations = _final_answer(turn.text, use_native)
            return _pack(answer or _no_answer_text(), context, tracer, extra_citations or citations)

        client.append_turn(messages, turn)
        results = []
        for call in calls:
            observation = _execute(db, call, tracer)
            citations.extend(_citations_from_observation(observation))
            results.append((call, _compact(observation)))
        client.append_tool_results(messages, results)

    # Итерации исчерпаны: просим модель ответить без инструментов, чтобы не отдавать пустоту.
    tracer.iterations = settings.agent_max_iterations
    messages.append(
        {
            "role": "user",
            "content": "Лимит вызовов инструментов исчерпан. Дай финальный ответ по уже собранным данным.",
        }
    )
    turn = _complete(client, messages, None, tracer)
    answer, extra_citations = _final_answer(turn.text, use_native)
    return _pack(
        answer or _no_answer_text(),
        context,
        tracer,
        extra_citations or citations,
        outcome="truncated",
        warning="Достигнут лимит итераций агента.",
    )


def _complete(client, messages: list[dict], tools: list[dict] | None, tracer: AgentTracer):
    try:
        turn = client.complete(messages, tools)
    except ToolCallingUnsupported:
        raise
    except Exception as exc:
        tracer.record_llm_call(usage=TokenUsage(), duration_ms=0, error=str(exc))
        raise
    tracer.record_llm_call(
        usage=turn.usage,
        duration_ms=turn.duration_ms,
        stop_reason=turn.stop_reason,
        requested_tools=[c.name for c in turn.tool_calls],
    )
    return turn


def _execute(db: Session, call, tracer: AgentTracer) -> dict:
    started = time.perf_counter()
    if call.parse_error:
        observation = {
            "error": call.parse_error,
            "hint": "Передай аргументы валидным JSON-объектом согласно схеме инструмента.",
        }
    else:
        observation = run_tool(db, call.name, call.args)
    tracer.record_tool_call(
        call.name,
        call.args,
        observation,
        int((time.perf_counter() - started) * 1000),
        call_id=call.id,
    )
    return observation


def _build_messages(question: str, context: list[dict], history: list[dict], use_native: bool) -> list[dict]:
    system = SYSTEM_PROMPT if use_native else f"{SYSTEM_PROMPT}\n{JSON_PROTOCOL_PROMPT}"
    messages: list[dict] = [{"role": "system", "content": system}]
    for item in history[-6:]:
        if item.get("role") in {"user", "assistant"} and item.get("content"):
            messages.append({"role": item["role"], "content": item["content"]})
    ctx = "\n\n".join(f"[{c['source']}] {c['content']}" for c in context)
    messages.append({"role": "user", "content": f"Контекст RAG:\n{ctx}\n\nВопрос: {question}"})
    return messages


def _final_answer(text: str, use_native: bool) -> tuple[str, list[str]]:
    """В native-режиме текст модели и есть ответ, в JSON-режиме его надо распарсить."""
    if use_native:
        return text.strip(), []
    parsed = _parse_json(text)
    return (parsed.get("answer") or text).strip(), parsed.get("citations") or []


def _json_tool_calls(text: str) -> list:
    from app.agents.providers import ToolCall

    parsed = _parse_json(text)
    name = parsed.get("tool")
    if not name:
        return []
    args = parsed.get("args")
    if not isinstance(args, dict):
        return [ToolCall(id="json-0", name=name, args={}, parse_error="args должен быть объектом")]
    return [ToolCall(id="json-0", name=name, args=args)]


def _citations_from_observation(observation: Any) -> list[str]:
    if not isinstance(observation, dict):
        return []
    found: list[str] = []
    for key in ("knowledge", "hits", "sources"):
        for item in observation.get(key) or []:
            if isinstance(item, dict) and item.get("source"):
                found.append(item["source"])
    return found


def _compact(value: Any) -> Any:
    """Урезает длинные строки перед отправкой обратно в модель — экономит токены."""
    if isinstance(value, str):
        return value if len(value) <= MAX_OBSERVATION_CHARS else value[:MAX_OBSERVATION_CHARS] + "…"
    if isinstance(value, dict):
        return {k: _compact(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_compact(v) for v in value[:5]]
    return value


def _no_answer_text() -> str:
    return "Не удалось получить устойчивый ответ модели. Эскалируйте технологу."


def _model_for(provider: str) -> str:
    if provider == "openai":
        return settings.openai_model
    if provider == "anthropic":
        return settings.anthropic_model
    return "rule-based"


def _mode_for(provider: str) -> str:
    if provider == "demo":
        return "demo"
    return "native_tools" if settings.agent_native_tools else "json_protocol"


def _demo_agent(
    db: Session,
    question: str,
    context: list[dict],
    tracer: AgentTracer,
    outcome: str = "answered",
) -> dict[str, Any]:
    q = question.lower()
    citations = [c["source"] for c in context[:3]]
    tracer.iterations = 1

    if any(word in q for word in ("создай лид", "заявк", "в amo", "в crm")):
        args = {
            "company": _extract_company(question) or "Клиент из чата",
            "need": question,
            "source": "employee-bot",
            "tags": _guess_tags(q),
        }
        obs = _demo_tool(db, "create_lead", args, tracer, "Нужно зафиксировать заявку в CRM.")
        answer = (
            f"Создал лид «{obs['company']}», стадия {obs['stage']}, CRM id {obs['crm_id']}. "
            "Отдел продаж получил уведомление в TeamChat."
        )
        return _pack(answer, context, tracer, citations, outcome=outcome)

    if any(word in q for word in ("кп", "коммерческ", "предложен")):
        args = {"company": _extract_company(question) or "Клиент", "need": question}
        obs = _demo_tool(db, "draft_quote", args, tracer, "Собрать черновик КП по базе знаний.")
        return _pack(obs["draft"], context, tracer, citations, outcome=outcome)

    obs = _demo_tool(
        db, "search_catalog", {"query": question}, tracer, "Сначала ищу в каталоге и регламентах."
    )

    lines = ["Подбор по демо-базе знаний:"]
    for product in obs["products"][:3]:
        lines.append(f"• {product['name']} ({product['sku']}, остаток {product['stock']}) — {product['summary']}")
    if context:
        lines.append("")
        lines.append(context[0]["content"].split("\n\n")[0][:500])
    if "срок" in q or "достав" in q or "склад" in q:
        lines.append("Логистика: склад в демо-склада, слот подтверждает логист. Бот не обещает точное окно.")
    lines.append("Если нужно — могу создать лид в CRM или набросать КП.")
    return _pack("\n".join(lines), context, tracer, citations, outcome=outcome)


def _demo_tool(db: Session, name: str, args: dict, tracer: AgentTracer, thought: str) -> dict:
    started = time.perf_counter()
    obs = run_tool(db, name, args)
    tracer.record_tool_call(
        name, args, obs, int((time.perf_counter() - started) * 1000), thought=thought
    )
    return obs


def _parse_json(raw: str) -> dict:
    match = re.search(r"\{[\s\S]*\}", raw or "")
    if not match:
        return {"answer": raw}
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return {"answer": raw}


def _extract_company(text: str) -> str | None:
    match = re.search(r"(ООО|АО|ПАО|ИП)\s+[«\"]?[\w\-\s]+", text, re.I)
    return match.group(0).strip(" «»\"") if match else None


def _guess_tags(q: str) -> str:
    tags = []
    if any(w in q for w in ("пищев", "nsf", "h1")):
        tags.append("food")
    if any(w in q for w in ("покрыт", "крепёж", "крепеж", "антипригар")):
        tags.append("coatings")
    if any(w in q for w in ("сож", "металл", "эмульс")):
        tags.append("metalworking")
    if any(w in q for w in ("масл", "гидра", "парк", "forge-fleet")):
        tags.append("fuels")
    return ",".join(tags)


def _pack(
    answer: str,
    context: list[dict],
    tracer: AgentTracer,
    citations: list[str],
    outcome: str = "answered",
    warning: str = "",
) -> dict[str, Any]:
    unique_citations = list(dict.fromkeys(citations))
    tracer.finish(answer, outcome=outcome, warning=warning)
    result = {
        "run_id": tracer.run_id,
        "answer": answer,
        "provider": tracer.provider,
        "model": tracer.model,
        "mode": tracer.mode,
        "citations": unique_citations,
        "context": context,
        "trace": tracer.steps,
        "usage": tracer.usage_dict(),
        "iterations": tracer.iterations,
    }
    if warning:
        result["warning"] = warning
    return result
