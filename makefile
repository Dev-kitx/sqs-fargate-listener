# ================================
# End-to-end SQS + LocalStack flow
# ================================

SHELL := /bin/sh
.ONESHELL:

# --- Config (tweak if needed) ---
REGION      ?= us-east-1
ENDPOINT    ?= http://localhost:4566
ACCOUNT     ?= 000000000000
QUEUE_NAME  ?= test-queue

# URL used by the container (host-based addressing recommended by LocalStack)
QUEUE_HOST  ?= sqs.$(REGION).localhost.localstack.cloud
QUEUE_PORT  ?= 4566
QUEUE_URL_IN_CONTAINER := http://$(QUEUE_HOST):$(QUEUE_PORT)/$(ACCOUNT)/$(QUEUE_NAME)

# URL used by your host for CLI (path-style)
QUEUE_URL_ON_HOST := $(ENDPOINT)/$(ACCOUNT)/$(QUEUE_NAME)

venv:
	@echo "🐍 Creating virtual environment..."
	python -m venv .venv
	.venv/bin/pip install --upgrade pip
	.venv/bin/pip install -e ".[dev]"
	@echo "✅ Done. Activate with: source .venv/bin/activate"

setup-env:
	@echo "🧪 Setting up env..."
	python -m pip install --upgrade pip
	pip install -e ".[dev]"
	@echo "✅ Setup completed."

run-pytest:
	@echo "🧪 Running unit tests..."
	pytest
	@echo "✅ Unit tests completed."

lint:
	@echo "🔍 Linting..."
	ruff check src/ tests/
	ruff format --check src/ tests/
	@echo "✅ Lint passed."

typecheck:
	@echo "🔍 Type checking..."
	mypy src/
	@echo "✅ Type check passed."

# --- Targets ---

## Start only LocalStack (detached)
localstack:
	@echo "🚀 Starting LocalStack..."
	docker compose up -d localstack
	@echo "⏳ Waiting for LocalStack SQS to respond..."
	for n in $$(seq 1 60); do \
		if aws --endpoint-url=$(ENDPOINT) sqs list-queues --region $(REGION) >/dev/null 2>&1; then \
			echo "✅ LocalStack ready"; exit 0; \
		fi; \
		sleep 2; \
	done; \
	echo "❌ LocalStack not ready within 120s"; exit 1

## Create the SQS queue in LocalStack
queue: localstack
	@echo "🪣 Creating queue: $(QUEUE_NAME)"
	-aws --endpoint-url=$(ENDPOINT) sqs create-queue --queue-name $(QUEUE_NAME) --region $(REGION) >/dev/null 2>&1 || true
	@echo "✅ Queue ensured at: $(QUEUE_URL_ON_HOST)"

## Bring up your worker service with QUEUE_URL injected
up: queue
	@echo "▶️  Starting worker with QUEUE_URL=$(QUEUE_URL_IN_CONTAINER)"
	QUEUE_URL=$(QUEUE_URL_IN_CONTAINER) docker compose up -d --build worker
	@echo "✅ Worker running. Tail logs with 'make logs'"

## Send one test message (from host)
send:
	@echo "📬 Sending test message to $(QUEUE_URL_ON_HOST)"
	aws --endpoint-url=$(ENDPOINT) sqs send-message \
	  --queue-url $(QUEUE_URL_ON_HOST) \
	  --message-body '{"hello":"world"}' \
	  --region $(REGION)
	@echo "✅ Sent."

## End-to-end: localstack → queue → worker → send
test: up send
	@echo "🎉 End-to-end test kicked off. Use 'make logs' to watch processing."

## Tail worker logs
logs:
	docker compose logs -f worker

## Stop everything
down:
	@echo "🧹 Stopping containers..."
	docker compose down

.PHONY: localstack queue up send test logs down
