.PHONY: install run test test-unit test-integration migrate docker-up docker-down

install:
	uv sync

run:
	uvicorn app.main:app --reload

test:
	python -m unittest discover -s tests

test-unit:
	python -m unittest discover -s tests/unit

test-integration:
	python -m unittest discover -s tests/integration

migrate:
	alembic upgrade head

docker-up:
	docker compose up -d --build

docker-down:
	docker compose down
