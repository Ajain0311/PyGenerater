"""
Pluggable image-generation backends (Phase 2).

A scene image can come from several sources; we try them in a configurable order
and fall back gracefully so a daily run NEVER stalls on image generation:

    auto      → ComfyUI → AUTOMATIC1111 → Gemini → gradient placeholder
    comfyui   → ComfyUI → Gemini → gradient
    a1111     → AUTOMATIC1111 → Gemini → gradient
    gemini    → Gemini → gradient
    gradient  → gradient only

Character consistency: each request carries a fixed `seed` (derived from the lead
character) and the kid-safe negative prompt, so the local SD backends reproduce
the same character look across shots. Cloud Gemini ignores the seed but still
uses the consistency tokens baked into the prompt by PromptAgent.

All heavy deps (PIL, requests, google-genai) are imported lazily INSIDE methods,
so this module loads anywhere and the selection/fallback logic is unit-testable.
"""

from __future__ import annotations

import io
import json
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.config import config
from src.content_policy import KID_SAFE_NEGATIVE
from src.utils import get_logger

log = get_logger("image_backends")


@dataclass
class ImageRequest:
    prompt: str
    negative: str = KID_SAFE_NEGATIVE
    seed: int = 0
    width: int = 1024
    height: int = 1024
    steps: int | None = None
    cfg: float | None = None

    def with_defaults(self) -> "ImageRequest":
        if self.steps is None:
            self.steps = config.SD_STEPS
        if self.cfg is None:
            self.cfg = config.SD_CFG
        return self


class ImageBackend(ABC):
    name: str = "backend"

    @abstractmethod
    def available(self) -> bool:
        """Cheap check — is this backend usable right now?"""

    @abstractmethod
    def generate(self, req: ImageRequest):
        """Return a PIL.Image (RGB) or None on failure."""


# ── Local: ComfyUI ───────────────────────────────────────────────────────────
class ComfyUIBackend(ImageBackend):
    name = "comfyui"

    def __init__(self, url: str | None = None, model: str | None = None):
        self.url = (url or config.COMFYUI_URL).rstrip("/")
        self.model = model or config.SD_MODEL

    def available(self) -> bool:
        # Needs both a reachable server AND a checkpoint name to build the graph.
        if not self.model:
            return False
        try:
            import requests
            requests.get(f"{self.url}/system_stats", timeout=3)
            return True
        except Exception:
            return False

    def _workflow(self, req: ImageRequest) -> dict[str, Any]:
        """A standard txt2img graph for the ComfyUI /prompt API."""
        return {
            "3": {"class_type": "KSampler", "inputs": {
                "seed": req.seed or 0, "steps": req.steps, "cfg": req.cfg,
                "sampler_name": "euler", "scheduler": "normal", "denoise": 1.0,
                "model": ["4", 0], "positive": ["6", 0],
                "negative": ["7", 0], "latent_image": ["5", 0]}},
            "4": {"class_type": "CheckpointLoaderSimple",
                  "inputs": {"ckpt_name": self.model}},
            "5": {"class_type": "EmptyLatentImage",
                  "inputs": {"width": req.width, "height": req.height, "batch_size": 1}},
            "6": {"class_type": "CLIPTextEncode",
                  "inputs": {"text": req.prompt, "clip": ["4", 1]}},
            "7": {"class_type": "CLIPTextEncode",
                  "inputs": {"text": req.negative, "clip": ["4", 1]}},
            "8": {"class_type": "VAEDecode",
                  "inputs": {"samples": ["3", 0], "vae": ["4", 2]}},
            "9": {"class_type": "SaveImage",
                  "inputs": {"filename_prefix": "kids", "images": ["8", 0]}},
        }

    def generate(self, req: ImageRequest):
        import requests
        from PIL import Image
        req.with_defaults()
        try:
            r = requests.post(f"{self.url}/prompt",
                              json={"prompt": self._workflow(req)}, timeout=15)
            r.raise_for_status()
            pid = r.json()["prompt_id"]
            deadline = time.time() + config.IMAGE_TIMEOUT
            while time.time() < deadline:
                hist = requests.get(f"{self.url}/history/{pid}", timeout=10).json()
                if pid in hist:
                    outs = hist[pid].get("outputs", {})
                    for node in outs.values():
                        for im in node.get("images", []):
                            data = requests.get(f"{self.url}/view", params={
                                "filename": im["filename"], "subfolder": im.get("subfolder", ""),
                                "type": im.get("type", "output")}, timeout=30).content
                            return Image.open(io.BytesIO(data)).convert("RGB")
                    break
                time.sleep(1.5)
            log.warning("ComfyUI: no image within timeout")
        except Exception as e:
            log.warning("ComfyUI generate failed: %s", e)
        return None


