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
    GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
    GEMINI_IMAGE_MODEL: str = os.getenv("GEMINI_IMAGE_MODEL", "imagen-3.0-generate-001")
    # Cap output tokens hard — the compact retention JSON fits in <2k tokens.
    # (Old value was 8192, which let the model ramble and burned free-tier quota.)
    GEMINI_MAX_OUTPUT_TOKENS: int = _int("GEMINI_MAX_OUTPUT_TOKENS", 2048)
    # Cache generated content per topic so re-runs cost ZERO Gemini tokens.
    CONTENT_CACHE: bool = os.getenv("CONTENT_CACHE", "true").lower() == "true"

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

    # ── Text-to-Speech (edge-tts neural voices, free, no API key) ──────────
    # language: "en" (Indian English) | "hi" (Hindi) | "hinglish" (mixed)
    TTS_LANGUAGE: str = os.getenv("TTS_LANGUAGE", "en")
    # Explicit voice override; if empty a voice is chosen from TTS_LANGUAGE.
    TTS_VOICE: str = os.getenv("TTS_VOICE", "")
    # Slightly slower than default reads more naturally and gives captions room.
    TTS_RATE: str = os.getenv("TTS_RATE", "-6%")
    TTS_PITCH: str = os.getenv("TTS_PITCH", "+0Hz")

    # ── Video ─────────────────────────────────────────────────────────────
    VIDEO_WIDTH: int = _int("VIDEO_WIDTH", 1080)
    VIDEO_HEIGHT: int = _int("VIDEO_HEIGHT", 1920)
    VIDEO_FPS: int = _int("VIDEO_FPS", 30)
    VIDEO_DURATION: int = _int("VIDEO_DURATION", 60)  # hard cap, not a target

    # ── Kinetic motion / captions ──────────────────────────────────────────
    # Background scene swaps on this cadence → constant visual novelty.
    SCENE_SECONDS: float = float(os.getenv("SCENE_SECONDS", "2.6"))
    # Max words on screen at once — keeps captions readable, never the full script.
    CAPTION_MAX_WORDS: int = _int("CAPTION_MAX_WORDS", 3)
    CAPTION_FONT_SIZE: int = _int("CAPTION_FONT_SIZE", 104)
    HOOK_FONT_SIZE: int = _int("HOOK_FONT_SIZE", 128)
    # Highlight colour for the word being spoken (R,G,B). Bright yellow-green pops.
    CAPTION_HIGHLIGHT: str = os.getenv("CAPTION_HIGHLIGHT", "255,221,0")
    HOOK_SECONDS: float = float(os.getenv("HOOK_SECONDS", "3.0"))
    CTA_SECONDS: float = float(os.getenv("CTA_SECONDS", "2.8"))
    # Optional drop-in font dir (e.g. Montserrat-ExtraBold.ttf, Anton.ttf).
    FONTS_DIR: Path = _path("FONTS_DIR", "assets/fonts")

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
