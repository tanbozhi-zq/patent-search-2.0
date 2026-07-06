# patent-search-service

Self-hosted patent search backend service based on FastAPI and OpenSearch.

## Stage

Current status: Stage 7 accepted, Stage 8 preparation.

Implemented so far:

- FastAPI app
- `GET /health`
- `.env` configuration loading
- `X-API-Key` auth dependency
- OpenSearch client construction
- `POST /api/patent/search`
- Boolean `q` parser and OpenSearch DSL builder
- analyzer compatibility mode through `index_analyzer_mode`
- `GET /api/patent/detail/{patent_id}`
- `GET /api/patent/detail/{patent_id}?include_description=true`
- `GET /api/patent/citations/{patent_id}`
- static manual test page under `/test/`
- pytest test suite
- systemd deployment template

Next stage:

- Stage 8: interface compatibility and exception handling hardening.
- Unify parameter validation errors into `{success, code, message, data}`.
- Audit remaining PatentHub/SaaS tool contract gaps against `patent_harness_base_副本/`.
- Confirm or document compatibility boundaries for `highlight`, `sort`, and pagination.

Project boundaries:

- `patent_harness_base_副本/` is a local read-only SaaS contract reference.
- Do not modify OpenSearch mapping or rebuild the index in the current stage.
- Do not enter SaaS integration or gray release before the corresponding stage documents are accepted.

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
