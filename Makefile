.PHONY: install test run compose-up compose-down

install:
	python -m pip install -e '.[test]'

test:
	pytest

run:
	uvicorn maintainer_os.main:app --reload

compose-up:
	docker compose up -d postgres redis

compose-down:
	docker compose down
