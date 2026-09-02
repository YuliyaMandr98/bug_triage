.PHONY: help setup dev stop clean

help:
	@echo "Triage Bugs Tool - Developer Makefile"
	@echo ""
	@echo "  make setup  - Create venv, install deps, copy .env.example -> .env"
	@echo "  make dev    - Start the app on http://localhost:8000 (Ctrl-C to stop)"
	@echo "  make stop   - Kill a background dev process (if started with 'make dev &')"
	@echo "  make clean  - Remove caches"

VENV := venv
PIP := $(VENV)/bin/pip
UVICORN := $(VENV)/bin/uvicorn
# Requires Python 3.11+ (the codebase uses `X | None` union syntax evaluated
# at import time). Prefer a versioned interpreter over a bare `python3`,
# which on macOS often resolves to the older system Python.
PYTHON_BIN := $(shell command -v python3.13 || command -v python3.12 || command -v python3.11 || command -v python3)

setup:
	@echo "==> Using interpreter: $(PYTHON_BIN) ($$($(PYTHON_BIN) --version))"
	@$(PYTHON_BIN) -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' || \
		(echo "ERROR: Python 3.11+ required. Install it (e.g. 'brew install python@3.11') and re-run make setup." && exit 1)
	@echo "==> Creating virtual environment..."
	$(PYTHON_BIN) -m venv $(VENV)
	@echo "==> Installing dependencies..."
	$(PIP) install --upgrade pip
	$(PIP) install -e .
	@[ -f .env ] || cp .env.example .env
	mkdir -p data/artifacts
	@echo ""
	@echo "Setup complete!"
	@echo ""
	@echo "Next steps:"
	@echo "  1. Edit .env and fill in your Jira/Confluence/Gemini credentials"
	@echo "     (or leave them blank and configure via the Integrations page instead)"
	@echo "  2. Run: make dev"

dev:
	@mkdir -p data
	@echo "==> Starting Triage Bugs Tool..."
	@echo "    App  -> http://localhost:8000"
	@echo "    Docs -> http://localhost:8000/docs"
	@echo "    Press Ctrl-C to stop."
	PYTHONPATH=$(PWD) $(UVICORN) apps.app.main:app --reload --port 8000

stop:
	-pkill -f "uvicorn apps.app.main"
	@echo "Dev process stopped"

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null; true
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null; true
	@echo "Cache cleaned"

.DEFAULT_GOAL := help
