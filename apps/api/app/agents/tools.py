from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.config import get_settings
from app.integrations.clients import CRMClient, TeamChatClient, S3Client
from app.models import Lead, Product
from app.rag.embeddings import tokenize
from app.rag.retrieve import retrieve

settings = get_settings()
crm = CRMClient(live=bool(settings.crm_access_token))
teamchat = TeamChatClient(live=bool(settings.teamchat_api_token or settings.teamchat_bot_token))
s3 = S3Client(live=False)


def search_catalog(db: Session, query: str) -> dict:
    hits = retrieve(db, query, limit=4)
    products = db.query(Product).all()
    tokens = [t for t in tokenize(query) if len(t) > 2]
    scored: list[tuple[float, Product]] = []
    for product in products:
        blob = f"{product.name} {product.brand} {product.tags} {product.summary} {product.category}".lower()
        score = sum(2.0 for token in tokens if token in blob)
        for hit in hits:
            hay = f"{hit['title']} {hit['content']}".lower()
            if product.sku.lower() in hay or product.name.lower() in hay:
                score += float(hit["score"])
        if score:
            scored.append((score, product))
    scored.sort(key=lambda item: item[0], reverse=True)
    picked = [item[1] for item in scored[:5]] or products[:3]
    return {
        "products": [
            {
                "sku": p.sku,
                "name": p.name,
                "brand": p.brand,
                "stock": p.in_stock,
                "summary": p.summary,
            }
            for p in picked
        ],
        "knowledge": hits,
    }


def create_lead(db: Session, payload: dict) -> dict:
    lead = Lead(
        company=payload.get("company") or "Не указана",
        contact=payload.get("contact") or "",
        phone=payload.get("phone") or "",
        email=payload.get("email") or "",
        source=payload.get("source") or "agent",
        stage="qualified" if payload.get("need") else "new",
        industry=payload.get("industry") or "",
        need=payload.get("need") or "",
        tags=payload.get("tags") or "",
        score=float(payload.get("score") or 0.6),
    )
    db.add(lead)
    db.commit()
    db.refresh(lead)
    result = crm.upsert_lead(db, _lead_dict(lead))
    lead.crm_id = result["crm_id"]
    db.commit()
    teamchat.notify(
        db,
        "sales",
        f"Новый лид: {lead.company} / {lead.need or 'задача не указана'} ({lead.source})",
    )
    s3.put_json(db, f"leads/{lead.id}.json", _lead_dict(lead))
    return _lead_dict(lead)


def draft_quote(db: Session, company: str, need: str) -> dict:
    hits = retrieve(db, need, limit=3)
    skus = []
    for hit in hits:
        for product in db.query(Product).all():
            if product.sku.lower() in hit["content"].lower() or product.name.split()[0].lower() in hit["title"].lower():
                skus.append(product.sku)
    skus = list(dict.fromkeys(skus))[:3]
    text = (
        f"Черновик КП для {company}\n"
        f"Задача: {need}\n"
        f"Рекомендованные позиции: {', '.join(skus) or 'уточнить у технолога'}\n"
        "Срок поставки: склад демо-склада, слот подтверждает логист.\n"
        "Цены — по актуальному прайсу, бот цены не фиксирует."
    )
    return {"company": company, "skus": skus, "draft": text, "sources": hits}


def notify_sales(db: Session, text: str) -> dict:
    return teamchat.notify(db, "sales", text)


def company_policy(db: Session, topic: str) -> dict:
    return {"hits": retrieve(db, topic, limit=3)}


