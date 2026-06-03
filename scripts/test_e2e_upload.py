"""
test_e2e_upload.py
Full end-to-end test: generates a real Short and uploads it to YouTube.
Bypasses Gemini (uses hardcoded script) so it works even when quota is zero.
"""

from __future__ import annotations
import sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from src.config import config
from src.database import init_db
from src.content_generator import VideoContent
from src.image_generator import ImageGenerator
from src.voiceover import VoiceoverGenerator
from src.video_generator import VideoGenerator
from src.thumbnail_generator import ThumbnailGenerator
from src.youtube_uploader import YouTubeUploader
from src.utils import sanitise_filename, get_logger

log = get_logger("e2e_test")

# ── Hardcoded content (no Gemini needed) ──────────────────────────────────────
CONTENT = VideoContent(
    topic="AI Revolution in India 2025",
    short_title="AI Is Changing India",
    youtube_title="AI Revolution in India 2025 — What You Need to Know! #shorts",
    youtube_description=(
        "Artificial Intelligence is transforming India at an incredible pace!\n\n"
        "From farmers using AI-powered crop predictions to students learning with "
        "personalised AI tutors — the revolution is real and it's happening right now.\n\n"
        "India is now home to over 1,000 AI startups and the government is investing "
        "billions in digital infrastructure. This is just the beginning!\n\n"
        "What do you think — is AI good or bad for India? Comment below!\n\n"
        "Follow for daily trending updates! 🔥 #AI #India #Technology #Shorts #Viral"
    ),
    script=(
        "Did you know India is now one of the fastest-growing AI nations in the world? "
        "In 2025, over one thousand AI startups are operating across Bengaluru, Hyderabad, "
        "and Mumbai — creating jobs and changing lives. "
        "Farmers in rural India are using AI apps to predict the best time to plant crops, "
        "saving thousands of rupees every season. "
        "Students are learning faster with AI tutors available 24/7 on their phones. "
        "Even doctors are using AI to diagnose diseases earlier than ever before. "
        "The Indian government has committed 10,000 crore rupees to AI research and development. "
        "This is not the future — this is happening right now, in your country. "
        "Follow this channel for daily trending topics that matter to you!"
    ),
    hashtags=["#AI", "#India", "#Technology", "#Shorts", "#Viral", "#IndiaFirst",
              "#AIRevolution", "#TechIndia", "#Trending", "#2025"],
    thumbnail_text="AI INDIA",
    image_prompts=[
        "Futuristic India cityscape with glowing AI holographic displays, night scene, 9:16",
        "Indian farmer using smartphone with AI crop prediction app, sunny field, 9:16",
        "Young Indian student learning with AI hologram teacher, modern classroom, 9:16",
        "Indian doctor using AI diagnosis screen in modern hospital, 9:16",
    ],
    input_tokens=0,
    output_tokens=0,
    cost_usd=0.0,
)

def main():
    print("=" * 55)
    print("  End-to-End Pipeline Test — Real YouTube Upload")
    print("=" * 55)

    init_db()
    slug = "e2e_test_" + str(int(time.time()))

    # Step 1: Images
    print("\n[1] Generating scene images (gradient fallback)…")
    image_gen = ImageGenerator()
    image_paths = image_gen.generate_scene_images(CONTENT.image_prompts, slug)
    print(f"    {len(image_paths)} images ready")

    # Step 2: Voiceover
    print("\n[2] Generating voiceover with gTTS…")
    voice_gen = VoiceoverGenerator()
    audio_path, subtitle_segments = voice_gen.generate(CONTENT.script, slug)
    print(f"    Audio saved: {audio_path.name}")
    print(f"    Subtitle segments: {len(subtitle_segments)}")

    # Step 3: Thumbnail
    print("\n[3] Generating thumbnail…")
    thumb_gen = ThumbnailGenerator()
    thumb_path = thumb_gen.generate(
        CONTENT.thumbnail_text, CONTENT.topic, slug,
        theme_idx=0, base_image=image_paths[0] if image_paths else None
    )
    print(f"    Thumbnail: {thumb_path.name}")

    # Step 4: Video
    print("\n[4] Rendering video with MoviePy (this takes ~60-90 seconds)…")
    video_gen = VideoGenerator()
    video_path = video_gen.generate(
        title=CONTENT.short_title,
        script=CONTENT.script,
        subtitle_segments=subtitle_segments,
        image_paths=image_paths,
        audio_path=audio_path,
        slug=slug,
    )
    size_mb = video_path.stat().st_size / 1_048_576
    print(f"    Video: {video_path.name} ({size_mb:.1f} MB)")

    # Step 5: Upload
    print("\n[5] Uploading to YouTube…")
    uploader = YouTubeUploader()
    result = uploader.upload(
        video_path=video_path,
        title=CONTENT.youtube_title,
        description=CONTENT.youtube_description,
        tags=CONTENT.hashtags,
        thumbnail_path=thumb_path,
        privacy_status="public",
    )

    print("\n" + "=" * 55)
    print("  PIPELINE COMPLETE!")
    print("=" * 55)
    print(f"  YouTube ID  : {result['id']}")
    print(f"  YouTube URL : {result['url']}")
    print(f"  Title       : {CONTENT.youtube_title[:60]}")
    print("=" * 55)
    return result

if __name__ == "__main__":
    main()
