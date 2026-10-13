from functools import lru_cache

from httpx import AsyncClient, HTTPStatusError, Response
from pydantic_ai.retries import AsyncTenacityTransport, RetryConfig, wait_retry_after
from tenacity import retry_if_exception_type, stop_after_attempt, wait_exponential


@lru_cache
def create_retrying_client() -> AsyncClient:
    """Create a client with smart retry handling for multiple error types."""
    return AsyncClient(transport=create_tenacity_transport())


@lru_cache
def create_tenacity_transport() -> AsyncTenacityTransport:
    """Creates a transport for smart retry handling for multiple error types"""

    def should_retry_status(response: Response) -> None:
        """Raise exceptions for retryable HTTP status codes."""
        if response.status_code in (429, 502, 503, 504):
            response.raise_for_status()

    return AsyncTenacityTransport(
        config=RetryConfig(
            retry=retry_if_exception_type((HTTPStatusError, ConnectionError)),
            wait=wait_retry_after(
                fallback_strategy=wait_exponential(multiplier=1, max=15), max_wait=60
            ),
            stop=stop_after_attempt(3),
            reraise=True,
        ),
        validate_response=should_retry_status,
    )
