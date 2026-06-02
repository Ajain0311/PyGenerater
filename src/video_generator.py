"""Build YouTube Shorts MP4 using MoviePy."""

from __future__ import annotations

import math
import os
import textwrap
from pathlib import Path
from typing import Optional

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from src.config import config
from src.utils import get_logger

log = get_logger(__name__)

W, H = config.VIDEO_WIDTH, config.VIDEO_HEIGHT
FPS = config.VIDEO_FPS
TOTAL_DURATION = config.VIDEO_DURATION


def _try_font(size: int):
    from PIL import ImageFont
    candidates = [
        "arial.ttf", "Arial.ttf",
        "DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except (IOError, OSError):
            continue
    return ImageFont.load_default()


def _pil_to_np(img: Image.Image) -> np.ndarray:
    return np.array(img.convert("RGB"))


def _load_bg_image(path: Path | None, idx: int) -> np.ndarray:
    """Load background image or create gradient fallback."""
    if path and path.exists():
        try:
            img = Image.open(path).convert("RGB")
            # Resize cover
            iw, ih = img.size
            scale = max(W / iw, H / ih)
            img = img.resize((int(iw * scale), int(ih * scale)), Image.LANCZOS)
            left = (img.width - W) // 2
            top = (img.height - H) // 2
            img = img.crop((left, top, left + W, top + H))
            return _pil_to_np(img)
        except Exception as e:
            log.warning("Could not load image %s: %s", path, e)

    # Gradient fallback
    GRADIENTS = [
        ((15, 32, 70), (84, 120, 195)),
        ((70, 15, 30), (195, 60, 90)),
        ((15, 70, 40), (60, 195, 110)),
        ((70, 40, 15), (195, 140, 60)),
        ((45, 15, 70), (130, 60, 195)),
    ]
    c1, c2 = GRADIENTS[idx % len(GRADIENTS)]
    frame = np.zeros((H, W, 3), dtype=np.uint8)
    for y in range(H):
        t = y / H
        r = int(c1[0] + (c2[0] - c1[0]) * t)
        g = int(c1[1] + (c2[1] - c1[1]) * t)
        b = int(c1[2] + (c2[2] - c1[2]) * t)
        frame[y, :] = [r, g, b]
    return frame


def _render_text_frame(
    bg: np.ndarray,
    title: str,
    subtitle: str,
    progress: float,
    watermark: str,
) -> np.ndarray:
    """Render text overlays onto background frame."""
    img = Image.fromarray(bg).convert("RGBA")
    draw = ImageDraw.Draw(img, "RGBA")

    # Semi-transparent bottom gradient for subtitle readability
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ov_draw = ImageDraw.Draw(overlay)
    for y in range(H // 2, H):
        alpha = int(180 * (y - H // 2) / (H // 2))
        ov_draw.line([(0, y), (W, y)], fill=(0, 0, 0, alpha))
    img = Image.alpha_composite(img, overlay)
    draw = ImageDraw.Draw(img, "RGBA")

    # Title (top area)
    title_font = _try_font(72)
    wrapped = textwrap.fill(title, width=18)
    y_title = 120
    for line in wrapped.split("\n"):
        bbox = draw.textbbox((0, 0), line, font=title_font)
        lw = bbox[2] - bbox[0]
        x = (W - lw) // 2
        draw.text((x + 3, y_title + 3), line, font=title_font, fill=(0, 0, 0, 180))
        draw.text((x, y_title), line, font=title_font, fill=(255, 255, 255, 255))
        y_title += (bbox[3] - bbox[1]) + 12

    # Subtitle (bottom area)
    if subtitle:
        sub_font = _try_font(60)
        wrapped_sub = textwrap.fill(subtitle, width=22)
        lines = wrapped_sub.split("\n")
        total_h = sum(
            draw.textbbox((0, 0), ln, font=sub_font)[3] - draw.textbbox((0, 0), ln, font=sub_font)[1] + 8
            for ln in lines
        )
        y_sub = H - total_h - 180
        for line in lines:
            bbox = draw.textbbox((0, 0), line, font=sub_font)
            lw = bbox[2] - bbox[0]
            lh = bbox[3] - bbox[1]
            x = (W - lw) // 2
            # Pill background
            pad = 16
            draw.rounded_rectangle(
                [(x - pad, y_sub - pad // 2), (x + lw + pad, y_sub + lh + pad // 2)],
                radius=12, fill=(0, 0, 0, 160)
            )
            draw.text((x, y_sub), line, font=sub_font, fill=(255, 240, 100, 255))
            y_sub += lh + 12

    # Progress bar
    bar_h, bar_y = 8, H - 100
    draw.rectangle([(0, bar_y), (W, bar_y + bar_h)], fill=(50, 50, 50, 200))
    draw.rectangle([(0, bar_y), (int(W * progress), bar_y + bar_h)], fill=(255, 50, 50, 255))

    # Watermark
    wm_font = _try_font(42)
    draw.text((W - 30, H - 70), watermark, font=wm_font, fill=(200, 200, 200, 180), anchor="rm")

    return _pil_to_np(img.convert("RGB"))


class VideoGenerator:
    def generate(
        self,
        title: str,
        script: str,
        subtitle_segments: list[dict],
        image_paths: list[Path],
        audio_path: Path,
        slug: str,
    ) -> Path:
        """Build the final MP4 and return its path."""
        from moviepy.editor import (
            AudioFileClip,
            ImageClip,
            VideoClip,
            concatenate_videoclips,
        )

        out_path = config.VIDEOS_DIR / f"{slug}.mp4"
        if out_path.exists():
            log.info("Video already exists, reusing: %s", out_path.name)
            return out_path

        log.info("Building video for slug=%s | %d scenes", slug, len(image_paths))

        # Load audio to get real duration
        audio_clip = AudioFileClip(str(audio_path))
        real_duration = min(audio_clip.duration, TOTAL_DURATION)
        log.info("Audio duration: %.1fs (capped at %ds)", audio_clip.duration, TOTAL_DURATION)

        # Build scene schedule
        n_scenes = max(len(image_paths), 1)
        scene_duration = real_duration / n_scenes

        clips = []
        for scene_idx, img_path in enumerate(image_paths):
            bg_np = _load_bg_image(img_path, scene_idx)
            scene_start = scene_idx * scene_duration
            scene_end = scene_start + scene_duration

            # Find subtitle segments active during this scene
            active_subs = [
                seg for seg in subtitle_segments
                if seg["start"] < scene_end and seg["end"] > scene_start
            ]
            sub_text = " ".join(seg["text"] for seg in active_subs) if active_subs else ""

            def make_frame(t, _bg=bg_np, _title=title, _sub=sub_text,
                           _start=scene_start, _total=real_duration):
                progress = (_start + t) / _total
                return _render_text_frame(_bg, _title, _sub, progress, config.CHANNEL_WATERMARK)

            clip = VideoClip(make_frame, duration=scene_duration)
            clip = clip.set_fps(FPS)

            # Fade in/out on each scene
            if scene_idx > 0:
                clip = clip.fadein(0.4)
            if scene_idx < n_scenes - 1:
                clip = clip.fadeout(0.4)

            clips.append(clip)

        # Concatenate scenes
        final_video = concatenate_videoclips(clips, method="compose")
        final_video = final_video.set_audio(audio_clip.subclip(0, final_video.duration))

        log.info("Rendering video → %s", out_path)
        final_video.write_videofile(
            str(out_path),
            fps=FPS,
            codec="libx264",
            audio_codec="aac",
            preset="fast",
            bitrate="4000k",
            audio_bitrate="192k",
            threads=2,
            logger=None,
        )

        audio_clip.close()
        final_video.close()
        for c in clips:
            c.close()

        log.info("Video saved: %s (%.1f MB)", out_path.name, out_path.stat().st_size / 1_048_576)
        return out_path
