"""
key_manager.py — Automatic Gemini API key rotation.
Tries each key in order; on 429/quota-exceeded moves to the next one.
Thread-safe index persisted in data/current_key_index.txt.
"""

from __future__ import annotations

import os
from pathlib import Path

from src.config import config
from src.utils import get_logger

log = get_logger(__name__)

_INDEX_FILE = config.DATA_DIR / "current_key_index.txt"


def _load_keys() -> list[str]:
    """Load all keys from env vars GEMINI_API_KEY, GEMINI_API_KEY_2 … GEMINI_API_KEY_N."""
    keys: list[str] = []

    # Primary key
    k1 = os.getenv("GEMINI_API_KEY", "")
    if k1:
        keys.append(k1)

    # Numbered extras: GEMINI_API_KEY_2 through GEMINI_API_KEY_20
    for i in range(2, 21):
        k = os.getenv(f"GEMINI_API_KEY_{i}", "")
        if k:
            keys.append(k)

    return keys


def _get_index() -> int:
    try:
        return int(_INDEX_FILE.read_text().strip())
    except Exception:
        return 0


def _set_index(idx: int) -> None:
    _INDEX_FILE.write_text(str(idx))


def get_active_key() -> str:
    """Return the currently active Gemini API key."""
    keys = _load_keys()
    if not keys:
        raise RuntimeError("No Gemini API keys configured.")
    idx = _get_index() % len(keys)
    return keys[idx]


def rotate_key(reason: str = "") -> str:
    """
    Mark current key as exhausted and move to the next one.
    Returns the new active key.
    """
    keys = _load_keys()
    if not keys:
        raise RuntimeError("No Gemini API keys configured.")

    old_idx = _get_index() % len(keys)
    new_idx = (old_idx + 1) % len(keys)
    _set_index(new_idx)

    log.warning(
        "Key %d/%d exhausted%s — rotating to key %d/%d",
        old_idx + 1, len(keys),
        f" ({reason})" if reason else "",
        new_idx + 1, len(keys),
    )
    return keys[new_idx]


def get_all_keys() -> list[str]:
    return _load_keys()


def key_status() -> dict:
    keys = _load_keys()
    idx = _get_index() % max(len(keys), 1)
    return {
        "total_keys": len(keys),
        "active_key_index": idx + 1,
        "active_key_preview": keys[idx][:12] + "…" if keys else "none",
    }