TOOLS = {
    "search_catalog": {
        "description": (
            "Поиск смазок, СОЖ и покрытий по демо-каталогу и базе знаний. "
            "Вызывай первым, когда нужно подобрать продукт или сослаться на регламент."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Запрос на русском: задача, узел, отрасль, допуск (например «NSF H1 подшипники фасовки»)",
                }
            },
            "required": ["query"],
        },
        "handler": lambda db, args: search_catalog(db, args.get("query", "")),
    },
    "create_lead": {
        "description": (
            "Создать лид в CRM и уведомить отдел продаж в TeamChat. "
            "Вызывай только когда клиент назван и понятна его задача."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "company": {"type": "string", "description": "Название компании клиента"},
                "need": {"type": "string", "description": "Задача клиента своими словами"},
                "contact": {"type": "string", "description": "Имя контактного лица"},
                "phone": {"type": "string", "description": "Телефон в любом формате"},
                "email": {"type": "string", "description": "Email контакта"},
                "industry": {"type": "string", "description": "Отрасль клиента"},
                "source": {
                    "type": "string",
                    "description": "Источник обращения",
                    "enum": ["site", "employee-bot", "phone", "teamchat", "agent"],
                },
                "tags": {
                    "type": "string",
                    "description": "Теги через запятую из набора: food, coatings, metalworking, fuels",
                },
            },
            "required": ["company", "need"],
        },
        "handler": lambda db, args: create_lead(db, args),
    },
    "draft_quote": {
        "description": "Собрать черновик коммерческого предложения по базе знаний. Цены и точные сроки не фиксирует.",
        "parameters": {
            "type": "object",
            "properties": {
                "company": {"type": "string", "description": "Название компании клиента"},
                "need": {"type": "string", "description": "Задача, под которую подбираются позиции"},
            },
            "required": ["company", "need"],
        },
        "handler": lambda db, args: draft_quote(db, args.get("company", "Клиент"), args.get("need", "")),
    },
    "notify_sales": {
        "description": "Отправить сообщение в канал продаж TeamChat. Используй для эскалации технологу или логисту.",
        "parameters": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Текст сообщения для отдела продаж"}
            },
            "required": ["text"],
        },
        "handler": lambda db, args: notify_sales(db, args.get("text", "")),
    },
    "company_policy": {
        "description": (
            "Найти внутренний регламент: квалификация лида, скрипт пропущенного звонка, логистика, эскалация."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "topic": {
                    "type": "string",
                    "description": "Тема регламента, например «пропущенный звонок» или «квалификация лида»",
                }
            },
            "required": ["topic"],
        },
        "handler": lambda db, args: company_policy(db, args.get("topic", "регламент")),
    },
}


def run_tool(db: Session, name: str, args: dict) -> dict:
    if name not in TOOLS:
        return {"error": f"unknown tool {name}", "available": sorted(TOOLS)}
    spec = TOOLS[name]
    invalid = _validate_args(spec["parameters"], args)
    if invalid:
        return {"error": "invalid arguments", "details": invalid, "schema": spec["parameters"]}
    return spec["handler"](db, args)


def _validate_args(schema: dict, args: dict) -> list[str]:
    """Минимальная проверка аргументов: обязательные поля, типы, enum.

    Модель регулярно присылает пустые строки вместо пропущенных полей, поэтому
    пустое значение обязательного поля тоже считается ошибкой — иначе в CRM
    улетают лиды с компанией «Не указана».
    """
    problems: list[str] = []
    if not isinstance(args, dict):
        return ["arguments must be an object"]
    properties = schema.get("properties", {})
    for field in schema.get("required", []):
        value = args.get(field)
        if value is None or (isinstance(value, str) and not value.strip()):
            problems.append(f"missing required field: {field}")
    for field, value in args.items():
        prop = properties.get(field)
        if prop is None:
            problems.append(f"unknown field: {field}")
            continue
        if prop.get("type") == "string" and not isinstance(value, str):
            problems.append(f"field {field} must be a string")
            continue
        allowed = prop.get("enum")
        if allowed and value not in allowed:
            problems.append(f"field {field} must be one of {allowed}")
    return problems


def _lead_dict(lead: Lead) -> dict:
    return {
        "id": lead.id,
        "company": lead.company,
        "contact": lead.contact,
        "phone": lead.phone,
        "email": lead.email,
        "source": lead.source,
        "stage": lead.stage,
        "industry": lead.industry,
        "need": lead.need,
        "tags": lead.tags,
        "owner": lead.owner,
        "crm_id": lead.crm_id,
        "score": lead.score,
        "created_at": (lead.created_at or datetime.now(timezone.utc)).isoformat(),
    }
