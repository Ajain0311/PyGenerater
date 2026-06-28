"""PromptAgent — builds the final image prompt for every scene.

Deterministic by design: character consistency comes from US controlling the
tokens, not from asking the model to "remember" a character. Each scene prompt =
shared cartoon style + the scene's visual description + the exact appearance
tokens of every character present + a fixed seed derived from the lead character.
That gives the same character a stable look across shots and across days.
"""

from __future__ import annotations

from src.agents.base import Agent, AgentContext
from src.content_policy import CARTOON_STYLE, KID_SAFE_NEGATIVE


class PromptAgent(Agent):
    name = "prompt"

    def run(self, ctx: AgentContext) -> AgentContext:
        pkg = ctx.package
        scene_seeds: list[int] = []
        scene_negs: list[str] = []

        for sc in pkg.scenes:
            present = [ctx.roster_by_name(n) for n in sc.characters]
            present = [c for c in present if c]

            char_tokens = []
            negatives = [KID_SAFE_NEGATIVE]
            for c in present:
                ap = (c.get("appearance_prompt") or c.get("species") or c.get("name") or "").strip()
                if ap:
                    char_tokens.append(f"{c['name']}: {ap}")
                if c.get("negative_prompt"):
                    negatives.append(str(c["negative_prompt"]))

            featuring = (" Featuring " + "; ".join(char_tokens) + "."
                         if char_tokens else "")
            sc.image_prompt = (
                f"{CARTOON_STYLE}. Scene: {sc.description}.{featuring} "
                f"vertical 9:16 composition, full scene, no text."
            )

            # Lead character's fixed seed → stable identity; offset per scene so
            # shots differ in pose/angle but keep the same character look.
            lead_seed = next((int(c.get("seed") or 0) for c in present if c.get("seed")), 0)
            scene_seeds.append((lead_seed + sc.index) if lead_seed else 0)
            scene_negs.append(", ".join(dict.fromkeys(negatives)))

        ctx.notes["scene_seeds"] = scene_seeds
        ctx.notes["scene_negatives"] = scene_negs
        self.log.info("Built %d image prompts (consistency seeds=%s)",
                      len(pkg.scenes), scene_seeds)
        return ctx
