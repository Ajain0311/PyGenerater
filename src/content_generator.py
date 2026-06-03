"""Generate retention-optimised Shorts content using Gemini (token-minimal)."""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from google import genai
from google.genai import types

from src.analytics import calculate_gemini_cost
from src.config import config
from src.key_manager import get_active_key, rotate_key, key_status
from src.utils import get_logger, sanitise_filename

log = get_logger(__name__)


@dataclass
class VideoContent:
    topic: str
    short_title: str
    script: str
    youtube_title: str
    youtube_description: str
    hashtags: list[str]
    thumbnail_text: str
    image_prompts: list[str]
    hook: str = ""
    cta: str = "Follow for more"
    language: str = "en"
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0


_LANG_RULES = {
    "en": "Write hook, script and cta in punchy, natural Indian English.",
    "hi": "Write hook, script and cta in natural conversational Hindi using Devanagari script.",
    "hinglish": ("Write in Hinglish — the natural Hindi-English mix urban Indians actually speak. "
                 "Keep Hindi words in Devanagari and English words in Latin script."),
}

# Compact prompt: forces a hook + curiosity gap + CTA, asks for SHORT image
# search phrases (not verbose paragraphs), and is tuned to emit <2k output
# tokens. response_mime_type=json removes markdown scaffolding waste + retries.
CONTENT_PROMPT = """\
You script a VIRAL faceless YouTube Shorts channel for Indian audience.
TOPIC: {topic}
{lang_rule}

MANDATORY VIRAL FORMULA — pick the best fit for the topic:
- ANIMAL SHOCK: "This [animal] LITERALLY [did human thing] — nobody knew why"
- COUNTDOWN: "[N] things about [topic] India never told you"
- MYSTERY REVEAL: "The real reason [topic] [surprising behaviour] — scientists are baffled"
- INDIA PRIDE: "India just [achievement] and BROKE the world"
- DID YOU KNOW: "For [X] years nobody knew this about [topic] — until NOW"

Return ONLY this JSON object:
{{
  "hook": "5-9 ALL-CAPS words. Emotional trigger: shock/pride/mystery/awe. Zero hashtags. Examples: 'THIS LION LITERALLY SAID HELLO', 'INDIA JUST BROKE THE WORLD', 'NOBODY KNEW THIS FOR 100 YEARS'",
  "script": "Voiceover 75-95 words. Rules: (1) First sentence = hook said naturally. (2) Sentences 2-3 deepen mystery — use 'but what happened next...' or 'scientists found something shocking'. (3) Drop the REVEAL exactly in the middle. (4) Final 2 sentences = callback to hook + loop payoff so the viewer watches again. MAX 7 words per sentence. Mix 3-word impact sentences with longer ones.",
  "cta": "3-4 words. E.g. 'Follow for more', 'Share this now'",
  "youtube_title": "Under 70 chars with an emotional keyword and the topic",
  "youtube_description": "2 short punchy paragraphs. Last line: 'Follow for daily trending updates!'",
  "hashtags": ["#shorts", "#viral", "#india", "#trending", "...4 more topic-specific tags"],
  "thumbnail_text": "2-4 ALL CAPS shock words",
  "image_queries": ["dramatic close-up search phrase for scene 1", "action scene 2", "wide scene 3", "reveal/payoff scene 4"]
}}
"""


