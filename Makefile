PYTHON ?= .venv/bin/python
PROMTOOL ?= promtool

.PHONY: install test check check-observability run

install:
	$(PYTHON) -m pip install -r requirements.txt

test:
	$(PYTHON) -m pytest -q

check:
	$(PYTHON) -m compileall -q app benchmarks mcp_server scripts
	$(PYTHON) -m pytest -q
	node --check app/static/admin/admin.js
	node --test tests/admin_dashboard.test.js

check-observability:
	command -v $(PROMTOOL)
	$(PROMTOOL) check config --syntax-only deployment/prometheus/prometheus.yml
	$(PROMTOOL) check rules deployment/observability/alert_rules.yml
	$(PROMTOOL) test rules deployment/observability/alert_rules_test.yml

run:
	$(PYTHON) -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --no-access-log
