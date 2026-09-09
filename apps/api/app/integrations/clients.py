from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy.orm import Session

from app.models import IntegrationEvent


def log_event(db: Session, system: str, action: str, payload: dict, ok: bool = True) -> None:
    db.add(
        IntegrationEvent(
            system=system,
            action=action,
            payload=json.dumps(payload, ensure_ascii=False),
            ok=ok,
        )
    )
    db.commit()


class CRMClient:
    def __init__(self, live: bool = False):
        self.mode = "live" if live else "mock"

    def upsert_lead(self, db: Session, lead: dict) -> dict:
        external_id = lead.get("crm_id") or f"CRM-{datetime.utcnow().strftime('%H%M%S')}-{lead.get('id', 0)}"
        body = {**lead, "crm_id": external_id, "pipeline": "Продажи OpsForge"}
        log_event(db, "crm", "upsert_lead", body)
        return {"ok": True, "mode": self.mode, "crm_id": external_id, "stage": lead.get("stage", "new")}

    def add_note(self, db: Session, crm_id: str, text: str) -> dict:
        payload = {"crm_id": crm_id, "note": text}
        log_event(db, "crm", "add_note", payload)
        return {"ok": True, "mode": self.mode}


class TeamChatClient:
    def __init__(self, live: bool = False):
        self.mode = "live" if live else "mock"

    def notify(self, db: Session, channel: str, text: str) -> dict:
        payload = {"channel": channel, "text": text}
        log_event(db, "teamchat", "notify", payload)
        return {"ok": True, "mode": self.mode, "message_id": f"tc-{int(datetime.utcnow().timestamp())}"}


class PhonePBXClient:
    def __init__(self, live: bool = False):
        self.mode = "live" if live else "mock"

    def acknowledge(self, db: Session, event: dict) -> dict:
        log_event(db, "phonepbx", "webhook", event)
        return {"ok": True, "mode": self.mode}


class S3Client:
    def __init__(self, live: bool = False):
        self.mode = "live" if live else "mock"

    def put_json(self, db: Session, key: str, payload: dict) -> dict:
        log_event(db, "s3", "put", {"key": key, **payload})
        return {"ok": True, "mode": self.mode, "key": key}
