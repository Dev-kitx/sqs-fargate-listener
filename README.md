# 🦾 sqs-fargate-listener

[![Release](https://img.shields.io/endpoint?url=https%3A%2F%2Fraw.githubusercontent.com%2FDev-kitx%2Fsqs-fargate-listener%2Fmain%2F.github%2Fbadges%2Frelease.json)](https://github.com/Dev-kitx/sqs-fargate-listener/releases/latest)
[![PyPI version](https://img.shields.io/pypi/v/sqs-fargate-listener?color=blue&logo=pypi&logoColor=white)](https://pypi.org/project/sqs-fargate-listener/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Downloads](https://img.shields.io/pypi/dm/sqs-fargate-listener?color=brightgreen)](https://pypi.org/project/sqs-fargate-listener/)
[![CI](https://img.shields.io/github/actions/workflow/status/Dev-Kitx/sqs-fargate-listener/ci.yml?branch=master&label=CI&logo=github)](https://github.com/Dev-Kitx/sqs-fargate-listener/actions)
[![codecov](https://codecov.io/gh/Dev-kitx/sqs-fargate-listener/graph/badge.svg?token=qv6il3a3vg)](https://codecov.io/gh/Dev-kitx/sqs-fargate-listener)
[![Code style: ruff](https://img.shields.io/badge/code%20style-ruff-orange?logo=ruff)](https://github.com/astral-sh/ruff)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](CONTRIBUTING.md)

> **A lightweight, Lambda-like SQS listener for Python running on ECS Fargate (or any container).**  
> Handles long-polling, visibility heartbeats, partial batch failure, graceful shutdown, async handlers, structured JSON logging, and health checks out of the box.



## 🚀 Why this exists

When you attach an **SQS** queue to **AWS Lambda**, AWS automatically manages:
  - polling the queue
  - invoking your handler
  - deleting successful messages
  - redriving failures.

On **ECS Fargate**, you don’t get this for free — you have to build a poller, manage visibility timeouts, handle retries, and scale your service yourself.

This package brings the same convenience to containers. Just decorate a function with `@sqs_listener(...)`, and the library does the rest.


### 🧩 Install

```bash
pip install sqs-fargate-listener
```

---

## ✨ Quick Start

### 💡 Processing Messages in `app.py`

When you decorate a function with `@sqs_listener(...)`, you’re telling the library:

> “Whenever messages arrive in this queue, call this function with them.”

The decorator automatically:
- polls the SQS queue,
- extends the visibility timeout while you work,
- invokes your function,
- deletes successful messages,
- and retries or sends failures to the DLQ.

**There are two handler styles you can use depending on your needs:**

### 🧩 1. Batch mode (recommended for throughput)

Batch mode receives a *list* of messages (up to `BATCH_SIZE`, default 10) and lets you mark which ones failed.

```python
# app.py
from sqs_fargate_listener import sqs_listener, run_listeners
from sqs_fargate_listener.types import SqsMessage, BatchResult

@sqs_listener(
    queue_url="https://sqs.us-east-1.amazonaws.com/123/orders-queue",
    mode="batch",
    batch_size=10,
)
def handle_orders(messages: list[SqsMessage]) -> BatchResult:
    failed = []

    for msg in messages:
        try:
            # Parse message as JSON (cached after first use)
            data = msg.json
            print(f"🛒 Processing order {data['order_id']} for {data['customer']}")
            
            # do your logic here (save to DB, call APIs, etc.)
            process_order(data)
        
        except Exception as e:
            print(f"❌ Failed to process {msg.message_id}: {e}")
            failed.append(msg.receipt_handle)
    
    # Only successful messages will be deleted
    return BatchResult(failed_receipt_handles=failed)

if __name__ == "__main__":
    run_listeners()
```

- ✅ **Advantages:**
  * fewer SQS API calls, better throughput
  * partial batch failure supported
  * automatic retries by SQS DLQ policy

### 🪄 2. Per-message mode (simple boolean handler)

**Per-message mode gives you one SqsMessage at a time and expects a boolean return:**
  * True → delete the message
  * False or exception → leave in queue for retry

```python
@sqs_listener(
    queue_url="https://sqs.us-east-1.amazonaws.com/123/email-queue",
    mode="per_message",
    worker_threads=4,
)
def handle_email(msg: SqsMessage) -> bool:
    data, err = msg.try_json()
    if err:
        print(f"Invalid JSON: {err}")
        return False

    print(f"📧 Sending email to {data['recipient']} ...")
    try:
        send_email(data)
        return True
    except Exception as e:
        print(f"Send failed: {e}")
        return False
```
> [!NOTE]
> **✅ Simpler to reason about. Best when each message is independent and quick to process.**

## 📦 Working with message attributes

**You can access message attributes attached to the SQS message:**

```python
attrs = msg.message_attributes()
trace_id = attrs.get("trace_id")
print(f"Processing message trace_id={trace_id}")
```

**Make sure your queue’s producer sets attributes when sending messages:**

```python
sqs.send_message(
	QueueUrl=queue_url,
	MessageBody=json.dumps({...}),
	MessageAttributes={
		"trace_id": {"DataType": "String", "StringValue": "abc-123"}
	}
)
```


### 🧩 SqsMessage helpers

| Property / Method      | Description                                 |
|------------------------|---------------------------------------------|
| .message_id            | SQS message ID                              |
| .body                  | Raw message body (string)                   |
| .json                  | Cached parsed JSON (raises if invalid)      |
| .try_json()            | Returns (data, error) safely                |
| .message_attributes()  | Simplified dict of SQS MessageAttributes    |
| .receipt_handle        | Internal SQS handle (used for deletion)     |


### 🧰 Example: multiple queues in one app

```python
from sqs_fargate_listener import sqs_listener, run_listeners
from sqs_fargate_listener.types import SqsMessage

@sqs_listener(queue_url="https://…/payments-queue", mode="batch")
def process_payments(msgs): ...

@sqs_listener(queue_url="https://…/notifications-queue", mode="per_message")
def send_notifications(msg): ...

if __name__ == "__main__":
    run_listeners()
```

> [!NOTE]
> **Both queues are polled concurrently, each on its own threads.**

### 🛡️ Error handling & retries
- Uncaught exceptions or return False → message not deleted → retried later.
- After `maxReceiveCount` (queue setting) → message sent to DLQ.
- You can safely raise any exception; it’s caught by the listener.

For longer jobs, visibility is automatically extended while your handler runs.

---

## ⚡️ Async handlers

Both `async def` and regular `def` handlers are supported — no configuration needed:

```python
import httpx
from sqs_fargate_listener import sqs_listener, run_listeners
from sqs_fargate_listener.types import SqsMessage, BatchResult

@sqs_listener(
    queue_url="https://sqs.us-east-1.amazonaws.com/123/events-queue",
    mode="per_message",
)
async def handle_event(msg: SqsMessage) -> bool:
    data = msg.json
    async with httpx.AsyncClient() as client:
        resp = await client.post("https://api.example.com/events", json=data)
        return resp.status_code == 200

if __name__ == "__main__":
    run_listeners()
```

> [!NOTE]
> Each handler invocation gets its own `asyncio.run()` call. For high-throughput async workloads, increase `worker_threads` to run multiple concurrent event loops.

---

## 🔍 Message filtering

Use `filter_fn` to skip messages that don’t belong to this handler. Non-matching messages have their visibility reset to 0 immediately so other consumers can pick them up — they are never silently dropped.

```python
@sqs_listener(
    queue_url="https://sqs.us-east-1.amazonaws.com/123/events-queue",
    mode="batch",
    filter_fn=lambda msg: msg.json.get("type") == "order",
)
def handle_orders(messages: list[SqsMessage]) -> BatchResult:
    ...
```

Useful when multiple services share the same queue and route by message type.

---

## 🔁 Retry with exponential backoff

By default, failed messages become visible again after `visibility_secs`. Enable `retry_backoff=True` to instead call `ChangeMessageVisibility` with an exponentially increasing delay so retries don’t pile up:

```python
@sqs_listener(
    queue_url="https://sqs.us-east-1.amazonaws.com/123/orders-queue",
    mode="per_message",
    retry_backoff=True,
    retry_backoff_base=30,   # first retry after 30s
    retry_backoff_max=600,   # cap at 10 minutes
)
def handle_order(msg: SqsMessage) -> bool:
    ...
```

Backoff formula: `min(base × 2^(receiveCount − 1), max)`

| Receive count | Delay (base=30s) |
|---------------|-----------------|
| 1             | 30s             |
| 2             | 60s             |
| 3             | 120s            |
| 4             | 240s            |
| 5+            | 600s (capped)   |

---

## 🪝 Hooks

Run callbacks after each message succeeds or fails — useful for metrics, alerting, or audit logging:

```python
import logging
log = logging.getLogger("myapp")

def on_success(msg):
    log.info("Processed", extra={"message_id": msg.message_id})

def on_failure(msg, exc):
    log.error("Failed", extra={"message_id": msg.message_id, "error": str(exc)})
    alert_pagerduty(msg)

@sqs_listener(
    queue_url="https://sqs.us-east-1.amazonaws.com/123/orders-queue",
    mode="per_message",
    on_success=on_success,
    on_failure=on_failure,
)
def handle_order(msg: SqsMessage) -> bool:
    ...
```

- `on_success(msg: SqsMessage)` — called after each successfully processed message.
- `on_failure(msg: SqsMessage, exc: Exception | None)` — called after each failure. `exc` is `None` in batch mode when failure is indicated via `BatchResult`.
- Exceptions raised inside hooks are logged and swallowed — they never affect message processing.

---

## 🩺 Health check endpoint

Set `health_check_port` to expose a lightweight HTTP server for ECS health checks and monitoring:

```python
@sqs_listener(
    queue_url="https://sqs.us-east-1.amazonaws.com/123/orders-queue",
    mode="batch",
    health_check_port=8080,
)
def handle_orders(messages): ...
```

**Endpoints:**

```
GET /health  → 200 {"status": "ok"}
GET /metrics → 200 {
  "queue_url": "https://...",
  "mode": "batch",
  "worker_threads": 4,
  "uptime_seconds": 123.4,
  "messages_processed": 1500,
  "messages_failed": 12,
  "messages_filtered": 0
}
```

**ECS Task Definition health check:**

```json
{
  "healthCheck": {
    "command": ["CMD-SHELL", "curl -f http://localhost:8080/health || exit 1"],
    "interval": 30,
    "timeout": 5,
    "retries": 3
  }
}
```

---

## 🪣 Dead-Letter Queue (DLQ) Setup

A DLQ catches messages that repeatedly fail so they don’t loop forever in your main queue.

### 1. Create the DLQ

```bash
aws sqs create-queue \
  --queue-name orders-queue-dlq \
  --region us-east-1
```

### 2. Attach it to your main queue via a Redrive Policy

```bash
# Get the DLQ ARN
DLQ_ARN=$(aws sqs get-queue-attributes \
  --queue-url https://sqs.us-east-1.amazonaws.com/123456789/orders-queue-dlq \
  --attribute-names QueueArn \
  --query ‘Attributes.QueueArn’ --output text)

# Set redrive policy on main queue
aws sqs set-queue-attributes \
  --queue-url https://sqs.us-east-1.amazonaws.com/123456789/orders-queue \
  --attributes "{\"RedrivePolicy\": \"{\\\"deadLetterTargetArn\\\":\\\"$DLQ_ARN\\\",\\\"maxReceiveCount\\\":\\\"5\\\"}\"}"
```

### Choosing `maxReceiveCount`

| Workload                        | Recommended value | Reasoning                          |
|---------------------------------|-------------------|------------------------------------|
| Idempotent, fast processing     | 3                 | Fail fast, avoid long retry cycles |
| External API calls (flaky)      | 5–10              | Allow for transient failures       |
| Critical, must-process messages | 10+               | Exhaust retries before giving up   |

> [!NOTE]
> `maxReceiveCount` counts how many times SQS delivers a message. If your `visibility_secs` expires before your handler finishes, the receive count increments — set `visibility_secs` and `max_extend` high enough for your workload.

### 3. Alert on DLQ depth

Create a CloudWatch alarm so you’re notified the moment messages land in the DLQ:

```bash
aws cloudwatch put-metric-alarm \
  --alarm-name "orders-dlq-not-empty" \
  --metric-name ApproximateNumberOfMessagesVisible \
  --namespace AWS/SQS \
  --dimensions Name=QueueName,Value=orders-queue-dlq \
  --statistic Sum \
  --period 60 \
  --threshold 1 \
  --comparison-operator GreaterThanOrEqualToThreshold \
  --evaluation-periods 1 \
  --alarm-actions arn:aws:sns:us-east-1:123456789:your-alert-topic \
  --treat-missing-data notBreaching
```

### IAM — add DLQ permissions to your task role

```json
{
  "Effect": "Allow",
  "Action": [
    "sqs:ReceiveMessage",
    "sqs:DeleteMessage",
    "sqs:ChangeMessageVisibility",
    "sqs:GetQueueAttributes"
  ],
  "Resource": [
    "arn:aws:sqs:REGION:ACCOUNT_ID:orders-queue",
    "arn:aws:sqs:REGION:ACCOUNT_ID:orders-queue-dlq"
  ]
}
```

---

### ⚙️ How it works

- **Each decorated function spawns one or more polling threads:**
	1.	Long-polls SQS (WaitTimeSeconds=20 by default).
	2.	Receives a batch (up to 10 messages).
	3.	Extends visibility timeout while processing (heartbeat thread).
	4.	Invokes your handler in either batch or per-message mode.
	5.	Deletes successfully processed messages.
	6.	Keeps failed ones for retry / DLQ.
	7.	Gracefully drains on SIGTERM (during ECS scale-in or container stop).

> [!NOTE]
> **Everything runs in-process with no need for AWS Lambda or additional services.**

### 🪶 Features at a glance

| Feature                   | Description                                                  |
|---------------------------|--------------------------------------------------------------|
| Decorator-based API       | Register multiple handlers with `@sqs_listener`.             |
| Batch & per-message modes | Choose your processing style.                                |
| Async handler support     | `async def` handlers work out of the box.                    |
| Automatic long-polling    | Efficient queue reads (WaitTimeSeconds=20).                  |
| Visibility heartbeat      | Prevents double processing during long jobs.                 |
| Partial batch failure     | Delete successes, leave failed messages.                     |
| Message filtering         | Skip non-matching messages without consuming them.           |
| Retry with backoff        | Exponential backoff via `ChangeMessageVisibility`.           |
| Hooks                     | `on_success` / `on_failure` callbacks per message.           |
| Health check endpoint     | `GET /health` and `GET /metrics` over HTTP.                  |
| Graceful shutdown         | Finishes in-flight work on SIGTERM.                          |
| Thread-safe concurrency   | Multiple pollers per queue.                                  |
| Structured JSON logging   | `LOG_JSON=1` for CloudWatch/Datadog/Splunk.                  |
| Built-in test helpers     | `FakeSQSQueue` — test handlers without AWS or mocking boto3. |
| Fully configurable        | Override via decorator args or env vars.                     |


---

## 🧠 Configuration

### Decorator arguments

```python
@sqs_listener(
  queue_url="...",           # required (or QUEUE_URL env var)
  mode="batch",              # "batch" or "per_message"

  # Polling
  wait_time=10,              # long-poll seconds (0–20)
  batch_size=5,              # messages per poll (1–10)
  visibility_secs=45,        # initial visibility timeout
  max_extend=600,            # max total visibility extension
  worker_threads=8,          # concurrent poller threads

  # Filtering
  filter_fn=lambda m: ...,   # skip non-matching messages

  # Retry
  retry_backoff=True,        # exponential backoff on failure
  retry_backoff_base=30,     # base delay in seconds
  retry_backoff_max=600,     # max delay cap in seconds

  # Hooks
  on_success=my_fn,          # called with (SqsMessage,) on success
  on_failure=my_fn,          # called with (SqsMessage, exc|None) on failure

  # Observability
  health_check_port=8080,    # enables GET /health and GET /metrics
)
```

## Environment variables

| Variable        | Default | Description                        |
|-----------------|---------|------------------------------------|
| WAIT_TIME       | 20      | SQS long-poll seconds              |
| BATCH_SIZE      | 10      | Max messages per poll              |
| VISIBILITY_SECS | 60      | Initial visibility timeout         |
| MAX_EXTEND      | 900     | Max total visibility extension     |
| WORKER_THREADS  | 4       | Poller threads per listener        |
| IDLE_SLEEP_MAX  | 2.0     | Max random sleep after empty poll  |


> [!NOTE]
> Environment variables are overridden by decorator arguments. Precedence: Decorator > Env > Default

---

## 🪵 Logging

Three output modes controlled entirely by env vars:

| Mode      | When                             | Best for                                               |
|-----------|----------------------------------|--------------------------------------------------------|
| **JSON**  | `LOG_JSON=1`                     | CloudWatch, Datadog, Splunk — one JSON object per line |
| **Color** | TTY detected + `LOG_USE_COLOR=1` | Local development                                      |
| **Plain** | Non-TTY, no JSON                 | Simple text output                                     |

### Environment variables

| Variable         | Default                                           | Description                                  |
|------------------|---------------------------------------------------|----------------------------------------------|
| LOG_JSON         | 0                                                 | Emit structured JSON logs (overrides color)  |
| LOG_LEVEL        | INFO                                              | One of DEBUG, INFO, WARN, ERROR              |
| LOG_USE_COLOR    | 1                                                 | Use ANSI colors (auto-disabled if not a TTY) |
| LOG_FORMAT       | colored pattern                                   | Colored log format string                    |
| LOG_PLAIN_FORMAT | [%(levelname)s] %(message)s (%(name)s:%(lineno)d) | Used in non-TTY / plain mode                 |
| LOG_DATEFMT      | %Y-%m-%d %H:%M:%S                                 | Timestamp format                             |

### Examples

**Local dev — colorized:**
```bash
LOG_LEVEL=DEBUG python app.py
```

**ECS / CloudWatch — structured JSON:**
```bash
LOG_JSON=1 python app.py
```

JSON output looks like:
```json
{"timestamp": "2025-04-20T10:30:00.123Z", "level": "INFO", "logger": "sqs_fargate_listener.engine", "message": "Received 5 message(s).", "line": 159}
{"timestamp": "2025-04-20T10:30:01.456Z", "level": "ERROR", "logger": "sqs_fargate_listener.engine", "message": "[handler] error: timeout", "line": 251, "message_id": "abc-123"}
```

Any `extra` fields you pass in your own handler logs are included automatically:

```python
import logging
log = logging.getLogger("myapp")
log.info("Order processed", extra={"order_id": "X99", "trace_id": "t-abc"})
# → {"timestamp": "...", "level": "INFO", ..., "order_id": "X99", "trace_id": "t-abc"}
```

> [!NOTE]
> The logger automatically detects non-TTY environments and switches to plain text when `LOG_JSON` is not set, so CloudWatch always gets readable output even without JSON mode.

---

## 🧰 IAM Permissions

The task role (or instance role) needs these actions on your queue:

```json
{
  "Effect": "Allow",
  "Action": [
    "sqs:ReceiveMessage",
    "sqs:DeleteMessage",
    "sqs:ChangeMessageVisibility",
    "sqs:GetQueueAttributes"
  ],
  "Resource": "arn:aws:sqs:REGION:ACCOUNT_ID:QUEUE_NAME"
}
```

## 🧭 Deploying on ECS Fargate

- Build a Docker image with your app and this package installed.

	```dockerfile
	FROM python:3.11-slim
	WORKDIR /app
	COPY . .
	RUN pip install .
	CMD ["python", "app.py"]
	```

- Create a Task Definition:
  * Use your queue’s QUEUE_URL as an environment variable.
  * Add your AWS permissions via IAM task role.
  * Set stopTimeout to ≥ 60 seconds for graceful drains.
  
- Run a Service on Fargate:
  * Launch at least one replica.
  * Enable autoscaling based on SQS metrics (ApproximateNumberOfMessagesVisible).

## ⚡️ Autoscaling tips

**Use target tracking on queue depth per task:**

| Metric                                            | Example Target       |
|---------------------------------------------------|----------------------|
| ApproximateNumberOfMessagesVisible / DesiredCount | 10 messages per task |


> [!NOTE]
> Scale out when backlog > target, in when < target.

---

## 🧱 How it differs from AWS Lambda + SQS


| Capability            | AWS Lambda              | sqs-fargate-listener    |
|-----------------------|-------------------------|-------------------------|
| Managed polling       | ✅                       | ✅ (inside container)    |
| Pay-per-invocation    | ✅                       | ❌ (you manage tasks)    |
| Concurrency autoscale | ✅                       | via ECS autoscaling     |
| Partial batch failure | ✅                       | ✅                       |
| Visibility heartbeat  | ✅                       | ✅                       |
| Code model            | def handler(event, ctx) | @sqs_listener decorator |
| Local debugging       | Limited                 | ✅                       |

---

## 🔒 Graceful shutdown behavior

**When Fargate stops a task, it sends SIGTERM.**

- **The listener:**
  * Stops fetching new messages.
  * Waits for active handlers to finish.
  * Extends visibility if needed.
  * Exits cleanly.

Set ECS container stopTimeout ≥ your typical processing time.

---


## 🧪 Testing

### Testing your handlers with FakeSQSQueue

Test your handlers without AWS, LocalStack, or mocking boto3:

```python
from sqs_fargate_listener import FakeSQSQueue
from sqs_fargate_listener.types import BatchResult

# --- Batch mode ---
def test_handle_orders():
    queue = FakeSQSQueue()
    queue.send({"order_id": "1", "amount": 99})
    queue.send({"order_id": "2", "amount": 0})   # will fail

    def handle_orders(messages):
        failed = [m.receipt_handle for m in messages if m.json["amount"] == 0]
        return BatchResult(failed_receipt_handles=failed)

    result = queue.run_handler(handle_orders, mode="batch")
    assert result.processed == 1
    assert result.failed == 1

# --- Per-message mode ---
def test_handle_email():
    queue = FakeSQSQueue()
    queue.send({"recipient": "a@example.com"})

    def handle_email(msg):
        return "@" in msg.json["recipient"]

    result = queue.run_handler(handle_email, mode="per_message")
    assert result.processed == 1
    assert queue.deleted_count == 1

# --- Async handler ---
async def handle_async(msg):
    return True

def test_async_handler():
    queue = FakeSQSQueue()
    queue.send({"event": "signup"})
    result = queue.run_handler(handle_async, mode="per_message")
    assert result.processed == 1

# --- With filter ---
def test_filter():
    queue = FakeSQSQueue()
    queue.send({"type": "order"})
    queue.send({"type": "refund"})   # filtered out

    result = queue.run_handler(
        lambda msgs: BatchResult(failed_receipt_handles=[]),
        mode="batch",
        filter_fn=lambda m: m.json.get("type") == "order",
    )
    assert result.processed == 1
    assert result.filtered == 1
```

**pytest fixture** — auto-resets between tests:

```python
# conftest.py  (or import directly)
from sqs_fargate_listener.testing import fake_sqs_queue   # re-export for pytest

def test_with_fixture(fake_sqs_queue):
    fake_sqs_queue.send({"id": "123"})
    result = fake_sqs_queue.run_handler(my_handler, mode="per_message")
    assert result.processed == 1
```

### Running the test suite

```bash
make run-pytest
```

This executes `pytest` against the `tests/` directory and produces a `coverage.xml` report. Key things covered:

- **`test_core.py`** — polling loop, visibility heartbeat, batch deletion, and graceful shutdown on SIGTERM.
- **`test_decorator.py`** — `@sqs_listener` registration, option propagation, and `run_listeners` orchestration.
- **`test_types.py`** — `SqsMessage` helpers (`.json`, `.try_json()`, `.message_attributes()`), and `BatchResult` validation.
- **`test_validation.py`** — input validation for all decorator and engine parameters.
- **`test_logging.py`** — `JsonFormatter` output, extra field passthrough, and `get_logger` behavior.

### End-to-End Tests (LocalStack)

- **Quick Test Setup:**

  * Start LocalStack and create queue: `make localstack queue`
  * Full e2e flow: `make test` (starts LocalStack, creates queue, runs worker, sends message)

- **Individual Testing Steps:**

  * Launch infrastructure: `make up` (starts LocalStack + worker)
  * Send test message: `make send`
  * Monitor processing: `make logs`
  * Cleanup: `make down`

- **Configuration:**

  * Default queue: `test-queue` in `us-east-1`
  * LocalStack endpoint: `http://localhost:4566`
  * Customize with env vars: `REGION`, `ENDPOINT`, `QUEUE_NAME`

> [!NOTE]
> Make sure `docker` and `aws` CLI are installed and configured in your local.

You can use [LocalStack](https://localstack.cloud) or AWS's [SQS mock](https://docs.aws.amazon.com/cli/latest/reference/sqs/) for local queues.

### End-to-end test example

```bash
#!/bin/zsh
docker compose up -d localstack
aws --endpoint-url=http://localhost:4566 sqs create-queue --queue-name test-queue --region us-east-1
export QUEUE_URL="http://sqs.us-east-1.localhost.localstack.cloud:4566/000000000000/test-queue"
docker compose up --build
```

---

## 🤝 Contributing

Contributions, bug reports, and feature requests are welcome! Please read the [Contributing Guide](CONTRIBUTING.md) before opening a pull request.

- Fork → Branch → PR against `master`.
- Follow [Conventional Commits](https://www.conventionalcommits.org/) for commit messages.
- Ensure all tests pass (`make run-pytest`) and code is linted (`make lint`).

---


## 🧾 License


**MIT © 2026**
