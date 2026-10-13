import time
from collections.abc import Generator
from contextlib import contextmanager

import structlog

logger = structlog.getLogger(__name__)


@contextmanager
def log_duration(activity_name: str) -> Generator[None]:
    """Context manager to log the duration of a block of code."""
    start = time.perf_counter()
    try:
        yield
    finally:
        end = time.perf_counter()
        logger.debug(f"{activity_name} took {end - start:.2f} seconds")
