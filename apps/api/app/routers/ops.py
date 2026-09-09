from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.models import CallEvent, KnowledgeChunk, Lead, WorkflowRun
from app.pipelines import run_named
from app.seed import catalog_snapshot

router = APIRouter(tags=["ops"])
settings = get_settings()


class LeadIn(BaseModel):
    company: str
    contact: str = ""
    phone: str = ""
    email: str = ""
    need: str = ""
    industry: str = ""


def _check_secret(secret: str | None) -> None:
    if secret and secret != settings.webhook_secret:
        raise HTTPException(status_code=401, detail="invalid webhook secret")


@router.get("/api/overview")
def overview(db: Session = Depends(get_db)) -> dict:
    leads = db.query(Lead).all()
    stages: dict[str, int] = {}
    for lead in leads:
        stages[lead.stage] = stages.get(lead.stage, 0) + 1
    return {
        "leads_total": len(leads),
        "leads_today_like": len(leads),
        "missed_calls": db.query(CallEvent).filter(CallEvent.status == "missed").count(),
        "knowledge_chunks": db.query(KnowledgeChunk).count(),
        "workflow_runs": db.query(WorkflowRun).count(),
        "stages": stages,
        "pipeline": [
            {
                "id": lead.id,
                "company": lead.company,
                "contact": lead.contact,
                "phone": lead.phone,
                "source": lead.source,
                "stage": lead.stage,
                "industry": lead.industry,
                "need": lead.need,
                "tags": lead.tags,
                "crm_id": lead.crm_id,
                "score": lead.score,
                "created_at": lead.created_at.isoformat() if lead.created_at else None,
            }
            for lead in sorted(leads, key=lambda x: x.created_at or x.id, reverse=True)
        ],
        "calls": [
            {
                "id": c.id,
                "caller": c.caller,
                "status": c.status,
                "duration": c.duration,
                "summary": c.summary,
                "created_at": c.created_at.isoformat() if c.created_at else None,
            }
            for c in db.query(CallEvent).order_by(CallEvent.created_at.desc()).limit(20)
        ],
        "workflows": [
            {
                "id": w.id,
                "name": w.name,
                "trigger": w.trigger,
                "status": w.status,
                "created_at": w.created_at.isoformat() if w.created_at else None,
                "result": w.result,
            }
            for w in db.query(WorkflowRun).order_by(WorkflowRun.created_at.desc()).limit(20)
        ],
        "catalog": catalog_snapshot(db),
    }


@router.get("/api/knowledge")
def knowledge(db: Session = Depends(get_db)) -> dict:
    chunks = db.query(KnowledgeChunk).all()
    grouped: dict[str, list[dict]] = {}
    for chunk in chunks:
        grouped.setdefault(chunk.source, []).append(
            {"id": chunk.id, "title": chunk.title, "content": chunk.content[:400]}
        )
    return {"documents": grouped, "chunks": len(chunks)}


@router.post("/api/leads")
def api_create_lead(body: LeadIn, db: Session = Depends(get_db)) -> dict:
    return run_named(db, "site-lead", body.model_dump())


@router.post("/webhooks/site-lead")
def webhook_site_lead(body: dict, db: Session = Depends(get_db), x_webhook_secret: str | None = Header(default=None)) -> dict:
    _check_secret(x_webhook_secret)
    return run_named(db, "site-lead", {**body, "trigger": "webhook-site"})


@router.post("/webhooks/crm")
def webhook_crm(body: dict, db: Session = Depends(get_db), x_webhook_secret: str | None = Header(default=None)) -> dict:
    _check_secret(x_webhook_secret)
    from app.integrations.clients import CRMClient

    CRMClient().upsert_lead(db, body)
    return {"ok": True}


@router.post("/webhooks/teamchat")
def webhook_teamchat(body: dict, db: Session = Depends(get_db), x_webhook_secret: str | None = Header(default=None)) -> dict:
    _check_secret(x_webhook_secret)
    return run_named(db, "teamchat-question", {**body, "trigger": "webhook-teamchat"})


@router.post("/webhooks/phonepbx")
def webhook_phonepbx(body: dict, db: Session = Depends(get_db), x_webhook_secret: str | None = Header(default=None)) -> dict:
    _check_secret(x_webhook_secret)
    return run_named(db, "missed-call", {**body, "trigger": "webhook-phonepbx"})


@router.post("/api/demo/{name}")
def demo(name: str, db: Session = Depends(get_db)) -> dict:
    samples = {
        "site-lead": {
            "company": "ООО «Литейный Двор»",
            "contact": "Андрей",
            "phone": "+7 495 777-12-12",
            "need": "Нужна масляная СОЖ на легированную сталь и промывка бака, эмульсия закисла",
            "industry": "Металлообработка",
        },
        "missed-call": {"caller": "+7 926 401-08-17", "company": "ООО «СеверТранс»", "call_id": "pbx-demo"},
        "teamchat-question": {
            "text": "Чем смазать подшипники фасовочной линии на пищевом производстве? Нужен NSF.",
            "channel": "sales",
        },
        "knowledge-sync": {},
    }
    if name not in samples:
        raise HTTPException(404, "unknown demo")
    return run_named(db, name, {**samples[name], "trigger": "demo"})
