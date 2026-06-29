"""
Kinetic-typography YouTube Shorts renderer (MoviePy 2.x).

Replaces the old static slideshow with a motion-first composition:

  • Backgrounds are never still — every scene gets a Ken Burns move (zoom / pan)
    and scenes cross-dissolve on a ~2.6s cadence for constant visual novelty.
  • A legibility gradient keeps text crisp over any background.
  • A bold animated HOOK occupies the first ~3 seconds (retention-critical).
  • Captions are word-by-word: ≤3 words on screen, the spoken word highlighted
    on a coloured chip, each word rising into place — driven by REAL TTS word
    timings so it is frame-accurate and never shows the whole script.
  • A CTA slides up at the end.

Public API (`VideoGenerator.generate`) is unchanged except for additive,
optional kwargs (hook / cta / language), so the rest of the pipeline keeps
working.
"""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter

from src.captions import Cue, build_caption_cues
from src.config import config
from src.utils import get_font, get_logger, has_devanagari

log = get_logger(__name__)

W, H = config.VIDEO_WIDTH, config.VIDEO_HEIGHT
FPS = config.VIDEO_FPS
TOTAL_DURATION = config.VIDEO_DURATION
SCENE = max(1.5, config.SCENE_SECONDS)
# Layout constants — all positions respect the YouTube Shorts mobile safe zone:
# top 0-8%: status bar/notch; right 86-100%: like/share buttons;
# bottom 75-100%: title/description UI overlay.
# Important text must live between 10% and 72% vertically.
CAPTION_Y = int(H * 0.58)          # caption band centre (58% = well above YT bottom UI)
HOOK_Y    = int(H * 0.30)          # hook centre (30% = clear of captions)
SAFE_W    = W - 200                # keep text inside phone-safe margins (wider guard)

_GRADIENTS = [
    ((18, 26, 64), (96, 64, 196)),
    ((72, 16, 38), (212, 58, 92)),
    ((12, 64, 56), (44, 196, 140)),
    ((68, 40, 14), (214, 150, 52)),
    ((40, 14, 70), (140, 60, 206)),
]


# Per-render highlight override — set by generate() when the scene palette is
# warm enough that the configured warm highlight would not pop against it.
_HL_OVERRIDE: tuple[int, int, int] | None = None


def _hl_color() -> tuple[int, int, int]:
    if _HL_OVERRIDE is not None:
        return _HL_OVERRIDE
    try:
        r, g, b = (int(x) for x in config.CAPTION_HIGHLIGHT.split(","))
        return (r, g, b)
    except Exception:
        return (255, 221, 0)


def _adaptive_highlight(image_paths: list[Path]) -> tuple[int, int, int] | None:
    """Electric cyan on warm scenes; otherwise keep the configured colour."""
    try:
        p = next((p for p in (image_paths or []) if p and Path(p).exists()), None)
        if not p:
            return None
        arr = np.asarray(Image.open(p).convert("RGB").resize((64, 64)), dtype=float)
        warmth = arr[..., 0].mean() - arr[..., 2].mean()  # red minus blue
        return (0, 229, 255) if warmth > 18 else None
    except Exception:
        return None


def _ease(p: float) -> float:
    """Smoothstep ease-in-out — mechanical linear motion reads as cheap."""
    p = min(max(p, 0.0), 1.0)
    return p * p * (3.0 - 2.0 * p)


# ── Image helpers ────────────────────────────────────────────────────────────
def _cover(img: Image.Image, w: int, h: int) -> Image.Image:
    iw, ih = img.size
    scale = max(w / iw, h / ih)
    img = img.resize((max(1, int(iw * scale)), max(1, int(ih * scale))), Image.LANCZOS)
    left = (img.width - w) // 2
    top = (img.height - h) // 2
    return img.crop((left, top, left + w, top + h))


def _gradient_np(idx: int, w: int, h: int) -> np.ndarray:
    c1, c2 = _GRADIENTS[idx % len(_GRADIENTS)]
    grad = np.zeros((h, w, 3), np.uint8)
    ramp = np.linspace(0.0, 1.0, h)[:, None]
    for ch in range(3):
        grad[:, :, ch] = (c1[ch] + (c2[ch] - c1[ch]) * ramp).astype(np.uint8)
    return grad


