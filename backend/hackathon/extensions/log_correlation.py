import uuid

import structlog
from starlette.requests import HTTPConnection
from starlette.types import ASGIApp, Receive, Scope, Send

logger = structlog.get_logger(__name__)


class LogCorrelationIdMiddleware:
    """Middleware to add a correlation ID and User ID to the logging context."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """ASGI middleware entry point."""

        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        structlog.contextvars.bind_contextvars(correlation_id=str(uuid.uuid4()))

        structlog.contextvars.bind_contextvars(
            path=scope.get("path"), method=scope.get("method")
        )

        HTTPConnection(scope)
        try:
            await self.app(scope, receive, send)
        finally:
            structlog.contextvars.unbind_contextvars("correlation_id", "method", "path")
