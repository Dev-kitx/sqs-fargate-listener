# tests/test_decorator.py
# -------------------------------------------------------------------
# Pytest unit tests for the sqs_fargate_listener.decorator module.
# -------------------------------------------------------------------

import pytest

import src.sqs_fargate_listener.decorator as dec

Q = "http://localhost:4566/000000000000/q"
Q1 = "http://localhost:4566/000000000000/q1"
Q2 = "http://localhost:4566/000000000000/q2"


# ---------------------------
# Fixtures
# ---------------------------


@pytest.fixture(autouse=True)
def clean_registry():
    dec._REGISTRY.clear()
    yield
    dec._REGISTRY.clear()


@pytest.fixture
def fake_engine_cls(monkeypatch):
    created = []

    class FakeEngine:
        def __init__(self, *, queue_url, handler, mode, **opts):
            self.kwargs = {"queue_url": queue_url, "handler": handler, "mode": mode, **opts}
            self.started = False
            self.joined = False
            created.append(self)

        def start(self):
            self.started = True

        def join(self):
            self.joined = True

    monkeypatch.setattr(dec, "SqsListenerEngine", FakeEngine)
    return created


# ---------------------------
# Tests: decorator basics
# ---------------------------


def test_decorator_raises_when_no_queue_url_and_no_env(monkeypatch):
    monkeypatch.delenv("QUEUE_URL", raising=False)

    with pytest.raises(ValueError, match="queue_url is required"):

        @dec.sqs_listener()
        def h(_): ...


def test_decorator_uses_env_queue_url_when_not_provided(monkeypatch):
    monkeypatch.setenv("QUEUE_URL", Q)

    @dec.sqs_listener()
    def handler(_): ...

    assert len(dec._REGISTRY) == 1
    spec = dec._REGISTRY[0]
    assert spec.queue_url == Q
    assert spec.handler is handler
    assert spec.mode == "batch"


def test_decorator_allows_explicit_queue_url_over_env(monkeypatch):
    monkeypatch.setenv("QUEUE_URL", "http://should/not/use")

    @dec.sqs_listener(queue_url=Q1, mode="per_message")
    def handler(_): ...

    assert len(dec._REGISTRY) == 1
    spec = dec._REGISTRY[0]
    assert spec.queue_url == Q1
    assert spec.mode == "per_message"


def test_decorator_returns_original_function(monkeypatch):
    monkeypatch.setenv("QUEUE_URL", Q)

    def f(_):
        return "ok"

    wrapped = dec.sqs_listener()(f)
    assert wrapped is f
    assert wrapped("x") == "ok"


def test_multiple_decorators_register_multiple_listeners(monkeypatch):
    monkeypatch.setenv("QUEUE_URL", Q1)

    @dec.sqs_listener()
    def h1(_): ...

    monkeypatch.setenv("QUEUE_URL", Q2)

    @dec.sqs_listener(mode="per_message")
    def h2(_): ...

    assert len(dec._REGISTRY) == 2
    assert dec._REGISTRY[0].queue_url == Q1
    assert dec._REGISTRY[1].queue_url == Q2
    assert dec._REGISTRY[0].handler is h1
    assert dec._REGISTRY[1].handler is h2


# ---------------------------
# Tests: option propagation
# ---------------------------


def test_options_propagate_and_none_are_dropped(monkeypatch, fake_engine_cls):
    monkeypatch.setenv("QUEUE_URL", Q)

    @dec.sqs_listener(
        mode="batch",
        wait_time=15,
        batch_size=7,
        visibility_secs=45,
        max_extend=300,
        worker_threads=2,
        extra_a="A",
        extra_b=0,
        none_opt=None,
    )
    def h(_): ...

    dec.run_listeners(block=False)

    assert len(fake_engine_cls) == 1
    kw = fake_engine_cls[0].kwargs

    assert kw["queue_url"] == Q
    assert kw["handler"] is h
    assert kw["mode"] == "batch"
    assert kw["wait_time"] == 15
    assert kw["batch_size"] == 7
    assert kw["visibility_secs"] == 45
    assert kw["max_extend"] == 300
    assert kw["worker_threads"] == 2
    assert kw["extra_a"] == "A"
    assert kw["extra_b"] == 0
    assert "none_opt" not in kw


# ---------------------------
# Tests: run_listeners behavior
# ---------------------------


def test_run_listeners_raises_when_empty_registry():
    with pytest.raises(RuntimeError, match="No listeners registered"):
        dec.run_listeners(block=False)


def test_run_listeners_starts_engines_no_block(monkeypatch, fake_engine_cls):
    monkeypatch.setenv("QUEUE_URL", Q)

    @dec.sqs_listener()
    def h(_): ...

    dec.run_listeners(block=False)

    assert len(fake_engine_cls) == 1
    eng = fake_engine_cls[0]
    assert eng.started is True
    assert eng.joined is False


def test_run_listeners_starts_and_joins_when_block_true(monkeypatch, fake_engine_cls):
    monkeypatch.setenv("QUEUE_URL", Q)

    @dec.sqs_listener()
    def h(_): ...

    dec.run_listeners(block=True)

    assert len(fake_engine_cls) == 1
    eng = fake_engine_cls[0]
    assert eng.started is True
    assert eng.joined is True


def test_run_listeners_multiple_handlers(monkeypatch, fake_engine_cls):
    monkeypatch.setenv("QUEUE_URL", Q1)

    @dec.sqs_listener()
    def h1(_): ...

    monkeypatch.setenv("QUEUE_URL", Q2)

    @dec.sqs_listener(mode="per_message", worker_threads=1)
    def h2(_): ...

    dec.run_listeners(block=False)

    assert len(fake_engine_cls) == 2
    q_urls = {e.kwargs["queue_url"] for e in fake_engine_cls}
    assert q_urls == {Q1, Q2}
    modes = {e.kwargs["mode"] for e in fake_engine_cls}
    assert modes == {"batch", "per_message"}
    assert all(e.started for e in fake_engine_cls)
    assert not any(e.joined for e in fake_engine_cls)


def test_logger_is_callable_and_module_has_logger():
    assert hasattr(dec, "_logger")
    assert hasattr(dec._logger, "info")