def _grade(img: Image.Image) -> Image.Image:
    """Light cinematic grade: stock photos arrive flat and washed out."""
    img = ImageEnhance.Color(img).enhance(1.18)
    img = ImageEnhance.Contrast(img).enhance(1.08)
    img = ImageEnhance.Brightness(img).enhance(1.02)
    return img


def _scene_base(path: Path | None, idx: int) -> np.ndarray:
    """A frame-and-a-quarter sized RGB array, giving room to zoom/pan."""
    bw, bh = int(W * 1.25), int(H * 1.25)
    if path and Path(path).exists():
        try:
            return np.array(_grade(_cover(Image.open(path).convert("RGB"), bw, bh)))
        except Exception as e:
            log.warning("bg image %s failed: %s", path, e)
    return _gradient_np(idx, bw, bh)


def _legibility_overlay() -> np.ndarray:
    """Dark top+bottom gradient + soft vignette so captions stay readable."""
    ov = np.zeros((H, W, 4), np.uint8)
    top = np.clip(np.linspace(150, 0, int(H * 0.34)), 0, 255).astype(np.uint8)
    ov[: top.size, :, 3] = top[:, None]
    bh = int(H * 0.5)
    bot = np.clip(np.linspace(0, 205, bh), 0, 255).astype(np.uint8)
    ov[H - bh:, :, 3] = np.maximum(ov[H - bh:, :, 3], bot[:, None])
    return ov


