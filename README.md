# patent-search-service

Self-hosted patent search backend service based on FastAPI and OpenSearch.

## Stage

Current stage: Stage 4 service skeleton.

Implemented in this stage:

- FastAPI app
- `GET /health`
- `.env` configuration loading
- `X-API-Key` auth dependency
- OpenSearch client construction
- pytest test scaffold
- systemd deployment template

Not implemented in this stage:

- Patent search query parsing
- OpenSearch search DSL
- Patent detail lookup
- Citation lookup

## Local Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python3 -m pytest -q
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## Health Check

```bash
curl http://127.0.0.1:8000/health
python3 scripts/smoke_health.py http://127.0.0.1:8000
```

## Production Deployment Shape

The first deployment uses:

- `work` Linux user
- Python venv
- FastAPI/Uvicorn
- systemd
- port `8000`
- `.env` for secrets

OpenSearch credentials must not be committed to Git.
