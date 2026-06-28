"""DialogueAgent — validates and normalises character speech.

Deterministic (no LLM call → no extra cost or failure surface): it guarantees
every dialogue line maps to a real cast member, clamps emotions to a known set
(so the future per-character voice/animation layers can rely on them), and
tallies lines per character. Keeping this a real, separate step means we can
later swap in an LLM "punch-up" pass without touching the rest of the pipeline.
"""

from __future__ import annotations

from src.agents.base import Agent, AgentContext

_EMOTIONS = {"happy", "excited", "curious", "surprised", "giggly", "sad", "neutral", "worried"}


class DialogueAgent(Agent):
    name = "dialogue"

    def run(self, ctx: AgentContext) -> AgentContext:
        segments = ctx.notes.get("segments", [])
        cast = ctx.package.characters or ([ctx.roster[0]["name"]] if ctx.roster else [])
        default_char = cast[0] if cast else "Narrator"

        tally: dict[str, int] = {}
        for seg in segments:
            if seg.get("type") != "dialogue":
                continue
            nm = seg.get("character", "")
            hit = ctx.roster_by_name(nm)
            speaker = hit["name"] if hit else default_char
            seg["character"] = speaker
            emo = str(seg.get("emotion", "happy")).lower()
            seg["emotion"] = emo if emo in _EMOTIONS else "happy"
            tally[speaker] = tally.get(speaker, 0) + 1

        ctx.notes["dialogue_by_character"] = tally
        self.log.info("Dialogue normalised | lines per character: %s", tally or "none")
        return ctx
