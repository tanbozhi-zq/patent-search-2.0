PYTHON ?= .venv/bin/python

.PHONY: install test check run

install:
	$(PYTHON) -m pip install -r requirements.txt

test:
	$(PYTHON) -m pytest -q

check:
	$(PYTHON) -m compileall -q app mcp_server scripts
	$(PYTHON) -m pytest -q

run:
	$(PYTHON) -m uvicorn app.main:app --host 0.0.0.0 --port 8000
