"""
generate_kids.py — produce ONE high-quality kids-cartoon Short.

This is the new production entrypoint (the legacy `main.py` trending pipeline is
left intact). All real work lives in src/pipeline.Orchestrator; this file is
just a thin CLI over it.

Examples:
  python generate_kids.py                       # auto: plan→…→upload one Short
  python generate_kids.py --category funny      # force a category
  python generate_kids.py --dry-run             # content only (no media/upload)
  python generate_kids.py --skip-upload         # render but don't upload
  python generate_kids.py --resume <run_uid>    # continue a crashed run
  python generate_kids.py --regenerate RENDER --run <run_uid>   # redo one step
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime

from src.config import config
from src.utils import get_logger

log = get_logger("generate_kids")


def _progress(step: str, status: str) -> None:
    icon = {"running": "▶", "done": "✓", "failed": "✗", "skipped": "·"}.get(status, "·")
    log.info("  %s %-14s %s", icon, step, status)


def main() -> None:
    p = argparse.ArgumentParser(description="Generate ONE kids-cartoon YouTube Short")
    p.add_argument("--category", default=None, help="funny|school|animals|magic|friendship|jungle|bedtime")
    p.add_argument("--topic", default=None, help="optional story seed/idea")
    p.add_argument("--language", default=None, help="hi|hinglish|en (default from config)")
    p.add_argument("--scenes", type=int, default=None, help="number of visual scenes")
    p.add_argument("--seconds", type=int, default=None, help="target spoken length")
    p.add_argument("--dry-run", action="store_true", help="content only; no media render/upload")
    p.add_argument("--skip-upload", action="store_true", help="render video but don't upload")
    p.add_argument("--auto", action="store_true",
                   help="scheduled automatic run — respects the 24h auto-upload gate")
    p.add_argument("--resume", metavar="UID", default=None, help="resume a prior run by uid")
    p.add_argument("--regenerate", metavar="STEP", default=None, help="redo STEP (and downstream)")
    p.add_argument("--run", metavar="UID", default=None, help="run uid for --regenerate")
    args = p.parse_args()

    log.info("=" * 60)
    log.info("  Kids Cartoon Shorts — ONE quality video")
    log.info("  %s | lang=%s", datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
             args.language or config.KIDS_LANGUAGE)
    log.info("=" * 60)

    missing = config.validate()
    if missing and not args.dry_run:
        log.warning("Missing config: %s (content needs GEMINI_API_KEY)", ", ".join(missing))

    if args.auto and config.DISABLE_AUTO_UPLOAD:
        log.info("Automatic YouTube upload is disabled (DISABLE_AUTO_UPLOAD=True). Setting --skip-upload.")
        args.skip_upload = True

    # In automatic mode, check the 24h gate BEFORE spending compute generating a
    # video that couldn't be uploaded anyway.
    if args.auto and not (args.dry_run or args.skip_upload):
        from src.scheduler import UploadScheduler
        ok, reason = UploadScheduler().can_auto_upload()
        if not ok:
            log.info("Automatic run skipped — %s", reason)
            sys.exit(0)

    from src.pipeline import Orchestrator
    orch = Orchestrator()

    common = dict(
        category=args.category, topic=args.topic, language=args.language,
        scene_count=args.scenes, target_seconds=args.seconds,
        dry=args.dry_run, skip_upload=args.skip_upload,
        upload_mode=("auto" if args.auto else "manual"), progress=_progress,
    )

    if args.regenerate:
        if not args.run:
            log.error("--regenerate requires --run <uid>")
            sys.exit(2)
        result = orch.regenerate_step(args.run, args.regenerate, **common)
    elif args.resume:
        result = orch.resume(args.resume, **common)
    else:
        result = orch.run(**common)

    log.info("-" * 60)
    log.info("Run %s → %s | cost=$%.5f", result.run_uid, result.status.upper(), result.cost_usd)
    if result.package:
        log.info("Title: %s", result.package.title)
        log.info("Hook : %s", result.package.hook)
        log.info("Scenes: %d | Script words: %d",
                 len(result.package.scenes), len(result.package.script.split()))
    if result.video_path:
        log.info("Video: %s", result.video_path)
    if result.youtube_url:
        log.info("YouTube: %s", result.youtube_url)
    if result.error:
        log.error("Error: %s", result.error)

    sys.exit(0 if result.status in ("done", "partial") else 1)


if __name__ == "__main__":
    main()
