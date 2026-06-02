"""Generate or fetch images for video scenes."""

from __future__ import annotations

import io
import time
from pathlib import Path
from typing import Optional

import requests
from PIL import Image, ImageDraw, ImageFilter

from src.config import config
from src.utils import api_retry, get_logger

log = get_logger(__name__)

GRADIENTS = [
    ((15, 32, 70),  (84, 120, 195)),
    ((70, 15, 30),  (195, 60, 90)),
    ((15, 70, 40),  (60, 195, 110)),
    ((70, 40, 15),  (195, 140, 60)),
    ((45, 15, 70),  (130, 60, 195)),
]

W, H = config.VIDEO_WIDTH, config.VIDEO_HEIGHT


def _make_gradient_bg(idx: int = 0) -> Image.Image:
    c1, c2 = GRADIENTS[idx % len(GRADIENTS)]
    img = Image.new("RGB", (W, H))
    draw = ImageDraw.Draw(img)
    for y in range(H):
        r = int(c1[0] + (c2[0] - c1[0]) * y / H)
        g = int(c1[1] + (c2[1] - c1[1]) * y / H)
        b = int(c1[2] + (c2[2] - c1[2]) * y / H)
        draw.line([(0, y), (W, y)], fill=(r, g, b))
    return img


def _overlay_dark(img: Image.Image, alpha: int = 120) -> Image.Image:
    overlay = Image.new("RGBA", img.size, (0, 0, 0, alpha))
    base = img.convert("RGBA")
    combined = Image.alpha_composite(base, overlay)
    return combined.convert("RGB")


def _resize_cover(img: Image.Image, w: int = W, h: int = H) -> Image.Image:
    aspect = w / h
    iw, ih = img.size
    if iw / ih > aspect:
        new_h = h
        new_w = int(iw * h / ih)
    else:
        new_w = w
        new_h = int(ih * w / iw)
    img = img.resize((new_w, new_h), Image.LANCZOS)
    left = (new_w - w) // 2
    top = (new_h - h) // 2
    return img.crop((left, top, left + w, top + h))


class ImageGenerator:
    def generate_scene_images(self, prompts: list[str], topic_slug: str) -> list[Path]:
        images: list[Path] = []
        for idx, prompt in enumerate(prompts):
            out_path = config.IMAGES_DIR / f"{topic_slug}_scene_{idx}.jpg"
            if out_path.exists():
                images.append(out_path)
                continue
            img = self._get_image(prompt, idx)
            img.save(out_path, "JPEG", quality=92)
            images.append(out_path)
            log.info("Scene %d image saved: %s", idx, out_path.name)
            time.sleep(0.3)
        return images

    def _get_image(self, prompt: str, idx: int) -> Image.Image:
        if config.GEMINI_API_KEY:
            img = self._try_gemini_imagen(prompt)
            if img:
                return _overlay_dark(_resize_cover(img))

        if config.UNSPLASH_ACCESS_KEY:
            img = self._try_unsplash(prompt)
            if img:
                return _overlay_dark(_resize_cover(img))

        log.warning("Using gradient fallback for scene %d", idx)
        return _overlay_dark(_make_gradient_bg(idx))

    @api_retry(max_attempts=2, wait_min=3, wait_max=10)
    def _try_gemini_imagen(self, prompt: str) -> Optional[Image.Image]:
        try:
            from google import genai
            from google.genai import types as gtypes
            client = genai.Client(api_key=config.GEMINI_API_KEY)
            result = client.models.generate_images(
                model=config.GEMINI_IMAGE_MODEL,
                prompt=f"Photorealistic, vertical 9:16 format. {prompt}",
                config=gtypes.GenerateImagesConfig(
                    number_of_images=1,
                    aspect_ratio="9:16",
                    safety_filter_level="BLOCK_SOME",
                ),
            )
            if result.generated_images:
                img_bytes = result.generated_images[0].image.image_bytes
                return Image.open(io.BytesIO(img_bytes))
        except Exception as e:
            log.debug("Gemini imagen failed: %s", e)
        return None

    @api_retry(max_attempts=2, wait_min=2, wait_max=8)
    def _try_unsplash(self, prompt: str) -> Optional[Image.Image]:
        try:
            query = " ".join(prompt.split()[:5])
            resp = requests.get(
                "https://api.unsplash.com/photos/random",
                params={"query": query, "orientation": "portrait", "content_filter": "high",
                        "client_id": config.UNSPLASH_ACCESS_KEY},
                timeout=10,
            )
            resp.raise_for_status()
            photo_url = resp.json()["urls"]["regular"]
            img_resp = requests.get(photo_url, timeout=15)
            img_resp.raise_for_status()
            return Image.open(io.BytesIO(img_resp.content))
        except Exception as e:
            log.debug("Unsplash fetch failed: %s", e)
        return None
