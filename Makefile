.PHONY: up down restart logs build check format

# Spin up the entire environment (DB + Web) in the background
up:
	docker compose up -d

# Stop and tear down all active containers without losing database volume data
down:
	docker compose down

# Rebuild container layers from scratch (useful when adding new packages via uv)
build:
	docker compose up -d --build

# Sync newly added pyproject.toml dependencies directly into the live container
sync:
	docker compose exec web uv sync

# Follow live container output logs
logs:
	docker compose logs -f web

# Quick restart for the app container
restart:
	docker compose restart web

# Run local code quality checks before pushing to Git
check:
	@echo "⚡ Running Astral ty Type Checker..."
	@uv run ty check
	@echo "⚡ Running Ruff Linter..."
	@uv run ruff check src/

format:
	@uv run ruff format src/
	@uv run ruff check --fix src/

# Generate a new auto-detected migration file based on model changes
# Usage: make migrate-gen msg="Added discount column to products"
alembic-gen:
	@uv run alembic revision --autogenerate -m "$(msg)"

# Apply all pending database migration scripts up to the latest head
alembic-apply:
	@uv run alembic upgrade head

# Rollback the database schema exactly one revision backwards
alembic-undo:
	@uv run alembic downgrade -1

# Run the Pytest verification suite
test:
	@echo "🧪 Running Async Test Suite..."
	docker compose exec web uv run pytest -v