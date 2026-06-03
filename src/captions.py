"""
Group per-word TTS timestamps into short, kinetic caption *cues*.

A cue is a tiny phrase (default ≤3 words) shown together on screen, with one
word "active" (spoken) at a time. Cues are built straight from the audio's real
word timings, so:

  • only a few words are ever on screen — never the whole script;
  • cues are strictly sequential → captions never overlap;
  • each word carries its own highlight window for word-by-word animation.

Breaks happen on word-count, on a natural speech pause (a timing gap), or when a
line would get too wide to read comfortably on a 9:16 phone screen.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class CaptionWord:
    text: str
    start: float
    end: float          # spoken end
    hl_end: float       # highlight stays until the next word starts (no flicker)


@dataclass
class Cue:
    words: list[CaptionWord] = field(default_factory=list)
    start: float = 0.0
    end: float = 0.0

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)


def build_caption_cues(
    word_segments: list[dict],
    max_words: int = 3,
    max_chars: int = 22,
    pause_gap: float = 0.45,
    fill_gaps: bool = True,
) -> list[Cue]:
    """
    Turn [{text,start,end}, …] into a list of Cue objects.

    max_words / max_chars : readability caps (small = more kinetic).
    pause_gap             : a silence longer than this forces a new cue.
    fill_gaps             : hold a cue on screen through trailing pauses so the
                            band never flickers to empty between phrases.
    """
    segs = [s for s in word_segments if (s.get("text") or "").strip()]
    if not segs:
        return []

    cues: list[Cue] = []
    cur: list[dict] = []
    cur_chars = 0

    def flush() -> None:
        nonlocal cur, cur_chars
        if not cur:
            return
        words = [
            CaptionWord(text=w["text"].strip(), start=float(w["start"]),
                        end=float(w["end"]), hl_end=float(w["end"]))
            for w in cur
        ]
        cues.append(Cue(words=words, start=words[0].start, end=words[-1].end))
        cur, cur_chars = [], 0

    prev_end: float | None = None
    for seg in segs:
        word = seg["text"].strip()
        gap = (float(seg["start"]) - prev_end) if prev_end is not None else 0.0

        too_many = len(cur) >= max_words
        too_wide = cur and (cur_chars + 1 + len(word)) > max_chars
        long_pause = cur and gap > pause_gap
        if too_many or too_wide or long_pause:
            flush()

        cur.append(seg)
        cur_chars += (1 if cur_chars else 0) + len(word)
        prev_end = float(seg["end"])
    flush()

    # Extend highlight of each word to the next word's start (smooth tracking),
    # and optionally stretch each cue to the next cue so the band stays filled.
    for ci, cue in enumerate(cues):
        for wi, w in enumerate(cue.words):
            if wi + 1 < len(cue.words):
                w.hl_end = cue.words[wi + 1].start
            else:
                w.hl_end = cue.end
        if fill_gaps:
            next_start = cues[ci + 1].start if ci + 1 < len(cues) else cue.end + 0.6
            cue.end = max(cue.end, next_start)
            cue.words[-1].hl_end = cue.end

    return cues