class ContentGenerator:
    def __init__(self):
        status = key_status()
        log.info(
            "ContentGenerator ready | model=%s | keys=%d | active=key %d | max_out=%d",
            config.GEMINI_MODEL, status["total_keys"], status["active_key_index"],
            config.GEMINI_MAX_OUTPUT_TOKENS,
        )
        self._cache_dir = config.DATA_DIR / "content_cache"
        self._cache_dir.mkdir(parents=True, exist_ok=True)

    def _make_client(self) -> genai.Client:
        return genai.Client(api_key=get_active_key())

    # ── Reuse cache: a previously generated topic costs ZERO Gemini tokens ───
    def _cache_path(self, topic: str, language: str) -> Path:
        return self._cache_dir / f"{sanitise_filename(topic)}__{language}.json"

    def _load_cache(self, topic: str, language: str) -> VideoContent | None:
        if not config.CONTENT_CACHE:
            return None
        p = self._cache_path(topic, language)
        if not p.exists():
            return None
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            log.info("Content cache HIT for %r (0 Gemini tokens)", topic)
            return self._build(topic, data, language, 0, 0, 0.0)
        except Exception as e:
            log.debug("cache read failed: %s", e)
            return None

    def _save_cache(self, topic: str, language: str, data: dict) -> None:
        try:
            self._cache_path(topic, language).write_text(
                json.dumps(data, ensure_ascii=False), encoding="utf-8")
        except Exception as e:
            log.debug("cache write failed: %s", e)

    def generate(self, topic: str, language: str | None = None, _attempt: int = 0) -> VideoContent:
        language = (language or config.TTS_LANGUAGE or "en").lower()
        if language not in _LANG_RULES:
            language = "en"

        cached = self._load_cache(topic, language)
        if cached is not None:
            return cached

        if _attempt >= 20:
            raise RuntimeError("All Gemini API keys exhausted.")

        log.info("Generating content for %r (lang=%s, key %d)",
                 topic, language, key_status()["active_key_index"])

        try:
            client = self._make_client()
            response = client.models.generate_content(
                model=config.GEMINI_MODEL,
                contents=CONTENT_PROMPT.format(topic=topic, lang_rule=_LANG_RULES[language]),
                config=types.GenerateContentConfig(
                    temperature=0.9,
                    top_p=0.95,
                    max_output_tokens=config.GEMINI_MAX_OUTPUT_TOKENS,
                    response_mime_type="application/json",
                ),
            )
            raw_text = (response.text or "").strip()

            usage = getattr(response, "usage_metadata", None)
            input_tokens = getattr(usage, "prompt_token_count", 0) or 0
            output_tokens = getattr(usage, "candidates_token_count", 0) or 0
            cost = calculate_gemini_cost(input_tokens, output_tokens)

            data = self._parse_json(raw_text)
            self._save_cache(topic, language, data)

            content = self._build(topic, data, language, input_tokens, output_tokens, cost)
            log.info("Content generated | hook=%r | tokens in=%d out=%d | cost=$%.4f",
                     content.hook, input_tokens, output_tokens, cost)
            return content

        except Exception as exc:
            err = str(exc)
            if any(code in err for code in ("429", "RESOURCE_EXHAUSTED", "quota")):
                rotate_key(reason="429 quota")
                log.info("Retrying with next key (attempt %d)…", _attempt + 2)
                time.sleep(2)
                return self.generate(topic, language=language, _attempt=_attempt + 1)
            if "Invalid JSON" in err and _attempt < 3:
                log.warning("JSON truncated, retrying same key…")
                time.sleep(3)
                return self.generate(topic, language=language, _attempt=_attempt + 1)
            raise

    def _build(self, topic, data, language, in_tok, out_tok, cost) -> VideoContent:
        hashtags = data.get("hashtags", [])
        if isinstance(hashtags, str):
            hashtags = [h.strip() for h in hashtags.split(",")]
        image_q = data.get("image_queries") or data.get("image_prompts") or [topic]
        if isinstance(image_q, str):
            image_q = [image_q]
        return VideoContent(
            topic=topic,
            short_title=data.get("short_title") or data.get("hook") or topic[:50],
            script=data.get("script", ""),
            youtube_title=data.get("youtube_title", topic),
            youtube_description=data.get("youtube_description", ""),
            hashtags=[h if str(h).startswith("#") else f"#{h}" for h in hashtags],
            thumbnail_text=data.get("thumbnail_text", topic[:24].upper()),
            image_prompts=image_q,
            hook=data.get("hook", ""),
            cta=data.get("cta", "Follow for more"),
            language=language,
            input_tokens=in_tok,
            output_tokens=out_tok,
            cost_usd=cost,
        )

    def _parse_json(self, text: str) -> dict[str, Any]:
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.MULTILINE)
        text = re.sub(r"```\s*$", "", text, flags=re.MULTILINE).strip()
        brace_count = 0
        start = text.find("{")
        if start == -1:
            raise ValueError("No JSON object found in response")
        for i, ch in enumerate(text[start:], start):
            if ch == "{":
                brace_count += 1
            elif ch == "}":
                brace_count -= 1
                if brace_count == 0:
                    try:
                        return json.loads(text[start: i + 1])
                    except json.JSONDecodeError:
                        break
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON from Gemini: {e}")
