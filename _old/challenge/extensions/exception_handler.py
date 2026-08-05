import structlog
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException
from starlette.requests import Request


async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Global exception handler to catch unhandled exceptions and return a JSON response."""
    logger = structlog.get_logger("global_exception_handler")
    message = "Unhandled exception occurred"
    if isinstance(exc, HTTPException):
        logger.debug(f"HTTPException: {exc.detail}", status_code=exc.status_code)
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "status": exc.status_code,
                "message": exc.detail if exc.detail else "An error occurred.",
            },
        )
    logger.exception(message, detail=str(exc))
    return JSONResponse(status_code=500, content={"message": message})
