"""SceneAgent — turns the story into a sequence of visual shots.

Segments are grouped deterministically (preserving exact narration/dialogue and
their order, which keeps audio↔caption timing intact), then a single LLM call
writes a concrete VISUAL description per shot and lists which characters appear.
Those descriptions feed the (deterministic) PromptAgent, which bakes in each
character's appearance tokens for cross-shot consistency.
"""

from __future__ import annotations

import math

from src.agents.base import Agent, AgentContext
from src.story_schema import DialogueLine, Scene

SCENE_PROMPT = """\
[AGENT:SCENE]
You are a storyboard artist for a cute kids' cartoon. For each numbered story
beat below, write a short, concrete VISUAL description of what we SEE (setting,
action, mood) — NOT the words spoken. Keep it child-friendly and filmable as a
single 2D cartoon illustration. List which of these characters appear: {cast}.

BEATS:
{beats}

Return ONLY this JSON (exactly {n} items, in order):
{{"scenes": [{{"description": "english visual description", "characters": ["names present"]}}]}}
"""


class SceneAgent(Agent):
    name = "scene"

    def _segments_from_package(self, pkg) -> list[dict]:
        """Rebuild the ordered segments from persisted scenes — lets SCENE be
        regenerated on its own (fresh process) without re-running STORY."""
        segs: list[dict] = []
        for sc in pkg.scenes:
            if sc.narration:
                segs.append({"type": "narration", "text": sc.narration})
            for dl in sc.dialogue:
                segs.append({"type": "dialogue", "character": dl.character,
                             "text": dl.text, "emotion": dl.emotion})
        return segs

    def _group_segments(self, segments: list[dict], n: int) -> list[list[dict]]:
        """Split the ordered segments into <=n contiguous groups, as even as
        possible."""
        if not segments:
            return []
        n = max(1, min(n, len(segments)))
        size = math.ceil(len(segments) / n)
        groups = [segments[i : i + size] for i in range(0, len(segments), size)]
        return groups

    def run(self, ctx: AgentContext) -> AgentContext:
        pkg = ctx.package
        segments = ctx.notes.get("segments") or self._segments_from_package(pkg)
        groups = self._group_segments(segments, ctx.scene_count)
        if not groups:
            self.log.warning("No segments to build scenes from — keeping fallback scene")
            return ctx

        # Build a readable beat list (the text of each group) for the LLM.
        beat_lines = []
        for i, g in enumerate(groups):
            txt = " ".join(s["text"] for s in g)
            beat_lines.append(f"{i+1}. {txt}")

        visuals: list[dict] = []
        try:
            data = self.ask(
                ctx,
                SCENE_PROMPT.format(
                    cast=", ".join(pkg.characters) or "the characters",
                    beats="\n".join(beat_lines),
                    n=len(groups),
                ),
                temperature=0.7,
            )
            visuals = data.get("scenes") or []
        except Exception as e:  # scene visuals are enrichment — degrade gracefully
            self.log.warning("Scene visual generation failed (%s) — using beats as visuals", e)

        per_scene_seconds = round(ctx.target_seconds / max(len(groups), 1), 2)
        scenes: list[Scene] = []
        for i, g in enumerate(groups):
            v = visuals[i] if i < len(visuals) else {}
            narration = " ".join(s["text"] for s in g if s["type"] == "narration")
            dialogue = [DialogueLine(s["character"], s["text"], s.get("emotion", "happy"))
                        for s in g if s["type"] == "dialogue"]
            present = [str(c).strip() for c in (v.get("characters") or []) if str(c).strip()]
            present = [c for c in present if ctx.roster_by_name(c)]
            if not present:
                present = list({dl.character for dl in dialogue}) or list(pkg.characters[:1])
            scenes.append(Scene(
                index=i,
                description=str(v.get("description") or " ".join(s["text"] for s in g))[:300],
                characters=present,
                narration=narration,
                dialogue=dialogue,
                seconds=per_scene_seconds,
            ))

        pkg.scenes = scenes
        pkg.rebuild_script()
        self.log.info("Built %d scenes | ~%.1fs each", len(scenes), per_scene_seconds)
        return ctx
