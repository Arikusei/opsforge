# Runbook

## Локально без Docker

```powershell
cd D:\Progs\Pet\opsforge
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r apps\api\requirements.txt
$env:PYTHONPATH = "apps\api"
$env:KNOWLEDGE_DIR = "$pwd\knowledge"
uvicorn app.main:app --reload --app-dir apps\api --port 8000
```

Открыть http://127.0.0.1:8000

## Docker

```powershell
copy .env.example .env
docker compose up --build
```

- UI: http://localhost:8080
- n8n: http://localhost:8080/n8n/ (opsforge / opsforge-n8n)
- MinIO API: http://localhost:8080/s3/
- Метрики: http://localhost:8080/metrics

Импорт воркфлоу: n8n → Import from File → `n8n/workflows/*.json`.

## SSL

```bash
bash scripts/make-ssl.sh
```

Затем раскомментировать TLS-сервер в `nginx/nginx.conf`.

## Проверки

```powershell
$env:PYTHONPATH = "apps\api"
pytest apps\api\tests -q
curl http://127.0.0.1:8000/health
```

## Типовые сбои

- Пустая база знаний: кнопка «Переиндексировать RAG» или `POST /api/demo/knowledge-sync`.
- 401 на webhook: заголовок `X-Webhook-Secret` должен совпасть с `.env`.
- LLM не отвечает: система сама падает в demo-контур, в ответе будет `warning`.
