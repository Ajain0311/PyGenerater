"""
Standalone smoke test for the kinetic pipeline (no Gemini / YouTube needed).

Runs the REAL voiceover (edge-tts, with word-level timing) and the REAL kinetic
renderer over canned content + whatever scene images already exist, then prints
the resulting MP4's duration/resolution.

    python scripts/test_kinetic.py
    python scripts/test_kinetic.py --hindi
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import config
from src.video_generator import VideoGenerator
from src.voiceover import VoiceoverGenerator

EN = dict(
    language="en",
    hook="India just broke a record",
    cta="Follow for more",
    script=(
        "India just broke a record nobody saw coming. "
        "For years, everyone said it was impossible. "
        "But here's the twist — it happened in under a minute. "
        "And the reason why will surprise you. "
        "Follow for more stories like this."
    ),
)

HI = dict(
    language="hinglish",
    hook="Ye record toot gaya",
    cta="Follow karo",
    script=(
        "India ne aaj ek aisa record toda jo kisi ne socha nahi tha. "
        "Sab kehte the ye impossible hai. "
        "Lekin twist ye hai — ye sirf ek minute mein hua. "
        "Aur iske peeche ki wajah aapko hairaan kar degi. "
        "Aise aur stories ke liye follow karo."
    ),
)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hindi", action="store_true", help="render the Hinglish sample")
    args = ap.parse_args()
    data = HI if args.hindi else EN

    slug = "_kinetic_test_hi" if args.hindi else "_kinetic_test_en"
    # Force a fresh render each run.
    for ext in (".mp4",):
        (config.VIDEOS_DIR / f"{slug}{ext}").unlink(missing_ok=True)
    (config.AUDIO_DIR / f"{slug}.mp3").unlink(missing_ok=True)
    (config.AUDIO_DIR / f"{slug}.timing.json").unlink(missing_ok=True)

    print(f"[1/3] edge-tts voiceover (lang={data['language']}) …")
    vo = VoiceoverGenerator()
    audio_path, segs = vo.generate(data["script"], slug, language=data["language"])
    print(f"      audio={audio_path.name}  words={len(segs)}  "
          f"span={segs[0]['start']:.2f}->{segs[-1]['end']:.2f}s")

    imgs = sorted(config.IMAGES_DIR.glob("*_scene_*.jpg"))[:4]
    print(f"[2/3] kinetic render (bg images={len(imgs)}) …")
    vg = VideoGenerator()
    out = vg.generate(
        title=data["hook"], script=data["script"], subtitle_segments=segs,
        image_paths=imgs, audio_path=audio_path, slug=slug,
        hook=data["hook"], cta=data["cta"], language=data["language"],
    )

    size_mb = out.stat().st_size / 1_048_576
    print(f"[3/3] done: {out}  ({size_mb:.1f} MB)")
    try:
        ff = __import__("imageio_ffmpeg").get_ffmpeg_exe()
        probe = subprocess.run(
            [ff, "-i", str(out)], capture_output=True, text=True)
        for line in probe.stderr.splitlines():
            if "Duration" in line or "Stream" in line:
                print("      " + line.strip())
    except Exception as e:
        print("      (ffprobe skipped:", e, ")")


if __name__ == "__main__":
    main()
