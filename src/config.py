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
    # When set, the app uses this database instead of local SQLite (e.g. a
    # Supabase/Postgres URL so state PERSISTS and is shared between your PC and
    # the cloud dashboard). Blank → local SQLite at DB_PATH.
    DATABASE_URL: str = os.getenv("DATABASE_URL", "")

    # ── Gemini AI ─────────────────────────────────────────────────────────
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
    GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
    GEMINI_IMAGE_MODEL: str = os.getenv("GEMINI_IMAGE_MODEL", "imagen-3.0-generate-001")
    # Output-token cap. Must leave headroom: on gemini-2.5 models "thinking"
    # tokens count against this same cap, and even with thinking disabled the
    # JSON needs ~1k tokens. 2048 caused truncated JSON → failed runs.
    GEMINI_MAX_OUTPUT_TOKENS: int = _int("GEMINI_MAX_OUTPUT_TOKENS", 4096)
    # Cache generated content per topic so re-runs cost ZERO Gemini tokens.
    CONTENT_CACHE: bool = os.getenv("CONTENT_CACHE", "true").lower() == "true"

    # ── YouTube ───────────────────────────────────────────────────────────
    YOUTUBE_CLIENT_ID: str = os.getenv("YOUTUBE_CLIENT_ID", "")
    YOUTUBE_CLIENT_SECRET: str = os.getenv("YOUTUBE_CLIENT_SECRET", "")
    YOUTUBE_REFRESH_TOKEN: str = os.getenv("YOUTUBE_REFRESH_TOKEN", "")
    YOUTUBE_PRIVACY_STATUS: str = os.getenv("YOUTUBE_PRIVACY_STATUS", "public")
    YOUTUBE_CATEGORY_ID: str = os.getenv("YOUTUBE_CATEGORY_ID", "22")
    YOUTUBE_TOKEN_FILE: Path = DATA_DIR / "youtube_token.json"
    # COPPA: a children's-content channel should usually self-declare videos as
    # "made for kids". Defaults False to preserve legacy behaviour; the kids
    # pipeline passes made_for_kids=True explicitly.
    YOUTUBE_MADE_FOR_KIDS: bool = os.getenv("YOUTUBE_MADE_FOR_KIDS", "false").lower() == "true"

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
    SCENE_SECONDS: float = float(os.getenv("SCENE_SECONDS", "2.2"))
    # Max words on screen at once — keeps captions readable, never the full script.
    CAPTION_MAX_WORDS: int = _int("CAPTION_MAX_WORDS", 3)
    CAPTION_FONT_SIZE: int = _int("CAPTION_FONT_SIZE", 112)
    HOOK_FONT_SIZE: int = _int("HOOK_FONT_SIZE", 152)
    # Highlight colour for the word being spoken (R,G,B). Bright yellow-green pops.
    CAPTION_HIGHLIGHT: str = os.getenv("CAPTION_HIGHLIGHT", "255,60,0")
    HOOK_SECONDS: float = float(os.getenv("HOOK_SECONDS", "2.6"))
    CTA_SECONDS: float = float(os.getenv("CTA_SECONDS", "3.0"))
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

    # ══════════════════════════════════════════════════════════════════════
    #  KIDS CARTOON MODE  (the production goal — ONE quality Short per day)
    #  All settings below are additive; the legacy "trends" path is untouched.
    # ══════════════════════════════════════════════════════════════════════
    # "kids"   → original kids-cartoon stories (default, the real channel).
    # "trends" → legacy faceless viral-facts pipeline (kept, never deleted).
    CONTENT_MODE: str = os.getenv("CONTENT_MODE", "kids")

    # Story categories the Planner agent may choose from.
    KIDS_CATEGORIES: tuple[str, ...] = (
        "funny", "school", "animals", "magic", "friendship", "jungle", "bedtime",
    )
    # Kids content is Hindi-first per the channel spec ("hi" | "hinglish" | "en").
    KIDS_LANGUAGE: str = os.getenv("KIDS_LANGUAGE", "hi")
    # Target spoken length (seconds) for ONE daily Short, and visual shot count.
    KIDS_TARGET_SECONDS: int = _int("KIDS_TARGET_SECONDS", 45)
    KIDS_SCENE_COUNT: int = _int("KIDS_SCENE_COUNT", 6)
    # How many characters a single story may feature (keeps it readable for kids).
    KIDS_MAX_CHARACTERS: int = _int("KIDS_MAX_CHARACTERS", 3)

    # ── Local image backend (fully wired in Phase 2; safe defaults now) ────
    # auto → try local (comfyui/a1111) then fall back to gemini then gradient.
    IMAGE_BACKEND: str = os.getenv("IMAGE_BACKEND", "auto")
    COMFYUI_URL: str = os.getenv("COMFYUI_URL", "http://127.0.0.1:8188")
    A1111_URL: str = os.getenv("A1111_URL", "http://127.0.0.1:7860")
    SD_MODEL: str = os.getenv("SD_MODEL", "")          # checkpoint name; blank = backend default
    SD_STEPS: int = _int("SD_STEPS", 24)
    SD_CFG: float = float(os.getenv("SD_CFG", "6.5"))
    IMAGE_TIMEOUT: int = _int("IMAGE_TIMEOUT", 600)     # CPU image gen can be slow

    # ── Per-character voice engine (Phase 2; edge-tts works today) ─────────
    # edge → free neural (default) | piper → local | coqui → local XTTS clone.
    TTS_ENGINE: str = os.getenv("TTS_ENGINE", "edge")
    PIPER_DIR: Path = _path("PIPER_DIR", "assets/piper")
    NARRATOR_VOICE: str = os.getenv("NARRATOR_VOICE", "hi-IN-MadhurNeural")

    # ── Lip-sync (Phase 3; optional, off by default — heavy on CPU) ────────
    LIPSYNC_ENABLED: bool = os.getenv("LIPSYNC_ENABLED", "false").lower() == "true"
    LIPSYNC_BACKEND: str = os.getenv("LIPSYNC_BACKEND", "wav2lip")

    # ── Background music / SFX library ─────────────────────────────────────
    MUSIC_VOLUME: float = float(os.getenv("MUSIC_VOLUME", "0.12"))

    # ── New-domain data + asset dirs ───────────────────────────────────────
    CHARACTERS_DIR: Path = _path("CHARACTERS_DIR", "assets/characters")
    MUSIC_DIR: Path = _path("MUSIC_DIR", "assets/music")
    SFX_DIR: Path = _path("SFX_DIR", "assets/sfx")
    # Per-environment runtime data lives UNDER DATA_DIR, so a custom DATA_DIR
    # (or a test override) keeps these together and out of the repo.
    RUNS_DIR: Path = DATA_DIR / "runs"
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    STORIES_DIR: Path = DATA_DIR / "stories"
    STORIES_DIR.mkdir(parents=True, exist_ok=True)
    ARCHIVE_DIR: Path = _path("ARCHIVE_DIR", "generated/archive")

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
