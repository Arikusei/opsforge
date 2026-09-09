"""MCP-сервер OpsForge: инструменты каталога, лидов и регламентов для Claude/Cursor."""

from __future__ import annotations

import json
import os

import httpx
from mcp.server.fastmcp import FastMCP

API = os.environ.get("OPSFORGE_API_URL", "http://127.0.0.1:8000")
SECRET = os.environ.get("WEBHOOK_SECRET", "opsforge-webhook-dev")

mcp = FastMCP("opsforge")


@mcp.tool()
def search_knowledge(query: str) -> str:
    """Ищет ответ в корпоративной базе знаний и каталоге OpsForge."""
    r = httpx.post(f"{API}/api/chat", json={"message": query, "channel": "mcp"}, timeout=30)
    r.raise_for_status()
    data = r.json()
    return json.dumps(
        {"answer": data.get("answer"), "citations": data.get("citations"), "provider": data.get("provider")},
        ensure_ascii=False,
        indent=2,
    )


@mcp.tool()
def create_site_lead(company: str, need: str, phone: str = "", contact: str = "") -> str:
    """Создаёт заявку: квалификация → CRM → уведомление в TeamChat."""
    r = httpx.post(
        f"{API}/webhooks/site-lead",
        json={"company": company, "need": need, "phone": phone, "contact": contact, "source": "mcp"},
        headers={"X-Webhook-Secret": SECRET},
        timeout=30,
    )
    r.raise_for_status()
    return json.dumps(r.json(), ensure_ascii=False, indent=2)


@mcp.tool()
def missed_call(caller: str, company: str = "") -> str:
    """Обрабатывает пропущенный звонок PhonePBX."""
    r = httpx.post(
        f"{API}/webhooks/phonepbx",
        json={"caller": caller, "company": company},
        headers={"X-Webhook-Secret": SECRET},
        timeout=30,
    )
    r.raise_for_status()
    return json.dumps(r.json(), ensure_ascii=False, indent=2)


@mcp.tool()
def integration_status() -> str:
    """Состояние адаптеров CRM, TeamChat, PhonePBX, S3, n8n."""
    r = httpx.get(f"{API}/api/integrations", timeout=15)
    r.raise_for_status()
    return json.dumps(r.json()["systems"], ensure_ascii=False, indent=2)


if __name__ == "__main__":
    mcp.run()
