.PHONY: help env install lint format typecheck test test-integration check secret-scan up down bootstrap seed sync \
        logs ps backlog

COMPOSE ?= docker compose
PROFILES ?= --profile core
-include .env
export

help:               ## this list
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | sed 's/:.*##/\t/'

env:                ## write .env with fresh local secrets (refuses to overwrite)
	python scripts/make_env.py

install:            ## Python deps incl. the triage extra and dev tools
	uv sync --all-extras

lint:
	uv run ruff check . && uv run ruff format --check .

format:
	uv run ruff format . && uv run ruff check --fix .

typecheck:
	uv run mypy

test:               ## unit and contract tests (a Testcontainers Postgres for the sync's state)
	uv run pytest -m "not integration" --cov --cov-report=term-missing --cov-report=xml

test-integration:   ## against the running stack: make up bootstrap first
	uv run pytest -m integration -v

check: lint typecheck test

secret-scan:        ## run before every push
	bash scripts/secret_scan.sh

up:                 ## the local stack (PROFILES="--profile core --profile tickets ..." for more)
	$(COMPOSE) $(PROFILES) up -d --wait --wait-timeout 600

down:               ## stop and DROP the volumes
	$(COMPOSE) --profile core --profile tickets --profile automation --profile llm --profile obs down -v

bootstrap:          ## migrations, then EspoCRM's fields, role and API user (Odoo bootstraps itself in odoo-init)
	uv run hub migrate
	uv run hub bootstrap espocrm

seed:               ## the default book: 300 accounts, seed 42
	uv run hub seed --accounts 300 --seed 42

sync:
	uv run hub sync

ps:
	$(COMPOSE) ps -a

logs:
	$(COMPOSE) logs -f --tail 100

backlog:            ## issues, milestones and the board on GitHub from docs/agile/backlog.json
	uv run python scripts/agile/sync_backlog.py --board && uv run python scripts/agile/board.py sprints
