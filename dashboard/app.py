"""
Streamlit Dashboard - YouTube Shorts Auto-Generator.

Modern, responsive control panel for the pipeline: monitor, create, upload,
analyse. Performance notes (why this file is structured the way it is):

  * Streamlit reruns the WHOLE script on every interaction. The old dashboard
    re-queried SQLite and rebuilt every chart on each rerun, which is what made
    tab switching laggy. Here every DB read goes through an @st.cache_data
    fetcher that returns plain (picklable) dicts and is invalidated only when we
    actually mutate data (_bust_cache). Switching pages now hits the cache, not
    the disk.
  * ORM objects are converted to dicts INSIDE the cached fetcher (and the
    session is always closed in a finally) so nothing leaks a detached
    SQLAlchemy instance into the UI layer.
  * Layout uses a responsive CSS grid for KPI/cards (auto-fit) instead of fixed
    st.columns, so it reflows on narrow/mobile widths. A media query also forces
    Streamlit's horizontal blocks to wrap on small screens.
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

# Add project root to path BEFORE importing src.*
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from src.config import config
from src.database import (
    AnalyticsRepo,
    TopicRepo,
    Video,
    VideoRepo,
    get_session,
    init_db,
)
from src.utils import get_logger

log = get_logger("dashboard")

# ── Page config ─────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Shorts Studio",
    page_icon="🎬",
    layout="wide",
    initial_sidebar_state="expanded",
)

NAV = {
    "Dashboard": "🏠",
    "Trending Topics": "🔥",
    "Create Video": "🎬",
    "Upload Manager": "📤",
    "Analytics": "📊",
    "Settings": "⚙️",
}

# Plotly dark template tuned to the app palette (transparent so cards show through).
_PLOTLY_LAYOUT = dict(
    template="plotly_dark",
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="rgba(0,0,0,0)",
    font=dict(color="#c9c9d6", family="Inter, sans-serif"),
    margin=dict(t=10, b=10, l=10, r=10),
)
_ACCENT = "#7c5cff"
_GREEN = "#34d399"
_BLUE = "#60a5fa"
_AMBER = "#fbbf24"
_RED = "#f87171"


# ── Global CSS (modern + responsive) ──────────────────────────────────────────
def _inject_css() -> None:
    st.markdown(
        """
