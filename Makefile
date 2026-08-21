SHELL := /bin/bash
export DATABASE_URL ?= postgresql://employee_user:employee_pass@localhost:$(or $(PGPORT),5432)/employee_db
.PHONY: db-up db-down install inventory validate parity fixtures-approve harness-test run verify clean

db-up:
	docker compose -f docker/docker-compose.yml up -d

db-down:
	docker compose -f docker/docker-compose.yml down

install:
	uv sync

inventory:
	uv run python inventory/inventory.py

validate:
	uv run python boomi/validate.py

parity:
	PYTHONPATH=harness uv run python harness/parity.py

fixtures-approve:
	PYTHONPATH=harness uv run python harness/parity.py --approve-fixtures "$(REASON)"

harness-test:
	uv run pytest harness/tests

run:
	cd harness && uv run python -m runner.server

verify: validate parity

clean:
	rm -rf inventory/report harness/reports .pytest_cache
