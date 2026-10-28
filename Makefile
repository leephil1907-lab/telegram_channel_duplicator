PYTHON ?= python3
VENV := .venv
PY := $(VENV)/bin/python

.PHONY: help build test check serve docker docker-logs docker-down clean

help:
	@echo "Targets: build test check serve docker docker-logs docker-down clean"

build:
	./build.sh

test:
	$(PY) -m pytest -q

check:
	$(PY) -m telegram_channel_duplicator --check

# Multi-user panel. Nothing starts automatically; each user configures and starts their own bot.
serve:
	$(PY) -m telegram_channel_duplicator --serve --data-dir data --port 8000

docker:
	docker compose up --build -d

docker-logs:
	docker compose logs -f

docker-down:
	docker compose down

clean:
	rm -rf build dist .pytest_cache *.egg-info state logs data
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
