from datetime import datetime, timezone
import pytz
import structlog
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response
from structlog import BoundLogger

from _old.challenge.config import get_settings

settings = get_settings()
logger: BoundLogger = structlog.get_logger("time_limit_middleware")


class TimeLimitMiddleware(BaseHTTPMiddleware):
    """Middleware to redirect users based on challenge time limits."""
    # Challenge start date: 24.11.2025 13:30 CET
    START_DATETIME = pytz.timezone('CET').localize(datetime(2025, 11, 24, 13, 30, 0))
    # Challenge end date: 28.11.2025 22:00 CET
    END_DATETIME = pytz.timezone('CET').localize(datetime(2025, 11, 28, 22, 0, 0))
    
    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        """Main middleware dispatch method."""
        if settings.APP_ENV == "local":
            return await call_next(request)

        path = request.url.path
        method = request.method
        cet_tz = pytz.timezone('CET')
        current_time = datetime.now(cet_tz)
        
        is_before_start = current_time < self.START_DATETIME
        is_after_end = current_time > self.END_DATETIME

        # Skip middleware for non-time-limited paths
        if not self._is_time_limited(path, method) or self._is_internal_user(request):
            return await call_next(request)

        # Before start time: ensure users are on countdown page
        if is_before_start:
            logger.info(f"Redirecting to countdown page. Current time: {current_time}, Start: {self.START_DATETIME}")
            return RedirectResponse(url="/countdown", status_code=302)
        
        # After end time: ensure users are on challenge concluded page
        if is_after_end:
            logger.info(f"Redirecting to challenge concluded page. Current time: {current_time}, End: {self.END_DATETIME}")
            return RedirectResponse(url="/challenge-concluded", status_code=302)

        # Continue with normal request processing
        return await call_next(request)
    
    def _is_time_limited(self, path: str, method: str) -> bool:
        """Check if a path should be subject to time-based redirects."""
        include = [
            # f"{settings.CHALLENGE_PAGES_ROOT_PATH}/dashboard",
            f"{settings.CHALLENGE_PAGES_ROOT_PATH}/email_form_submission/",
            f"{settings.CHALLENGE_PAGES_ROOT_PATH}/file_form_submission/",
            # settings.CHAINLIT_ROOT_PATH
        ]
        if any(path.startswith(included_path) for included_path in include) and method == "POST":
            return True
        return False

    def _is_internal_user(self, request: Request) -> bool:
        if request.state.user is None:
            return False
        return request.state.user.identifier.endswith("@lapiscode.com") or request.state.user.identifier.endswith("@codify.ch")