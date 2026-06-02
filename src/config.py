"""Centralised configuration loaded from environment variables."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent


def _path(env_key: str, default: str) -> Path:
    p = Path(os.getenv(env_key, str(BASE_DIR / default)))
    p.mkdir(parents=True, exist_ok=True)
    return p


def _int(key: str, default: int) -> int:
    try:
        return int(os.getenv(key, str(default)))
    except ValueError:
        return default


class Config:
    # ── Paths ──────────────────────────────────────────────────────────────
    BASE_DIR: Path = BASE_DIR
    DATA_DIR: Path = _path("DATA_DIR", "data")
    LOGS_DIR: Path = _path("LOGS_DIR", "logs")
    GENERATED_DIR: Path = _path("GENERATED_DIR", "generated")
    AUDIO_DIR: Path = _path("AUDIO_DIR", "generated/audio")
    IMAGES_DIR: Path = _path("IMAGES_DIR", "generated/images")
    VIDEOS_DIR: Path = _path("VIDEOS_DIR", "generated/videos")
    THUMBNAILS_DIR: Path = _path("THUMBNAILS_DIR", "generated/thumbnails")
    DB_PATH: Path = DATA_DIR / "app.db"

    # ── Gemini AI ─────────────────────────────────────────────────────────
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
    GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
    GEMINI_IMAGE_MODEL: str = os.getenv("GEMINI_IMAGE_MODEL", "imagen-3.0-generate-001")

    # ── YouTube ───────────────────────────────────────────────────────────
    YOUTUBE_CLIENT_ID: str = os.getenv("YOUTUBE_CLIENT_ID", "")
    YOUTUBE_CLIENT_SECRET: str = os.getenv("YOUTUBE_CLIENT_SECRET", "")
    YOUTUBE_REFRESH_TOKEN: str = os.getenv("YOUTUBE_REFRESH_TOKEN", "")
    YOUTUBE_PRIVACY_STATUS: str = os.getenv("YOUTUBE_PRIVACY_STATUS", "public")
    YOUTUBE_CATEGORY_ID: str = os.getenv("YOUTUBE_CATEGORY_ID", "22")
    YOUTUBE_TOKEN_FILE: Path = DATA_DIR / "youtube_token.json"

    # ── Unsplash ──────────────────────────────────────────────────────────
    UNSPLASH_ACCESS_KEY: str = os.getenv("UNSPLASH_ACCESS_KEY", "")

    # ── Google Trends ─────────────────────────────────────────────────────
    TRENDS_GEO: str = os.getenv("TRENDS_GEO", "IN")
    TRENDS_COUNT: int = _int("TRENDS_COUNT", 20)

    # ── Video ─────────────────────────────────────────────────────────────
    VIDEO_WIDTH: int = _int("VIDEO_WIDTH", 1080)
    VIDEO_HEIGHT: int = _int("VIDEO_HEIGHT", 1920)
    VIDEO_FPS: int = _int("VIDEO_FPS", 30)
    VIDEO_DURATION: int = _int("VIDEO_DURATION", 60)

    # ── Generation ────────────────────────────────────────────────────────
    SHORTS_PER_RUN: int = _int("SHORTS_PER_RUN", 1)

    # ── Channel branding ──────────────────────────────────────────────────
    CHANNEL_NAME: str = os.getenv("CHANNEL_NAME", "TrendSnap AI")
    CHANNEL_WATERMARK: str = os.getenv("CHANNEL_WATERMARK", "@TrendSnapAI")

    # ── Reliability ───────────────────────────────────────────────────────
    MAX_RETRIES: int = _int("MAX_RETRIES", 3)
    RETRY_DELAY: int = _int("RETRY_DELAY", 5)
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")

    # ── Derived sub-dirs (ensure they exist) ──────────────────────────────
    def __init_subclass__(cls) -> None:
        pass

    @classmethod
    def validate(cls) -> list[str]:
        """Return list of missing required env-vars."""
        missing: list[str] = []
        for key in ("GEMINI_API_KEY",):
            if not getattr(cls, key):
                missing.append(key)
        return missing


config = Config()
