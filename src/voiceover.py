"""
Voiceover generation.

Primary engine: Microsoft **edge-tts** — free, no API key, neural voices that
sound human, with native Hindi / Indian-English voices AND, crucially,
`WordBoundary` events that give us EXACT per-word timestamps. Those timestamps
drive perfectly-synced kinetic captions downstream (no more guessing from a
words-per-minute estimate).

Fallback: gTTS (kept from the original pipeline) with duration-proportional
word timing, used only if edge-tts is unreachable.

Returns (audio_path, word_segments) where word_segments is a list of
{"text", "start", "end"} — one entry PER WORD, in seconds.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

from src.config import config
from src.utils import get_logger

log = get_logger(__name__)

# Neural voices confirmed available in edge-tts (verified via list_voices()).
# Hindi/Hinglish use a Hindi voice because it pronounces Devanagari correctly
# and still handles interleaved Latin/English words naturally.
_VOICE_MAP = {
    "en": "en-IN-NeerjaNeural",
    "english": "en-IN-NeerjaNeural",
    "en-m": "en-IN-PrabhatNeural",
    "hi": "hi-IN-SwaraNeural",
    "hindi": "hi-IN-SwaraNeural",
    "hi-m": "hi-IN-MadhurNeural",
    "hinglish": "hi-IN-SwaraNeural",
}
_DEFAULT_VOICE = "en-IN-NeerjaNeural"

# edge-tts offsets/durations are in 100-nanosecond ticks.
_TICKS_PER_SECOND = 10_000_000


def _pick_voice(language: str | None, voice: str | None) -> str:
    if voice:
        return voice
    if config.TTS_VOICE:
        return config.TTS_VOICE
    return _VOICE_MAP.get((language or config.TTS_LANGUAGE or "en").lower(), _DEFAULT_VOICE)


class VoiceoverGenerator:
    def generate(
        self,
        script: str,
        slug: str,
        language: str | None = None,
        voice: str | None = None,
    ) -> tuple[Path, list[dict]]:
        """Generate MP3 voiceover and return (audio_path, word_segments)."""
        out_path = config.AUDIO_DIR / f"{slug}.mp3"
        chosen_voice = _pick_voice(language, voice)

        # Reuse a previously generated clip + its cached timing when present.
        if out_path.exists() and out_path.stat().st_size > 0:
            cached = self._load_timing(slug)
            if cached:
                log.info("Audio + timing reused: %s (%d words)", out_path.name, len(cached))
                return out_path, cached

        word_segments: list[dict] = []
        try:
            word_segments = self._edge_tts(script, out_path, chosen_voice)
            log.info("edge-tts ok | voice=%s | %d word timestamps", chosen_voice, len(word_segments))
        except Exception as exc:
            log.warning("edge-tts failed (%s) — falling back to gTTS.", exc)
            word_segments = self._gtts_fallback(script, out_path, language)

        if not word_segments:
            # edge-tts produced audio but no boundaries — estimate from duration.
            word_segments = self._estimate_word_timing(script, self._audio_duration(out_path))

        self._save_timing(slug, word_segments)
        return out_path, word_segments

    # ── edge-tts (primary) ──────────────────────────────────────────────────
    def _edge_tts(self, script: str, out_path: Path, voice: str) -> list[dict]:
        import edge_tts

        async def _run() -> list[dict]:
            communicate = edge_tts.Communicate(
                script,
                voice,
                rate=config.TTS_RATE,
                pitch=config.TTS_PITCH,
                boundary="WordBoundary",   # default is SentenceBoundary — we need words
            )
            words: list[dict] = []
            with open(out_path, "wb") as fh:
                async for chunk in communicate.stream():
                    if chunk["type"] == "audio":
                        fh.write(chunk["data"])
                    elif chunk["type"] == "WordBoundary":
                        start = chunk["offset"] / _TICKS_PER_SECOND
                        end = (chunk["offset"] + chunk["duration"]) / _TICKS_PER_SECOND
                        text = (chunk.get("text") or "").strip()
                        if text:
                            words.append({"text": text, "start": round(start, 3), "end": round(end, 3)})
            return words

        return _run_async(_run())

    # ── gTTS (fallback) ───────────────────────────────────────────────────--
    def _gtts_fallback(self, script: str, out_path: Path, language: str | None) -> list[dict]:
        from gtts import gTTS

        lang_code = "hi" if (language or config.TTS_LANGUAGE).lower().startswith("hi") else "en"
        tts = gTTS(text=script, lang=lang_code, tld="co.in", slow=False)
        tts.save(str(out_path))
        return self._estimate_word_timing(script, self._audio_duration(out_path))

    # ── Timing helpers ──────────────────────────────────────────────────────
    @staticmethod
    def _estimate_word_timing(script: str, total_duration: float) -> list[dict]:
        """Distribute total duration across words proportional to word length."""
        words = [w for w in re.findall(r"\S+", script) if w]
        if not words:
            return []
        weights = [max(len(re.sub(r"[^\w]", "", w)), 1) for w in words]
        total_w = sum(weights)
        segments: list[dict] = []
        cursor = 0.0
        for word, weight in zip(words, weights):
            dur = total_duration * weight / total_w
            segments.append({
                "text": word.strip(),
                "start": round(cursor, 3),
                "end": round(cursor + dur, 3),
            })
            cursor += dur
        return segments

    @staticmethod
    def _audio_duration(path: Path) -> float:
        try:
            from moviepy import AudioFileClip
            clip = AudioFileClip(str(path))
            d = float(clip.duration)
            clip.close()
            return d
        except Exception:
            return 40.0  # safe default for a Short

    # ── Timing cache (so asset reuse keeps perfect sync without re-calling TTS) ─
    def _timing_path(self, slug: str) -> Path:
        return config.AUDIO_DIR / f"{slug}.timing.json"

    def _save_timing(self, slug: str, segments: list[dict]) -> None:
        import json
        try:
            self._timing_path(slug).write_text(json.dumps(segments), encoding="utf-8")
        except Exception as e:
            log.debug("Could not cache timing: %s", e)

    def _load_timing(self, slug: str) -> list[dict]:
        import json
        p = self._timing_path(slug)
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                return []
        return []


def _run_async(coro):
    """Run an async coroutine whether or not a loop is already running."""
    try:
        return asyncio.run(coro)
    except RuntimeError:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()
