PY := .venv/bin/python

.PHONY: install run migrate test lint format check
install:
	python3 -m venv .venv && .venv/bin/pip install -r requirements/local.txt
run:
	$(PY) manage.py runserver
migrate:
	$(PY) manage.py migrate
test:
	.venv/bin/pytest
lint:
	.venv/bin/ruff check .
format:
	.venv/bin/ruff format .
check:
	$(PY) manage.py check && $(PY) manage.py makemigrations --check --dry-run
