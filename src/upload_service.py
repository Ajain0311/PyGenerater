"""
Upload service — the single entry point for sending a rendered video to YouTube.

Used by BOTH the Streamlit dashboard (manual uploads) and the pipeline/auto job
(automatic uploads), so the scheduler timer is always updated consistently:

  * mode="manual" → upload immediately, no gate; stamps last_manual_at only.
  * mode="auto"   → enforce the 24h gate; on success stamps last_auto_at.

Keeping this in the backend means Streamlit only ever calls `upload_video()`,
never the uploader directly — no business logic in the UI.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from src.config import config
from src.utils import get_logger

log = get_logger("upload_service")


def upload_video(
    video_id: int, *, mode: str = "manual", session=None, scheduler=None,
) -> dict[str, Any]:
    """Upload an existing rendered Video row.

    Returns one of:
      {"uploaded": True, "id": ..., "url": ...}
      {"skipped": True, "reason": "..."}        # auto gate not yet open
    Raises on hard errors (missing file, auth, API failure).
    """
    from src.database import Video, VideoRepo, get_session
    from src.scheduler import UploadScheduler

    mode = mode.lower()
    if mode not in ("manual", "auto"):
        raise ValueError(f"mode must be 'manual' or 'auto', got {mode!r}")

    session = session or get_session()
    sched = scheduler or UploadScheduler(session)
    repo = VideoRepo(session)

    video = session.get(Video, video_id)
    if not video or not video.video_path or not Path(video.video_path).exists():
        raise FileNotFoundError(f"Video {video_id} has no rendered file to upload.")

    # AUTOMATIC uploads respect the 24h gate; MANUAL uploads never do.
    if mode == "auto":
        if config.DISABLE_AUTO_UPLOAD:
            log.info("Auto upload is disabled in config. Skipping upload for video %s.", video_id)
            return {"skipped": True, "reason": "Automatic upload is disabled in config"}
        allowed, reason = sched.can_auto_upload()
        if not allowed:
            log.info("Auto upload skipped for video %s: %s", video_id, reason)
            return {"skipped": True, "reason": reason}

    hashtags = video.hashtags_list
    description = (video.description or "").strip()
    if hashtags and not any(h in description for h in hashtags[:1]):
        description = (description + "\n\n" + " ".join(hashtags)).strip()
    made_for_kids = True if video.kind == "kids" else config.YOUTUBE_MADE_FOR_KIDS

    from src.youtube_uploader import YouTubeUploader
    repo.update_status(video_id, "uploading")
    log.info("Uploading video %s (%s, made_for_kids=%s)…", video_id, mode, made_for_kids)
    try:
        result = YouTubeUploader().upload(
            video_path=Path(video.video_path),
            title=(video.title or "Cartoon Short")[:100],
            description=description,
            tags=hashtags,
            thumbnail_path=Path(video.thumbnail_path) if video.thumbnail_path else None,
            made_for_kids=made_for_kids,
        )
    except Exception as e:
        repo.update_status(video_id, "upload_failed", error_message=str(e))
        raise

    repo.update_status(
        video_id, "uploaded",
        youtube_id=result.get("id"), youtube_url=result.get("url"),
        uploaded_at=datetime.utcnow(),
    )

    # Only a SUCCESSFUL automatic upload advances the 24h timer.
    if mode == "auto":
        sched.record_auto_upload(video_id=video_id)
    else:
        sched.record_manual_upload(video_id=video_id)

    log.info("Upload OK (%s): %s", mode, result.get("url"))
    return {"uploaded": True, "id": result.get("id"), "url": result.get("url")}
