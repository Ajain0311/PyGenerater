"""Generate TTS voiceover from script text using gTTS."""

from __future__ import annotations

import math
from pathlib import Path

from gtts import gTTS

from src.config import config
from src.utils import api_retry, get_logger

log = get_logger(__name__)


def _estimate_duration(text: str, wpm: int = 145) -> float:
    """Rough estimate of spoken duration in seconds."""
    words = len(text.split())
    return (words / wpm) * 60


def _split_script_to_segments(script: str, n_scenes: int) -> list[str]:
    """
    Split the script into N roughly equal segments for subtitle timing.
    Returns list of text segments (one per scene).
    """
    # Split on sentence boundaries
    import re
    sentences = re.split(r"(?<=[.!?])\s+", script.strip())
    sentences = [s.strip() for s in sentences if s.strip()]

    if not sentences:
        return [script] * n_scenes

    # Distribute sentences into n_scenes buckets
    per_scene = max(1, math.ceil(len(sentences) / n_scenes))
    segments: list[str] = []
    for i in range(0, len(sentences), per_scene):
        chunk = " ".join(sentences[i : i + per_scene])
        segments.append(chunk)

    # Pad or trim to exactly n_scenes
    while len(segments) < n_scenes:
        segments.append(segments[-1] if segments else "")
    return segments[:n_scenes]


class VoiceoverGenerator:
    def generate(
        self,
        script: str,
        slug: str,
        lang: str = "en",
        tld: str = "co.in",
    ) -> tuple[Path, list[dict]]:
        """
        Generate MP3 voiceover and return (audio_path, subtitle_segments).

        subtitle_segments: list of {text, start_time, end_time}
        """
        out_path = config.AUDIO_DIR / f"{slug}.mp3"
        if out_path.exists():
            log.info("Audio already exists, reusing: %s", out_path.name)
        else:
            self._generate_mp3(script, out_path, lang, tld)

        # Estimate total duration
        total_duration = _estimate_duration(script)
        log.info("Audio duration estimate: %.1f seconds", total_duration)

        # Build subtitle segments based on script sentences
        import re
        sentences = re.split(r"(?<=[.!?])\s+", script.strip())
        sentences = [s.strip() for s in sentences if s.strip()]
        n = len(sentences)
        time_per_sentence = total_duration / max(n, 1)

        segments: list[dict] = []
        for i, sentence in enumerate(sentences):
            start = i * time_per_sentence
            end = start + time_per_sentence
            segments.append({"text": sentence, "start": round(start, 2), "end": round(end, 2)})

        return out_path, segments

    @api_retry(max_attempts=3, wait_min=3, wait_max=15)
    def _generate_mp3(self, script: str, out_path: Path, lang: str, tld: str) -> None:
        log.info("Generating TTS audio → %s", out_path.name)
        tts = gTTS(text=script, lang=lang, tld=tld, slow=False)
        tts.save(str(out_path))
        log.info("Audio saved: %s", out_path)
