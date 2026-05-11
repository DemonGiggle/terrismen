PYTHON ?= ./.venv/bin/python
PIP ?= ./.venv/bin/pip

.PHONY: dev-setup run test

dev-setup:
	python3 -m venv .venv
	$(PIP) install --upgrade pip
	$(PIP) install -e ".[dev]"

run:
	$(PYTHON) -m terrismen.app

test:
	$(PYTHON) -m pytest
