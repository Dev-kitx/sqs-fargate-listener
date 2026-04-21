from importlib.metadata import PackageNotFoundError, version

from .decorator import run_listeners, sqs_listener
from .testing import FakeSQSQueue, RunResult
from .types import BatchResult, SqsMessage

try:
    __version__ = version("sqs-fargate-listener")
except PackageNotFoundError:
    __version__ = "unknown"

__all__ = [
    "sqs_listener",
    "run_listeners",
    "SqsMessage",
    "BatchResult",
    "FakeSQSQueue",
    "RunResult",
    "__version__",
]
