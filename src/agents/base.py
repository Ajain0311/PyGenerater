"""Agent framework: shared context, result envelope, and the Agent ABC.

Agents are small, single-responsibility units that read and enrich a shared
`AgentContext` (which carries the `StoryPackage` under construction and the
injected `LLM`). They never talk to the database or the renderer directly —
the Orchestrator owns side effects, persistence, and step state.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from src.llm import LLM, LLMResponse
from src.story_schema import StoryPackage
from src.utils import get_logger


@dataclass
class AgentContext:
    package: StoryPackage
    roster: list[dict[str, Any]]            # available characters (plain dicts)
    llm: LLM
    language: str = "hi"
    scene_count: int = 6
    target_seconds: int = 45
    max_characters: int = 3
    topic: str | None = None
    category: str | None = None
    # running cost accounting (summed across every agent's LLM calls)
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    notes: dict[str, Any] = field(default_factory=dict)

    def account(self, resp: LLMResponse) -> LLMResponse:
        self.input_tokens += resp.input_tokens
        self.output_tokens += resp.output_tokens
        self.cost_usd += resp.cost_usd
        return resp

    def roster_by_name(self, name: str) -> dict[str, Any] | None:
        low = (name or "").strip().lower()
        for c in self.roster:
            if str(c.get("name", "")).strip().lower() == low:
                return c
        return None


@dataclass
class AgentResult:
    name: str
    ok: bool
    artifact: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


class Agent(ABC):
    """Base class. Subclasses set `name` and implement `run`."""

    name: str = "agent"

    def __init__(self) -> None:
        self.log = get_logger(f"agent.{self.name}")

    @abstractmethod
    def run(self, ctx: AgentContext) -> AgentContext:
        """Read/enrich `ctx.package` and return the (mutated) context."""
        raise NotImplementedError

    # convenience for subclasses
    def ask(self, ctx: AgentContext, prompt: str, *, temperature: float = 0.9,
            max_output_tokens: int | None = None) -> dict:
        resp = ctx.llm.complete_json(
            prompt, temperature=temperature, max_output_tokens=max_output_tokens
        )
        ctx.account(resp)
        return resp.data
