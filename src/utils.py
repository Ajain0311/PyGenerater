"""Shared utilities: logging, retry helpers, file ops."""

from __future__ import annotations

import functools
import logging
import sys
import time
from pathlib import Path
from typing import Any, Callable, TypeVar

import colorlog
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from src.config import config

F = TypeVar("F", bound=Callable[..., Any])

_LOGGERS: dict[str, logging.Logger] = {}


def get_logger(name: str) -> logging.Logger:
    if name in _LOGGERS:
        return _LOGGERS[name]

    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, config.LOG_LEVEL.upper(), logging.INFO))

    if not logger.handlers:
        # Console handler with colour
        ch = colorlog.StreamHandler(sys.stdout)
        ch.setFormatter(
            colorlog.ColoredFormatter(
                "%(log_color)s%(asctime)s [%(levelname)-8s] %(name)s: %(message)s",
                datefmt="%H:%M:%S",
                log_colors={
                    "DEBUG": "cyan",
                    "INFO": "green",
                    "WARNING": "yellow",
                    "ERROR": "red",
                    "CRITICAL": "bold_red",
                },
            )
        )
        logger.addHandler(ch)

        # File handler
        log_file = config.LOGS_DIR / "app.log"
        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setFormatter(
            logging.Formatter(
                "%(asctime)s [%(levelname)-8s] %(name)s: %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
        logger.addHandler(fh)

    _LOGGERS[name] = logger
    return logger


def api_retry(
    max_attempts: int = 3,
    wait_min: int = 2,
    wait_max: int = 30,
    exceptions: tuple[type[Exception], ...] = (Exception,),
) -> Callable[[F], F]:
    """Decorator: exponential-backoff retry for API calls."""

    def decorator(fn: F) -> F:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            log = get_logger(fn.__module__)
            for attempt in range(1, max_attempts + 1):
                try:
                    return fn(*args, **kwargs)
                except exceptions as exc:
                    if attempt == max_attempts:
                        log.error("All %d attempts failed for %s: %s", max_attempts, fn.__name__, exc)
                        raise
                    wait = min(wait_min * (2 ** (attempt - 1)), wait_max)
                    log.warning("Attempt %d/%d failed (%s). Retrying in %ds…", attempt, max_attempts, exc, wait)
                    time.sleep(wait)
            return None  # unreachable

        return wrapper  # type: ignore[return-value]

    return decorator


def sanitise_filename(name: str, max_len: int = 80) -> str:
    """Strip unsafe chars and truncate for use as a filename."""
    import re

    name = re.sub(r'[\\/:*?"<>|]', "_", name)
    name = re.sub(r"\s+", "_", name.strip())
    return name[:max_len]


def format_duration(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}h {m}m {s}s"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"


def chunk_text(text: str, words_per_chunk: int = 10) -> list[str]:
    """Split text into chunks of ~N words for subtitle display."""
    words = text.split()
    return [
        " ".join(words[i : i + words_per_chunk])
        for i in range(0, len(words), words_per_chunk)
    ]


def safe_delete(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass
