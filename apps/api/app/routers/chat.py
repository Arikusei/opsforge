from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.agents.react import agent_capabilities, available_provider, run_agent
from app.db import get_db
from app.models import AgentRun, Conversation, LlmCall, Message

router = APIRouter(prefix="/api", tags=["chat"])


class ChatIn(BaseModel):
    message: str
    conversation_id: int | None = None
    channel: str = "web"


class ChatOut(BaseModel):
    conversation_id: int
    run_id: str
    answer: str
    provider: str
    model: str = ""
    mode: str = "demo"
    iterations: int = 0
    usage: dict = Field(default_factory=dict)
    citations: list[str] = Field(default_factory=list)
    context: list[dict] = Field(default_factory=list)
    trace: list[dict] = Field(default_factory=list)
    warning: str | None = None


@router.post("/chat", response_model=ChatOut)
def chat(body: ChatIn, db: Session = Depends(get_db)) -> ChatOut:
    conv = db.get(Conversation, body.conversation_id) if body.conversation_id else None
    if conv is None:
        conv = Conversation(channel=body.channel, title=body.message[:80])
        db.add(conv)
        db.commit()
        db.refresh(conv)
    db.add(Message(conversation_id=conv.id, role="user", content=body.message))
    db.commit()
    history = _history(db, conv.id)
    result = run_agent(db, body.message, history=history, channel=body.channel)
    db.add(
        Message(
            conversation_id=conv.id,
            role="assistant",
            content=result["answer"],
            meta=json.dumps(
                {
                    "run_id": result.get("run_id"),
                    "citations": result.get("citations"),
                    "provider": result.get("provider"),
                    "usage": result.get("usage"),
                },
                ensure_ascii=False,
            ),
        )
    )
    db.commit()
    passthrough = ("run_id", "answer", "provider", "model", "mode", "iterations", "usage", "citations", "context", "trace")
    return ChatOut(
        conversation_id=conv.id,
        **{k: result[k] for k in passthrough if k in result},
        warning=result.get("warning"),
    )


@router.get("/chat/status")
def chat_status() -> dict:
    return {"provider": available_provider(), **agent_capabilities()}


@router.get("/agent/runs")
def agent_runs(limit: int = 20, db: Session = Depends(get_db)) -> dict:
    """Сводка по прогонам агента: стоимость, токены, латентность, доля фолбэков."""
    runs = db.query(AgentRun).order_by(AgentRun.created_at.desc()).limit(min(limit, 200)).all()
    total = db.query(AgentRun).count()
    return {
        "total": total,
        "runs": [
            {
                "run_id": r.run_id,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "channel": r.channel,
                "provider": r.provider,
                "model": r.model,
                "mode": r.mode,
                "outcome": r.outcome,
                "question": r.question[:160],
                "iterations": r.iterations,
                "tool_calls": r.tool_calls,
                "total_tokens": r.prompt_tokens + r.completion_tokens,
                "cost_usd": r.cost_usd,
                "latency_ms": r.latency_ms,
                "warning": r.warning or None,
            }
            for r in runs
        ],
    }


@router.get("/agent/runs/{run_id}")
def agent_run_detail(run_id: str, db: Session = Depends(get_db)) -> dict:
    run = db.query(AgentRun).filter(AgentRun.run_id == run_id).one_or_none()
    if run is None:
        raise HTTPException(404, "run not found")
    calls = db.query(LlmCall).filter(LlmCall.run_id == run_id).order_by(LlmCall.step).all()
    return {
        "run_id": run.run_id,
        "created_at": run.created_at.isoformat() if run.created_at else None,
        "channel": run.channel,
        "provider": run.provider,
        "model": run.model,
        "mode": run.mode,
        "outcome": run.outcome,
        "question": run.question,
        "answer": run.answer,
        "iterations": run.iterations,
        "warning": run.warning or None,
        "usage": {
            "prompt_tokens": run.prompt_tokens,
            "completion_tokens": run.completion_tokens,
            "total_tokens": run.prompt_tokens + run.completion_tokens,
            "cost_usd": run.cost_usd,
            "latency_ms": run.latency_ms,
        },
        "steps": json.loads(run.trace or "[]"),
        "llm_calls": [
            {
                "step": c.step,
                "model": c.model,
                "stop_reason": c.stop_reason,
                "prompt_tokens": c.prompt_tokens,
                "completion_tokens": c.completion_tokens,
                "cost_usd": c.cost_usd,
                "latency_ms": c.latency_ms,
                "requested_tools": [t for t in (c.requested_tools or "").split(",") if t],
                "error": c.error or None,
            }
            for c in calls
        ],
    }


def _history(db: Session, conversation_id: int, limit: int = 6) -> list[dict]:
    rows = (
        db.query(Message)
        .filter(Message.conversation_id == conversation_id)
        .order_by(Message.id.desc())
        .limit(limit + 1)
        .all()
    )
    # Последнее сообщение — только что записанный вопрос, он передаётся отдельно.
    return [{"role": m.role, "content": m.content} for m in reversed(rows[1:])]
