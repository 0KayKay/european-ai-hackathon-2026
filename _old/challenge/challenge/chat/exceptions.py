from collections.abc import Callable, Coroutine
from functools import wraps
from typing import Any, ParamSpec, TypeVar

import structlog
from chainlit import ErrorMessage

P = ParamSpec("P")
R = TypeVar("R")


def handle_chat_exceptions[**P, R](
    func: Callable[P, Coroutine[Any, Any, R]],
) -> Callable[P, Coroutine[Any, Any, None]]:
    """Decorator that returns exceptions as ErrorMessage to the user."""

    logger = structlog.get_logger("chat_exception_handler")

    @wraps(func)
    async def wrapper(*args: P.args, **kwargs: P.kwargs) -> None:
        try:
            await func(*args, **kwargs)
        except Exception as exc:
            logger.exception(exc)
            await _handle_exception(exc)
        return None

    return wrapper


async def _handle_exception(exc: Exception) -> None:
    user_facing_message = (
        f"```json\n{exc.message}\n```"
        if hasattr(exc, "message")
        else f"```json\n{exc.__class__.__name__}:{exc!s}\n```"
    )
    await ErrorMessage(author="system", content=user_facing_message).send()
