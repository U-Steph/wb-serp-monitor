# WB SERP Monitor - local (no Docker) and Docker entry points.
#
# Local path, in order:   make install  ->  make init-db  ->  make scheduler (or make collect-once)  ->  make web
#   - collect-once and scheduler send REAL requests to Wildberries and Google Sheets.
#   - settings come from an optional .env (WBMON_* variables); it is loaded into the environment of each recipe.
# Docker path:            make up   (works without any .env file: compose has built-in defaults;
#                                    `make down` keeps the data volume; never run `docker compose down -v`)
#
# Compatible with GNU Make 3.81 and bash 3.2 (the macOS defaults): no .ONESHELL, no bash-4 features.
SHELL := /bin/bash
VENV  ?= .venv
PY    := $(VENV)/bin/python
# Load .env into the environment of one recipe line (values must be shell-safe: see .env.example).
ENVLOAD := set -a; [ -f .env ] && . ./.env; set +a;

.DEFAULT_GOAL := help
.PHONY: help install test lint init-db collect-once scheduler web up up-prebuilt down logs ps

help:  ## list targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | sed -E 's/:.*## /\t/'

install:  ## venv + editable install with dev and web extras
	python3 -m venv $(VENV)
	$(PY) -m pip install -e ".[dev,web]"

test:  ## offline test suite
	$(PY) -m pytest -q

lint:  ## ruff
	$(VENV)/bin/ruff check src tests

init-db:  ## create/migrate the local SQLite DB
	@$(ENVLOAD) $(VENV)/bin/wbmon init-db

collect-once:  ## ONE real collection run (sends real requests to Wildberries and Google)
	@$(ENVLOAD) $(VENV)/bin/wbmon collect

scheduler:  ## hourly scheduler in the foreground (sends real requests)
	@$(ENVLOAD) $(VENV)/bin/wbmon scheduler

web:  ## Streamlit UI on http://localhost:8501
	@$(ENVLOAD) $(VENV)/bin/streamlit run src/wbmon/ui/app.py

up:  ## build and start the whole stack
	@if [ -f .env.example ] && [ ! -f .env ]; then cp .env.example .env; fi
	docker compose up -d --build

up-prebuilt:  ## start the stack from an image loaded with `docker load` (no build)
	@if [ -f .env.example ] && [ ! -f .env ]; then cp .env.example .env; fi
	docker compose up -d --no-build

down:  ## stop the stack, KEEP the data volume (never add the volumes flag)
	docker compose down

logs:  ## follow collector and web logs
	docker compose logs -f --tail=200 collector web

ps:  ## container status and health
	docker compose ps
