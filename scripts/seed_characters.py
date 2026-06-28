"""
Seed the roster with 3 ORIGINAL, copyright-free kids-cartoon characters.

Idempotent: re-running updates the same rows (matched by name) instead of
duplicating. Run once after setup, or edit/extend characters from the dashboard.

    python scripts/seed_characters.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.database import CharacterRepo, get_session, init_db  # noqa: E402
from src.utils import get_logger  # noqa: E402

log = get_logger("seed")

# Original cast — designed to be visually distinct and easy to keep consistent.
# appearance_prompt = the exact tokens the image backend reuses every shot.
CHARACTERS = [
    dict(
        name="Chintu",
        slug="chintu",
        species="baby elephant",
        personality="curious, giggly, kind-hearted, asks lots of questions",
        description="A tiny grey baby elephant who turns every little thing into a big adventure.",
        clothes="a bright red polka-dot scarf",
        appearance_prompt=("small chubby grey baby elephant, big sparkly blue eyes, "
                           "tiny trunk, round ears, bright red polka-dot scarf, "
                           "cute friendly smile, 2D cartoon mascot"),
        negative_prompt="realistic, scary tusks, aggressive",
        seed=110011,
        voice_engine="edge", voice_id="hi-IN-MadhurNeural",
        voice_rate="+6%", voice_pitch="+8Hz",
    ),
    dict(
        name="Gappu",
        slug="gappu",
        species="talking parrot",
        personality="cheeky, talkative, loves jokes and silly rhymes",
        description="A green parrot who never stops chatting and always has a punchline.",
        clothes="tiny yellow bow tie",
        appearance_prompt=("small round green parrot, bright orange beak, "
                           "yellow bow tie, big cheerful eyes, tiny wings, "
                           "2D cartoon mascot, playful pose"),
        negative_prompt="realistic, dull colors",
        seed=220022,
        voice_engine="edge", voice_id="hi-IN-SwaraNeural",
        voice_rate="+14%", voice_pitch="+22Hz",
    ),
    dict(
        name="Rani",
        slug="rani",
        species="clever little fox",
        personality="smart, gentle, the calm one who solves the silly problems",
        description="A small orange fox cub who is the clever, caring friend of the group.",
        clothes="a small blue flower behind her ear",
        appearance_prompt=("small cute orange fox cub, fluffy white-tipped tail, "
                           "gentle green eyes, small blue flower behind ear, "
                           "soft round face, 2D cartoon mascot"),
        negative_prompt="realistic, sharp fangs, sly evil look",
        seed=330033,
        voice_engine="edge", voice_id="hi-IN-SwaraNeural",
        voice_rate="+2%", voice_pitch="+0Hz",
    ),
]


def main() -> None:
    init_db()
    repo = CharacterRepo(get_session())
    for c in CHARACTERS:
        row = repo.upsert(**c)
        log.info("seeded character: %s (id=%s, seed=%s)", row.name, row.id, row.seed)
    log.info("Done — %d characters in roster.", repo.count())


if __name__ == "__main__":
    main()
