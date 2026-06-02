"""Generate video content using Gemini with automatic key rotation."""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Any

from google import genai
from google.genai import types

from src.analytics import calculate_gemini_cost
from src.config import config
from src.key_manager import get_active_key, rotate_key, key_status
from src.utils import get_logger

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
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0


CONTENT_PROMPT = """\
You are a viral YouTube Shorts script writer. Generate content for a trending topic.

TOPIC: {topic}

Return ONLY valid JSON (no markdown, no extra text) with EXACTLY this structure:

{{
  "short_title": "Catchy 5-word title",
  "youtube_title": "Full YouTube title under 70 chars with keywords",
  "youtube_description": "2-3 paragraph description with hook, details, CTA (150-300 words)",
  "hashtags": ["#trending", "#shorts", "#india", "#viral", "#topic"],
  "thumbnail_text": "Bold 3-4 word text for thumbnail",
  "script": "Complete 60-second spoken script (120-150 words). Conversational tone. Start with a hook. End with CTA. No markdown, plain text only.",
  "image_prompts": [
    "Detailed visual description for scene 1, photorealistic, vertical 9:16 format",
    "Detailed visual description for scene 2, photorealistic, vertical 9:16 format",
    "Detailed visual description for scene 3, photorealistic, vertical 9:16 format",
    "Detailed visual description for scene 4, photorealistic, vertical 9:16 format"
  ]
}}

Rules:
- Output ONLY the JSON object, nothing else before or after
- Script: 120-150 words, speakable in 55-60 seconds
- Hashtags: 8-10 tags mixing trending + niche + topic
- Image prompts: vivid, specific, safe for all ages
- youtube_title must contain the topic keyword and be under 70 chars
- Description ends with: "Follow for daily trending updates!"
- Focus on Indian audience perspective
"""


class ContentGenerator:
    def __init__(self):
        status = key_status()
        log.info(
            "ContentGenerator ready | model=%s | keys=%d | active=key %d",
            config.GEMINI_MODEL, status["total_keys"], status["active_key_index"],
        )

    def _make_client(self) -> genai.Client:
        return genai.Client(api_key=get_active_key())

    def generate(self, topic: str, _attempt: int = 0) -> VideoContent:
        """Generate content with automatic key rotation on quota errors."""
        if _attempt >= 20:
            raise RuntimeError("All Gemini API keys exhausted.")

        log.info("Generating content for: %r (key %d)", topic, key_status()["active_key_index"])

        try:
            client = self._make_client()
            response = client.models.generate_content(
                model=config.GEMINI_MODEL,
                contents=CONTENT_PROMPT.format(topic=topic),
                config=types.GenerateContentConfig(
                    temperature=0.8,
                    top_p=0.95,
                    max_output_tokens=8192,
                ),
            )
            raw_text = response.text.strip()

            usage = getattr(response, "usage_metadata", None)
            input_tokens = getattr(usage, "prompt_token_count", 0) or 0
            output_tokens = getattr(usage, "candidates_token_count", 0) or 0
            cost = calculate_gemini_cost(input_tokens, output_tokens)

            data = self._parse_json(raw_text)
            hashtags = data.get("hashtags", [])
            if isinstance(hashtags, str):
                hashtags = [h.strip() for h in hashtags.split(",")]

            content = VideoContent(
                topic=topic,
                short_title=data.get("short_title", topic[:50]),
                script=data.get("script", ""),
                youtube_title=data.get("youtube_title", topic),
                youtube_description=data.get("youtube_description", ""),
                hashtags=[h if h.startswith("#") else f"#{h}" for h in hashtags],
                thumbnail_text=data.get("thumbnail_text", topic[:20].upper()),
                image_prompts=data.get("image_prompts", [f"Professional photo about {topic}"]),
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_usd=cost,
            )
            log.info(
                "Content generated | tokens in=%d out=%d | cost=$%.4f",
                input_tokens, output_tokens, cost,
            )
            return content

        except Exception as exc:
            err = str(exc)

            # Quota / rate-limit → rotate key and retry immediately
            if any(code in err for code in ("429", "RESOURCE_EXHAUSTED", "quota")):
                new_key_preview = rotate_key(reason="429 quota")
                log.info("Retrying with next key (attempt %d)…", _attempt + 2)
                time.sleep(2)
                return self.generate(topic, _attempt=_attempt + 1)

            # JSON truncation → retry same key (once)
            if "Invalid JSON" in err and _attempt < 3:
                log.warning("JSON truncated, retrying same key…")
                time.sleep(3)
                return self.generate(topic, _attempt=_attempt + 1)

            raise

    def _parse_json(self, text: str) -> dict[str, Any]:
        # Strip markdown fencing
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.MULTILINE)
        text = re.sub(r"```\s*$", "", text, flags=re.MULTILINE)
        text = text.strip()

        # Find first complete JSON object
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
                        return json.loads(text[start : i + 1])
                    except json.JSONDecodeError:
                        break

        # Last-resort: try full text
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON from Gemini: {e}")
