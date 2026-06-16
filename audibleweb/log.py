"""Structured logging: key=value format, job_id context injection, file rotation."""

import logging
import logging.handlers
from contextvars import ContextVar
from pathlib import Path

from audibleweb.config import LoggingConfig

_job_id_var: ContextVar[str | None] = ContextVar("job_id", default=None)


class _JobIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.job_id = _job_id_var.get() or ""  # type: ignore[attr-defined]
        return True


class _KVFormatter(logging.Formatter):
    _DATEFMT = "%Y-%m-%dT%H:%M:%S"

    def format(self, record: logging.LogRecord) -> str:
        record.message = record.getMessage()
        ts = self.formatTime(record, self._DATEFMT)
        job_id = getattr(record, "job_id", "")
        job_part = f" job_id={job_id}" if job_id else ""
        exc_part = ""
        if record.exc_info:
            exc_part = " exc=" + self.formatException(record.exc_info).replace(
                "\n", "\\n"
            )
        return (
            f"time={ts}"
            f" level={record.levelname}"
            f" logger={record.name}"
            f" msg={record.message}"
            f"{job_part}"
            f"{exc_part}"
        )


def set_job_id(job_id: str | None) -> None:
    _job_id_var.set(job_id)


def setup_logging(config: LoggingConfig) -> logging.Handler | None:
    """Configure audibleweb logger with a rotating file handler.

    Returns the created handler, or None if log_path is empty.
    """
    if not config.log_path:
        return None

    log_path = Path(config.log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    handler = logging.handlers.RotatingFileHandler(
        log_path,
        maxBytes=config.max_bytes,
        backupCount=config.backup_count,
        encoding="utf-8",
    )
    handler.addFilter(_JobIdFilter())
    handler.setFormatter(_KVFormatter())

    logger = logging.getLogger("audibleweb")
    logger.setLevel(config.log_level.upper())
    logger.addHandler(handler)
    return handler
