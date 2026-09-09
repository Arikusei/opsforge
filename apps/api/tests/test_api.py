from fastapi.testclient import TestClient

from app.main import app, init_db

init_db()
client = TestClient(app)


def test_health():
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"


def test_overview_has_seeded_leads():
    res = client.get("/api/overview")
    data = res.json()
    assert data["leads_total"] >= 4
    assert data["knowledge_chunks"] > 0


def test_chat_food_question():
    res = client.post("/api/chat", json={"message": "Какая смазка с NSF H1 есть для пищевого производства?"})
    assert res.status_code == 200
    body = res.json()
    assert "FOOD-397" in body["answer"] or "пищев" in body["answer"].lower()
    assert body["citations"]


def test_site_lead_pipeline():
    res = client.post(
        "/webhooks/site-lead",
        json={"company": "ООО Тест", "need": "Нужна СОЖ для чугуна", "phone": "+7999"},
        headers={"X-Webhook-Secret": "opsforge-webhook-dev"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["lead"]["crm_id"]
    assert "metalworking" in data["tags"]


def test_missed_call_pipeline():
    res = client.post(
        "/webhooks/phonepbx",
        json={"caller": "+7 900 111-22-33", "company": "ООО Звонок"},
        headers={"X-Webhook-Secret": "opsforge-webhook-dev"},
    )
    assert res.status_code == 200
    assert res.json()["script"]
