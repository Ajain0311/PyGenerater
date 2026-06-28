"""PlannerAgent — decides the shape of today's story.

Picks a category, casts 1–3 ORIGINAL characters from the roster, and lays out a
short beat outline (setup → funny middle → gentle resolution). Output is the
skeleton the Story agent fleshes out.
"""

from __future__ import annotations

from src.agents.base import Agent, AgentContext
from src.content_policy import check_text

PLANNER_PROMPT = """\
[AGENT:PLANNER]
You plan ONE short, funny, wholesome cartoon story for an Indian kids' YouTube
Shorts channel (ages 3-10). It must be ORIGINAL — never use any existing TV/film
character. Keep it gentle: no violence, no fear, no facts/documentary, no moral
lecturing. Just a cute, funny little adventure with a warm ending.

CATEGORY: {category}
AVAILABLE CHARACTERS (choose {min_c}-{max_c} that fit, by exact name):
{roster}

Return ONLY this JSON:
{{
  "category": "one of: {categories}",
  "characters": ["exact names from the list above"],
  "title": "short catchy Hindi title (Devanagari), max 8 words",
  "logline": "one-sentence English premise of the funny situation",
  "moral": "tiny warm takeaway in simple Hindi (Devanagari), max 10 words",
  "beats": [
    "beat 1: the setup (English, one line)",
    "beat 2: the funny complication",
    "beat 3: it gets sillier",
    "beat 4: the turning point",
    "beat 5: the warm, funny resolution"
  ]
}}
"""


class PlannerAgent(Agent):
    name = "planner"

    def _roster_text(self, ctx: AgentContext) -> str:
        lines = []
        for c in ctx.roster:
            lines.append(
                f"- {c.get('name')} — {c.get('species','')}; "
                f"personality: {c.get('personality','')}"
            )
        return "\n".join(lines) or "- (no characters defined)"

    def run(self, ctx: AgentContext) -> AgentContext:
        from src.config import config

        category = ctx.category or "funny"
        data = self.ask(
            ctx,
            PLANNER_PROMPT.format(
                category=category,
                roster=self._roster_text(ctx),
                categories="|".join(config.KIDS_CATEGORIES),
                min_c=1, max_c=ctx.max_characters,
            ),
            temperature=1.0,
        )

        # Keep only characters that really exist in the roster; cap the cast.
        chosen = []
        for nm in (data.get("characters") or []):
            hit = ctx.roster_by_name(nm)
            if hit and hit["name"] not in chosen:
                chosen.append(hit["name"])
        if not chosen and ctx.roster:
            chosen = [ctx.roster[0]["name"]]
        chosen = chosen[: ctx.max_characters]

        pkg = ctx.package
        pkg.category = str(data.get("category") or category)
        pkg.title = str(data.get("title") or "").strip()
        pkg.logline = str(data.get("logline") or "").strip()
        pkg.moral = str(data.get("moral") or "").strip()
        pkg.characters = chosen
        pkg.language = ctx.language

        beats = [str(b).strip() for b in (data.get("beats") or []) if str(b).strip()]
        ctx.notes["beats"] = beats or [pkg.logline or "a funny little adventure"]

        check_text(f"{pkg.title} {pkg.logline} {pkg.moral} " + " ".join(beats),
                   where="planner")
        self.log.info("Planned %r | cast=%s | %d beats",
                      pkg.title, chosen, len(ctx.notes["beats"]))
        return ctx
