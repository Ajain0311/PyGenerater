"""
Content guardrails for the kids channel.

Two jobs:
  1. ORIGINALITY — never reproduce copyrighted/trademarked characters or shows.
  2. KID-SAFETY — keep stories free of violence, fear, and adult themes, and
     keep the channel ON-topic (funny stories, NOT facts/documentaries).

These are defensive checks the pipeline runs on generated text; on a violation
the agent re-rolls (the Orchestrator catches `PolicyViolation`).
"""

from __future__ import annotations

import re

# Trademarked characters / franchises that must NEVER appear (the user's list
# plus the usual suspects a model might drift toward for "Indian kids cartoon").
BANNED_FRANCHISES: tuple[str, ...] = (
    "motu", "patlu", "doraemon", "nobita", "shinchan", "shin chan",
    "ninja hattori", "chhota bheem", "chota bheem", "bheem", "kalia",
    "mickey", "minnie", "donald duck", "disney", "pixar", "frozen", "elsa",
    "anna", "moana", "encanto", "pokemon", "pikachu", "doremon",
    "tom and jerry", "tom & jerry", "peppa pig", "peppa", "cocomelon",
    "spiderman", "spider-man", "batman", "superman", "marvel", "avengers",
    "minions", "despicable me", "paw patrol", "barbie", "hello kitty",
    "naruto", "dragon ball", "ben 10", "ben10", "oggy", "kungfu panda",
    "kung fu panda", "winnie the pooh", "scooby", "smurf", "mr bean",
    "gattu", "vir robot", "roll no 21", "krishna", "hanuman", "ganesha",
)

# Themes that are off-channel or unsafe for ages 3–10.
BANNED_THEMES: tuple[str, ...] = (
    "documentary", "educational fact", "why lions hunt", "why tigers attack",
    "kill", "killed", "blood", "gun", "knife", "weapon", "death", "dead",
    "horror", "scary monster", "ghost attack", "violence", "fight to the death",
    "war", "drugs", "alcohol", "romance", "kissing", "dating",
)

# Reusable negative prompt for image generation (kid-safe).
KID_SAFE_NEGATIVE = (
    "scary, horror, violence, blood, gore, weapon, gun, knife, dark, creepy, "
    "realistic human photo, nsfw, adult, suggestive, ugly, deformed, distorted, "
    "extra limbs, text, watermark, logo, copyrighted character"
)

# Positive style scaffold every cartoon image shares (keeps a consistent look).
CARTOON_STYLE = (
    "cute 2D cartoon illustration for young children, soft rounded shapes, "
    "bright cheerful colors, clean thick outlines, simple friendly background, "
    "warm soft lighting, storybook style, high quality"
)


class PolicyViolation(Exception):
    """Raised when generated content breaks an originality / safety rule."""


def _hits(text: str, terms: tuple[str, ...]) -> list[str]:
    low = (text or "").lower()
    found = []
    for t in terms:
        # word-ish boundary so "bheem" matches but "bheemashankar" still flags
        # (we'd rather over-flag a banned franchise than under-flag it).
        if re.search(rf"(?<![a-z]){re.escape(t)}", low):
            found.append(t)
    return sorted(set(found))


def find_franchise_violations(text: str) -> list[str]:
    return _hits(text, BANNED_FRANCHISES)


def find_unsafe_themes(text: str) -> list[str]:
    return _hits(text, BANNED_THEMES)


def check_text(text: str, *, where: str = "content") -> None:
    """Raise PolicyViolation if `text` contains banned franchises or themes."""
    franchises = find_franchise_violations(text)
    if franchises:
        raise PolicyViolation(
            f"{where}: copyrighted/trademarked reference(s): {', '.join(franchises)}"
        )
    themes = find_unsafe_themes(text)
    if themes:
        raise PolicyViolation(
            f"{where}: unsafe/off-channel theme(s): {', '.join(themes)}"
        )


def check_story_package(pkg) -> None:
    """Validate the whole assembled story (title, script, scenes, dialogue)."""
    blob_parts = [
        pkg.title, pkg.logline, pkg.moral, pkg.hook, pkg.cta,
        pkg.youtube_title, pkg.youtube_description, pkg.thumbnail_text,
        pkg.narration_script,
    ]
    for sc in pkg.scenes:
        blob_parts.append(sc.description)
        blob_parts.append(sc.narration)
        blob_parts.extend(dl.text for dl in sc.dialogue)
    check_text("\n".join(p for p in blob_parts if p), where="story")
