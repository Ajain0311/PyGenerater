"""
Character management service — the backend the dashboard "Characters" page calls.

CRUD + JSON import/export + validation (originality/kid-safety). No Streamlit or
rendering here; the UI only calls these functions. Characters are the backbone
of visual + voice consistency, so this is where we guard against accidental use
of copyrighted characters.
"""

from __future__ import annotations

import json
from typing import Any

from slugify import slugify

from src.content_policy import check_text
from src.database import CharacterRepo, get_session
from src.utils import get_logger

log = get_logger("character_service")

# Editable fields (whitelist) + their defaults.
FIELDS: dict[str, Any] = {
    "name": "", "slug": "", "species": "", "personality": "", "description": "",
    "clothes": "", "appearance_prompt": "", "negative_prompt": "", "seed": 0,
    "reference_image": "", "voice_engine": "edge", "voice_id": "",
    "voice_rate": "+0%", "voice_pitch": "+0Hz", "is_active": True,
}


def _to_dict(c) -> dict[str, Any]:
    return {
        "id": c.id, "name": c.name, "slug": c.slug, "species": c.species,
        "personality": c.personality, "description": c.description,
        "clothes": c.clothes, "appearance_prompt": c.appearance_prompt,
        "negative_prompt": c.negative_prompt or "", "seed": c.seed or 0,
        "reference_image": c.reference_image or "",
        "voice_engine": c.voice_engine, "voice_id": c.voice_id,
        "voice_rate": c.voice_rate, "voice_pitch": c.voice_pitch,
        "is_active": bool(c.is_active),
    }


def _clean(data: dict[str, Any]) -> dict[str, Any]:
    """Keep only known fields, coercing types."""
    out: dict[str, Any] = {}
    for k, default in FIELDS.items():
        if k not in data or data[k] is None:
            continue
        v = data[k]
        if k == "seed":
            try:
                v = int(v)
            except (TypeError, ValueError):
                v = 0
        elif k == "is_active":
            v = bool(v)
        else:
            v = str(v).strip()
        out[k] = v
    return out


def validate_character(data: dict[str, Any]) -> None:
    """Raise ValueError if the character is invalid or breaks originality rules."""
    name = str(data.get("name", "")).strip()
    if not name:
        raise ValueError("Character needs a name.")
    blob = " ".join(str(data.get(k, "")) for k in
                    ("name", "species", "description", "appearance_prompt", "clothes"))
    try:
        check_text(blob, where="character")
    except Exception as e:
        # surface as a clean validation error for the UI
        raise ValueError(str(e))


def list_characters(active_only: bool = False, session=None) -> list[dict[str, Any]]:
    session = session or get_session()
    repo = CharacterRepo(session)
    rows = repo.get_active() if active_only else repo.get_all()
    return [_to_dict(c) for c in rows]


def get_character(character_id: int, session=None) -> dict[str, Any] | None:
    session = session or get_session()
    c = CharacterRepo(session).get(character_id)
    return _to_dict(c) if c else None


def save_character(data: dict[str, Any], character_id: int | None = None,
                   session=None) -> dict[str, Any]:
    """Create (character_id None) or update an existing character."""
    session = session or get_session()
    repo = CharacterRepo(session)
    fields = _clean(data)
    validate_character({**FIELDS, **fields})
    if not fields.get("slug"):
        fields["slug"] = slugify(fields.get("name", "") or "character")

    if character_id:
        c = repo.update(character_id, **fields)
        if not c:
            raise ValueError(f"Character {character_id} not found.")
        log.info("Updated character %s (%s)", character_id, c.name)
    else:
        if repo.by_name(fields["name"]):
            raise ValueError(f"A character named {fields['name']!r} already exists.")
        c = repo.create(**fields)
        log.info("Created character %s (%s)", c.id, c.name)
    return _to_dict(c)


def delete_character(character_id: int, session=None) -> None:
    session = session or get_session()
    CharacterRepo(session).delete(character_id)
    log.info("Deleted character %s", character_id)


def export_characters(session=None) -> str:
    """Export all characters as a pretty JSON string (for download / backup)."""
    chars = list_characters(session=session)
    for c in chars:
        c.pop("id", None)   # ids are environment-specific; import matches by name
    return json.dumps({"characters": chars}, ensure_ascii=False, indent=2)


def import_characters(text: str, session=None) -> tuple[int, list[str]]:
    """Import characters from a JSON string (upsert by name). Returns
    (imported_count, errors)."""
    session = session or get_session()
    repo = CharacterRepo(session)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        return 0, [f"Invalid JSON: {e}"]
    items = data.get("characters") if isinstance(data, dict) else data
    if not isinstance(items, list):
        return 0, ["Expected a list of characters or {\"characters\": [...]}."]

    imported, errors = 0, []
    for i, raw in enumerate(items):
        try:
            fields = _clean(raw if isinstance(raw, dict) else {})
            validate_character({**FIELDS, **fields})
            if not fields.get("slug"):
                fields["slug"] = slugify(fields.get("name", "") or f"character-{i}")
            repo.upsert(**fields)
            imported += 1
        except Exception as e:  # noqa: BLE001 — collect per-row errors for the UI
            errors.append(f"Row {i + 1}: {e}")
    log.info("Imported %d character(s), %d error(s)", imported, len(errors))
    return imported, errors
