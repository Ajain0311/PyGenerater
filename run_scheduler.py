"""
run_scheduler.py — local auto-upload daemon for the kids channel.

Periodically checks the 24h automatic-upload gate and, when the window is open,
generates ONE kids Short and uploads it (mode="auto", which advances the timer
only on success). Manual uploads from the dashboard are completely unaffected.

This is OPTIONAL. The simplest alternative is Windows Task Scheduler running
`python generate_kids.py --auto` every few hours — the --auto gate makes that
safe to run as often as you like (it just skips until 24h have passed).

    python run_scheduler.py            # check hourly, generate when due
    python run_scheduler.py --once     # one gate-checked attempt, then exit
"""

from __future__ import annotations

import argparse
import time

from src.scheduler import UploadScheduler, fmt_duration
from src.utils import get_logger

log = get_logger("run_scheduler")

CHECK_EVERY_SECONDS = 3600   # cheap DB read; the 24h gate does the real limiting


def attempt_once() -> bool:
    """If the auto window is open, generate + upload one Short. Returns True if
    a run was started."""
    sched = UploadScheduler()
    ok, why = sched.can_auto_upload()
    if not ok:
        secs = sched.seconds_until_next_auto()
        log.info("Not due — %s (next in %s)", why, fmt_duration(secs))
        return False
    log.info("Auto-upload window OPEN — generating today's Short…")
    from src.pipeline import Orchestrator
    result = Orchestrator().run(upload_mode="auto")
    log.info("Run %s → %s | %s", result.run_uid, result.status.upper(),
             result.youtube_url or "(no upload)")
    return True


def main() -> None:
    ap = argparse.ArgumentParser(description="Local kids auto-upload scheduler")
    ap.add_argument("--once", action="store_true", help="one gate-checked attempt then exit")
    ap.add_argument("--every", type=int, default=CHECK_EVERY_SECONDS,
                    help="seconds between gate checks (default 3600)")
    args = ap.parse_args()

    from src.config import config
    import sys

    if config.DISABLE_AUTO_UPLOAD:
        log.warning("Automatic YouTube upload is disabled (DISABLE_AUTO_UPLOAD=True). Scheduler will not run.")
        sys.exit(0)

    if args.once:
        attempt_once()
        return

    log.info("Local scheduler started — checking every %s; uploads at most once/24h.",
             fmt_duration(args.every))
    try:
        from apscheduler.schedulers.blocking import BlockingScheduler  # optional
        sched = BlockingScheduler()
        sched.add_job(_safe_tick, "interval", seconds=args.every)
        _safe_tick()  # run an immediate check on start
        sched.start()
    except ImportError:
        log.info("(apscheduler not installed — using a simple loop)")
        while True:
            _safe_tick()
            time.sleep(args.every)


def _safe_tick() -> None:
    try:
        attempt_once()
    except Exception as e:  # never let one bad run kill the daemon
        log.error("Scheduled attempt failed: %s", e, exc_info=True)


if __name__ == "__main__":
    main()
