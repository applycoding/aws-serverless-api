.PHONY: install test lint validate check

export SAM_CLI_TELEMETRY := 0

install:
	python3 -m pip install -r requirements-dev.txt

test:
	pytest

lint:
	ruff check .
	ruff format --check .

validate:
	sam validate --lint --template-file template.yaml --region us-east-1
	cfn-lint template.yaml

check: lint test validate