# ── Local: AUTOMATIC1111 / SD WebUI ──────────────────────────────────────────
class A1111Backend(ImageBackend):
    name = "a1111"

    def __init__(self, url: str | None = None):
        self.url = (url or config.A1111_URL).rstrip("/")

    def available(self) -> bool:
        try:
            import requests
            requests.get(f"{self.url}/sdapi/v1/sd-models", timeout=3)
            return True
        except Exception:
            return False

    def generate(self, req: ImageRequest):
        import base64
        import requests
        from PIL import Image
        req.with_defaults()
        try:
            payload = {
                "prompt": req.prompt, "negative_prompt": req.negative,
                "seed": req.seed or -1, "steps": req.steps, "cfg_scale": req.cfg,
                "width": req.width, "height": req.height, "sampler_name": "Euler",
            }
            r = requests.post(f"{self.url}/sdapi/v1/txt2img", json=payload,
                              timeout=config.IMAGE_TIMEOUT)
            r.raise_for_status()
            b64 = r.json()["images"][0]
            return Image.open(io.BytesIO(base64.b64decode(b64))).convert("RGB")
        except Exception as e:
            log.warning("A1111 generate failed: %s", e)
        return None


# ── Cloud: Gemini Imagen (free tier, fallback) ───────────────────────────────
class GeminiImageBackend(ImageBackend):
    name = "gemini"

    def available(self) -> bool:
        return bool(config.GEMINI_API_KEY)

    def generate(self, req: ImageRequest):
        from PIL import Image
        try:
            from google import genai
            from google.genai import types as gtypes
            client = genai.Client(api_key=config.GEMINI_API_KEY)
            result = client.models.generate_images(
                model=config.GEMINI_IMAGE_MODEL,
                prompt=f"vertical 9:16. {req.prompt}",
                config=gtypes.GenerateImagesConfig(
                    number_of_images=1, aspect_ratio="9:16",
                    safety_filter_level="BLOCK_LOW_AND_ABOVE"),
            )
            if result.generated_images:
                data = result.generated_images[0].image.image_bytes
                return Image.open(io.BytesIO(data)).convert("RGB")
        except Exception as e:
            log.warning("Gemini image failed: %s", e)
        return None


# ── Always-available placeholder ─────────────────────────────────────────────
class GradientBackend(ImageBackend):
    name = "gradient"
    _PALETTES = [((255, 209, 102), (255, 145, 77)), ((130, 200, 255), (90, 130, 246)),
                 ((180, 235, 150), (70, 190, 120)), ((214, 175, 255), (150, 100, 230)),
                 ((255, 180, 200), (245, 110, 150))]

    def available(self) -> bool:
        return True

    def generate(self, req: ImageRequest):
        import numpy as np
        from PIL import Image
        idx = (req.seed or 0) % len(self._PALETTES)
        c1, c2 = self._PALETTES[idx]
        h, w = req.height, req.width
        ramp = np.linspace(0.0, 1.0, h)[:, None]
        arr = np.zeros((h, w, 3), np.uint8)
        for ch in range(3):
            arr[:, :, ch] = (c1[ch] + (c2[ch] - c1[ch]) * ramp).astype(np.uint8)
        return Image.fromarray(arr, "RGB")


# ── Selection + fallback ─────────────────────────────────────────────────────
_REGISTRY = {
    "comfyui": ComfyUIBackend, "a1111": A1111Backend,
    "gemini": GeminiImageBackend, "gradient": GradientBackend,
}

_CHAINS = {
    "auto": ["comfyui", "a1111", "gemini", "gradient"],
    "comfyui": ["comfyui", "gemini", "gradient"],
    "a1111": ["a1111", "gemini", "gradient"],
    "gemini": ["gemini", "gradient"],
    "gradient": ["gradient"],
}


def select_backends(mode: str | None = None) -> list[ImageBackend]:
    mode = (mode or config.IMAGE_BACKEND or "auto").lower()
    order = _CHAINS.get(mode, _CHAINS["auto"])
    return [_REGISTRY[name]() for name in order]


def generate_one(req: ImageRequest, backends: list[ImageBackend]):
    """Try each available backend in order; return (image, backend_name) or
    (None, None)."""
    for b in backends:
        try:
            if not b.available():
                continue
        except Exception:
            continue
        img = b.generate(req)
        if img is not None:
            return img, b.name
    return None, None


def render_scene_images(
    prompts: list[str], slug: str, *,
    seeds: list[int] | None = None,
    negatives: list[str] | None = None,
    backends: list[ImageBackend] | None = None,
    out_dir: Path | None = None,
) -> list[Path]:
    """Generate one image per prompt with per-scene seed/negative, caching by
    file existence. Returns the saved paths (always one per prompt — gradient
    guarantees a result)."""
    backends = backends if backends is not None else select_backends()
    out_dir = out_dir or config.IMAGES_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    log.info("Image backends: %s", " → ".join(b.name for b in backends))
    for i, prompt in enumerate(prompts):
        out = out_dir / f"{slug}_scene_{i}.jpg"
        if out.exists() and out.stat().st_size > 0:
            paths.append(out)
            continue
        req = ImageRequest(
            prompt=prompt,
            negative=(negatives[i] if negatives and i < len(negatives) else KID_SAFE_NEGATIVE),
            seed=(seeds[i] if seeds and i < len(seeds) else 0),
            width=config.VIDEO_WIDTH, height=config.VIDEO_HEIGHT,
        )
        img, used = generate_one(req, backends)
        if img is None:  # should be unreachable (gradient always works)
            log.error("All backends failed for scene %d", i)
            continue
        img.save(out, "JPEG", quality=92)
        log.info("scene %d image via %s → %s", i, used, out.name)
        paths.append(out)
    return paths
