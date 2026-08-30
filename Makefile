.PHONY: install dev run test lint fmt

install:          ## Install runtime + dev dependencies
	pip install -r requirements-dev.txt

dev:              ## Run with autoreload for local development
	uvicorn backend.main:app --reload --env-file .env

run:              ## Run the production server
	uvicorn backend.main:app --host 0.0.0.0 --port $${PORT:-8000}

test:             ## Run the test suite
	pytest

lint:             ## Lint with ruff
	ruff check .

fmt:              ## Auto-fix lint issues
	ruff check --fix .
