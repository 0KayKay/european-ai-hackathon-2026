import logging.config
from typing import Any

import structlog
from structlog.typing import EventDict, Processor

from challenge.config import get_settings

logger: structlog.stdlib.BoundLogger = structlog.get_logger()


def _add_log_severity(_: Any, method_name: str, event_dict: EventDict) -> EventDict:
    event_dict["severity"] = method_name.upper()
    return event_dict


def configure_logging() -> None:
    """
    Configures structlog and the default Python logger.
    Uses JSON formatting if settings.LOG_FORMAT is 'json', otherwise uses
    a human-readable console format. Sets the log level from settings.
    """

    settings = get_settings()
    log_level = settings.LOG_LEVEL.upper()
    json_format = settings.LOG_FORMAT == "json"

    shared_processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        _add_log_severity if json_format else structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.UnicodeDecoder(),
        structlog.processors.StackInfoRenderer(),
    ]

    if json_format:
        shared_processors.append(structlog.processors.format_exc_info)
        shared_processors.append(structlog.processors.EventRenamer(to="message"))

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.make_filtering_bound_logger(settings.LOG_LEVEL),
        cache_logger_on_first_use=True,
    )

    renderer = (
        structlog.processors.JSONRenderer()
        if settings.LOG_FORMAT == "json"
        else structlog.dev.ConsoleRenderer()
    )

    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "default": {
                    "()": structlog.stdlib.ProcessorFormatter,
                    "processors": [
                        structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                        renderer,
                    ],
                    "foreign_pre_chain": shared_processors,
                },
            },
            "handlers": {
                "default": {
                    "level": log_level,
                    "class": "logging.StreamHandler",
                    "formatter": "default",
                },
            },
            "loggers": {
                "": {
                    "handlers": ["default"],
                    "level": log_level,
                    "propagate": True,
                },
                "asyncio": {
                    "propagate": False,
                },
                "uvicorn": {
                    "propagate": False,
                },
                "httpcore": {
                    "propagate": False,
                },
                "urllib3": {
                    "propagate": False,
                },
                "google_genai": {
                    "propagate": False,
                },
                "httpx": {
                    "propagate": False,
                },
                "google.auth": {
                    "propagate": False,
                },
            },
        }
    )
