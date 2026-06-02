"""
Streamlit Dashboard — YouTube Shorts Auto-Generator
Provides manual control, monitoring, and analytics UI.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from datetime import datetime, date
from pathlib import Path
from typing import Any

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

from src.config import config
from src.database import (
    AnalyticsRepo,
    TopicRepo,
    VideoRepo,
    init_db,
    get_session,
)
from src.utils import get_logger

log = get_logger("dashboard")

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Shorts Generator Dashboard",
    page_icon="🎬",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Custom CSS ────────────────────────────────────────────────────────────────
st.markdown(
    """
<style>
  .stMetric { background: #1e1e2e; border-radius: 12px; padding: 16px; }
  .status-badge {
    display: inline-block; padding: 3px 10px; border-radius: 20px;
    font-size: 12px; font-weight: 600;
  }
  .status-uploaded   { background: #1a4731; color: #4ade80; }
  .status-generated  { background: #1a3a4a; color: #60a5fa; }
  .status-generating { background: #3a2e10; color: #fbbf24; }
  .status-failed     { background: #3a1a1a; color: #f87171; }
  .status-pending    { background: #2a2a3a; color: #a0a0b0; }
  div[data-testid="stSidebar"] { background: #0f0f1a; }
</style>
""",
    unsafe_allow_html=True,
)


# ── Init DB on first load ─────────────────────────────────────────────────────
@st.cache_resource
def _init():
    init_db()
    return True

_init()


# ── Sidebar navigation ────────────────────────────────────────────────────────
def sidebar():
    st.sidebar.image("https://img.icons8.com/color/96/youtube-shorts.png", width=60)
    st.sidebar.title("Shorts Generator")
    st.sidebar.markdown(f"**Channel:** {config.CHANNEL_NAME}")
    st.sidebar.divider()

    page = st.sidebar.radio(
        "Navigation",
        ["🏠 Dashboard", "🔍 Trending Topics", "🎬 Create Video", "📤 Upload Manager", "📊 Analytics", "⚙️ Settings"],
        label_visibility="collapsed",
    )
    st.sidebar.divider()
    st.sidebar.caption(f"Last refresh: {datetime.now().strftime('%H:%M:%S')}")
    return page


# ─────────────────────────────────────────────────────────────────────────────
#  Pages
# ─────────────────────────────────────────────────────────────────────────────

def page_dashboard():
    st.title("🏠 Dashboard Overview")
    session = get_session()
    video_repo = VideoRepo(session)
    analytics_repo = AnalyticsRepo(session)

    counts = video_repo.count_by_status()
    total = sum(counts.values())
    uploaded = counts.get("uploaded", 0)
    generated = counts.get("generated", 0)
    failed = counts.get("failed", 0)

    today_analytics = analytics_repo.today()

    # ── KPI cards ─────────────────────────────────────────────────────────
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Total Videos", total)
    c2.metric("Uploaded", uploaded, delta=f"+{today_analytics.videos_uploaded} today")
    c3.metric("Ready to Upload", generated)
    c4.metric("Failed", failed)
    c5.metric("Est. Cost Today", f"${today_analytics.total_cost_usd:.4f}")

    st.divider()

    # ── Recent videos ──────────────────────────────────────────────────────
    col_left, col_right = st.columns([2, 1])

    with col_left:
        st.subheader("Recent Videos")
        videos = video_repo.get_all(limit=10)
        if videos:
            for v in videos:
                status_class = f"status-{v.status.replace('_', '-')}"
                badge = f'<span class="status-badge {status_class}">{v.status.upper()}</span>'
                with st.container():
                    cols = st.columns([3, 1, 1])
                    cols[0].markdown(f"**{v.title or 'Untitled'}** {badge}", unsafe_allow_html=True)
                    cols[1].caption(v.created_at.strftime("%m/%d %H:%M") if v.created_at else "—")
                    if v.youtube_url:
                        cols[2].link_button("▶ Watch", v.youtube_url)
                    elif v.status == "generated":
                        if cols[2].button("Upload", key=f"upload_{v.id}"):
                            _upload_single(v.id)
        else:
            st.info("No videos yet. Go to **Create Video** to get started.")

    with col_right:
        st.subheader("Status Breakdown")
        if counts:
            fig = px.pie(
                names=list(counts.keys()),
                values=list(counts.values()),
                color_discrete_sequence=px.colors.qualitative.Set3,
                hole=0.4,
            )
            fig.update_layout(showlegend=True, height=280, margin=dict(t=0, b=0))
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("No data yet.")

    session.close()


def page_trending_topics():
    st.title("🔍 Trending Topics")

    tab1, tab2 = st.tabs(["Live Trends", "Database Topics"])

    with tab1:
        st.subheader("Fetch Live Trending Topics")
        geo = st.selectbox("Region", ["IN", "US", "GB", "AU", "CA", "SG"], index=0)
        col1, col2 = st.columns([1, 4])
        fetch_btn = col1.button("🔄 Fetch Now", type="primary")

        if fetch_btn or st.session_state.get("last_trends"):
            with st.spinner("Fetching from Google Trends…"):
                try:
                    from src.trends import TrendsFetcher
                    fetcher = TrendsFetcher()
                    session = get_session()
                    existing = TopicRepo(session).all_keywords()
                    topics = fetcher.get_scored_topics(exclude_keywords=existing, geo=geo)
                    st.session_state["last_trends"] = topics
                    session.close()
                except Exception as e:
                    st.error(f"Failed to fetch trends: {e}")
                    topics = []

            topics = st.session_state.get("last_trends", [])
            if topics:
                st.success(f"Found {len(topics)} trending topics")
                df = pd.DataFrame(topics)[["keyword", "score", "category", "source"]].rename(
                    columns={"keyword": "Topic", "score": "Score", "category": "Category", "source": "Source"}
                )
                st.dataframe(df, use_container_width=True, hide_index=True)

                col_a, col_b = st.columns(2)
                if col_a.button("💾 Save All to Database"):
                    session = get_session()
                    inserted = TopicRepo(session).bulk_insert_new(topics)
                    session.close()
                    st.success(f"Saved {inserted} new topics to database.")

                selected_topic = col_b.selectbox(
                    "Select topic to create video",
                    [t["keyword"] for t in topics],
                    key="trend_select",
                )
                if st.button("🎬 Create Video for Selected Topic"):
                    st.session_state["create_topic"] = selected_topic
                    st.switch_page("pages/create_video.py") if False else None
                    st.session_state["nav_page"] = "🎬 Create Video"
                    st.rerun()

    with tab2:
        st.subheader("Saved Topics")
        session = get_session()
        topics = TopicRepo(session).get_all(limit=200)
        session.close()

        if topics:
            df_data = [
                {
                    "ID": t.id,
                    "Topic": t.keyword,
                    "Score": round(t.score or 0, 1),
                    "Category": t.category or "—",
                    "Processed": "✅" if t.is_processed else "⏳",
                    "Uploaded": "✅" if t.is_uploaded else "—",
                    "Added": t.created_at.strftime("%m/%d %H:%M") if t.created_at else "—",
                }
                for t in topics
            ]
            df = pd.DataFrame(df_data)

            col1, col2 = st.columns([2, 1])
            search = col1.text_input("🔍 Search topics", placeholder="Type to filter…")
            show_unprocessed = col2.checkbox("Show unprocessed only", value=False)

            if search:
                df = df[df["Topic"].str.contains(search, case=False, na=False)]
            if show_unprocessed:
                df = df[df["Processed"] == "⏳"]

            st.dataframe(df, use_container_width=True, hide_index=True, height=400)
            st.caption(f"Showing {len(df)} of {len(topics)} topics")
        else:
            st.info("No topics in database. Fetch trending topics above.")


def page_create_video():
    st.title("🎬 Create Video")

    st.markdown("Generate a complete YouTube Short from any topic.")

    col1, col2 = st.columns([3, 1])
    topic_input = col1.text_input(
        "Topic / Keyword",
        value=st.session_state.get("create_topic", ""),
        placeholder="e.g. India wins cricket World Cup",
    )
    n_shorts = col2.number_input("# of Shorts", min_value=1, max_value=5, value=1)

    with st.expander("⚙️ Advanced Options"):
        skip_upload = st.checkbox("Skip YouTube upload (generate only)", value=False)
        dry_run = st.checkbox("Dry run (content only, no video render)", value=False)
        privacy = st.selectbox("Privacy Status", ["public", "unlisted", "private"], index=0)

    if st.button("🚀 Generate & Upload", type="primary", disabled=not topic_input.strip()):
        _run_pipeline_ui(
            topic_keyword=topic_input.strip() if topic_input.strip() else None,
            n_shorts=int(n_shorts),
            skip_upload=skip_upload,
            dry_run=dry_run,
        )

    st.divider()
    st.subheader("Quick Generate from Saved Topics")
    session = get_session()
    unprocessed = TopicRepo(session).get_unprocessed(limit=20)
    session.close()

    if unprocessed:
        topic_options = {f"#{t.id} — {t.keyword} (score: {t.score:.0f})": t.keyword for t in unprocessed}
        selected = st.selectbox("Select unprocessed topic", list(topic_options.keys()))
        if st.button("🎬 Generate for Selected"):
            _run_pipeline_ui(
                topic_keyword=topic_options[selected],
                n_shorts=1,
                skip_upload=False,
                dry_run=False,
            )
    else:
        st.info("No unprocessed topics. Fetch trending topics first.")


def _run_pipeline_ui(topic_keyword, n_shorts, skip_upload, dry_run):
    """Run pipeline with real-time Streamlit progress display."""
    from main import run_pipeline

    progress_bar = st.progress(0, text="Initialising…")
    status_area = st.empty()
    log_area = st.empty()

    steps = [
        (10, "Generating AI content with Gemini…"),
        (30, "Creating scene images…"),
        (50, "Generating voiceover…"),
        (65, "Creating thumbnail…"),
        (80, "Rendering video (this takes ~1-2 min)…"),
        (95, "Uploading to YouTube…"),
        (100, "Complete!"),
    ]

    result_container = st.empty()

    def update(pct, msg):
        progress_bar.progress(pct, text=msg)
        status_area.info(f"**{msg}**")

    try:
        update(5, f"Starting pipeline for: {topic_keyword or 'trending topics'}…")
        ids = run_pipeline(
            topic_keyword=topic_keyword,
            n_shorts=n_shorts,
            skip_upload=skip_upload,
            dry_run=dry_run,
        )
        progress_bar.progress(100, text="Done!")
        if ids:
            result_container.success(f"✅ Successfully uploaded {len(ids)} short(s)!")
            for vid_id in ids:
                st.markdown(f"▶ [https://www.youtube.com/shorts/{vid_id}](https://www.youtube.com/shorts/{vid_id})")
        elif dry_run:
            result_container.info("✅ Dry run complete — content generated, no video rendered.")
        elif skip_upload:
            result_container.success("✅ Video generated and saved locally.")
        else:
            result_container.warning("Pipeline ran but no videos were uploaded. Check logs.")
    except Exception as e:
        progress_bar.progress(0, text="Failed")
        result_container.error(f"❌ Pipeline error: {e}")
        log.error("Dashboard pipeline error", exc_info=True)


def _upload_single(video_id: int):
    """Upload a single generated video by DB id."""
    session = get_session()
    video_repo = VideoRepo(session)

    video = session.query(__import__("src.database", fromlist=["Video"]).Video).get(video_id)
    if not video or not video.video_path:
        st.error("Video not found or path missing.")
        session.close()
        return

    from src.youtube_uploader import YouTubeUploader
    uploader = YouTubeUploader()
    with st.spinner("Uploading…"):
        try:
            result = uploader.upload(
                video_path=Path(video.video_path),
                title=video.title or "Trending Short",
                description=video.description or "",
                tags=video.hashtags_list,
                thumbnail_path=Path(video.thumbnail_path) if video.thumbnail_path else None,
            )
            video_repo.update_status(
                video_id,
                "uploaded",
                youtube_id=result["id"],
                youtube_url=result["url"],
                uploaded_at=datetime.utcnow(),
            )
            st.success(f"Uploaded! {result['url']}")
        except Exception as e:
            st.error(f"Upload failed: {e}")
    session.close()


def page_upload_manager():
    st.title("📤 Upload Manager")

    session = get_session()
    video_repo = VideoRepo(session)

    tab1, tab2 = st.tabs(["Ready to Upload", "All Videos"])

    with tab1:
        pending = video_repo.get_pending_uploads()
        if pending:
            st.info(f"{len(pending)} video(s) ready for upload.")
            for v in pending:
                with st.container():
                    cols = st.columns([3, 1, 1, 1])
                    cols[0].write(f"**{v.title or 'Untitled'}**")
                    cols[1].caption(v.created_at.strftime("%m/%d") if v.created_at else "—")
                    if v.video_path and Path(v.video_path).exists():
                        size_mb = Path(v.video_path).stat().st_size / 1_048_576
                        cols[2].caption(f"{size_mb:.1f} MB")
                    if cols[3].button("⬆️ Upload", key=f"up_{v.id}"):
                        _upload_single(v.id)
                        st.rerun()

            if st.button("⬆️ Upload All", type="primary"):
                for v in pending:
                    _upload_single(v.id)
                st.rerun()
        else:
            st.success("No videos pending upload.")

    with tab2:
        all_videos = video_repo.get_all(limit=100)
        if all_videos:
            df_data = []
            for v in all_videos:
                df_data.append(
                    {
                        "ID": v.id,
                        "Title": (v.title or "Untitled")[:60],
                        "Status": v.status,
                        "Cost": f"${v.estimated_cost_usd:.4f}" if v.estimated_cost_usd else "—",
                        "YouTube ID": v.youtube_id or "—",
                        "Created": v.created_at.strftime("%m/%d %H:%M") if v.created_at else "—",
                        "Uploaded": v.uploaded_at.strftime("%m/%d %H:%M") if v.uploaded_at else "—",
                    }
                )
            df = pd.DataFrame(df_data)
            st.dataframe(df, use_container_width=True, hide_index=True, height=500)
        else:
            st.info("No videos yet.")

    session.close()


def page_analytics():
    st.title("📊 Analytics")

    session = get_session()
    analytics_repo = AnalyticsRepo(session)
    video_repo = VideoRepo(session)

    history = analytics_repo.get_history(30)
    all_videos = video_repo.get_all(limit=500)
    session.close()

    if not history:
        st.info("No analytics data yet. Run the pipeline to start tracking.")
        return

    # ── Daily summary chart ────────────────────────────────────────────────
    df = pd.DataFrame(
        [
            {
                "Date": a.date,
                "Topics Fetched": a.topics_fetched,
                "Videos Generated": a.videos_generated,
                "Videos Uploaded": a.videos_uploaded,
                "API Errors": a.api_errors,
                "Cost ($)": round(a.total_cost_usd, 4),
            }
            for a in reversed(history)
        ]
    )

    st.subheader("Daily Activity")
    fig = go.Figure()
    fig.add_trace(go.Bar(x=df["Date"], y=df["Videos Generated"], name="Generated", marker_color="#60a5fa"))
    fig.add_trace(go.Bar(x=df["Date"], y=df["Videos Uploaded"], name="Uploaded", marker_color="#4ade80"))
    fig.update_layout(barmode="group", height=300, margin=dict(t=10, b=0))
    st.plotly_chart(fig, use_container_width=True)

    # ── Cost tracking ──────────────────────────────────────────────────────
    st.subheader("API Cost Tracking")
    col1, col2 = st.columns(2)

    total_cost = sum(a.total_cost_usd for a in history)
    total_videos = sum(a.videos_generated for a in history)
    col1.metric("Total API Cost (30d)", f"${total_cost:.4f}")
    col2.metric("Cost per Video", f"${total_cost / max(total_videos, 1):.4f}")

    if df["Cost ($)"].sum() > 0:
        fig2 = px.area(df, x="Date", y="Cost ($)", title="Daily API Spend (USD)")
        fig2.update_layout(height=250, margin=dict(t=30, b=0))
        st.plotly_chart(fig2, use_container_width=True)

    # ── Status distribution ────────────────────────────────────────────────
    st.subheader("Video Status Distribution")
    counts = video_repo.count_by_status() if hasattr(video_repo, "_session") else {}
    if all_videos:
        status_counts: dict[str, int] = {}
        for v in all_videos:
            status_counts[v.status] = status_counts.get(v.status, 0) + 1
        fig3 = px.pie(
            names=list(status_counts.keys()),
            values=list(status_counts.values()),
            color_discrete_sequence=px.colors.qualitative.Pastel,
        )
        fig3.update_layout(height=300)
        st.plotly_chart(fig3, use_container_width=True)

    st.subheader("Raw Data")
    st.dataframe(df, use_container_width=True, hide_index=True)


def page_settings():
    st.title("⚙️ Settings")

    tab1, tab2, tab3 = st.tabs(["API Keys", "YouTube OAuth", "Channel Settings"])

    with tab1:
        st.subheader("API Configuration")
        st.markdown("Configure your API keys below (stored in `.env` file only — not in the database).")

        with st.form("api_keys_form"):
            gemini_key = st.text_input("Gemini API Key", value=config.GEMINI_API_KEY[:8] + "…" if config.GEMINI_API_KEY else "", type="password")
            unsplash_key = st.text_input("Unsplash Access Key", value=config.UNSPLASH_ACCESS_KEY[:8] + "…" if config.UNSPLASH_ACCESS_KEY else "", type="password")

            col1, col2 = st.columns(2)
            yt_client_id = col1.text_input("YouTube Client ID", value=config.YOUTUBE_CLIENT_ID[:15] + "…" if config.YOUTUBE_CLIENT_ID else "", type="password")
            yt_client_secret = col2.text_input("YouTube Client Secret", value="***" if config.YOUTUBE_CLIENT_SECRET else "", type="password")
            yt_refresh_token = st.text_input("YouTube Refresh Token", value="***" if config.YOUTUBE_REFRESH_TOKEN else "", type="password")

            if st.form_submit_button("💾 Save to .env"):
                _save_env_values(
                    gemini_key=gemini_key,
                    unsplash_key=unsplash_key,
                    yt_client_id=yt_client_id,
                    yt_client_secret=yt_client_secret,
                    yt_refresh_token=yt_refresh_token,
                )
                st.success("✅ Values saved to .env file. Restart the app to apply.")

        # API Health check
        st.subheader("API Health Check")
        col1, col2 = st.columns(2)
        if col1.button("🧪 Test Gemini API"):
            _test_gemini()
        if col2.button("🧪 Test YouTube API"):
            _test_youtube()

    with tab2:
        st.subheader("YouTube OAuth Setup")
        st.markdown(
            """
**How to get your YouTube refresh token:**
1. Go to [Google Cloud Console](https://console.cloud.google.com)
2. Create a project and enable **YouTube Data API v3**
3. Create OAuth 2.0 credentials (Desktop app type)
4. Download the `client_secrets.json`
5. Run the script below:

```bash
python scripts/setup_youtube_auth.py --secrets client_secrets.json
```

Or use the browser flow below:
"""
        )

        if st.button("🔐 Start YouTube OAuth Flow (Browser)"):
            _run_oauth_flow()

        token_file = config.YOUTUBE_TOKEN_FILE
        if token_file.exists():
            st.success(f"✅ Token file found at `{token_file}`")
            if st.button("🗑️ Delete Token File"):
                token_file.unlink()
                st.warning("Token deleted. Re-authenticate before uploading.")
        else:
            st.warning("No token file found. Please authenticate.")

    with tab3:
        st.subheader("Channel & Generation Settings")

        with st.form("channel_settings"):
            col1, col2 = st.columns(2)
            geo = col1.selectbox("Trends Region", ["IN", "US", "GB", "AU", "CA"], index=["IN", "US", "GB", "AU", "CA"].index(config.TRENDS_GEO))
            shorts_per_run = col2.number_input("Shorts per run", min_value=1, max_value=10, value=config.SHORTS_PER_RUN)
            privacy = st.selectbox("Default Privacy", ["public", "unlisted", "private"], index=["public", "unlisted", "private"].index(config.YOUTUBE_PRIVACY_STATUS))

            if st.form_submit_button("💾 Save Settings"):
                _update_env({"TRENDS_GEO": geo, "SHORTS_PER_RUN": str(shorts_per_run), "YOUTUBE_PRIVACY_STATUS": privacy})
                st.success("Settings updated. Restart to apply.")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _save_env_values(**kwargs):
    env_path = config.BASE_DIR / ".env"
    existing: dict[str, str] = {}
    if env_path.exists():
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    k, _, v = line.partition("=")
                    existing[k.strip()] = v.strip()

    mapping = {
        "GEMINI_API_KEY": kwargs.get("gemini_key", ""),
        "UNSPLASH_ACCESS_KEY": kwargs.get("unsplash_key", ""),
        "YOUTUBE_CLIENT_ID": kwargs.get("yt_client_id", ""),
        "YOUTUBE_CLIENT_SECRET": kwargs.get("yt_client_secret", ""),
        "YOUTUBE_REFRESH_TOKEN": kwargs.get("yt_refresh_token", ""),
    }
    for k, v in mapping.items():
        if v and not v.startswith("***") and not v.endswith("…"):
            existing[k] = v

    with open(env_path, "w") as f:
        for k, v in existing.items():
            f.write(f"{k}={v}\n")


def _update_env(updates: dict[str, str]):
    env_path = config.BASE_DIR / ".env"
    existing: dict[str, str] = {}
    if env_path.exists():
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    k, _, v = line.partition("=")
                    existing[k.strip()] = v.strip()
    existing.update(updates)
    with open(env_path, "w") as f:
        for k, v in existing.items():
            f.write(f"{k}={v}\n")


def _test_gemini():
    with st.spinner("Testing Gemini API…"):
        try:
            import google.generativeai as genai
            genai.configure(api_key=config.GEMINI_API_KEY)
            model = genai.GenerativeModel("gemini-1.5-flash")
            resp = model.generate_content("Say 'Gemini OK' in exactly 2 words.")
            st.success(f"✅ Gemini API working: `{resp.text.strip()}`")
        except Exception as e:
            st.error(f"❌ Gemini API error: {e}")


def _test_youtube():
    with st.spinner("Testing YouTube API…"):
        try:
            from src.youtube_uploader import YouTubeUploader
            uploader = YouTubeUploader()
            info = uploader.get_channel_info()
            if info:
                st.success(f"✅ YouTube API connected: **{info.get('title')}** | {info.get('subscribers')} subscribers")
            else:
                st.warning("YouTube API connected but no channel info returned.")
        except Exception as e:
            st.error(f"❌ YouTube API error: {e}")


def _run_oauth_flow():
    with st.spinner("Opening browser for YouTube OAuth…"):
        try:
            from src.youtube_uploader import run_oauth_flow_from_config
            creds = run_oauth_flow_from_config()
            if creds:
                st.success("✅ OAuth complete! Token saved.")
            else:
                st.error("OAuth failed. Check client ID and secret.")
        except Exception as e:
            st.error(f"OAuth error: {e}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    page = sidebar()

    # Handle nav from other pages
    if "nav_page" in st.session_state:
        page = st.session_state.pop("nav_page")

    if page == "🏠 Dashboard":
        page_dashboard()
    elif page == "🔍 Trending Topics":
        page_trending_topics()
    elif page == "🎬 Create Video":
        page_create_video()
    elif page == "📤 Upload Manager":
        page_upload_manager()
    elif page == "📊 Analytics":
        page_analytics()
    elif page == "⚙️ Settings":
        page_settings()


if __name__ == "__main__":
    main()
