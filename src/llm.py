"""
LLM provider abstraction (dependency-injectable).

The agent pipeline depends on the `LLM` protocol, never on Gemini directly, so:
  * the whole content chain can run offline against `FakeLLM` in tests/CI;
  * a different free model can be dropped in later without touching agents.

`GeminiLLM` reuses the project's key rotation + cost accounting and carries over
the battle-tested lenient-JSON parsing from the legacy content generator
(brace-matching + truncation repair). `google-genai` is imported lazily so this
module loads even when the heavy dependency isn't installed.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol, runtime_checkable

from src.config import config
from src.utils import get_logger

log = get_logger(__name__)


@dataclass
class LLMResponse:
    data: dict[str, Any]
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    raw: str = ""


@runtime_checkable
class LLM(Protocol):
    def complete_json(
        self, prompt: str, *, temperature: float = 0.9, max_output_tokens: int | None = None
    ) -> LLMResponse:
        ...


# ── Lenient JSON parsing (shared, proven logic) ──────────────────────────────
def parse_json_lenient(text: str) -> dict[str, Any]:
    """Extract the first JSON object from a model response, tolerating markdown
    fences and truncation."""
    text = re.sub(r"^```(?:json)?\s*", "", (text or "").strip(), flags=re.MULTILINE)
    text = re.sub(r"```\s*$", "", text, flags=re.MULTILINE).strip()
    start = text.find("{")
    if start == -1:
        raise ValueError("No JSON object found in response")

    brace = 0
    for i, ch in enumerate(text[start:], start):
        if ch == "{":
            brace += 1
        elif ch == "}":
            brace -= 1
            if brace == 0:
                try:
                    return json.loads(text[start : i + 1])
                except json.JSONDecodeError:
                    break
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        repaired = _repair_truncated_json(text[start:])
        if repaired is not None:
            log.warning("Recovered truncated JSON by repairing it")
            return repaired
        raise ValueError(f"Invalid JSON from model: {e}")


def _repair_truncated_json(text: str) -> dict[str, Any] | None:
    stack: list[str] = []
    in_str = esc = False
    for ch in text:
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch in "{[":
            stack.append(ch)
        elif ch in "}]":
            if stack:
                stack.pop()
    repaired = text.rstrip("\\") + ('"' if in_str else "")
    repaired = re.sub(r",\s*$", "", repaired)
    repaired = re.sub(r",?\s*\"[^\"]*\"\s*:\s*$", "", repaired)
    repaired += "".join("}" if c == "{" else "]" for c in reversed(stack))
    try:
        data = json.loads(repaired)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        return None


# ── Gemini implementation (free tier) ────────────────────────────────────────
_GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"


class GeminiLLM:
    """Free-tier Gemini over the REST API (no google-genai SDK dependency, so it
    runs on Python 3.14 too). Automatic key rotation + truncation-safe JSON.

    `http_post` is injectable ((url, json_body) -> dict) for testing against the
    live API without the `requests` TLS path; production uses `requests`.
    """

    def __init__(self, model: str | None = None, http_post=None):
        self.model = model or config.GEMINI_MODEL
        self._http_post = http_post

    def _post(self, url: str, body: dict) -> dict:
        if self._http_post is not None:
            return self._http_post(url, body)
        import requests
        r = requests.post(url, json=body, timeout=90)
        try:
            return r.json()
        except Exception:
            return {"error": {"message": f"HTTP {r.status_code}: {r.text[:200]}"}}

    def complete_json(
        self, prompt: str, *, temperature: float = 0.9, max_output_tokens: int | None = None,
        _attempt: int = 0,
    ) -> LLMResponse:
        from src.analytics import calculate_gemini_cost
        from src.key_manager import get_active_key, rotate_key

        if _attempt >= 20:
            raise RuntimeError("All Gemini API keys exhausted.")

        gen_cfg: dict[str, Any] = {
            "temperature": temperature, "topP": 0.95,
            "maxOutputTokens": max_output_tokens or config.GEMINI_MAX_OUTPUT_TOKENS,
            "responseMimeType": "application/json",
        }
        # 2.5 models "think" by default; thinking tokens eat the output cap and
        # truncate JSON. Disable where the model allows.
        if "2.5-pro" not in self.model:
            gen_cfg["thinkingConfig"] = {"thinkingBudget": 0}
        body = {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": gen_cfg}
        key = get_active_key()
        url = f"{_GEMINI_BASE}/models/{self.model}:generateContent?key={key}"

        try:
            data = self._post(url, body)
            if isinstance(data, dict) and data.get("error"):
                raise RuntimeError(str(data["error"].get("message", data["error"])))
            cands = data.get("candidates") or []
            raw = ""
            if cands:
                parts = cands[0].get("content", {}).get("parts", []) or []
                raw = "".join(p.get("text", "") for p in parts).strip()
            usage = data.get("usageMetadata", {}) or {}
            in_tok = usage.get("promptTokenCount", 0) or 0
            out_tok = usage.get("candidatesTokenCount", 0) or 0
            parsed = parse_json_lenient(raw)
            return LLMResponse(
                data=parsed, input_tokens=in_tok, output_tokens=out_tok,
                cost_usd=calculate_gemini_cost(in_tok, out_tok), raw=raw,
            )
        except Exception as exc:
            err = str(exc)
            if any(c in err for c in ("429", "RESOURCE_EXHAUSTED", "quota")):
                rotate_key(reason="429 quota")
                time.sleep(2)
                return self.complete_json(prompt, temperature=temperature,
                                          max_output_tokens=max_output_tokens, _attempt=_attempt + 1)
            if any(c in err for c in ("503", "UNAVAILABLE", "overloaded", "high demand")):
                wait = min(30, 6 * (_attempt + 1))
                log.warning("Gemini 503/overloaded — waiting %ds (attempt %d)…", wait, _attempt + 2)
                time.sleep(wait)
                return self.complete_json(prompt, temperature=temperature,
                                          max_output_tokens=max_output_tokens, _attempt=_attempt + 1)
            if ("Invalid JSON" in err or "No JSON object" in err) and _attempt < 3:
                log.warning("JSON parse failed, retrying…")
                time.sleep(2)
                return self.complete_json(prompt, temperature=temperature,
                                          max_output_tokens=max_output_tokens, _attempt=_attempt + 1)
            raise


# ── Fake implementation (offline tests / dry runs) ───────────────────────────
class FakeLLM:
    """Returns canned JSON. `responses` maps a substring that appears in the
    prompt → the dict to return (first match wins). A callable may be supplied
    for full control: fn(prompt) -> dict."""

    def __init__(self, responses: dict[str, dict] | Callable[[str], dict]):
        self.responses = responses
        self.calls: list[str] = []

    def complete_json(
        self, prompt: str, *, temperature: float = 0.9, max_output_tokens: int | None = None
    ) -> LLMResponse:
        self.calls.append(prompt)
        if callable(self.responses):
            return LLMResponse(data=self.responses(prompt))
        for marker, payload in self.responses.items():
            if marker in prompt:
                return LLMResponse(data=payload)
        raise ValueError(f"FakeLLM: no canned response matched prompt (markers: "
                         f"{list(self.responses)})")


def default_llm() -> LLM:
    """Factory the orchestrator uses when no LLM is injected."""
    return GeminiLLM()
