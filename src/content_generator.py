"""Generate video content (script, title, description, hashtags) using Gemini."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from google import genai
from google.genai import types

from src.analytics import calculate_gemini_cost
from src.config import config
from src.utils import api_retry, get_logger

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

Create a complete YouTube Short video package. Return ONLY valid JSON with this exact structure:

{{
  "short_title": "Catchy 5-word title",
  "youtube_title": "Full YouTube title under 70 chars with keywords",
  "youtube_description": "2-3 paragraph description with hook, details, CTA (150-300 words)",
  "hashtags": ["#trending", "#shorts", "#india", "#viral", "#topic"],
  "thumbnail_text": "Bold 3-4 word text for thumbnail",
  "script": "Complete 60-second spoken script (120-150 words). Conversational tone. Start with a hook. Include facts, emotion, CTA at end. NO markdown, just plain text.",
  "image_prompts": [
    "Detailed visual description for scene 1 (photorealistic, 9:16 vertical)",
    "Detailed visual description for scene 2 (photorealistic, 9:16 vertical)",
    "Detailed visual description for scene 3 (photorealistic, 9:16 vertical)",
    "Detailed visual description for scene 4 (photorealistic, 9:16 vertical)"
  ]
}}

Rules:
- Script must be exactly speakable in 55-60 seconds
- Hashtags: 5-10 tags, mix of trending + niche
- Image prompts must be vivid, specific, safe for all audiences
- youtube_title must include the topic keyword
- Description must end with "Follow for daily trending updates!"
- Focus on Indian audience perspective
"""


class ContentGenerator:
    def __init__(self):
        if not config.GEMINI_API_KEY:
            raise ValueError("GEMINI_API_KEY is not set.")
        self._client = genai.Client(api_key=config.GEMINI_API_KEY)
        log.info("ContentGenerator initialised with model=%s", config.GEMINI_MODEL)

    @api_retry(max_attempts=3, wait_min=5, wait_max=60)
    def generate(self, topic: str) -> VideoContent:
        log.info("Generating content for topic: %r", topic)
        prompt = CONTENT_PROMPT.format(topic=topic)

        response = self._client.models.generate_content(
            model=config.GEMINI_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.8,
                top_p=0.95,
                max_output_tokens=8192,
            ),
        )
        raw_text = response.text.strip()

        # Extract token usage
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

    def _parse_json(self, text: str) -> dict[str, Any]:
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.MULTILINE)
        text = re.sub(r"```\s*$", "", text, flags=re.MULTILINE)
        text = text.strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            log.warning("JSON parse error, attempting extraction: %s", e)
            match = re.search(r"\{.*\}", text, re.DOTALL)
            if match:
                try:
                    return json.loads(match.group())
                except json.JSONDecodeError:
                    pass
            raise ValueError(f"Invalid JSON from Gemini: {e}")
