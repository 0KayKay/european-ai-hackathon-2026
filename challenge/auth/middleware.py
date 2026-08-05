import structlog
from chainlit.auth.cookie import get_token_from_cookies
from chainlit.auth.jwt import decode_jwt as decode_chainlit_token
from fastapi import status
from fastapi.exceptions import HTTPException
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response
from structlog import BoundLogger
from chainlit import User

from challenge.config import get_settings

settings = get_settings()
logger: BoundLogger = structlog.get_logger("auth_middleware")


class AuthMiddleware(BaseHTTPMiddleware):
    """Middleware to decide whether to authenticate users via Entra ID or LDAP."""

    async def dispatch(
            self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        """Main middleware dispatch method."""
        if request["path"] == f"{settings.CHAINLIT_ROOT_PATH}/login":
            return RedirectResponse(settings.CHAINLIT_ROOT_PATH, status_code=status.HTTP_302_FOUND)
        if self.is_public(request):
            # add user to request if logged in for
            try:
                user = await self.try_getting_valid_chainlit_session(request)
                request.state.user = user
            except Exception:
                pass
            return await call_next(request)
        if self.is_eager_websocket_request(request):
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={
                    "status": 401,
                    "message": "Eager WebSocket connection without authentication",
                },
            )
        try:
            # if returns user, session is valid. Otherwise raises exception
            user = await self.try_getting_valid_chainlit_session(request)
            request.state.user = user
            return await call_next(request)
        except Exception:
            pass

        return RedirectResponse("/signup", status_code=status.HTTP_302_FOUND)

    @staticmethod
    def is_public(request: Request) -> bool:
        """Check if authentication should be skipped for this request."""
        exclude = [
            f"{settings.CHAINLIT_ROOT_PATH}/set-session-cookie",
            f"{settings.CHAINLIT_ROOT_PATH}/public",
            f"{settings.CHAINLIT_ROOT_PATH}/assets",
            "/auth",
            "/docs",
            "/redoc",
            "/openapi.json",
            # add any other paths that should be excluded from auth
        ]
        if request.scope["type"] != "http":
            return True
        if any(request["path"].startswith(excluded_path) for excluded_path in exclude):
            return True
        return not request["path"].startswith(settings.CHAINLIT_ROOT_PATH) and not request["path"].startswith(settings.CHALLENGE_PAGES_ROOT_PATH)

    @staticmethod
    def is_eager_websocket_request(request: Request) -> bool:
        """Check if the request is a WebSocket request that tries to connect before user authenticated."""
        return request["path"].startswith(f"{settings.CHAINLIT_ROOT_PATH}/ws") and not get_token_from_cookies(
            request.cookies
        )

    @staticmethod
    def get_auth_header(request: Request) -> str | None:
        """Extract the Authorization header."""
        return request.headers.get("Authorization")

    @staticmethod
    async def try_getting_valid_chainlit_session(request: Request) -> User:
        """Attempt to authenticate using Chainlit token."""
        token = get_token_from_cookies(request.cookies)
        if not token:
            raise HTTPException(status_code=400, detail="No Chainlit token found")
        user = decode_chainlit_token(token)
        if not user:
            raise HTTPException(
                status_code=400, detail="Could not decode Chainlit token"
            )
        return user
