"""
Typed data contract for a kids-cartoon story.

`StoryPackage` is the single source of truth that flows through the agent
pipeline and is persisted (as JSON) on the Story row. It is deliberately
renderer-compatible: `narration_script`, `image_prompts`, `hook`, `cta`,
`youtube_title/description/hashtags/thumbnail_text` map straight onto the
existing VideoGenerator / VoiceoverGenerator / ThumbnailGenerator inputs, so the
domain pivot reuses the whole render path instead of rewriting it.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class DialogueLine:
    character: str               # character NAME (matches Character.name)
    text: str                    # spoken line (in the story language)
    emotion: str = "neutral"     # happy | excited | sad | curious | surprised …

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "DialogueLine":
        return cls(
            character=str(d.get("character", "")).strip(),
            text=str(d.get("text", "")).strip(),
            emotion=str(d.get("emotion", "neutral")).strip() or "neutral",
        )


@dataclass
class Scene:
    index: int
    description: str                       # visual description (English → image gen)
    characters: list[str] = field(default_factory=list)   # names present in shot
    narration: str = ""                    # narrator voiceover text for this beat
    dialogue: list[DialogueLine] = field(default_factory=list)
    image_prompt: str = ""                 # filled by PromptAgent (consistency tokens)
    negative_prompt: str = ""              # kid-safe + per-character negatives
    seed: int = 0                          # fixed seed → stable character look
    seconds: float = 0.0                   # estimated on-screen duration

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["dialogue"] = [dl.to_dict() for dl in self.dialogue]
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Scene":
        return cls(
            index=int(d.get("index", 0)),
            description=str(d.get("description", "")).strip(),
            characters=[str(c).strip() for c in (d.get("characters") or []) if str(c).strip()],
            narration=str(d.get("narration", "")).strip(),
            dialogue=[DialogueLine.from_dict(x) for x in (d.get("dialogue") or [])],
            image_prompt=str(d.get("image_prompt", "")).strip(),
            negative_prompt=str(d.get("negative_prompt", "")).strip(),
            seed=int(d.get("seed", 0) or 0),
            seconds=float(d.get("seconds", 0.0) or 0.0),
        )

    @property
    def spoken_text(self) -> str:
        """Narration + dialogue in reading order — what the voiceover says."""
        parts: list[str] = []
        if self.narration:
            parts.append(self.narration)
        for dl in self.dialogue:
            if dl.text:
                parts.append(dl.text)
        return " ".join(parts).strip()


@dataclass
class StoryPackage:
    topic: str = ""
    category: str = ""
    language: str = "hi"
    title: str = ""                # internal short title
    logline: str = ""              # one-line premise
    moral: str = ""                # gentle takeaway / punchline
    hook: str = ""                 # opening title-card text (kid-friendly, NOT shock)
    cta: str = ""                  # closing call to action
    characters: list[str] = field(default_factory=list)   # names featured
    scenes: list[Scene] = field(default_factory=list)
    narration_script: str = ""     # full TTS script (built from scenes)
    youtube_title: str = ""
    youtube_description: str = ""
    hashtags: list[str] = field(default_factory=list)
    thumbnail_text: str = ""

    # ── renderer-facing derived views ──────────────────────────────────────
    @property
    def image_prompts(self) -> list[str]:
        return [(sc.image_prompt or sc.description) for sc in self.scenes]

    @property
    def script(self) -> str:
        """The voiceover script: prefer an explicit narration_script, else
        stitch every scene's spoken text together."""
        if self.narration_script.strip():
            return self.narration_script.strip()
        return " ".join(sc.spoken_text for sc in self.scenes if sc.spoken_text).strip()

    def rebuild_script(self) -> str:
        self.narration_script = " ".join(
            sc.spoken_text for sc in self.scenes if sc.spoken_text
        ).strip()
        return self.narration_script

    # ── serialisation ───────────────────────────────────────────────────────
    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["scenes"] = [sc.to_dict() for sc in self.scenes]
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "StoryPackage":
        pkg = cls(
            topic=str(d.get("topic", "")),
            category=str(d.get("category", "")),
            language=str(d.get("language", "hi")),
            title=str(d.get("title", "")),
            logline=str(d.get("logline", "")),
            moral=str(d.get("moral", "")),
            hook=str(d.get("hook", "")),
            cta=str(d.get("cta", "")),
            characters=[str(c) for c in (d.get("characters") or [])],
            scenes=[Scene.from_dict(s) for s in (d.get("scenes") or [])],
            narration_script=str(d.get("narration_script", "")),
            youtube_title=str(d.get("youtube_title", "")),
            youtube_description=str(d.get("youtube_description", "")),
            hashtags=[str(h) for h in (d.get("hashtags") or [])],
            thumbnail_text=str(d.get("thumbnail_text", "")),
        )
        return pkg

    @classmethod
    def from_json(cls, text: str) -> "StoryPackage":
        return cls.from_dict(json.loads(text))
