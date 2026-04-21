# tests/test_validation.py
# -------------------------------------------------------------------
# Tests for input validation in decorator.py and core.py.
# -------------------------------------------------------------------

import pytest
import src.sqs_fargate_listener.decorator as dec
import src.sqs_fargate_listener.core as core

Q = "http://localhost:4566/000000000000/q"


@pytest.fixture(autouse=True)
def clean_registry():
    dec._REGISTRY.clear()
    yield
    dec._REGISTRY.clear()


# ---------------------------
# Decorator-level validation
# ---------------------------

@pytest.mark.parametrize("mode", ["BATCH", "per-message", "stream", ""])
def test_invalid_mode_raises(mode):
    with pytest.raises(ValueError, match="mode must be one of"):
        @dec.sqs_listener(queue_url=Q, mode=mode)
        def h(_): ...


@pytest.mark.parametrize("batch_size", [0, -1, 11, 100])
def test_invalid_batch_size_raises(batch_size):
    with pytest.raises(ValueError, match="batch_size"):
        @dec.sqs_listener(queue_url=Q, batch_size=batch_size)
        def h(_): ...


@pytest.mark.parametrize("wait_time", [-1, 21, 100])
def test_invalid_wait_time_raises(wait_time):
    with pytest.raises(ValueError, match="wait_time"):
        @dec.sqs_listener(queue_url=Q, wait_time=wait_time)
        def h(_): ...


@pytest.mark.parametrize("val", [0, -1, -100])
def test_invalid_visibility_secs_raises(val):
    with pytest.raises(ValueError, match="visibility_secs"):
        @dec.sqs_listener(queue_url=Q, visibility_secs=val)
        def h(_): ...


@pytest.mark.parametrize("val", [0, -1])
def test_invalid_max_extend_raises(val):
    with pytest.raises(ValueError, match="max_extend"):
        @dec.sqs_listener(queue_url=Q, max_extend=val)
        def h(_): ...


@pytest.mark.parametrize("val", [0, -1])
def test_invalid_worker_threads_raises(val):
    with pytest.raises(ValueError, match="worker_threads"):
        @dec.sqs_listener(queue_url=Q, worker_threads=val)
        def h(_): ...


@pytest.mark.parametrize("url", ["not-a-url", "sqs://queue", "ftp://queue", "queue-name", ""])
def test_invalid_queue_url_format_raises(url, monkeypatch):
    monkeypatch.delenv("QUEUE_URL", raising=False)
    with pytest.raises(ValueError, match="queue_url"):
        @dec.sqs_listener(queue_url=url)
        def h(_): ...


def test_valid_http_queue_url_accepted(monkeypatch):
    monkeypatch.delenv("QUEUE_URL", raising=False)

    @dec.sqs_listener(queue_url="http://localhost:4566/000/my-queue")
    def h(_): ...

    assert len(dec._REGISTRY) == 1


def test_valid_https_queue_url_accepted(monkeypatch):
    monkeypatch.delenv("QUEUE_URL", raising=False)

    @dec.sqs_listener(queue_url="https://sqs.us-east-1.amazonaws.com/123/my-queue")
    def h(_): ...

    assert len(dec._REGISTRY) == 1


def test_valid_boundary_values_accepted(monkeypatch):
    monkeypatch.delenv("QUEUE_URL", raising=False)

    @dec.sqs_listener(
        queue_url=Q,
        batch_size=1,
        wait_time=0,
        visibility_secs=1,
        max_extend=1,
        worker_threads=1,
    )
    def h(_): ...

    assert len(dec._REGISTRY) == 1


# ---------------------------
# Engine-level validation
# ---------------------------

@pytest.mark.parametrize("url", ["not-a-url", "", "sqs://queue"])
def test_engine_invalid_queue_url_raises(monkeypatch, url):
    monkeypatch.setattr(core.boto3, "client", lambda *a, **kw: None)
    with pytest.raises(ValueError, match="queue_url"):
        core.SqsListenerEngine(queue_url=url, handler=lambda _: None)


@pytest.mark.parametrize("mode", ["BATCH", "per-message", "invalid"])
def test_engine_invalid_mode_raises(monkeypatch, mode):
    monkeypatch.setattr(core.boto3, "client", lambda *a, **kw: None)
    with pytest.raises(ValueError, match="mode"):
        core.SqsListenerEngine(queue_url=Q, handler=lambda _: None, mode=mode)


@pytest.mark.parametrize("batch_size", [0, 11])
def test_engine_invalid_batch_size_raises(monkeypatch, batch_size):
    monkeypatch.setattr(core.boto3, "client", lambda *a, **kw: None)
    with pytest.raises(ValueError, match="batch_size"):
        core.SqsListenerEngine(queue_url=Q, handler=lambda _: None, batch_size=batch_size)


@pytest.mark.parametrize("wait_time", [-1, 21])
def test_engine_invalid_wait_time_raises(monkeypatch, wait_time):
    monkeypatch.setattr(core.boto3, "client", lambda *a, **kw: None)
    with pytest.raises(ValueError, match="wait_time"):
        core.SqsListenerEngine(queue_url=Q, handler=lambda _: None, wait_time=wait_time)


def test_engine_invalid_worker_threads_raises(monkeypatch):
    monkeypatch.setattr(core.boto3, "client", lambda *a, **kw: None)
    with pytest.raises(ValueError, match="worker_threads"):
        core.SqsListenerEngine(queue_url=Q, handler=lambda _: None, worker_threads=0)


def test_engine_invalid_visibility_secs_raises(monkeypatch):
    monkeypatch.setattr(core.boto3, "client", lambda *a, **kw: None)
    with pytest.raises(ValueError, match="visibility_secs"):
        core.SqsListenerEngine(queue_url=Q, handler=lambda _: None, visibility_secs=0)
