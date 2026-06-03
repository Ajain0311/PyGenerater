"""Generate eye-catching YouTube Shorts thumbnails using Pillow."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from src.config import config
from src.utils import get_logger

log = get_logger(__name__)

THUMB_W, THUMB_H = 1080, 1920

# Colour themes: (gradient_top, gradient_bottom, text_colour, accent)
THEMES = [
    ((10, 20, 60),   (50, 100, 220),  (255, 255, 255), (255, 200, 0)),
    ((60, 10, 10),   (220, 50, 50),   (255, 255, 255), (255, 220, 100)),
    ((10, 50, 20),   (50, 180, 80),   (255, 255, 255), (200, 255, 100)),
    ((40, 10, 60),   (160, 50, 220),  (255, 255, 255), (255, 150, 255)),
    ((60, 40, 10),   (220, 160, 50),  (255, 255, 255), (100, 220, 255)),
]


def _gradient(img: Image.Image, c1: tuple, c2: tuple) -> Image.Image:
    draw = ImageDraw.Draw(img)
    for y in range(THUMB_H):
        r = int(c1[0] + (c2[0] - c1[0]) * y / THUMB_H)
        g = int(c1[1] + (c2[1] - c1[1]) * y / THUMB_H)
        b = int(c1[2] + (c2[2] - c1[2]) * y / THUMB_H)
        draw.line([(0, y), (THUMB_W, y)], fill=(r, g, b))
    return img


def _try_font(size: int):
    from src.utils import get_font
    return get_font(size, bold=True)


def _draw_text_with_shadow(
    draw: ImageDraw.Draw,
    text: str,
    xy: tuple[int, int],
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    fill: tuple,
    shadow_offset: int = 4,
    align: str = "center",
    max_width: int = 900,
) -> int:
    """Draw text with drop shadow, wrapping at max_width. Returns text height."""
    # Word-wrap
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        test = (current + " " + word).strip()
        bbox = draw.textbbox((0, 0), test, font=font)
        if bbox[2] - bbox[0] <= max_width:
            current = test
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)

    x, y = xy
    total_h = 0
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        lw = bbox[2] - bbox[0]
        lh = bbox[3] - bbox[1]
        lx = x - lw // 2 if align == "center" else x
        # Shadow
        draw.text((lx + shadow_offset, y + shadow_offset), line, font=font, fill=(0, 0, 0, 180))
        draw.text((lx, y), line, font=font, fill=fill)
        y += lh + 10
        total_h += lh + 10
    return total_h


class ThumbnailGenerator:
    def generate(
        self,
        thumbnail_text: str,
        topic: str,
        slug: str,
        theme_idx: int = 0,
        base_image: Optional[Path] = None,
    ) -> Path:
        out_path = config.THUMBNAILS_DIR / f"{slug}_thumb.jpg"
        if out_path.exists():
            return out_path

        img = Image.new("RGB", (THUMB_W, THUMB_H))
        theme = THEMES[theme_idx % len(THEMES)]

        # Background: use base image or gradient
        if base_image and base_image.exists():
            try:
                bg = Image.open(base_image).convert("RGB")
                # Resize cover
                aw, ah = bg.size
                scale = max(THUMB_W / aw, THUMB_H / ah)
                new_size = (int(aw * scale), int(ah * scale))
                bg = bg.resize(new_size, Image.LANCZOS)
                left = (new_size[0] - THUMB_W) // 2
                top = (new_size[1] - THUMB_H) // 2
                img = bg.crop((left, top, left + THUMB_W, top + THUMB_H))
                # Dark overlay
                overlay = Image.new("RGBA", (THUMB_W, THUMB_H), (0, 0, 0, 150))
                img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
            except Exception:
                img = _gradient(img, theme[0], theme[1])
        else:
            img = _gradient(img, theme[0], theme[1])

        draw = ImageDraw.Draw(img, "RGBA")

        # Bold diagonal accent stripe across top-left
        draw.polygon([(0, 0), (THUMB_W, 0), (THUMB_W, 30), (0, 90)], fill=(*theme[3], 255))

        # VIRAL badge — top-right corner
        badge_font = _try_font(52)
        bx, by = THUMB_W - 320, 40
        draw.rounded_rectangle([(bx, by), (bx + 280, by + 76)], radius=18, fill=(220, 0, 0, 240))
        draw.text((bx + 140, by + 38), "VIRAL FACTS", font=badge_font, fill=(255,255,255), anchor="mm")

        # Main title — large, bold, centred, with heavy drop shadow
        title_font = _try_font(148)
        title_text = thumbnail_text.upper()
        _draw_text_with_shadow(
            draw, title_text, (THUMB_W // 2, 280),
            title_font, (255, 255, 255), shadow_offset=8
        )

        # Accent underline below title
        draw.rounded_rectangle([(120, 700), (THUMB_W - 120, 724)], radius=8, fill=(*theme[3], 255))

        # Topic label
        sub_font = _try_font(72)
        _draw_text_with_shadow(
            draw, topic.upper(), (THUMB_W // 2, 780),
            sub_font, (*theme[3], 255), shadow_offset=4
        )

        # "WATCH NOW" CTA box at bottom third
        cta_font = _try_font(64)
        cta_y = int(THUMB_H * 0.72)
        draw.rounded_rectangle(
            [(THUMB_W // 2 - 260, cta_y), (THUMB_W // 2 + 260, cta_y + 90)],
            radius=25, fill=(*theme[3], 240)
        )
        draw.text((THUMB_W // 2, cta_y + 45), "▶  WATCH NOW", font=cta_font,
                  fill=(0, 0, 0), anchor="mm")

        # Bottom: channel watermark
        wm_font = _try_font(48)
        draw.text((THUMB_W // 2, THUMB_H - 60), config.CHANNEL_WATERMARK,
                  font=wm_font, fill=(220, 220, 220), anchor="mm",
                  stroke_width=3, stroke_fill=(0, 0, 0, 200))

        img.save(out_path, "JPEG", quality=95)
        log.info("Thumbnail saved: %s", out_path.name)
        return out_path
