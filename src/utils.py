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
        # Force UTF-8 on Windows so arrow/box chars don't crash cp1252 streams.
        import io
        stream = sys.stdout
        if hasattr(stream, "buffer"):
            stream = io.TextIOWrapper(stream.buffer, encoding="utf-8", errors="replace")
        # Console handler with colour
        ch = colorlog.StreamHandler(stream)
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


# ── Fonts ───────────────────────────────────────────────────────────────────
# Caption rendering needs (a) a heavy/bold Latin font for that "viral caption"
# look and (b) a Devanagari-capable font for Hindi/Hinglish. We resolve both
# from a drop-in assets/fonts dir first, then common system locations, so the
# pipeline renders correct glyphs on Windows (Nirmala UI) and on the Ubuntu CI
# runner (Noto/Lohit) alike.

_FONT_CACHE: dict[tuple[int, bool, bool], Any] = {}

_LATIN_BOLD_CANDIDATES = [
    "Montserrat-ExtraBold.ttf", "Montserrat-Bold.ttf", "Anton-Regular.ttf",
    "Anton.ttf", "BebasNeue-Regular.ttf", "Poppins-Bold.ttf",
    "arialbd.ttf", "Arial Bold.ttf", "ariblk.ttf",
    "DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf", "C:/Windows/Fonts/seguibl.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
]

_DEVANAGARI_CANDIDATES = [
    "NotoSansDevanagari-Bold.ttf", "NotoSansDevanagari-Regular.ttf",
    "Lohit-Devanagari.ttf",
    "C:/Windows/Fonts/NirmalaB.ttf", "C:/Windows/Fonts/Nirmala.ttf",
    "C:/Windows/Fonts/mangalb.ttf", "C:/Windows/Fonts/mangal.ttf",
    "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Bold.ttf",
    "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Regular.ttf",
    "/usr/share/fonts/truetype/lohit-devanagari/Lohit-Devanagari.ttf",
    "/usr/share/fonts/truetype/Sarai/Sarai.ttf",
]


def has_devanagari(text: str) -> bool:
    """True if the string contains any Devanagari codepoint (U+0900–U+097F)."""
    return any("ऀ" <= ch <= "ॿ" for ch in text)


def get_font(size: int, bold: bool = True, devanagari: bool = False):
    """
    Return a PIL ImageFont sized `size`. Picks a Devanagari-capable face when
    `devanagari=True`, otherwise a heavy Latin face. Uses raqm layout when the
    Pillow build supports it (needed for correct Hindi conjuncts/matras).
    """
    from PIL import ImageFont

    key = (size, bold, devanagari)
    if key in _FONT_CACHE:
        return _FONT_CACHE[key]

    from src.config import config
    candidates: list[str] = []
    # Drop-in fonts win, so a user can ship Montserrat/Anton without code changes.
    try:
        for p in sorted(config.FONTS_DIR.glob("*.ttf")) + sorted(config.FONTS_DIR.glob("*.otf")):
            candidates.append(str(p))
    except Exception:
        pass
    candidates += _DEVANAGARI_CANDIDATES if devanagari else _LATIN_BOLD_CANDIDATES

    # Prefer raqm so complex-script (Devanagari) shaping is correct, but only
    # request it when the Pillow build actually has it — otherwise PIL warns and
    # falls back anyway. Linux CI wheels ship with raqm; some Windows builds don't.
    layout = getattr(ImageFont, "Layout", None)
    engines: list = [None]
    if layout is not None:
        have_raqm = False
        try:
            from PIL import features
            have_raqm = features.check("raqm")
        except Exception:
            have_raqm = False
        if devanagari and have_raqm and getattr(layout, "RAQM", None) is not None:
            engines = [layout.RAQM, getattr(layout, "BASIC", None)]
        elif getattr(layout, "BASIC", None) is not None:
            engines = [layout.BASIC]
    engines = [e for e in engines if e is not None] or [None]

    font = None
    for path in candidates:
        for engine in engines:
            try:
                font = (ImageFont.truetype(path, size, layout_engine=engine)
                        if engine is not None else ImageFont.truetype(path, size))
                break
            except (IOError, OSError, ValueError):
                continue
        if font is not None:
            break
    if font is None:
        font = ImageFont.load_default()

    _FONT_CACHE[key] = font
    return font


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
