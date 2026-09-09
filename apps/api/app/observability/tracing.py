"""Трейсинг агентных прогонов: шаги, токены, стоимость, латентность.

Трейс собирается в память, затем пишется в SQLite (`agent_runs`, `llm_calls`),
экспортируется в Prometheus и опционально отправляется на webhook (например,
в n8n — оттуда удобно строить алерты по стоимости и падениям инструментов).
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from prometheus_client import Counter, Histogram
from sqlalchemy.orm import Session

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

# USD за 1M токенов: (вход, выход). Ключ ищется по самому длинному совпадающему префиксу.
PRICING_USD_PER_1M: dict[str, tuple[float, float]] = {
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4.1-nano": (0.10, 0.40),
    "gpt-4.1": (2.00, 8.00),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.00),
    "claude-haiku": (0.80, 4.00),
    "claude-sonnet": (3.00, 15.00),
    "claude-opus": (15.00, 75.00),
}

LLM_CALLS = Counter(
    "opsforge_llm_calls_total", "Запросы к LLM", ["provider", "model", "status"]
)
LLM_TOKENS = Counter(
    "opsforge_llm_tokens_total", "Токены LLM", ["provider", "model", "kind"]
)
LLM_COST = Counter(
    "opsforge_llm_cost_usd_total", "Расчётная стоимость LLM в USD", ["provider", "model"]
)
LLM_LATENCY = Histogram(
    "opsforge_llm_latency_seconds",
    "Латентность запроса к LLM",
    ["provider", "model"],
    buckets=(0.25, 0.5, 1, 2, 4, 8, 15, 30, 60),
)
AGENT_RUNS = Counter(
    "opsforge_agent_runs_total", "Прогоны агента", ["provider", "mode", "outcome"]
)
AGENT_LATENCY = Histogram(
    "opsforge_agent_latency_seconds",
    "Латентность полного прогона агента",
    ["provider", "mode"],
    buckets=(0.5, 1, 2, 5, 10, 20, 45, 90),
)
AGENT_ITERATIONS = Histogram(
    "opsforge_agent_iterations",
    "Число итераций агентного цикла",
    ["provider", "mode"],
    buckets=(1, 2, 3, 4, 5, 6, 8),
)
TOOL_CALLS = Counter(
    "opsforge_agent_tool_calls_total", "Вызовы инструментов агентом", ["tool", "status"]
)


@dataclass
class TokenUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def __add__(self, other: TokenUsage) -> TokenUsage:
        return TokenUsage(
            self.prompt_tokens + other.prompt_tokens,
            self.completion_tokens + other.completion_tokens,
        )


def _price_overrides() -> dict[str, tuple[float, float]]:
    raw = (settings.llm_price_overrides or "").strip()
    if not raw:
        return {}
    out: dict[str, tuple[float, float]] = {}
    for entry in raw.split(";"):
        if ":" not in entry:
            continue
        model, prices = entry.split(":", 1)
        parts = prices.split(",")
        if len(parts) != 2:
            continue
        try:
            out[model.strip()] = (float(parts[0]), float(parts[1]))
        except ValueError:
            continue
    return out


def estimate_cost_usd(model: str, usage: TokenUsage) -> float:
    """Оценка стоимости по прайсу. Неизвестная модель даёт 0 — без выдумывания цифр."""
    table = {**PRICING_USD_PER_1M, **_price_overrides()}
    matches = [key for key in table if model.startswith(key)]
    if not matches:
        return 0.0
    key = max(matches, key=len)
    price_in, price_out = table[key]
    cost = usage.prompt_tokens / 1_000_000 * price_in + usage.completion_tokens / 1_000_000 * price_out
    return round(cost, 6)


@dataclass
class AgentTracer:
    provider: str
    model: str = ""
    mode: str = "demo"
    channel: str = "web"
    question: str = ""
    run_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    steps: list[dict] = field(default_factory=list)
    llm_calls: list[dict] = field(default_factory=list)
    usage: TokenUsage = field(default_factory=TokenUsage)
    cost_usd: float = 0.0
    iterations: int = 0
    outcome: str = "answered"
    answer: str = ""
    warning: str = ""
    _started: float = field(default_factory=time.perf_counter)
    _latency_ms: int = 0

    def record_retrieval(self, query: str, hits: list[dict], duration_ms: int) -> None:
        self.steps.append(
            {
                "type": "retrieval",
                "action": "rag.retrieve",
                "args": {"query": query},
                "observation": {
                    "hits": len(hits),
                    "sources": [h["source"] for h in hits],
                    "top_score": hits[0]["score"] if hits else 0,
                },
                "duration_ms": duration_ms,
                "ok": True,
            }
        )

    def record_llm_call(
        self,
        *,
        usage: TokenUsage,
        duration_ms: int,
        stop_reason: str = "",
        requested_tools: list[str] | None = None,
        error: str = "",
    ) -> None:
        cost = estimate_cost_usd(self.model, usage)
        self.usage = self.usage + usage
        self.cost_usd = round(self.cost_usd + cost, 6)
        step = len(self.llm_calls) + 1
        self.llm_calls.append(
            {
                "step": step,
                "provider": self.provider,
                "model": self.model,
                "stop_reason": stop_reason,
                "prompt_tokens": usage.prompt_tokens,
                "completion_tokens": usage.completion_tokens,
                "cost_usd": cost,
                "latency_ms": duration_ms,
                "requested_tools": ",".join(requested_tools or [])[:255],
                "error": error[:2000],
            }
        )
        status = "error" if error else "ok"
        LLM_CALLS.labels(self.provider, self.model, status).inc()
        LLM_LATENCY.labels(self.provider, self.model).observe(duration_ms / 1000)
        if usage.prompt_tokens:
            LLM_TOKENS.labels(self.provider, self.model, "prompt").inc(usage.prompt_tokens)
        if usage.completion_tokens:
            LLM_TOKENS.labels(self.provider, self.model, "completion").inc(usage.completion_tokens)
        if cost:
            LLM_COST.labels(self.provider, self.model).inc(cost)

    def record_tool_call(
        self,
        name: str,
        args: dict,
        observation: Any,
        duration_ms: int,
        *,
        thought: str = "",
        call_id: str = "",
    ) -> None:
        ok = not (isinstance(observation, dict) and observation.get("error"))
        self.steps.append(
            {
                "type": "tool",
                "thought": thought or f"Вызов инструмента {name}",
                "action": name,
                "call_id": call_id,
                "args": args,
                "observation": observation,
                "duration_ms": duration_ms,
                "ok": ok,
            }
        )
        TOOL_CALLS.labels(name, "ok" if ok else "error").inc()

    def finish(self, answer: str, outcome: str = "answered", warning: str = "") -> None:
        self.answer = answer
        self.outcome = outcome
        self.warning = warning
        self._latency_ms = int((time.perf_counter() - self._started) * 1000)
        AGENT_RUNS.labels(self.provider, self.mode, outcome).inc()
        AGENT_LATENCY.labels(self.provider, self.mode).observe(self._latency_ms / 1000)
        AGENT_ITERATIONS.labels(self.provider, self.mode).observe(self.iterations)

    @property
    def latency_ms(self) -> int:
        return self._latency_ms or int((time.perf_counter() - self._started) * 1000)

    @property
    def tool_call_count(self) -> int:
        return sum(1 for s in self.steps if s["type"] == "tool")

    def usage_dict(self) -> dict:
        return {
            "prompt_tokens": self.usage.prompt_tokens,
            "completion_tokens": self.usage.completion_tokens,
            "total_tokens": self.usage.total_tokens,
            "cost_usd": self.cost_usd,
            "llm_calls": len(self.llm_calls),
            "latency_ms": self.latency_ms,
        }

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "provider": self.provider,
            "model": self.model,
            "mode": self.mode,
            "channel": self.channel,
            "question": self.question,
            "answer": self.answer,
            "outcome": self.outcome,
            "iterations": self.iterations,
            "warning": self.warning,
            "usage": self.usage_dict(),
            "steps": self.steps,
            "llm_calls": self.llm_calls,
        }

    def persist(self, db: Session) -> None:
        """Пишет трейс в БД и шлёт на webhook. Любая ошибка здесь не должна ломать ответ."""
        if settings.trace_persist:
            try:
                self._write_db(db)
            except Exception:
                db.rollback()
                logger.exception("не удалось сохранить трейс агента run_id=%s", self.run_id)
        if settings.trace_webhook_url:
            self._post_webhook()

    def _write_db(self, db: Session) -> None:
        from app.models import AgentRun, LlmCall

        db.add(
            AgentRun(
                run_id=self.run_id,
                channel=self.channel,
                provider=self.provider,
                model=self.model,
                mode=self.mode,
                question=self.question,
                answer=self.answer,
                outcome=self.outcome,
                iterations=self.iterations,
                tool_calls=self.tool_call_count,
                prompt_tokens=self.usage.prompt_tokens,
                completion_tokens=self.usage.completion_tokens,
                cost_usd=self.cost_usd,
                latency_ms=self.latency_ms,
                trace=json.dumps(self.steps, ensure_ascii=False, default=str),
                warning=self.warning,
            )
        )
        for call in self.llm_calls:
            db.add(LlmCall(run_id=self.run_id, **call))
        db.commit()

    def _post_webhook(self) -> None:
        import httpx

        try:
            httpx.post(
                settings.trace_webhook_url,
                json=self.to_dict(),
                headers={"X-Webhook-Secret": settings.webhook_secret},
                timeout=3.0,
            )
        except Exception as exc:
            logger.warning("трейс не доставлен на webhook: %s", exc)
