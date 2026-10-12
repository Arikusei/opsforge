from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.models import CallEvent, IntegrationEvent, Lead, Product, WorkflowRun
from app.rag.ingest import ingest_knowledge, seed_products


def bootstrap(db: Session) -> None:
    seed_products(db)
    if not db.query(Lead).count():
        _seed_leads(db)
    if not db.query(CallEvent).count():
        _seed_calls(db)
    ingest_knowledge(db)


def _seed_leads(db: Session) -> None:
    rows = [
        Lead(
            company="ООО «ТехноСтан»",
            contact="Игорь Смирнов",
            phone="+7 495 111-22-33",
            email="igor@tehnostan.example",
            source="site",
            stage="qualified",
            industry="Металлообработка",
            need="Подбор СОЖ на чугун, есть запах в баке — нужна промывка системы",
            tags="metalworking,sozh",
            crm_id="CRM-1001",
            score=0.86,
        ),
        Lead(
            company="АО «ХлебПром»",
            contact="Марина Орлова",
            phone="+7 498 222-10-10",
            email="qms@hlebprom.example",
            source="teamchat",
            stage="proposal",
            industry="Пищевое производство",
            need="Смазка NSF H1 на фасовочную линию, аудит сети через 3 недели",
            tags="food,nsf",
            crm_id="CRM-1002",
            score=0.93,
        ),
        Lead(
            company="ООО «РемДорМаш»",
            contact="Павел Ким",
            phone="+7 495 300-40-50",
            source="phonepbx",
            stage="callback",
            industry="Железнодорожная техника",
            need="Замки дверей клинят на морозе, ищут сухое покрытие",
            tags="coatings,railway",
            crm_id="CRM-1003",
            score=0.74,
        ),
        Lead(
            company="ИП АвтоПарк Юг",
            contact="Денис",
            phone="+7 916 555-01-01",
            source="site",
            stage="new",
            industry="Автопарк",
            need="Моторные масла на 40 единиц коммерческого транспорта",
            tags="fuels,fleet",
            crm_id="CRM-1004",
            score=0.61,
        ),
    ]
    db.add_all(rows)
    db.commit()


def _seed_calls(db: Session) -> None:
    lead = db.query(Lead).filter(Lead.crm_id == "CRM-1003").first()
    now = datetime.now(timezone.utc)
    db.add_all(
        [
            CallEvent(
                external_id="pbx-8801",
                caller="+7 495 300-40-50",
                status="missed",
                summary="Не дозвонились, клиент из РемДорМаш",
                lead_id=lead.id if lead else None,
                created_at=now - timedelta(hours=2),
            ),
            CallEvent(
                external_id="pbx-8802",
                caller="+7 495 111-22-33",
                status="answered",
                duration=312,
                summary="ТехноСтан: обсудили промывку бака и замену СОЖ",
                created_at=now - timedelta(hours=5),
            ),
        ]
    )
    db.add(
        WorkflowRun(
            name="missed-call",
            trigger="seed",
            status="success",
            payload=json.dumps({"caller": "+7 495 300-40-50"}),
            result=json.dumps({"ok": True}),
            created_at=now - timedelta(hours=2),
        )
    )
    db.add(
        IntegrationEvent(
            system="crm",
            action="upsert_lead",
            payload='{"crm_id": "CRM-1001"}',
        )
    )
    db.commit()


def catalog_snapshot(db: Session) -> list[dict]:
    return [
        {
            "sku": p.sku,
            "name": p.name,
            "brand": p.brand,
            "category": p.category,
            "summary": p.summary,
            "tags": p.tags,
            "in_stock": p.in_stock,
        }
        for p in db.query(Product).all()
    ]