<style>
  @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&display=swap');

  html, body, [class*="css"], .stApp, button, input, textarea, select {
    font-family: 'Inter', -apple-system, system-ui, sans-serif !important;
  }

  /* App background: subtle radial glow over deep navy */
  .stApp {
    background:
      radial-gradient(1200px 600px at 12% -8%, rgba(124,92,255,0.16), transparent 60%),
      radial-gradient(900px 500px at 100% 0%, rgba(56,189,248,0.10), transparent 55%),
      #0b0b14;
  }

  /* Trim Streamlit chrome for a cleaner, app-like feel */
  #MainMenu, footer, header [data-testid="stToolbar"] { visibility: hidden; }
  .block-container { padding-top: 2.2rem; padding-bottom: 3rem; max-width: 1320px; }

  /* Fade content in on rerun so page switches feel smooth, not janky */
  .main .block-container { animation: fadeUp .35s ease; }
  @keyframes fadeUp { from {opacity:0; transform: translateY(8px);} to {opacity:1; transform:none;} }

  /* ── Sidebar ─────────────────────────────────────────────── */
  section[data-testid="stSidebar"] {
    background: linear-gradient(180deg, #12121d 0%, #0a0a12 100%);
    border-right: 1px solid rgba(255,255,255,0.06);
  }
  section[data-testid="stSidebar"] .block-container { padding-top: 1.2rem; }

  /* Sidebar radio styled as a nav menu */
  section[data-testid="stSidebar"] div[role="radiogroup"] label {
    display:flex; align-items:center; gap:.5rem;
    padding:.6rem .85rem; margin:.18rem 0; border-radius:12px;
    cursor:pointer; transition: all .18s ease; font-weight:600; font-size:.96rem;
    border:1px solid transparent;
  }
  section[data-testid="stSidebar"] div[role="radiogroup"] label:hover {
    background: rgba(124,92,255,0.12); border-color: rgba(124,92,255,0.25);
  }
  section[data-testid="stSidebar"] div[role="radiogroup"] label:has(input:checked) {
    background: linear-gradient(90deg, rgba(124,92,255,0.28), rgba(124,92,255,0.06));
    border-color: rgba(124,92,255,0.55);
    box-shadow: 0 4px 18px rgba(124,92,255,0.18);
  }
  section[data-testid="stSidebar"] div[role="radiogroup"] label > div:first-child { display:none; } /* hide radio dot */

  /* ── Headings ────────────────────────────────────────────── */
  .page-head { display:flex; align-items:center; gap:.85rem; margin-bottom:.2rem; }
  .page-head .ico {
    font-size:1.9rem; width:54px; height:54px; display:grid; place-items:center;
    border-radius:16px; background: rgba(124,92,255,0.14);
    border:1px solid rgba(124,92,255,0.3);
  }
  .page-head h1 {
    font-size:1.9rem; font-weight:800; margin:0; line-height:1.1;
    background: linear-gradient(90deg,#fff,#b9aaff);
    -webkit-background-clip:text; background-clip:text; -webkit-text-fill-color:transparent;
  }
  .page-sub { color:#8a8a9e; font-size:.92rem; margin:.1rem 0 1.3rem 0; }

  /* ── KPI grid (responsive, auto-fit) ─────────────────────── */
  .kpi-grid {
    display:grid; gap:14px; margin: .2rem 0 .4rem 0;
    grid-template-columns: repeat(auto-fit, minmax(168px, 1fr));
  }
  .kpi {
    position:relative; overflow:hidden; padding:18px 18px 16px;
    border-radius:18px; background: rgba(255,255,255,0.035);
    border:1px solid rgba(255,255,255,0.08);
    backdrop-filter: blur(10px); transition: transform .18s ease, border-color .18s ease;
  }
  .kpi:hover { transform: translateY(-3px); border-color: rgba(124,92,255,0.4); }
  .kpi::before { content:""; position:absolute; top:0; left:0; right:0; height:3px;
    background: var(--accent, #7c5cff); opacity:.9; }
  .kpi .label { color:#8a8a9e; font-size:.78rem; font-weight:600; text-transform:uppercase; letter-spacing:.04em; }
  .kpi .value { font-size:2.0rem; font-weight:800; color:#fff; margin-top:.25rem; line-height:1; }
  .kpi .delta { font-size:.8rem; font-weight:600; margin-top:.4rem; color:#34d399; }
  .kpi .delta.flat { color:#8a8a9e; }

  /* ── Glass cards / list rows ─────────────────────────────── */
  .card {
    border-radius:16px; background: rgba(255,255,255,0.035);
    border:1px solid rgba(255,255,255,0.08); padding:14px 16px; margin-bottom:10px;
    transition: border-color .18s ease, background .18s ease;
  }
  .card:hover { border-color: rgba(124,92,255,0.35); background: rgba(124,92,255,0.05); }
  .row { display:flex; align-items:center; justify-content:space-between; gap:12px; }
  .row .title { font-weight:600; color:#ececf4; font-size:.98rem; }
  .row .meta  { color:#7c7c90; font-size:.82rem; white-space:nowrap; }

  .badge { display:inline-block; padding:3px 11px; border-radius:999px;
    font-size:.72rem; font-weight:700; letter-spacing:.03em; }
  .b-uploaded   { background: rgba(52,211,153,0.16); color:#34d399; }
  .b-generated  { background: rgba(96,165,250,0.16);  color:#60a5fa; }
  .b-generating { background: rgba(251,191,36,0.16);  color:#fbbf24; }
  .b-uploading  { background: rgba(251,191,36,0.16);  color:#fbbf24; }
  .b-failed     { background: rgba(248,113,113,0.16); color:#f87171; }
  .b-upload-failed { background: rgba(248,113,113,0.16); color:#f87171; }
  .b-pending    { background: rgba(160,160,176,0.16); color:#a0a0b0; }

  /* ── Buttons ─────────────────────────────────────────────── */
  .stButton > button, .stDownloadButton > button, .stLinkButton > a {
    border-radius:12px !important; font-weight:700 !important; border:1px solid rgba(255,255,255,0.12) !important;
    transition: transform .15s ease, box-shadow .15s ease, background .15s ease !important;
  }
  .stButton > button:hover { transform: translateY(-2px); }
  .stButton > button[kind="primary"] {
    background: linear-gradient(90deg,#7c5cff,#5b8cff) !important; border:none !important;
    box-shadow: 0 6px 20px rgba(124,92,255,0.35) !important;
  }
  .stButton > button[kind="primary"]:hover { box-shadow: 0 10px 28px rgba(124,92,255,0.5) !important; }

  /* Inputs */
  div[data-baseweb="input"], div[data-baseweb="select"], .stTextArea textarea {
    border-radius:12px !important;
  }

  /* Tabs */
  .stTabs [data-baseweb="tab-list"] { gap:6px; }
  .stTabs [data-baseweb="tab"] {
    border-radius:10px 10px 0 0; padding:8px 16px; font-weight:600;
  }
  .stTabs [aria-selected="true"] { background: rgba(124,92,255,0.14); }

  /* Metric fallback (where native st.metric is still used) */
  div[data-testid="stMetric"] {
    background: rgba(255,255,255,0.035); border:1px solid rgba(255,255,255,0.08);
    border-radius:16px; padding:14px 16px;
  }

  /* Scrollbar */
  ::-webkit-scrollbar { width:10px; height:10px; }
  ::-webkit-scrollbar-thumb { background: rgba(124,92,255,0.4); border-radius:8px; }
  ::-webkit-scrollbar-track { background: transparent; }

  /* ── Responsive: stack columns on small screens ──────────── */
  @media (max-width: 880px) {
    div[data-testid="stHorizontalBlock"] { flex-wrap: wrap !important; }
    div[data-testid="stHorizontalBlock"] > div[data-testid="column"] {
      flex: 1 1 100% !important; min-width: 100% !important;
    }
    .page-head h1 { font-size:1.5rem; }
    .block-container { padding-left: 1rem; padding-right: 1rem; }
  }
</style>
""",
        unsafe_allow_html=True,
    )


# ── Init DB once ───────────────────────────────────────────────────────────--
@st.cache_resource
def _bootstrap() -> bool:
    init_db()
    return True


# ── Cached data layer (the lag fix) ────────────────────────────────────────--
# Each returns plain picklable data and closes its session. TTL gives a gentle
# auto-refresh; mutations call _bust_cache() for instant consistency.
_TTL = 20


def _bust_cache() -> None:
    st.cache_data.clear()


def _vid_to_dict(v: Video) -> dict:
    size_mb = None
    if v.video_path and Path(v.video_path).exists():
        try:
            size_mb = Path(v.video_path).stat().st_size / 1_048_576
        except OSError:
            size_mb = None
    return {
        "id": v.id,
        "title": v.title or "Untitled",
        "status": v.status or "pending",
        "youtube_url": v.youtube_url,
        "youtube_id": v.youtube_id,
        "video_path": v.video_path,
        "thumbnail_path": v.thumbnail_path,
        "description": v.description or "",
        "hashtags": v.hashtags_list,
        "cost": float(v.estimated_cost_usd or 0.0),
        "size_mb": size_mb,
        "created_at": v.created_at,
        "uploaded_at": v.uploaded_at,
    }


@st.cache_data(ttl=_TTL, show_spinner=False)
def d_overview() -> dict:
    s = get_session()
    try:
        vr, ar = VideoRepo(s), AnalyticsRepo(s)
        counts = dict(vr.count_by_status())
        t = ar.today()
        return {
            "counts": counts,
            "today_uploaded": int(t.videos_uploaded or 0),
            "today_cost": float(t.total_cost_usd or 0.0),
        }
    finally:
        s.close()


@st.cache_data(ttl=_TTL, show_spinner=False)
def d_videos(limit: int = 50) -> list[dict]:
    s = get_session()
    try:
        return [_vid_to_dict(v) for v in VideoRepo(s).get_all(limit=limit)]
    finally:
        s.close()


@st.cache_data(ttl=_TTL, show_spinner=False)
def d_pending() -> list[dict]:
    s = get_session()
    try:
        return [_vid_to_dict(v) for v in VideoRepo(s).get_pending_uploads()]
    finally:
        s.close()


@st.cache_data(ttl=_TTL, show_spinner=False)
def d_topics(limit: int = 200) -> list[dict]:
    s = get_session()
    try:
        return [
            {
                "id": t.id, "keyword": t.keyword, "score": round(t.score or 0, 1),
                "category": t.category or "-", "processed": bool(t.is_processed),
                "uploaded": bool(t.is_uploaded),
                "created_at": t.created_at,
            }
            for t in TopicRepo(s).get_all(limit=limit)
        ]
    finally:
        s.close()


@st.cache_data(ttl=_TTL, show_spinner=False)
def d_unprocessed(limit: int = 25) -> list[dict]:
    s = get_session()
    try:
        return [
            {"id": t.id, "keyword": t.keyword, "score": float(t.score or 0)}
            for t in TopicRepo(s).get_unprocessed(limit=limit)
        ]
    finally:
        s.close()


@st.cache_data(ttl=_TTL, show_spinner=False)
def d_history(days: int = 30) -> list[dict]:
    s = get_session()
    try:
        return [
            {
                "date": a.date, "topics_fetched": a.topics_fetched or 0,
                "videos_generated": a.videos_generated or 0,
                "videos_uploaded": a.videos_uploaded or 0,
                "api_errors": a.api_errors or 0,
                "cost": round(a.total_cost_usd or 0.0, 4),
            }
            for a in reversed(AnalyticsRepo(s).get_history(days))
        ]
    finally:
        s.close()


# ── Reusable UI components ─────────────────────────────────────────────────--
def page_header(title: str, subtitle: str = "") -> None:
    icon = NAV.get(title, "•")
    st.markdown(
        f'<div class="page-head"><div class="ico">{icon}</div><h1>{title}</h1></div>',
        unsafe_allow_html=True,
    )
    if subtitle:
        st.markdown(f'<div class="page-sub">{subtitle}</div>', unsafe_allow_html=True)


def kpi_grid(items: list[dict]) -> None:
    """items: [{label, value, accent, delta?, delta_flat?}]"""
    cells = []
    for it in items:
        delta = ""
        if it.get("delta"):
            cls = "delta flat" if it.get("delta_flat") else "delta"
            delta = f'<div class="{cls}">{it["delta"]}</div>'
        cells.append(
            f'<div class="kpi" style="--accent:{it.get("accent", _ACCENT)}">'
            f'<div class="label">{it["label"]}</div>'
            f'<div class="value">{it["value"]}</div>{delta}</div>'
        )
    st.markdown(f'<div class="kpi-grid">{"".join(cells)}</div>', unsafe_allow_html=True)


def badge(status: str) -> str:
    cls = "b-" + status.replace("_", "-")
    return f'<span class="badge {cls}">{status.upper()}</span>'


def styled_chart(fig: go.Figure, height: int = 300) -> None:
    fig.update_layout(**_PLOTLY_LAYOUT, height=height)
    st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})


# ─────────────────────────────────────────────────────────────────────────────
#  Pages
# ─────────────────────────────────────────────────────────────────────────────
def page_dashboard() -> None:
    page_header("Dashboard", "Live overview of your Shorts pipeline.")

    ov = d_overview()
    counts = ov["counts"]
    total = sum(counts.values())
    uploaded = counts.get("uploaded", 0)
    generated = counts.get("generated", 0)
    failed = counts.get("failed", 0) + counts.get("upload_failed", 0)

    up_delta = f"+{ov['today_uploaded']} today" if ov["today_uploaded"] else "none today"
    kpi_grid([
        {"label": "Total Videos", "value": total, "accent": _ACCENT},
        {"label": "Uploaded", "value": uploaded, "accent": _GREEN,
         "delta": up_delta, "delta_flat": ov["today_uploaded"] == 0},
        {"label": "Ready to Upload", "value": generated, "accent": _BLUE},
        {"label": "Failed", "value": failed, "accent": _RED},
        {"label": "Cost Today", "value": f"${ov['today_cost']:.4f}", "accent": _AMBER},
    ])

    st.write("")
    col_left, col_right = st.columns([2, 1], gap="large")

    with col_left:
        st.markdown("##### Recent activity")
        videos = d_videos(limit=8)
        if not videos:
            st.info("No videos yet. Open **Create Video** to generate your first Short.")
        for v in videos:
            meta = v["created_at"].strftime("%b %d, %H:%M") if v["created_at"] else "-"
            st.markdown(
                f'<div class="card"><div class="row">'
                f'<span class="title">{v["title"][:54]}</span>{badge(v["status"])}</div>'
                f'<div class="row" style="margin-top:6px"><span class="meta">{meta}</span>'
                f'<span class="meta">{("$%.4f" % v["cost"]) if v["cost"] else ""}</span></div></div>',
                unsafe_allow_html=True,
            )
            if v["youtube_url"]:
                st.link_button("▶ Watch on YouTube", v["youtube_url"])

    with col_right:
        st.markdown("##### Status breakdown")
        if counts:
            colors = {"uploaded": _GREEN, "generated": _BLUE, "generating": _AMBER,
                      "failed": _RED, "upload_failed": _RED, "pending": "#a0a0b0"}
            fig = go.Figure(go.Pie(
                labels=[k.replace("_", " ").title() for k in counts],
                values=list(counts.values()), hole=0.62,
                marker=dict(colors=[colors.get(k, _ACCENT) for k in counts]),
                textinfo="value",
            ))
            fig.update_layout(showlegend=True, legend=dict(orientation="h", y=-0.1))
            styled_chart(fig, height=300)
        else:
            st.info("No data to chart yet.")


def page_trending_topics() -> None:
    page_header("Trending Topics", "Pull live trends or browse what's already saved.")
    tab1, tab2 = st.tabs(["🌐 Live Trends", "💾 Saved Topics"])

    with tab1:
        c1, c2 = st.columns([1, 3])
        geo = c1.selectbox("Region", ["IN", "US", "GB", "AU", "CA", "SG"], index=0)
        if c2.button("🔄 Fetch trending now", type="primary"):
            with st.spinner("Querying Google Trends…"):
                try:
                    from src.trends import TrendsFetcher
                    s = get_session()
                    existing = TopicRepo(s).all_keywords()
                    s.close()
                    topics = TrendsFetcher().get_scored_topics(exclude_keywords=existing, geo=geo)
                    st.session_state["last_trends"] = topics
                except Exception as e:
                    st.error(f"Failed to fetch trends: {e}")

        topics = st.session_state.get("last_trends", [])
        if topics:
            st.success(f"Found {len(topics)} trending topics.")
            df = pd.DataFrame(topics)
            keep = [c for c in ["keyword", "score", "category", "source"] if c in df.columns]
            st.dataframe(
                df[keep].rename(columns=str.title), width="stretch", hide_index=True, height=320,
            )
            a, b = st.columns(2)
            if a.button("💾 Save all to database"):
                s = get_session()
                inserted = TopicRepo(s).bulk_insert_new(topics)
                s.close()
                _bust_cache()
                st.toast(f"Saved {inserted} new topics", icon="✅")
            pick = b.selectbox("Create a video for…", [t["keyword"] for t in topics])
            if b.button("🎬 Use this topic"):
                st.session_state["create_topic"] = pick
                st.session_state["go_create"] = True
                st.rerun()

    with tab2:
        topics = d_topics(limit=300)
        if not topics:
            st.info("No saved topics yet. Fetch some from the Live Trends tab.")
            return
        c1, c2 = st.columns([3, 1])
        search = c1.text_input("Search", placeholder="Filter topics…", label_visibility="collapsed")
        only_new = c2.checkbox("Unprocessed only")
        df = pd.DataFrame(topics)
        if search:
            df = df[df["keyword"].str.contains(search, case=False, na=False)]
        if only_new:
            df = df[~df["processed"]]
        view = df.assign(
            processed=df["processed"].map({True: "✅", False: "⏳"}),
            uploaded=df["uploaded"].map({True: "✅", False: "—"}),
        ).rename(columns=str.title)
        st.dataframe(view, width="stretch", hide_index=True, height=440)
        st.caption(f"Showing {len(df)} of {len(topics)} topics")


def page_create_video() -> None:
    page_header("Create Video", "Turn any topic into a finished Short — content, voice, video, upload.")

    default_topic = st.session_state.pop("create_topic", "") if st.session_state.get("go_create") else st.session_state.get("create_topic", "")
    st.session_state.pop("go_create", None)

    c1, c2 = st.columns([3, 1])
    topic = c1.text_input("Topic / keyword", value=default_topic,
                          placeholder="e.g. India wins the cricket World Cup")
    n = c2.number_input("Shorts", min_value=1, max_value=5, value=1)

    with st.expander("⚙️ Advanced options"):
        a, b, c = st.columns(3)
        skip_upload = a.checkbox("Skip upload (generate only)")
        dry_run = b.checkbox("Dry run (content only)")
        c.selectbox("Privacy", ["public", "unlisted", "private"], index=0, key="cv_privacy")

    if st.button("🚀 Generate" + ("" if skip_upload or dry_run else " & Upload"),
                 type="primary", disabled=not topic.strip()):
        _run_pipeline_ui(topic.strip(), int(n), skip_upload, dry_run)

    st.divider()
    st.markdown("##### Quick-generate from a saved topic")
    unp = d_unprocessed(limit=25)
    if unp:
        opts = {f"{t['keyword']}  ·  score {t['score']:.0f}": t["keyword"] for t in unp}
        sel = st.selectbox("Unprocessed topics", list(opts.keys()), label_visibility="collapsed")
        if st.button("🎬 Generate for selected"):
            _run_pipeline_ui(opts[sel], 1, False, False)
    else:
        st.info("No unprocessed topics. Fetch trends first.")


def _run_pipeline_ui(topic_keyword, n_shorts, skip_upload, dry_run) -> None:
    from main import run_pipeline

    bar = st.progress(0, text="Starting…")
    status = st.empty()
    result = st.empty()
    try:
        status.info(f"Working on **{topic_keyword}** — content → images → voice → video"
                    f"{'' if skip_upload or dry_run else ' → upload'}.")
        bar.progress(15, text="Running pipeline (this can take 1-2 min)…")
        ids = run_pipeline(topic_keyword=topic_keyword, n_shorts=n_shorts,
                           skip_upload=skip_upload, dry_run=dry_run)
        bar.progress(100, text="Done")
        _bust_cache()
        if ids:
            result.success(f"Uploaded {len(ids)} short(s)!")
            for vid in ids:
                st.link_button("▶ Watch", f"https://www.youtube.com/shorts/{vid}")
        elif dry_run:
            result.info("Dry run complete — content generated, no video rendered.")
        elif skip_upload:
            result.success("Video generated and saved locally.")
        else:
            result.warning("Pipeline finished but nothing was uploaded. Check the logs.")
    except Exception as e:
        bar.progress(0, text="Failed")
        result.error(f"Pipeline error: {e}")
        log.error("Dashboard pipeline error", exc_info=True)


def _upload_single(video_id: int) -> bool:
    s = get_session()
    try:
        video = s.get(Video, video_id)
        if not video or not video.video_path:
            st.error("Video not found or has no rendered file.")
            return False
        from src.youtube_uploader import YouTubeUploader
        with st.spinner(f"Uploading '{(video.title or 'Short')[:40]}'…"):
            result = YouTubeUploader().upload(
                video_path=Path(video.video_path),
                title=video.title or "Trending Short",
                description=video.description or "",
                tags=video.hashtags_list,
                thumbnail_path=Path(video.thumbnail_path) if video.thumbnail_path else None,
            )
            VideoRepo(s).update_status(
                video_id, "uploaded", youtube_id=result["id"],
                youtube_url=result["url"], uploaded_at=datetime.utcnow(),
            )
        st.toast("Uploaded!", icon="✅")
        return True
    except Exception as e:
        st.error(f"Upload failed: {e}")
        return False
    finally:
        s.close()


def page_upload_manager() -> None:
    page_header("Upload Manager", "Push rendered videos to YouTube.")
    tab1, tab2 = st.tabs(["⬆️ Ready to upload", "🗂️ All videos"])

    with tab1:
        pending = d_pending()
        if not pending:
            st.success("Nothing waiting — every rendered video is uploaded.")
        else:
            st.info(f"{len(pending)} video(s) ready.")
            for v in pending:
                col = st.columns([4, 1, 1])
                size = f"{v['size_mb']:.1f} MB" if v["size_mb"] else "-"
                col[0].markdown(
                    f'<div class="card"><div class="row"><span class="title">{v["title"][:48]}</span>'
                    f'<span class="meta">{size}</span></div></div>', unsafe_allow_html=True)
                if col[1].button("Upload", key=f"up_{v['id']}", type="primary"):
                    if _upload_single(v["id"]):
                        _bust_cache(); st.rerun()
            if st.button("⬆️ Upload all", type="primary"):
                ok = sum(_upload_single(v["id"]) for v in pending)
                _bust_cache()
                st.toast(f"Uploaded {ok}/{len(pending)}", icon="✅")
                st.rerun()

    with tab2:
        videos = d_videos(limit=100)
        if not videos:
            st.info("No videos yet.")
        else:
            df = pd.DataFrame([{
                "ID": v["id"], "Title": v["title"][:55], "Status": v["status"],
                "Cost": f"${v['cost']:.4f}" if v["cost"] else "-",
                "YouTube": v["youtube_id"] or "-",
                "Created": v["created_at"].strftime("%m/%d %H:%M") if v["created_at"] else "-",
            } for v in videos])
            st.dataframe(df, width="stretch", hide_index=True, height=520)


def page_analytics() -> None:
    page_header("Analytics", "Generation, uploads and cost over the last 30 days.")
    history = d_history(30)
    if not history:
        st.info("No analytics yet. Run the pipeline to start tracking.")
        return
    df = pd.DataFrame(history)

    total_cost = df["cost"].sum()
    total_gen = int(df["videos_generated"].sum())
    total_up = int(df["videos_uploaded"].sum())
    kpi_grid([
        {"label": "Generated (30d)", "value": total_gen, "accent": _BLUE},
        {"label": "Uploaded (30d)", "value": total_up, "accent": _GREEN},
        {"label": "API Cost (30d)", "value": f"${total_cost:.4f}", "accent": _AMBER},
        {"label": "Cost / Video", "value": f"${total_cost / max(total_gen, 1):.4f}", "accent": _ACCENT},
    ])

    st.write("")
    st.markdown("##### Daily activity")
    fig = go.Figure()
    fig.add_bar(x=df["date"], y=df["videos_generated"], name="Generated", marker_color=_BLUE)
    fig.add_bar(x=df["date"], y=df["videos_uploaded"], name="Uploaded", marker_color=_GREEN)
    fig.update_layout(barmode="group", legend=dict(orientation="h", y=1.1))
    styled_chart(fig, height=300)

    if df["cost"].sum() > 0:
        st.markdown("##### Daily API spend")
        fig2 = go.Figure(go.Scatter(
            x=df["date"], y=df["cost"], fill="tozeroy", mode="lines",
            line=dict(color=_ACCENT, width=2), fillcolor="rgba(124,92,255,0.2)"))
        styled_chart(fig2, height=240)


def page_settings() -> None:
    page_header("Settings", "Keys, OAuth and channel defaults. Secrets live in .env only.")
    tab1, tab2, tab3 = st.tabs(["🔑 API Keys", "🔐 YouTube OAuth", "📺 Channel"])

    with tab1:
        with st.form("api_keys"):
            gemini = st.text_input("Gemini API Key", type="password",
                                   value="set" if config.GEMINI_API_KEY else "")
            unsplash = st.text_input("Unsplash Access Key", type="password",
                                     value="set" if config.UNSPLASH_ACCESS_KEY else "")
            a, b = st.columns(2)
            cid = a.text_input("YouTube Client ID", type="password",
                               value="set" if config.YOUTUBE_CLIENT_ID else "")
            csec = b.text_input("YouTube Client Secret", type="password",
                                value="set" if config.YOUTUBE_CLIENT_SECRET else "")
            rtok = st.text_input("YouTube Refresh Token", type="password",
                                 value="set" if config.YOUTUBE_REFRESH_TOKEN else "")
            if st.form_submit_button("💾 Save to .env", type="primary"):
                _save_env({
                    "GEMINI_API_KEY": gemini, "UNSPLASH_ACCESS_KEY": unsplash,
                    "YOUTUBE_CLIENT_ID": cid, "YOUTUBE_CLIENT_SECRET": csec,
                    "YOUTUBE_REFRESH_TOKEN": rtok,
                })
                st.toast("Saved to .env — restart to apply", icon="✅")

        st.markdown("##### Health checks")
        a, b = st.columns(2)
        if a.button("🧪 Test Gemini"):
            _test_gemini()
        if b.button("🧪 Test YouTube"):
            _test_youtube()

    with tab2:
        st.markdown(
            "1. [Google Cloud Console](https://console.cloud.google.com) → enable **YouTube Data API v3**\n"
            "2. Create OAuth **Desktop** credentials, download `client_secrets.json`\n"
            "3. Run: `python scripts/setup_youtube_auth.py --secrets client_secrets.json`")
        if st.button("🔐 Start browser OAuth"):
            _run_oauth()
        token = config.YOUTUBE_TOKEN_FILE
        if token.exists():
            st.success(f"Token found at `{token}`")
            if st.button("🗑️ Delete token"):
                token.unlink()
                st.warning("Token deleted — re-authenticate before uploading.")
        else:
            st.warning("No token file. Authenticate before uploading.")

    with tab3:
        with st.form("channel"):
            regions = ["IN", "US", "GB", "AU", "CA"]
            a, b = st.columns(2)
            geo = a.selectbox("Trends region", regions,
                              index=regions.index(config.TRENDS_GEO) if config.TRENDS_GEO in regions else 0)
            spr = b.number_input("Shorts per run", 1, 10, value=config.SHORTS_PER_RUN)
            privs = ["public", "unlisted", "private"]
            priv = st.selectbox("Default privacy", privs,
                                index=privs.index(config.YOUTUBE_PRIVACY_STATUS) if config.YOUTUBE_PRIVACY_STATUS in privs else 0)
            if st.form_submit_button("💾 Save settings", type="primary"):
                _save_env({"TRENDS_GEO": geo, "SHORTS_PER_RUN": str(spr),
                           "YOUTUBE_PRIVACY_STATUS": priv})
                st.toast("Settings saved — restart to apply", icon="✅")


# ── Settings helpers ─────────────────────────────────────────────────────────
def _save_env(updates: dict[str, str]) -> None:
    """Merge updates into .env. Placeholder values ('set'/'') are ignored so a
    masked field never overwrites a real secret with junk."""
    env_path = config.BASE_DIR / ".env"
    existing: dict[str, str] = {}
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, _, v = line.partition("=")
                existing[k.strip()] = v.strip()
    for k, v in updates.items():
        if v and v != "set":
            existing[k] = v
    env_path.write_text("".join(f"{k}={v}\n" for k, v in existing.items()), encoding="utf-8")


def _test_gemini() -> None:
    with st.spinner("Testing Gemini…"):
        try:
            from google import genai as genai_client
            client = genai_client.Client(api_key=config.GEMINI_API_KEY)
            resp = client.models.generate_content(
                model=config.GEMINI_MODEL, contents="Say 'Gemini OK' in exactly 2 words.")
            st.success(f"Gemini OK ({config.GEMINI_MODEL}): {resp.text.strip()}")
        except Exception as e:
            st.error(f"Gemini error: {e}")


def _test_youtube() -> None:
    with st.spinner("Testing YouTube…"):
        try:
            from src.youtube_uploader import YouTubeUploader
            info = YouTubeUploader().get_channel_info()
            if info:
                st.success(f"Connected: {info.get('title')} · {info.get('subscribers')} subs")
            else:
                st.warning("Connected but no channel info returned.")
        except Exception as e:
            st.error(f"YouTube error: {e}")


def _run_oauth() -> None:
    with st.spinner("Opening browser for OAuth…"):
        try:
            from src.youtube_uploader import run_oauth_flow_from_config
            if run_oauth_flow_from_config():
                st.success("OAuth complete — token saved.")
            else:
                st.error("OAuth failed. Check client ID/secret.")
        except Exception as e:
            st.error(f"OAuth error: {e}")


# ── Sidebar + router ───────────────────────────────────────────────────────--
def sidebar() -> str:
    with st.sidebar:
        st.markdown(
            '<div style="display:flex;align-items:center;gap:.6rem;margin-bottom:.2rem">'
            '<span style="font-size:1.7rem">🎬</span>'
            '<span style="font-size:1.25rem;font-weight:800;background:linear-gradient(90deg,#fff,#b9aaff);'
            '-webkit-background-clip:text;-webkit-text-fill-color:transparent">Shorts Studio</span></div>',
            unsafe_allow_html=True)
        st.caption(f"Channel · {config.CHANNEL_NAME}")
        st.divider()
        labels = [f"{ico}  {name}" for name, ico in NAV.items()]
        default = st.session_state.get("nav_default", 0)
        choice = st.radio("Navigation", labels, index=default, label_visibility="collapsed")
        st.divider()
        st.caption(f"Updated {datetime.now().strftime('%H:%M:%S')}")
        return choice.split("  ", 1)[1]


def main() -> None:
    _inject_css()
    _bootstrap()

    # Cross-page jump (e.g. "Use this topic" -> Create Video)
    if st.session_state.get("go_create"):
        st.session_state["nav_default"] = list(NAV).index("Create Video")

    page = sidebar()
    {
        "Dashboard": page_dashboard,
        "Trending Topics": page_trending_topics,
        "Create Video": page_create_video,
        "Upload Manager": page_upload_manager,
        "Analytics": page_analytics,
        "Settings": page_settings,
    }[page]()


if __name__ == "__main__":
    main()
