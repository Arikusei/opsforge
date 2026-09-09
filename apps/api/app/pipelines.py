from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy.orm import Session

from app.agents.tools import create_lead
from app.integrations.clients import CRMClient, PhonePBXClient, TeamChatClient, S3Client
from app.models import CallEvent, Lead, WorkflowRun
from app.rag.retrieve import retrieve

crm = CRMClient()
teamchat = TeamChatClient()
pbx = PhonePBXClient()
s3 = S3Client()


def run_named(db: Session, name: str, payload: dict) -> dict:
    runners = {
        "site-lead": site_lead,
        "missed-call": missed_call,
        "teamchat-question": teamchat_question,
        "knowledge-sync": knowledge_sync_stub,
    }
    if name not in runners:
        raise ValueError(f"unknown workflow {name}")
    result = runners[name](db, payload)
    db.add(
        WorkflowRun(
            name=name,
            trigger=payload.get("trigger") or "api",
            status="success",
            payload=json.dumps(payload, ensure_ascii=False),
            result=json.dumps(result, ensure_ascii=False),
        )
    )
    db.commit()
    return result


def site_lead(db: Session, payload: dict) -> dict:
    need = payload.get("need") or payload.get("message") or ""
    tags = []
    low = need.lower()
    if any(w in low for w in ("пищев", "nsf", "h1")):
        tags.append("food")
    if any(w in low for w in ("сож", "металл", "чугун", "эмульс")):
        tags.append("metalworking")
    if any(w in low for w in ("покрыт", "forgecoat", "креп")):
        tags.append("coatings")
    hits = retrieve(db, need, limit=3)
    suggestion = hits[0]["title"] if hits else "Требуется технолог"
    lead = create_lead(
        db,
        {
            "company": payload.get("company") or "Заявка с сайта",
            "contact": payload.get("contact") or "",
            "phone": payload.get("phone") or "",
            "email": payload.get("email") or "",
            "source": "site",
            "industry": payload.get("industry") or "",
            "need": need,
            "tags": ",".join(tags),
            "score": 0.82 if tags else 0.55,
        },
    )
    crm.add_note(db, lead["crm_id"], f"AI-квалификация: {suggestion}. Теги: {lead['tags']}")
    return {
        "lead": lead,
        "qualification": suggestion,
        "tags": tags,
        "next_step": "Менеджер связывается в рабочее время 09:00–18:00",
    }


def missed_call(db: Session, payload: dict) -> dict:
    caller = payload.get("caller") or "+7 495 000-00-00"
    pbx.acknowledge(db, payload)
    existing = db.query(Lead).filter(Lead.phone == caller).first()
    if existing:
        lead = existing
        lead.stage = "callback"
        db.commit()
        created = False
    else:
        created_lead = create_lead(
            db,
            {
                "company": payload.get("company") or f"Звонок {caller}",
                "phone": caller,
                "source": "phonepbx",
                "need": "Пропущенный звонок, требуется перезвон",
                "tags": "callback",
                "score": 0.7,
            },
        )
        lead = db.get(Lead, created_lead["id"])
        created = True
    event = CallEvent(
        external_id=payload.get("call_id") or f"pbx-{int(datetime.utcnow().timestamp())}",
        caller=caller,
        status="missed",
        summary="Пропущенный звонок. Создана задача на перезвон 15 минут.",
        lead_id=lead.id,
    )
    db.add(event)
    db.commit()
    teamchat.notify(
        db,
        "sales",
        f"Пропущенный звонок {caller}. Сделка {lead.crm_id}. Перезвон в течение 15 минут.",
    )
    return {
        "call_id": event.external_id,
        "lead_id": lead.id,
        "crm_id": lead.crm_id,
        "created_lead": created,
        "script": "Представиться, напомнить о недозвоне, уточнить задачу, не называть цену до квалификации.",
    }


def teamchat_question(db: Session, payload: dict) -> dict:
    from app.agents.react import run_agent

    question = payload.get("text") or payload.get("question") or ""
    result = run_agent(db, question)
    teamchat.notify(db, payload.get("channel") or "internal", result["answer"][:1500])
    return {"question": question, "answer": result["answer"], "citations": result["citations"]}


def knowledge_sync_stub(db: Session, payload: dict) -> dict:
    from app.rag.ingest import ingest_knowledge

    count = ingest_knowledge(db)
    s3.put_json(db, "knowledge/sync.json", {"chunks": count, "at": datetime.utcnow().isoformat()})
    return {"chunks": count}
