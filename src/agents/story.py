"""StoryAgent — writes the actual kids story.

Expands the planner's beats into a short, funny, Hindi narration with woven
character dialogue, plus the YouTube metadata (title, description, hashtags,
thumbnail words). Output is an ordered list of narration/dialogue *segments* so
the Scene agent can group them into shots without losing who-says-what.
"""

from __future__ import annotations

from src.agents.base import Agent, AgentContext
from src.content_policy import check_text
from src.story_schema import DialogueLine, Scene

_LANG_LABEL = {
    "hi": "natural, simple conversational Hindi (Devanagari script)",
    "hinglish": "playful Hinglish (Hindi in Devanagari + common English words)",
    "en": "simple, playful Indian English a small child understands",
}

STORY_PROMPT = """\
[AGENT:STORY]
Write ONE complete, funny, wholesome cartoon story for Indian kids (ages 3-10).
Language: {lang}.
It must be ORIGINAL (no existing show/film characters), gentle and safe: no
violence, no fear, no facts/lessons-as-lecture. Make children giggle, end warm.

TITLE: {title}
PREMISE: {logline}
GENTLE TAKEAWAY: {moral}
BEATS (follow in order, keep it flowing):
{beats}

CHARACTERS (use ONLY these, true to personality):
{characters}

Total spoken length must be about {words} words (a ~{seconds}s Short). Short
punchy sentences. Mostly narration, with a few short fun dialogue lines.

Return ONLY this JSON:
{{
  "hook": "inviting opening title-card line in {lang}, max 6 words, no hashtags",
  "segments": [
    {{"type": "narration", "text": "..."}},
    {{"type": "dialogue", "character": "<exact name>", "text": "...", "emotion": "excited|happy|curious|surprised|giggly|sad"}}
  ],
  "cta": "warm closing line in {lang} inviting kids to follow for a daily story",
  "youtube_title": "fun {lang} title under 70 chars",
  "youtube_description": "2 short cheerful {lang} lines; last line invites a follow",
  "hashtags": ["#shorts", "#kids", "#cartoon", "#hindi", "#kahani", "...3 more"],
  "thumbnail_text": "2-3 BIG {lang} words for the thumbnail"
}}
"""


class StoryAgent(Agent):
    name = "story"

    def _characters_text(self, ctx: AgentContext) -> str:
        names = set(ctx.package.characters)
        lines = []
        for c in ctx.roster:
            if c["name"] in names:
                lines.append(f"- {c['name']} ({c.get('species','')}): "
                             f"{c.get('personality','')}")
        return "\n".join(lines) or "- a friendly original character"

    def run(self, ctx: AgentContext) -> AgentContext:
        pkg = ctx.package
        words = max(60, int(ctx.target_seconds * 2.4))   # ~Hindi speaking rate
        beats = "\n".join(f"  {i+1}. {b}" for i, b in enumerate(ctx.notes.get("beats", [])))

        data = self.ask(
            ctx,
            STORY_PROMPT.format(
                lang=_LANG_LABEL.get(ctx.language, _LANG_LABEL["hi"]),
                title=pkg.title or "(untitled)",
                logline=pkg.logline,
                moral=pkg.moral,
                beats=beats or "  1. a funny little adventure",
                characters=self._characters_text(ctx),
                words=words, seconds=ctx.target_seconds,
            ),
            temperature=0.95,
        )

        pkg.hook = str(data.get("hook") or pkg.title or "").strip()
        pkg.cta = str(data.get("cta") or "रोज़ नई कहानी के लिए फॉलो करो!").strip()
        pkg.youtube_title = str(data.get("youtube_title") or pkg.title).strip()
        pkg.youtube_description = str(data.get("youtube_description") or "").strip()
        pkg.thumbnail_text = str(data.get("thumbnail_text") or pkg.title[:18]).strip()

        tags = data.get("hashtags") or ["#shorts", "#kids", "#cartoon", "#hindi", "#kahani"]
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split(",")]
        pkg.hashtags = [t if str(t).startswith("#") else f"#{t}" for t in tags if str(t).strip()]

        # Normalise segments and keep them for the Dialogue/Scene agents.
        segments: list[dict] = []
        for seg in (data.get("segments") or []):
            stype = str(seg.get("type", "narration")).lower()
            text = str(seg.get("text", "")).strip()
            if not text:
                continue
            if stype == "dialogue":
                nm = str(seg.get("character", "")).strip()
                hit = ctx.roster_by_name(nm)
                segments.append({
                    "type": "dialogue",
                    "character": hit["name"] if hit else (pkg.characters[0] if pkg.characters else nm),
                    "text": text,
                    "emotion": str(seg.get("emotion", "happy")).strip() or "happy",
                })
            else:
                segments.append({"type": "narration", "text": text})

        if not segments:
            raise ValueError("StoryAgent produced no usable segments")
        ctx.notes["segments"] = segments

        # Preliminary single-scene fallback so a script exists even if the
        # Scene agent later fails — keeps the pipeline resumable/degradable.
        pkg.scenes = [Scene(
            index=0,
            description=pkg.logline or pkg.title,
            characters=list(pkg.characters),
            narration=" ".join(s["text"] for s in segments if s["type"] == "narration"),
            dialogue=[DialogueLine(s["character"], s["text"], s.get("emotion", "happy"))
                      for s in segments if s["type"] == "dialogue"],
        )]
        pkg.rebuild_script()

        check_story_blob(pkg, segments)
        self.log.info("Story written | %d segments | ~%d words | hook=%r",
                      len(segments), len(pkg.script.split()), pkg.hook)
        return ctx


def check_story_blob(pkg, segments) -> None:
    blob = " ".join([
        pkg.hook, pkg.cta, pkg.youtube_title, pkg.youtube_description,
        pkg.thumbnail_text, *[s["text"] for s in segments],
    ])
    check_text(blob, where="story")