# ── Text layout / rendering ──────────────────────────────────────────────────
def _layout(words: list[str], size: int, max_width: int):
    """Wrap words into lines that fit max_width. Returns (lines, total_w, total_h).
    Each line item is (word, font, w, h)."""
    space = max(8, size // 4)
    line_h = int(size * 1.18)
    items = []
    for wd in words:
        font = get_font(size, bold=True, devanagari=has_devanagari(wd))
        x0, y0, x1, y1 = font.getbbox(wd)
        items.append((wd, font, x1 - x0, line_h))
    lines: list[list] = [[]]
    widths = [0]
    for it in items:
        cur_w = widths[-1] + (space if lines[-1] else 0) + it[2]
        if lines[-1] and cur_w > max_width:
            lines.append([it]); widths.append(it[2])
        else:
            lines[-1].append(it); widths[-1] = cur_w
    total_w = max(widths) if widths else 0
    total_h = line_h * len(lines)
    return lines, total_w, total_h, space, line_h


def _word_positions(lines: list[list], space: int, line_h: int, pad: int):
    """Flatten the layout into absolute (word, font, x, y, width) tuples."""
    positions = []
    y = pad
    for line in lines:
        line_w = sum(it[2] for it in line) + space * (len(line) - 1)
        x = (W - line_w) // 2
        for (wd, font, ww, _h) in line:
            positions.append((wd, font, x, y, ww))
            x += ww + space
        y += line_h
    return positions


def _shadow_layer(size_px: tuple[int, int], positions: list, stroke: int) -> Image.Image:
    """Soft blurred drop-shadow under the text — reads cleaner than a fat
    rasterised stroke alone and adds depth on bright backgrounds."""
    shadow = Image.new("RGBA", size_px, (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    for (wd, font, x, y, _ww) in positions:
        sd.text((x, y + 6), wd, font=font, fill=(0, 0, 0, 170),
                stroke_width=stroke, stroke_fill=(0, 0, 0, 170))
    return shadow.filter(ImageFilter.GaussianBlur(5))


def _render_words(words: list[str], active: int, size: int, highlight: bool = True) -> np.ndarray:
    """Render a centred caption band (full video width) with the active word
    on a coloured chip; inactive words white with a black stroke over a soft
    drop-shadow."""
    lines, _, total_h, space, line_h = _layout(words, size, SAFE_W)
    pad = max(16, size // 5)
    stroke = max(4, size // 12)
    hl = _hl_color()
    positions = _word_positions(lines, space, line_h, pad)

    canvas = Image.new("RGBA", (W, total_h + pad * 2), (0, 0, 0, 0))
    canvas = Image.alpha_composite(canvas, _shadow_layer(canvas.size, positions, stroke))
    draw = ImageDraw.Draw(canvas)

    for gi, (wd, font, x, y, ww) in enumerate(positions):
        if highlight and gi == active:
            chip = max(10, size // 8)
            draw.rounded_rectangle(
                [x - chip, y - chip // 2, x + ww + chip, y + line_h - chip // 2],
                radius=max(12, size // 6), fill=(*hl, 255),
            )
            draw.text((x, y), wd, font=font, fill=(12, 12, 12, 255))
        else:
            draw.text((x, y), wd, font=font, fill=(255, 255, 255, 255),
                      stroke_width=stroke, stroke_fill=(0, 0, 0, 255))
    return np.array(canvas)


def _render_banner(text: str, size: int, color: tuple, accent: tuple | None = None) -> np.ndarray:
    """Centred multi-line banner (used for hook + CTA)."""
    words = text.split()
    lines, total_w, total_h, space, line_h = _layout(words, size, SAFE_W)
    pad = max(24, size // 4)
    stroke = max(5, size // 11)
    positions = _word_positions(lines, space, line_h, pad)

    canvas = Image.new("RGBA", (W, total_h + pad * 2), (0, 0, 0, 0))
    canvas = Image.alpha_composite(canvas, _shadow_layer(canvas.size, positions, stroke))
    draw = ImageDraw.Draw(canvas)
    for (wd, font, x, y, _ww) in positions:
        draw.text((x, y), wd, font=font, fill=(*color, 255),
                  stroke_width=stroke, stroke_fill=(0, 0, 0, 255))
    if accent:  # underline accent bar under the banner
        bar_w = min(total_w + pad, SAFE_W)
        bx = (W - bar_w) // 2
        by = total_h + pad + pad // 3
        draw.rounded_rectangle([bx, by, bx + bar_w, by + max(8, size // 12)],
                               radius=8, fill=(*accent, 255))
    return np.array(canvas)


def _chevron(size: int = 90) -> np.ndarray:
    """A drawn down-chevron (avoids unrenderable emoji) for the CTA."""
    img = Image.new("RGBA", (size * 2, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    hl = _hl_color()
    d.line([(size * 0.3, size * 0.25), (size, size * 0.8)], fill=(*hl, 255), width=max(8, size // 8))
    d.line([(size, size * 0.8), (size * 1.7, size * 0.25)], fill=(*hl, 255), width=max(8, size // 8))
    return np.array(img)


def _flash_clip(start: float, dur: float = 0.14) -> "ImageClip":
    """Soft luma pulse on each scene cut. Kept well below full white — a
    255-alpha strobe is harsh on the eyes and a photosensitivity hazard."""
    from moviepy import ImageClip, vfx
    arr = np.full((H, W, 4), [255, 255, 255, 120], dtype=np.uint8)
    return (ImageClip(arr, transparent=True)
            .with_start(start)
            .with_duration(dur)
            .with_effects([vfx.CrossFadeOut(dur)]))


# ── Clip builders ──────────────────────────────────────────────────────────--
def _ken_burns(base: np.ndarray, dur: float, variant: int, start: float, xfade: float):
    from moviepy import ImageClip, vfx

    bh, bw = base.shape[0], base.shape[1]
    clip = ImageClip(base).with_duration(dur)
    # Deterministic per-scene variety: zoom depth cycles 1.22 / 1.25 / 1.28 so
    # consecutive scenes never repeat the exact same move.
    depth = 1.22 + 0.03 * ((variant * 7) % 3)
    kind = variant % 4

    if kind == 0:             # eased zoom-in
        s0, s1 = 1.0, depth
        scale = lambda t: s0 + (s1 - s0) * _ease(t / dur)
        clip = clip.resized(scale)
        clip = clip.with_position(lambda t: ((W - bw * scale(t)) / 2, (H - bh * scale(t)) / 2))
    elif kind == 1:           # eased zoom-out
        s0, s1 = depth, 1.0
        scale = lambda t: s0 + (s1 - s0) * _ease(t / dur)
        clip = clip.resized(scale)
        clip = clip.with_position(lambda t: ((W - bw * scale(t)) / 2, (H - bh * scale(t)) / 2))
    elif kind == 2:           # eased horizontal pan at fixed scale
        s = depth
        cw, chh = bw * s, bh * s
        marg = (cw - W) / 2
        direction = 1 if (variant // 4) % 2 == 0 else -1
        clip = clip.resized(s)
        clip = clip.with_position(lambda t: (
            (W - cw) / 2 + direction * marg * (1 - 2 * _ease(t / dur)),
            (H - chh) / 2,
        ))
    else:                     # eased diagonal drift (pan + slight vertical)
        s = depth
        cw, chh = bw * s, bh * s
        mx, my = (cw - W) / 2, (chh - H) / 2
        direction = 1 if (variant // 4) % 2 == 0 else -1
        clip = clip.resized(s)
        clip = clip.with_position(lambda t: (
            (W - cw) / 2 + direction * mx * (1 - 2 * _ease(t / dur)),
            (H - chh) / 2 - direction * my * 0.5 * (1 - 2 * _ease(t / dur)),
        ))

    clip = clip.with_start(start)
    if start > 0 and xfade > 0:
        clip = clip.with_effects([vfx.CrossFadeIn(xfade)])
    return clip


def _background(image_paths: list[Path], duration: float):
    from moviepy import CompositeVideoClip

    xfade = 0.45
    valid = [p for p in (image_paths or []) if p and Path(p).exists()]
    n_seg = max(1, int(np.ceil((duration - xfade) / (SCENE - xfade))))
    bases = [_scene_base(valid[i % len(valid)] if valid else None, i) for i in range(n_seg)]

    clips = []
    flashes = []
    for i, base in enumerate(bases):
        start = i * (SCENE - xfade)
        clips.append(_ken_burns(base, SCENE + xfade, i, start, xfade))
        if i > 0:
            flashes.append(_flash_clip(start))

    bg = CompositeVideoClip(clips, size=(W, H)).with_duration(duration)
    return bg, flashes


def _caption_clips(
    cues: list[Cue],
    duration: float,
    cap_limit: float | None = None,
    cap_start: float = 0.0,
):
    """cap_start: suppress captions before this time (e.g. during hook)."""
    from moviepy import ImageClip, vfx

    limit = duration if cap_limit is None else cap_limit
    size = config.CAPTION_FONT_SIZE
    clips = []
    for cue in cues:
        if cue.start >= limit:
            break
        words = [w.text for w in cue.words]
        for wi, w in enumerate(cue.words):
            # Don't show captions while the hook dominates the screen.
            seg_start = max(cap_start, w.start)
            seg_end = min(w.hl_end, limit)
            seg_dur = seg_end - seg_start
            if seg_dur <= 0.04:
                continue
            arr = _render_words(words, wi, size)
            img_h = arr.shape[0]
            y_top = CAPTION_Y - img_h // 2
            # Rise-in only (no resized() — that overflows a 1080-wide canvas on mobile).
            clip = (ImageClip(arr, transparent=True)
                    .with_start(seg_start)
                    .with_duration(seg_dur)
                    .with_position(lambda t, y=y_top: ("center", int(y + 38 * max(0.0, 1 - t / 0.18))))
                    .with_effects([vfx.CrossFadeIn(min(0.05, seg_dur / 2))]))
            clips.append(clip)
    return clips


def _hook_clip(text: str, duration: float):
    from moviepy import ImageClip, vfx

    dur = min(config.HOOK_SECONDS, duration)
    if dur <= 0.2 or not text.strip():
        return None
    arr = _render_banner(text.upper(), config.HOOK_FONT_SIZE, (255, 255, 255), accent=_hl_color())
    img_h = arr.shape[0]
    y = HOOK_Y - img_h // 2
    return (ImageClip(arr, transparent=True)
            .with_start(0.0)
            .with_duration(dur)
            # punch-in: scale settles 1.12 → 1.0 over 0.25s
            .resized(lambda t: 1.0 + 0.12 * max(0.0, 1 - t / 0.25))
            .with_position(lambda t, yy=y: ("center", yy))
            .with_effects([vfx.CrossFadeIn(0.12), vfx.CrossFadeOut(0.3)]))


def _cta_clips(text: str, duration: float):
    from moviepy import ImageClip, vfx

    dur = min(config.CTA_SECONDS, duration)
    if dur <= 0.3:
        return []
    start = duration - dur
    arr = _render_banner(text.upper(), int(config.CAPTION_FONT_SIZE * 1.05), _hl_color(), accent=(255, 255, 255))
    img_h = arr.shape[0]
    y_target = int(H * 0.62) - img_h // 2
    banner = (ImageClip(arr, transparent=True)
              .with_start(start)
              .with_duration(dur)
              # slide up from below + settle
              .with_position(lambda t, yt=y_target: ("center", int(yt + 90 * max(0.0, 1 - t / 0.3))))
              .with_effects([vfx.CrossFadeIn(0.2)]))
    chev = _chevron(110)
    chev_clip = (ImageClip(chev, transparent=True)
                 .with_start(start)
                 .with_duration(dur)
                 .with_position(("center", y_target + img_h + 20))
                 .with_effects([vfx.CrossFadeIn(0.2)]))
    return [banner, chev_clip]


def _watermark_clip(text: str, duration: float):
    from moviepy import ImageClip
    if not text:
        return None
    font = get_font(40, bold=True)
    img = Image.new("RGBA", (W, 70), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    x0, _, x1, _ = font.getbbox(text)
    d.text(((W - (x1 - x0)) // 2, 8), text, font=font, fill=(255, 255, 255, 150),
           stroke_width=3, stroke_fill=(0, 0, 0, 160))
    return (ImageClip(np.array(img), transparent=True)
            .with_duration(duration)
            .with_position(("center", int(H * 0.83))))


def _progress_bar_clip(duration: float):
    """Thin curiosity meter at top: fills left-to-right as the Short plays.
    Psychologically keeps viewers watching — they want to see it fill."""
    from moviepy import VideoClip
    hl = _hl_color()
    bar_h = 10

    def make_frame(t: float) -> np.ndarray:
        progress = min(t / max(duration, 0.01), 1.0)
        arr = np.zeros((bar_h, W, 3), np.uint8)
        fill_w = max(0, int(W * progress))
        if fill_w:
            arr[:, :fill_w] = hl
        arr[:, fill_w:] = [28, 28, 28]
        return arr

    return VideoClip(make_frame, duration=duration).with_position(("center", 0))


# ── Character cutout / puppet layer ───────────────────────────────────────────
def _load_cutout(path) -> Image.Image | None:
    """Load a character PNG as RGBA. Transparent cutouts look best, but an opaque
    image still works (it just shows as a card)."""
    try:
        if not path or not Path(path).exists():
            return None
        return Image.open(path).convert("RGBA")
    except Exception as e:
        log.warning("cutout load failed %s: %s", path, e)
        return None


def _scene_windows(scenes, duration: float) -> list[tuple[float, float]]:
    """Time window per scene, proportional to each scene's spoken text so a
    wordier beat stays on screen longer. Falls back to equal slices."""
    weights = []
    for sc in scenes:
        txt = (getattr(sc, "narration", "") or "") + " ".join(
            dl.text for dl in getattr(sc, "dialogue", []) or [])
        weights.append(max(len(txt.split()), 1))
    total = sum(weights) or 1
    out, cursor = [], 0.0
    for w in weights:
        dur = duration * w / total
        out.append((cursor, min(cursor + dur, duration)))
        cursor += dur
    if out:
        out[-1] = (out[-1][0], duration)
    return out


def _character_clip(img: Image.Image, t0: float, dur: float, cx: int, target_h: int):
    """A character cutout placed at horizontal centre `cx`, animated with a
    fade-in, a gentle vertical bob and a subtle breathing scale so it reads as
    'alive' rather than a pasted sticker."""
    from moviepy import ImageClip, vfx
    import math

    iw, ih = img.size
    scale = target_h / max(ih, 1)
    base = img.resize((max(1, int(iw * scale)), target_h), Image.LANCZOS)
    arr = np.array(base)
    cw = base.width
    y_mid = int(H * 0.46)            # vertical anchor (centre of the character)

    def pos(t):
        bob = 10 * math.sin(2 * math.pi * (t / 2.6))      # ~2.6s bob cycle
        intro = 60 * max(0.0, 1 - t / 0.4)                # slide-up entrance
        return (cx - cw / 2, y_mid - target_h / 2 + bob + intro)

    clip = (ImageClip(arr, transparent=True)
            .with_start(t0).with_duration(dur)
            .resized(lambda t: 1.0 + 0.02 * math.sin(2 * math.pi * (t / 3.1)))  # breathe
            .with_position(pos)
            .with_effects([vfx.CrossFadeIn(min(0.35, dur / 2))]))
    return clip


def _character_layers(scenes, character_images: dict, duration: float) -> list:
    """One animated cutout per character present in each scene, laid out across
    the frame and confined to that scene's time window."""
    if not scenes or not character_images:
        return []
    windows = _scene_windows(scenes, duration)
    target_h = int(H * 0.52)
    layers = []
    for sc, (t0, t1) in zip(scenes, windows):
        present = [n for n in (sc.characters or []) if character_images.get(n)]
        if not present:
            continue
        n = len(present)
        for i, name in enumerate(present):
            img = _load_cutout(character_images[name])
            if img is None:
                continue
            # spread characters across the central band
            cx = int(W * (i + 1) / (n + 1))
            scale = 1.0 if n == 1 else 0.82       # shrink a little when sharing the frame
            layers.append(_character_clip(img, t0, max(0.1, t1 - t0), cx, int(target_h * scale)))
    return layers


# ── Public API ───────────────────────────────────────────────────────────────
class VideoGenerator:
    def generate(
        self,
        title: str,
        script: str,
        subtitle_segments: list[dict],
        image_paths: list[Path],
        audio_path: Path,
        slug: str,
        hook: str | None = None,
        cta: str | None = None,
        language: str = "en",
        scenes: list | None = None,
        character_images: dict | None = None,
    ) -> Path:
        out_path = config.VIDEOS_DIR / f"{slug}.mp4"
        if out_path.exists() and out_path.stat().st_size > 0:
            log.info("Video exists, reusing: %s", out_path.name)
            return out_path

        global _HL_OVERRIDE
        _HL_OVERRIDE = _adaptive_highlight(image_paths)
        if _HL_OVERRIDE:
            log.info("Warm scene palette detected — switching highlight to cyan")
        try:
            return self._render(out_path, slug, image_paths, audio_path,
                                subtitle_segments, hook, cta, title,
                                scenes=scenes, character_images=character_images)
        finally:
            _HL_OVERRIDE = None

    def _render(self, out_path: Path, slug: str, image_paths, audio_path,
                subtitle_segments, hook, cta, title,
                scenes=None, character_images=None) -> Path:
        from moviepy import AudioFileClip, CompositeVideoClip, ImageClip, afx

        audio = AudioFileClip(str(audio_path))
        duration = float(min(audio.duration, TOTAL_DURATION))
        log.info("Kinetic render | slug=%s | dur=%.1fs | %d word-cues",
                 slug, duration, len(subtitle_segments))

        cues = build_caption_cues(
            subtitle_segments,
            max_words=config.CAPTION_MAX_WORDS,
        )

        # Captions stop where the CTA begins so the two never collide on screen.
        cta_text = _sanitize_text(cta or "Follow for more")
        cta_dur = min(config.CTA_SECONDS, duration)
        cap_limit = max(0.0, duration - cta_dur) if cta_text else duration

        hook_text = (hook or title or "").strip()
        # Captions wait until the hook fades out — no simultaneous text layers.
        hook_dur = min(config.HOOK_SECONDS, duration) if hook_text else 0.0

        bg, flash_clips = _background(image_paths, duration)
        layers = [bg]
        layers.extend(flash_clips)
        layers.append(ImageClip(_legibility_overlay(), transparent=True).with_duration(duration))
        # Character cutouts (puppet mode) sit above the background but below text.
        char_layers = _character_layers(scenes or [], character_images or {}, duration)
        if char_layers:
            layers.extend(char_layers)
            log.info("Puppet mode: %d character cutout layer(s)", len(char_layers))
        layers.append(_progress_bar_clip(duration))
        layers.extend(_caption_clips(cues, duration, cap_limit=cap_limit, cap_start=hook_dur))

        hk = _hook_clip(hook_text, duration)
        if hk is not None:
            layers.append(hk)

        layers.extend(_cta_clips(cta_text, duration))

        wm = _watermark_clip(config.CHANNEL_WATERMARK, duration)
        if wm is not None:
            layers.append(wm)

        final = CompositeVideoClip(layers, size=(W, H)).with_duration(duration)
        voice = audio.subclipped(0, duration).with_effects(
            [afx.AudioFadeIn(0.05), afx.AudioFadeOut(0.35)]
        )
        final = final.with_audio(voice).with_fps(FPS)

        log.info("Rendering -> %s", out_path)
        final.write_videofile(
            str(out_path),
            fps=FPS,
            codec="libx264",
            audio_codec="aac",
            preset="fast",
            bitrate="8000k",
            audio_bitrate="192k",
            threads=max(2, (os.cpu_count() or 4) - 1),
            logger=None,
        )

        audio.close()
        final.close()
        log.info("Video saved: %s (%.1f MB)", out_path.name, out_path.stat().st_size / 1_048_576)
        return out_path


def _sanitize_text(text: str) -> str:
    """Drop characters that PIL can't render (emoji / non-BMP) so we never bake
    tofu boxes into the frame; collapse whitespace."""
    cleaned = "".join(ch for ch in text if ord(ch) < 0x2500 and ch.isprintable() or ch == " ")
    return " ".join(cleaned.split()) or "Follow for more"
