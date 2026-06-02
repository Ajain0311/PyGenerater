"""
main.py — YouTube Shorts Auto-Generator
Orchestrates the full pipeline: trends → content → images → voiceover → video → upload.
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

from src.config import config
from src.database import (
    AnalyticsRepo,
    TopicRepo,
    VideoRepo,
    get_session,
    init_db,
)
from src.utils import get_logger, sanitise_filename

log = get_logger(__name__)


def run_pipeline(
    topic_keyword: str | None = None,
    n_shorts: int | None = None,
    skip_upload: bool = False,
    dry_run: bool = False,
) -> list[str]:
    """
    Run the full pipeline.
    Returns list of YouTube video IDs (or empty list on dry_run/failure).
    """
    n = n_shorts or config.SHORTS_PER_RUN
    uploaded_ids: list[str] = []

    session = get_session()
    topic_repo = TopicRepo(session)
    video_repo = VideoRepo(session)
    analytics_repo = AnalyticsRepo(session)

    # ── 1. Fetch trending topics ───────────────────────────────────────────
    if topic_keyword:
        # Manual topic override
        topics_to_process: list[dict] = [
            {"keyword": topic_keyword, "score": 100.0, "category": "manual", "geo": config.TRENDS_GEO}
        ]
        log.info("Manual topic override: %r", topic_keyword)
    else:
        log.info("Fetching trending topics (geo=%s)…", config.TRENDS_GEO)
        from src.trends import TrendsFetcher
        fetcher = TrendsFetcher()
        existing = topic_repo.all_keywords()
        raw_topics = fetcher.get_scored_topics(exclude_keywords=existing)

        if not raw_topics:
            log.warning("No new topics found. Exiting.")
            return []

        inserted = topic_repo.bulk_insert_new(raw_topics)
        analytics_repo.increment("topics_fetched", inserted)
        log.info("Inserted %d new topics into DB", inserted)

        topics_to_process = [
            {"keyword": t.keyword, "score": t.score, "category": t.category, "id": t.id}
            for t in topic_repo.get_unprocessed(limit=n)
        ]

    if not topics_to_process:
        log.info("No unprocessed topics available.")
        return []

    # ── 2–6. Generate shorts ───────────────────────────────────────────────
    from src.content_generator import ContentGenerator
    from src.image_generator import ImageGenerator
    from src.voiceover import VoiceoverGenerator
    from src.video_generator import VideoGenerator
    from src.thumbnail_generator import ThumbnailGenerator
    from src.youtube_uploader import YouTubeUploader

    content_gen = ContentGenerator()
    image_gen = ImageGenerator()
    voice_gen = VoiceoverGenerator()
    video_gen = VideoGenerator()
    thumb_gen = ThumbnailGenerator()
    uploader = YouTubeUploader() if not skip_upload else None

    for idx, topic_data in enumerate(topics_to_process[:n]):
        keyword = topic_data["keyword"]
        topic_db_id = topic_data.get("id")
        log.info("─" * 60)
        log.info("[%d/%d] Processing topic: %r", idx + 1, min(n, len(topics_to_process)), keyword)

        slug = sanitise_filename(keyword) + f"_{int(time.time())}"
        video_record = video_repo.create(
            topic_id=topic_db_id,
            title=keyword,
            status="generating",
        )

        try:
            # ── Content generation ────────────────────────────────────────
            log.info("Generating content with Gemini…")
            content = content_gen.generate(keyword)

            # ── Image generation ──────────────────────────────────────────
            log.info("Generating scene images…")
            image_paths = image_gen.generate_scene_images(content.image_prompts, slug)

            # ── Voiceover ─────────────────────────────────────────────────
            log.info("Generating voiceover…")
            audio_path, subtitle_segments = voice_gen.generate(content.script, slug)

            # ── Thumbnail ─────────────────────────────────────────────────
            log.info("Generating thumbnail…")
            base_img = image_paths[0] if image_paths else None
            thumb_path = thumb_gen.generate(
                content.thumbnail_text,
                keyword,
                slug,
                theme_idx=idx,
                base_image=base_img,
            )

            if dry_run:
                log.info("DRY RUN — skipping video render and upload.")
                video_repo.update_status(video_record.id, "generated",
                                         title=content.youtube_title,
                                         description=content.youtube_description,
                                         script=content.script,
                                         audio_path=str(audio_path),
                                         thumbnail_path=str(thumb_path))
                if topic_db_id:
                    topic_repo.mark_processed(topic_db_id)
                continue

            # ── Video generation ──────────────────────────────────────────
            log.info("Rendering video with MoviePy…")
            video_path = video_gen.generate(
                title=content.short_title,
                script=content.script,
                subtitle_segments=subtitle_segments,
                image_paths=image_paths,
                audio_path=audio_path,
                slug=slug,
            )

            # Update DB with generated paths
            video_repo.update_status(
                video_record.id,
                "generated",
                title=content.youtube_title,
                description=content.youtube_description,
                script=content.script,
                hashtags=str(content.hashtags),
                thumbnail_text=content.thumbnail_text,
                audio_path=str(audio_path),
                video_path=str(video_path),
                thumbnail_path=str(thumb_path),
                gemini_input_tokens=content.input_tokens,
                gemini_output_tokens=content.output_tokens,
                estimated_cost_usd=content.cost_usd,
            )
            analytics_repo.increment("videos_generated")
            analytics_repo.increment("total_cost_usd", content.cost_usd)

            # ── YouTube Upload ────────────────────────────────────────────
            if skip_upload or not uploader:
                log.info("Upload skipped (--skip-upload flag).")
            else:
                log.info("Uploading to YouTube…")
                video_repo.update_status(video_record.id, "uploading")
                try:
                    result = uploader.upload(
                        video_path=video_path,
                        title=content.youtube_title,
                        description=content.youtube_description,
                        tags=content.hashtags,
                        thumbnail_path=thumb_path,
                    )
                    youtube_id = result["id"]
                    youtube_url = result["url"]
                    video_repo.update_status(
                        video_record.id,
                        "uploaded",
                        youtube_id=youtube_id,
                        youtube_url=youtube_url,
                        uploaded_at=datetime.utcnow(),
                    )
                    analytics_repo.increment("videos_uploaded")
                    uploaded_ids.append(youtube_id)
                    log.info("Uploaded! URL: %s", youtube_url)
                except Exception as e:
                    log.error("Upload failed: %s", e)
                    video_repo.update_status(video_record.id, "upload_failed", error_message=str(e))
                    analytics_repo.increment("api_errors")

            if topic_db_id:
                topic_repo.mark_processed(topic_db_id)

        except Exception as exc:
            log.error("Pipeline failed for topic %r: %s", keyword, exc, exc_info=True)
            video_repo.update_status(video_record.id, "failed", error_message=str(exc))
            analytics_repo.increment("api_errors")

    log.info("─" * 60)
    log.info("Pipeline complete. Uploaded %d video(s).", len(uploaded_ids))
    return uploaded_ids


def main() -> None:
    parser = argparse.ArgumentParser(
        description="YouTube Shorts Auto-Generator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py                          # Auto-fetch trending topics and generate
  python main.py --topic "AI Revolution"  # Generate for a specific topic
  python main.py --n 3                    # Generate 3 shorts
  python main.py --skip-upload            # Generate only, don't upload
  python main.py --dry-run                # Content generation only, no video render
""",
    )
    parser.add_argument("--topic", type=str, default=None, help="Manual topic override")
    parser.add_argument("--n", type=int, default=None, help="Number of shorts to generate")
    parser.add_argument("--skip-upload", action="store_true", help="Generate video but skip YouTube upload")
    parser.add_argument("--dry-run", action="store_true", help="Run content generation only, skip video and upload")
    args = parser.parse_args()

    log.info("=" * 60)
    log.info("  YouTube Shorts Auto-Generator  v1.0")
    log.info("  %s", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    log.info("=" * 60)

    # Validate config
    missing = config.validate()
    if missing:
        log.error("Missing required env vars: %s", ", ".join(missing))
        log.error("Please check your .env file or environment variables.")
        sys.exit(1)

    # Init database
    init_db()

    # Run pipeline
    try:
        ids = run_pipeline(
            topic_keyword=args.topic,
            n_shorts=args.n,
            skip_upload=args.skip_upload,
            dry_run=args.dry_run,
        )
        if ids:
            log.info("Successfully uploaded %d short(s):", len(ids))
            for vid_id in ids:
                log.info("  → https://www.youtube.com/shorts/%s", vid_id)
        sys.exit(0)
    except KeyboardInterrupt:
        log.info("Interrupted by user.")
        sys.exit(0)
    except Exception as e:
        log.critical("Unhandled error: %s", e, exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
