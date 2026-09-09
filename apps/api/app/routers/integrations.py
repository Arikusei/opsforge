from __future__ import annotations

import json

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.agents.react import available_provider
from app.config import get_settings
from app.db import get_db
from app.models import IntegrationEvent

router = APIRouter(prefix="/api", tags=["health"])
settings = get_settings()


@router.get("/integrations")
def integrations(db: Session = Depends(get_db)) -> dict:
    last = {}
    rows = db.query(IntegrationEvent).order_by(IntegrationEvent.created_at.desc()).limit(50)
    for row in rows:
        last.setdefault(row.system, []).append(
            {
                "action": row.action,
                "ok": row.ok,
                "created_at": row.created_at.isoformat() if row.created_at else None,
                "payload": json.loads(row.payload) if row.payload else {},
            }
        )
    counts = dict(
        db.query(IntegrationEvent.system, func.count(IntegrationEvent.id)).group_by(IntegrationEvent.system).all()
    )
    return {
        "llm": available_provider(),
        "systems": {
            "crm": {"mode": "live" if settings.crm_access_token else "mock", "events": counts.get("crm", 0)},
            "teamchat": {
                "mode": "live" if (settings.teamchat_api_token or settings.teamchat_bot_token) else "mock",
                "events": counts.get("teamchat", 0),
            },
            "phonepbx": {"mode": "live" if settings.phonepbx_api_key else "mock", "events": counts.get("phonepbx", 0)},
            "s3": {"mode": "mock", "events": counts.get("s3", 0)},
            "n8n": {"mode": "external", "ui": f"http://{settings.n8n_host}:{settings.n8n_port}"},
        },
        "recent": last,
    }
