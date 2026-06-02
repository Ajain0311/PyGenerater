"""
test_pipeline.py — Quick integration test for each pipeline stage.
Run this to verify all dependencies are working before full automation.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _ok(msg: str): print(f"  ✅ {msg}")
def _fail(msg: str, err: str): print(f"  ❌ {msg}: {err}")


def test_config():
    print("\n[1] Configuration")
    from src.config import config
    missing = config.validate()
    if missing:
        _fail("Config", f"Missing env vars: {missing}")
    else:
        _ok(f"Config valid | geo={config.TRENDS_GEO} | model={config.GEMINI_MODEL}")


def test_database():
    print("\n[2] Database")
    try:
        from src.database import init_db, get_session, TopicRepo
        init_db()
        session = get_session()
        count = session.query(__import__("src.database", fromlist=["Topic"]).Topic).count()
        session.close()
        _ok(f"Database OK | {count} topics stored")
    except Exception as e:
        _fail("Database", str(e))


def test_trends():
    print("\n[3] Google Trends")
    try:
        from src.trends import TrendsFetcher
        fetcher = TrendsFetcher()
        topics = fetcher._fetch_daily("IN")
        if topics:
            _ok(f"Trends OK | {len(topics)} topics | sample: {topics[0]['keyword']!r}")
        else:
            _fail("Trends", "No topics returned")
    except Exception as e:
        _fail("Trends", str(e))


def test_gemini():
    print("\n[4] Gemini API")
    try:
        import google.generativeai as genai
        from src.config import config
        genai.configure(api_key=config.GEMINI_API_KEY)
        model = genai.GenerativeModel("gemini-1.5-flash")
        resp = model.generate_content("Say 'ok' in 1 word.")
        _ok(f"Gemini API OK | response: {resp.text.strip()!r}")
    except Exception as e:
        _fail("Gemini", str(e))


def test_tts():
    print("\n[5] Text-to-Speech (gTTS)")
    try:
        from gtts import gTTS
        from src.config import config
        tts = gTTS("Testing voiceover generation.", lang="en", tld="co.in")
        test_path = config.AUDIO_DIR / "_test_tts.mp3"
        tts.save(str(test_path))
        if test_path.exists():
            _ok(f"gTTS OK | saved to {test_path.name}")
            test_path.unlink()
        else:
            _fail("gTTS", "File not created")
    except Exception as e:
        _fail("gTTS", str(e))


def test_pillow():
    print("\n[6] Pillow (Image)")
    try:
        from PIL import Image, ImageDraw
        img = Image.new("RGB", (100, 100), (255, 0, 0))
        _ok(f"Pillow OK | version {Image.__version__}")
    except Exception as e:
        _fail("Pillow", str(e))


def test_moviepy():
    print("\n[7] MoviePy")
    try:
        import moviepy
        from moviepy.editor import ColorClip
        clip = ColorClip((100, 100), color=(0, 255, 0), duration=0.1)
        clip.close()
        _ok(f"MoviePy OK | version {moviepy.__version__}")
    except Exception as e:
        _fail("MoviePy", str(e))


def test_youtube():
    print("\n[8] YouTube API")
    try:
        from src.youtube_uploader import YouTubeUploader
        uploader = YouTubeUploader()
        info = uploader.get_channel_info()
        if info:
            _ok(f"YouTube API OK | channel: {info.get('title')!r}")
        else:
            _fail("YouTube API", "No channel info (check credentials)")
    except Exception as e:
        _fail("YouTube API", str(e))


def main():
    print("=" * 50)
    print("  Pipeline Integration Test")
    print("=" * 50)

    test_config()
    test_database()
    test_trends()
    test_gemini()
    test_tts()
    test_pillow()
    test_moviepy()
    test_youtube()

    print("\nDone. Fix any ❌ items before running the full pipeline.")


if __name__ == "__main__":
    main()
