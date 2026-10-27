import json
import logging.config
from typing import Any

import structlog
from pydantic.dataclasses import dataclass
from structlog.typing import EventDict, Processor

from hackathon_guardrail.config import get_settings

logger: structlog.stdlib.BoundLogger = structlog.get_logger()


def _add_log_severity(_: Any, method_name: str, event_dict: EventDict) -> EventDict:
    event_dict["severity"] = method_name.upper()
    return event_dict


def _json_default_handler(obj: Any) -> str:
    """Convert non-serializable objects to strings."""
    if isinstance(obj, Exception):
        return f"{type(obj).__name__}: {obj!s}"
    # Fallback: convert any other non-serializable object to string
    return str(obj)


def _json_serializer(obj: Any, *args: Any, **kwargs: Any) -> str:
    return json.dumps(obj, default=_json_default_handler)


@dataclass
class ExternalLogger:
    """Helper for easier logger management"""

    name: str
    level: str | None = None


def configure_logging() -> dict[str, Any]:
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

    json_renderer = structlog.processors.JSONRenderer(serializer=_json_serializer)
    console_renderer = structlog.dev.ConsoleRenderer()

    external_loggers: list[ExternalLogger] = [
        ExternalLogger("uvicorn", level="INFO"),
        ExternalLogger("uvicorn.error", level="WARNING"),
        ExternalLogger("uvicorn.access", level="WARNING"),
        ExternalLogger("httpcore2", level="WARNING"),
        ExternalLogger("httpx2", level="WARNING"),
        ExternalLogger("urllib3", level="WARNING"),
        ExternalLogger("openai._base_client", level="WARNING"),
        ExternalLogger("tzlocal", level="WARNING"),
        ExternalLogger("py.warnings", level="WARNING"),
        ExternalLogger("watchfiles.main", level="WARNING"),
        ExternalLogger("asyncio", level="WARNING"),
    ]

    loggers_dict = {
        "": {
            "handlers": ["default"],
            "level": log_level,
            "propagate": True,
        }
    }

    for ext_logger in external_loggers:
        loggers_dict[ext_logger.name] = {
            "level": ext_logger.level or log_level,
            "propagate": True,
        }

    dict_config = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "default": {
                "()": structlog.stdlib.ProcessorFormatter,
                "processors": [
                    structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                    json_renderer if json_format else console_renderer,
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
        "root": {
            "handlers": ["default"],
            "level": log_level,
        },
        "loggers": loggers_dict,
    }

    logging.config.dictConfig(dict_config)

    # Capture Python warnings through logging system at WARNING level
    logging.captureWarnings(True)

    return dict_config


def normalize_external_loggers() -> None:
    """Force all existing non-root loggers to emit through the root logger."""
    manager = logging.root.manager

    for obj in manager.loggerDict.values():
        if not isinstance(obj, logging.Logger):
            continue
        obj.handlers.clear()
        obj.propagate = True
