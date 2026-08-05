import uuid

import structlog
from starlette.types import ASGIApp, Receive, Scope, Send


class LogCorrelationIdMiddleware:
    """Middleware to add a correlation ID to the logging context for each request."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """ASGI middleware entry point."""

        if scope["type"] not in ["http", "websocket"]:
            return await self.app(scope, receive, send)

        structlog.contextvars.bind_contextvars(
            correlation_id=str(uuid.uuid4()),
        )
        if scope["type"] == "http":
            structlog.contextvars.bind_contextvars(
                path=scope["path"], method=scope["method"]
            )

        await self.app(scope, receive, send)

        structlog.contextvars.unbind_contextvars("correlation_id", "method", "path")
        return None
