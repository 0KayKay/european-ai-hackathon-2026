from httpx2 import (
    AsyncBaseTransport,
    AsyncClient,
    Auth,
    HTTPStatusError,
    Response,
    Timeout,
    TransportError,
)
from pydantic_ai.retries import (
    AsyncHTTPX2TenacityTransport,
    RetryConfig,
    wait_exponential,
    wait_retry_after,
)
from tenacity import retry_if_exception_type, stop_after_attempt


def create_retrying_client(
    headers: dict[str, str] | None = None,
    timeout: Timeout | None = None,
    auth: Auth | None = None,
    *,
    transport: AsyncBaseTransport | None = None,
) -> AsyncClient:
    """Create a general-purpose HTTP client with retry handling."""
    return AsyncClient(
        transport=transport if transport is not None else create_tenacity_transport(),
        follow_redirects=True,
        headers=headers,
        auth=auth,
        timeout=timeout or Timeout(60.0, connect=5.0),
    )


def create_tenacity_transport() -> AsyncHTTPX2TenacityTransport:
    """Create the existing general-purpose retry transport."""

    def should_retry_status(response: Response) -> None:
        if response.status_code in (429, 502, 503, 504):
            response.raise_for_status()

    return AsyncHTTPX2TenacityTransport(
        config=RetryConfig(
            retry=retry_if_exception_type((HTTPStatusError, TransportError)),
            wait=wait_retry_after(
                fallback_strategy=wait_exponential(multiplier=1, min=0.5, max=10),
                max_wait=30,
            ),
            stop=stop_after_attempt(3),
            reraise=True,
        ),
        validate_response=should_retry_status,
    )
