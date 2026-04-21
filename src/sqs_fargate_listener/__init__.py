from importlib.metadata import PackageNotFoundError, version

from .decorator import sqs_listener, run_listeners
from .types import SqsMessage, BatchResult
from .testing import FakeSQSQueue, RunResult

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
