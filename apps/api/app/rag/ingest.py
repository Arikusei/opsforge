from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import KnowledgeChunk, Product
from app.rag.embeddings import hashed_embedding


PRODUCTS = [
    {
        "sku": "FORGE-CLEAN-555",
        "name": "Очиститель систем подачи СОЖ ForgeClean 555",
        "brand": "ForgeClean",
        "category": "Очистители / СОЖ",
        "summary": "Промывка и санация систем подачи СОЖ перед заменой эмульсии.",
        "tags": "sozh,service,metalworking",
        "in_stock": 42,
    },
    {
        "sku": "FORGE-SOZH-632",
        "name": "Масляная СОЖ ForgeSozh 632",
        "brand": "ForgeSozh",
        "category": "СОЖ",
        "summary": "Масляная СОЖ для тяжёлой металлообработки и легированных сталей.",
        "tags": "sozh,metalworking",
        "in_stock": 18,
    },
    {
        "sku": "FORGE-FOOD-397",
        "name": "Смазка NSF H1 ForgeFood 397",
        "brand": "ForgeFood",
        "category": "Пищевые материалы",
        "summary": "Полусинтетическая смазка с допуском NSF H1 для пищевых производств.",
        "tags": "food,nsf,h1",
        "in_stock": 65,
    },
    {
        "sku": "FORGE-COAT-1031",
        "name": "Фрикционное покрытие ForgeCoat 1031",
        "brand": "ForgeCoat",
        "category": "Покрытия",
        "summary": "Стабилизация момента затяжки крепежа, сухой смазочный слой на весь срок службы.",
        "tags": "coatings,fasteners",
        "in_stock": 27,
    },
    {
        "sku": "FORGE-COAT-1007",
        "name": "Антипригарное покрытие ForgeCoat 1007",
        "brand": "ForgeCoat",
        "category": "Покрытия",
        "summary": "Снижает налипание на линиях упаковки, повышает OEE.",
        "tags": "coatings,packaging",
        "in_stock": 14,
    },
    {
        "sku": "FORGE-COAT-FLON",
        "name": "Покрытие ForgeCoat Flon",
        "brand": "ForgeCoat",
        "category": "Покрытия",
        "summary": "Узлы трения при перепадах температур, в том числе ж/д замки.",
        "tags": "coatings,railway",
        "in_stock": 9,
    },
    {
        "sku": "FORGE-IND",
        "name": "Индустриальные масла ForgeOil IND",
        "brand": "ForgeOil",
        "category": "Индустриальные масла",
        "summary": "Гидравлика, редукторы, компрессоры. Позиция демо-каталога OpsForge.",
        "tags": "fuels,industrial",
        "in_stock": 120,
    },
    {
        "sku": "FORGE-FLEET",
        "name": "Масла ForgeFleet",
        "brand": "ForgeOil",
        "category": "ГСМ / транспорт",
        "summary": "Моторные и трансмиссионные масла для легкового и коммерческого транспорта.",
        "tags": "fuels,fleet",
        "in_stock": 200,
    },
    {
        "sku": "FORGE-CUT",
        "name": "СОЖ ForgeCut",
        "brand": "ForgeCut",
        "category": "СОЖ",
        "summary": "Линейка СОЖ и сервисных продуктов для металлообработки.",
        "tags": "sozh,metalworking",
        "in_stock": 11,
    },
    {
        "sku": "FORGE-PEN",
        "name": "Проникающая смазка ForgePen",
        "brand": "ForgePen",
        "category": "Универсальные смазки",
        "summary": "Многофункциональная проникающая жидкость для сервиса и полевых работ.",
        "tags": "service,retail",
        "in_stock": 300,
    },
    {
        "sku": "FORGE-HT",
        "name": "Специальные смазки ForgeHT",
        "brand": "ForgeHT",
        "category": "Индустриальные смазки",
        "summary": "Высокотемпературные, водостойкие и малошумные смазки, СОЖ и масла.",
        "tags": "industrial,ht",
        "in_stock": 40,
    },
]


def ingest_knowledge(db: Session) -> int:
    settings = get_settings()
    root = Path(settings.knowledge_dir)
    count = 0
    if not root.exists():
        return 0
    db.query(KnowledgeChunk).delete()
    for path in sorted(root.rglob("*.md")):
        text = path.read_text(encoding="utf-8")
        title = next((line[2:].strip() for line in text.splitlines() if line.startswith("# ")), path.stem)
        chunks = _split(text)
        for chunk in chunks:
            db.add(
                KnowledgeChunk(
                    source=str(path.relative_to(root)).replace("\\", "/"),
                    title=title,
                    content=chunk,
                    embedding=json.dumps(hashed_embedding(chunk)),
                )
            )
            count += 1
    db.commit()
    return count


def seed_products(db: Session) -> None:
    desired = {row["sku"]: row for row in PRODUCTS}
    existing = {p.sku: p for p in db.query(Product).all()}
    for sku, row in desired.items():
        product = existing.get(sku)
        if product is None:
            db.add(Product(**row))
            continue
        for key, value in row.items():
            setattr(product, key, value)
    for sku, product in existing.items():
        if sku not in desired:
            db.delete(product)
    db.commit()


def _split(text: str, size: int = 700) -> list[str]:
    parts, buf = [], []
    length = 0
    for para in text.split("\n\n"):
        para = para.strip()
        if not para:
            continue
        if length + len(para) > size and buf:
            parts.append("\n\n".join(buf))
            buf, length = [para], len(para)
        else:
            buf.append(para)
            length += len(para)
    if buf:
        parts.append("\n\n".join(buf))
    return parts or [text]
