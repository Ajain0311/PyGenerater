"""
Streamlit Dashboard - YouTube Shorts Auto-Generator.

Full project control center: monitor, automate, create, upload, analyse,
debug. Performance notes (why this file is structured the way it is):

  * Streamlit reruns the WHOLE script on every interaction. Every DB read goes
    through an @st.cache_data fetcher that returns plain (picklable) dicts and
    is invalidated only when we actually mutate data (_bust_cache). Switching
    pages hits the cache, not the disk.
  * ORM objects are converted to dicts INSIDE the cached fetcher (and the
    session is always closed in a finally) so nothing leaks a detached
    SQLAlchemy instance into the UI layer.
  * Layout uses a responsive CSS grid for KPI/cards (auto-fit) instead of fixed
    st.columns, so it reflows on narrow/mobile widths. A media query also forces
    Streamlit's horizontal blocks to wrap on small screens.
  * Long pipeline runs launch as a detached subprocess (Background mode) so the
    UI never blocks; live logs stream from the run's log file.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Add project root to path BEFORE importing src.*
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
import plotly.graph_objects as go
import requests
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
    "Automation": "🤖",
    "Create Video": "🎬",
    "Topics": "🔥",
    "Videos": "📚",
    "Analytics": "📊",
    "Logs": "🗞️",
    "System": "🩺",
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

IST = timezone(timedelta(hours=5, minutes=30))


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
  #MainMenu, footer { visibility: hidden; }
  header, [data-testid="stHeader"] { display: none !important; }
  .block-container { padding-top: 1rem; padding-bottom: 3rem; max-width: 1320px; }

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
  .b-success    { background: rgba(52,211,153,0.16); color:#34d399; }
  .b-failure    { background: rgba(248,113,113,0.16); color:#f87171; }
  .b-in-progress{ background: rgba(251,191,36,0.16);  color:#fbbf24; }
  .b-queued     { background: rgba(160,160,176,0.16); color:#a0a0b0; }
  .b-cancelled  { background: rgba(160,160,176,0.16); color:#a0a0b0; }
  .b-running    { background: rgba(251,191,36,0.16);  color:#fbbf24; }
  .b-done       { background: rgba(52,211,153,0.16); color:#34d399; }

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

  /* Log viewer */
  .logbox {
    font-family: 'Cascadia Code', Consolas, monospace; font-size:.8rem; line-height:1.5;
    background:#0a0a12; border:1px solid rgba(255,255,255,0.08); border-radius:12px;
    padding:14px; max-height:540px; overflow-y:auto; white-space:pre-wrap; word-break:break-word;
  }
  .log-err { color:#f87171; font-weight:600; }
  .log-warn { color:#fbbf24; }
  .log-info { color:#9a9ab0; }

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
    .block-container { padding-top: 0.5rem; padding-left: 1rem; padding-right: 1rem; }
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
        "script": v.script or "",
        "error": v.error_message or "",
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
        vr, ar, tr = VideoRepo(s), AnalyticsRepo(s), TopicRepo(s)
        counts = dict(vr.count_by_status())
        t = ar.today()
        return {
            "counts": counts,
            "today_uploaded": int(t.videos_uploaded or 0),
            "today_cost": float(t.total_cost_usd or 0.0),
            "queue": tr.count_unprocessed(),
            "total_cost": float(vr.total_cost() or 0.0),
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
def d_topics(limit: int = 300) -> list[dict]:
    s = get_session()
    try:
        return [
            {
                "id": t.id, "keyword": t.keyword, "score": round(t.score or 0, 1),
                "category": t.category or "-", "processed": bool(t.is_processed),
                "uploaded": bool(t.is_uploaded), "fails": int(t.fail_count or 0),
                "source": t.source or "-",
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


# ── GitHub Actions helpers ───────────────────────────────────────────────────
WORKFLOW_FILE = "auto_shorts.yml"


@st.cache_data(ttl=300, show_spinner=False)
def _gh_repo() -> str | None:
    """owner/name parsed from the git remote."""
    try:
        out = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True, text=True, cwd=config.BASE_DIR, timeout=10,
        )
        m = re.search(r"github\.com[:/]([^/\s]+/[^/\s.]+)", out.stdout.strip())
        return m.group(1) if m else None
    except Exception:
        return None


@st.cache_data(ttl=300, show_spinner=False)
def _gh_token() -> str:
    tok = os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN") or ""
    if tok:
        return tok
    try:  # fall back to the gh CLI's stored token
        out = subprocess.run(["gh", "auth", "token"], capture_output=True,
                             text=True, timeout=10)
        return out.stdout.strip() if out.returncode == 0 else ""
    except Exception as e:
        log.warning("No GitHub token: env unset and `gh auth token` failed (%s)", e)
        return ""


def _gh_headers() -> dict:
    return {
        "Authorization": f"Bearer {_gh_token()}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


@st.cache_data(ttl=30, show_spinner=False)
def gh_runs(limit: int = 12) -> list[dict]:
    repo = _gh_repo()
    if not repo or not _gh_token():
        return []
    try:
        r = requests.get(
            f"https://api.github.com/repos/{repo}/actions/workflows/{WORKFLOW_FILE}/runs",
            headers=_gh_headers(), params={"per_page": limit}, timeout=15)
        r.raise_for_status()
        out = []
        for run in r.json().get("workflow_runs", []):
            started = run.get("run_started_at")
            updated = run.get("updated_at")
            dur = ""
            try:
                t0 = datetime.fromisoformat(started.replace("Z", "+00:00"))
                t1 = datetime.fromisoformat(updated.replace("Z", "+00:00"))
                mins, secs = divmod(int((t1 - t0).total_seconds()), 60)
                dur = f"{mins}m{secs:02d}s"
                local = t0.astimezone(IST).strftime("%d %b %H:%M")
            except Exception:
                local = started or "-"
            out.append({
                "id": run["id"],
                "status": run.get("status"),          # queued | in_progress | completed
                "conclusion": run.get("conclusion"),  # success | failure | cancelled
                "event": run.get("event"),
                "started": local,
                "duration": dur,
                "url": run.get("html_url"),
            })
        return out
    except Exception as e:
        log.warning("gh_runs failed: %s", e)
        return []


def gh_dispatch(inputs: dict) -> tuple[bool, str]:
    repo = _gh_repo()
    if not repo:
        return False, "Could not detect GitHub repo from git remote."
    if not _gh_token():
        return False, "No GitHub token. Set GITHUB_TOKEN in .env or login with `gh auth login`."
    try:
        r = requests.post(
            f"https://api.github.com/repos/{repo}/actions/workflows/{WORKFLOW_FILE}/dispatches",
            headers=_gh_headers(),
            json={"ref": "main", "inputs": inputs}, timeout=15)
        if r.status_code == 204:
            return True, "Workflow dispatched — it appears in the runs list within ~10s."
        return False, f"GitHub API {r.status_code}: {r.text[:300]}"
    except Exception as e:
        return False, str(e)


def gh_cancel(run_id: int) -> tuple[bool, str]:
    repo = _gh_repo()
    try:
        r = requests.post(
            f"https://api.github.com/repos/{repo}/actions/runs/{run_id}/cancel",
            headers=_gh_headers(), timeout=15)
        return (r.status_code == 202), f"HTTP {r.status_code}"
    except Exception as e:
        return False, str(e)


def _next_schedule_times(every_hours: int = 4, count: int = 3) -> list[str]:
    """Next fire times of cron '0 */N * * *' shown in IST."""
    now = datetime.now(timezone.utc)
    nxt = now.replace(minute=0, second=0, microsecond=0)
    out = []
    while len(out) < count:
        nxt += timedelta(hours=1)
        if nxt.hour % every_hours == 0:
            out.append(nxt.astimezone(IST).strftime("%d %b · %I:%M %p IST"))
    return out


# ── Background pipeline runs (non-blocking) ──────────────────────────────────
def _runs_dir() -> Path:
    p = config.LOGS_DIR / "runs"
    p.mkdir(parents=True, exist_ok=True)
    return p


def start_background_run(cli_args: list[str], label: str) -> dict:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = _runs_dir() / f"run_{stamp}.log"
    f = open(log_file, "w", encoding="utf-8")
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"}
    proc = subprocess.Popen(
        [sys.executable, "-u", "main.py", *cli_args],
        stdout=f, stderr=subprocess.STDOUT, cwd=config.BASE_DIR, env=env,
    )
    info = {"proc": proc, "log_file": str(log_file), "label": label,
            "started": datetime.now().strftime("%H:%M:%S"), "file": f}
    st.session_state.setdefault("bg_runs", []).append(info)
    return info


def render_background_runs() -> None:
    runs = st.session_state.get("bg_runs", [])
    if not runs:
        return
    head = st.columns([3, 1])
    head[0].markdown("##### Background runs (this session)")
    if head[1].button("🧹 Clear finished"):
        for r in runs:
            if r["proc"].poll() is not None and not r["file"].closed:
                r["file"].close()
        st.session_state["bg_runs"] = [r for r in runs if r["proc"].poll() is None]
        st.rerun()
    for i, r in enumerate(runs):
        rc = r["proc"].poll()
        if rc is not None and not r["file"].closed:
            r["file"].close()
        state = "running" if rc is None else ("done" if rc == 0 else "failed")
        cols = st.columns([3, 1, 1])
        cols[0].markdown(
            f'<div class="card"><div class="row"><span class="title">{r["label"]}</span>'
            f'{badge(state)}</div><div class="row" style="margin-top:6px">'
            f'<span class="meta">started {r["started"]}</span>'
            f'<span class="meta">exit={rc if rc is not None else "…"}</span></div></div>',
            unsafe_allow_html=True)
        if cols[1].button("📜 Logs", key=f"bglog_{i}"):
            st.session_state["show_bg_log"] = r["log_file"]
        if rc is None and cols[2].button("🛑 Stop", key=f"bgstop_{i}"):
            r["proc"].terminate()
            st.toast("Stop signal sent", icon="🛑")
    sel = st.session_state.get("show_bg_log")
    if sel and Path(sel).exists():
        tail = Path(sel).read_text(encoding="utf-8", errors="replace")[-8000:]
        st.markdown(f'<div class="logbox">{_colorize_log(tail)}</div>', unsafe_allow_html=True)
        if st.button("🔄 Refresh log"):
            st.rerun()


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
    cls = "b-" + (status or "pending").replace("_", "-")
    return f'<span class="badge {cls}">{(status or "?").upper()}</span>'


def styled_chart(fig: go.Figure, height: int = 300) -> None:
    fig.update_layout(**_PLOTLY_LAYOUT, height=height)
    st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})


def _colorize_log(text: str) -> str:
    out = []
    for line in text.splitlines():
        esc = (line.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
        if "ERROR" in line or "CRITICAL" in line or "Traceback" in line:
            out.append(f'<span class="log-err">{esc}</span>')
        elif "WARNING" in line:
            out.append(f'<span class="log-warn">{esc}</span>')
        else:
            out.append(f'<span class="log-info">{esc}</span>')
    return "\n".join(out)


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
        {"label": "Topic Queue", "value": ov["queue"], "accent": _AMBER},
        {"label": "Cost Today", "value": f"${ov['today_cost']:.4f}", "accent": _AMBER},
    ])

    # Automation status strip
    runs = gh_runs(limit=5)
    if runs:
        last = runs[0]
        state = last["conclusion"] or last["status"]
        nxt = _next_schedule_times()[0]
        st.markdown(
            f'<div class="card" style="margin-top:12px"><div class="row">'
            f'<span class="title">🤖 Last automation run: {badge(state)} '
            f'<span class="meta">{last["started"]} · {last["duration"]} · {last["event"]}</span></span>'
            f'<span class="meta">next scheduled: {nxt}</span></div></div>',
            unsafe_allow_html=True)

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

    render_background_runs()


def page_automation() -> None:
    page_header("Automation", "Trigger, monitor and control the GitHub Actions pipeline.")

    repo = _gh_repo()
    token_ok = bool(_gh_token())
    if not repo:
        st.error("Could not detect the GitHub repository from the git remote.")
        return
    if not token_ok:
        st.warning(
            "No GitHub token found. Set **GITHUB_TOKEN** in `.env` (classic token with "
            "`repo` + `workflow` scopes) or run `gh auth login` once in a terminal.")

    nxt = _next_schedule_times(count=3)
    kpi_grid([
        {"label": "Repository", "value": repo.split("/")[-1], "accent": _ACCENT},
        {"label": "Schedule", "value": "every 4h", "accent": _BLUE,
         "delta": f"next: {nxt[0]}", "delta_flat": True},
        {"label": "Token", "value": "OK" if token_ok else "missing",
         "accent": _GREEN if token_ok else _RED},
    ])
    st.caption("Upcoming scheduled runs: " + "  ·  ".join(nxt))

    st.write("")
    tab1, tab2 = st.tabs(["▶️ Trigger a run", "📜 Recent runs"])

    with tab1:
        with st.form("dispatch"):
            topic = st.text_input("Topic (blank = auto trending/queue)")
            a, b, c = st.columns(3)
            n = a.number_input("Shorts", 1, 5, 1)
            skip_upload = b.checkbox("Skip upload")
            dry_run = c.checkbox("Dry run")
            if st.form_submit_button("🚀 Run on GitHub Actions", type="primary",
                                     disabled=not token_ok):
                ok, msg = gh_dispatch({
                    "topic": topic.strip(),
                    "n_shorts": str(int(n)),
                    "skip_upload": skip_upload,
                    "dry_run": dry_run,
                })
                (st.success if ok else st.error)(msg)
                if ok:
                    st.cache_data.clear()
        st.caption("Runs in the cloud — your PC can be off. Perfect for the 4-hour auto-upload cycle.")

    with tab2:
        head = st.columns([4, 1])
        head[0].markdown("##### Latest workflow runs")
        if head[1].button("🔄 Refresh"):
            st.cache_data.clear()
            st.rerun()
        runs = gh_runs(limit=12)
        if not runs:
            st.info("No runs found (or token missing).")
        for r in runs:
            state = r["conclusion"] or r["status"] or "queued"
            cols = st.columns([5, 1, 1])
            cols[0].markdown(
                f'<div class="card"><div class="row">'
                f'<span class="title">#{r["id"]} · {r["event"]}</span>{badge(state)}</div>'
                f'<div class="row" style="margin-top:6px">'
                f'<span class="meta">{r["started"]}</span>'
                f'<span class="meta">{r["duration"]}</span></div></div>',
                unsafe_allow_html=True)
            cols[1].link_button("Open", r["url"])
            if r["status"] in ("queued", "in_progress"):
                if cols[2].button("Cancel", key=f"cancel_{r['id']}"):
                    ok, msg = gh_cancel(r["id"])
                    (st.toast if ok else st.error)("Cancelled" if ok else msg)
                    st.cache_data.clear()
                    st.rerun()


def page_create_video() -> None:
    page_header("Create Video", "Turn any topic into a finished Short — content, voice, video, upload.")

    default_topic = st.session_state.pop("create_topic", "") if st.session_state.get("go_create") else st.session_state.get("create_topic", "")
    st.session_state.pop("go_create", None)

    c1, c2 = st.columns([3, 1])
    topic = c1.text_input("Topic / keyword", value=default_topic,
                          placeholder="e.g. The Indian Lake That Turns Pink")
    n = c2.number_input("Shorts", min_value=1, max_value=5, value=1)

    with st.expander("⚙️ Advanced options"):
        a, b, c = st.columns(3)
        skip_upload = a.checkbox("Skip upload (generate only)")
        dry_run = b.checkbox("Dry run (content only)")
        background = c.checkbox("Run in background", value=True,
                                help="Doesn't block the dashboard — watch progress in Background runs below.")

    if st.button("🚀 Generate" + ("" if skip_upload or dry_run else " & Upload"),
                 type="primary", disabled=not topic.strip()):
        cli = ["--topic", topic.strip(), "--n", str(int(n))]
        if skip_upload:
            cli.append("--skip-upload")
        if dry_run:
            cli.append("--dry-run")
        if background:
            start_background_run(cli, f"🎬 {topic.strip()[:40]}")
            st.success("Started in background — progress below.")
            st.rerun()
        else:
            _run_pipeline_ui(topic.strip(), int(n), skip_upload, dry_run)

    st.divider()
    st.markdown("##### Quick-generate from a saved topic")
    unp = d_unprocessed(limit=25)
    if unp:
        opts = {f"{t['keyword']}  ·  score {t['score']:.0f}": t["keyword"] for t in unp}
        sel = st.selectbox("Unprocessed topics", list(opts.keys()), label_visibility="collapsed")
        if st.button("🎬 Generate for selected (background)"):
            start_background_run(["--topic", opts[sel], "--n", "1"], f"🎬 {opts[sel][:40]}")
            st.rerun()
    else:
        st.info("No unprocessed topics. Fetch trends or add topics on the Topics page.")

    render_background_runs()


def _run_pipeline_ui(topic_keyword, n_shorts, skip_upload, dry_run) -> None:
    from main import run_pipeline

    bar = st.progress(0, text="Starting…")
    status = st.empty()
    result = st.empty()
    try:
        status.info(f"Working on **{topic_keyword}** — content → images → voice → video"
                    f"{'' if skip_upload or dry_run else ' → upload'}.")
        bar.progress(15, text="Running pipeline (this can take a few minutes)…")
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
            result.warning("Pipeline finished but nothing was uploaded. Check the Logs page.")
    except Exception as e:
        bar.progress(0, text="Failed")
        result.error(f"Pipeline error: {e}")
        log.error("Dashboard pipeline error", exc_info=True)


def page_topics() -> None:
    page_header("Topics", "Manage the generation queue: fetch trends, add your own, retire duds.")
    tab1, tab2, tab3 = st.tabs(["💾 Queue", "🌐 Live Trends", "➕ Add Topics"])

    with tab1:
        topics = d_topics(limit=300)
        if not topics:
            st.info("No saved topics yet. Fetch some trends or add your own.")
        else:
            c1, c2, c3 = st.columns([3, 1, 1])
            search = c1.text_input("Search", placeholder="Filter topics…", label_visibility="collapsed")
            only_new = c2.checkbox("Unprocessed only")
            show_failed = c3.checkbox("Failed only")
            df = pd.DataFrame(topics)
            if search:
                df = df[df["keyword"].str.contains(search, case=False, na=False)]
            if only_new:
                df = df[~df["processed"]]
            if show_failed:
                df = df[df["fails"] > 0]

            view = df.assign(
                processed=df["processed"].map({True: "✅", False: "⏳"}),
                uploaded=df["uploaded"].map({True: "✅", False: "—"}),
            )[["id", "keyword", "score", "category", "source", "fails", "processed", "uploaded"]]
            st.dataframe(view.rename(columns=str.title), width="stretch",
                         hide_index=True, height=380)
            st.caption(f"Showing {len(df)} of {len(topics)} topics")

            st.markdown("##### Act on a topic")
            if len(df):
                sel_map = {f"#{r.id} · {r.keyword[:60]}": int(r.id) for r in df.itertuples()}
                pick = st.selectbox("Topic", list(sel_map.keys()), key="topic_selector",
                                    label_visibility="collapsed")
                tid = sel_map[pick]
                a, b, c, d_, e = st.columns(5)
                if a.button("🎬 Generate"):
                    kw = df[df["id"] == tid]["keyword"].iloc[0]
                    start_background_run(["--topic", kw, "--n", "1"], f"🎬 {kw[:40]}")
                    st.toast("Started in background", icon="🎬")
                if b.button("♻️ Requeue"):
                    s = get_session(); TopicRepo(s).requeue(tid); s.close()
                    _bust_cache(); st.rerun()
                if c.button("🛑 Retire"):
                    s = get_session(); TopicRepo(s).mark_processed(tid); s.close()
                    _bust_cache(); st.rerun()
                if d_.button("🗑️ Delete"):
                    s = get_session(); TopicRepo(s).delete(tid); s.close()
                    _bust_cache(); st.rerun()
                if e.button("🧹 Retire all failed"):
                    s = get_session(); repo = TopicRepo(s)
                    for r in df[df["fails"] > 0].itertuples():
                        repo.mark_processed(int(r.id))
                    s.close(); _bust_cache(); st.rerun()

    with tab2:
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
            st.success(f"Found {len(topics)} topics (source: {topics[0].get('source', '?')}).")
            df = pd.DataFrame(topics)
            keep = [c for c in ["keyword", "score", "category", "source"] if c in df.columns]
            st.dataframe(df[keep].rename(columns=str.title), width="stretch",
                         hide_index=True, height=320)
            if st.button("💾 Save all to queue"):
                s = get_session()
                inserted = TopicRepo(s).bulk_insert_new(topics)
                s.close()
                _bust_cache()
                st.toast(f"Saved {inserted} new topics", icon="✅")

    with tab3:
        st.markdown("##### Add your own topics (one per line)")
        manual = st.text_area("Topics", placeholder="Why Indian Trains Honk Differently At Night\nThe Village Where Birds Commit Suicide",
                              label_visibility="collapsed", height=140)
        score = st.slider("Priority score", 0, 100, 95,
                          help="Higher = generated sooner.")
        if st.button("➕ Add to queue", type="primary", disabled=not manual.strip()):
            s = get_session()
            repo = TopicRepo(s)
            added = 0
            for line in manual.splitlines():
                if line.strip() and repo.add_manual(line.strip(), score=float(score)):
                    added += 1
            s.close()
            _bust_cache()
            st.success(f"Added {added} topic(s) to the queue.")

        st.divider()
        st.markdown("##### 🤖 Or let Gemini invent fresh viral topics")
        n_ideas = st.slider("How many", 5, 25, 10)
        if st.button("✨ Generate topic ideas"):
            with st.spinner("Asking Gemini for fresh ideas…"):
                try:
                    from src.content_generator import ContentGenerator
                    s = get_session()
                    existing = sorted(TopicRepo(s).all_keywords())
                    ideas = ContentGenerator().generate_topic_ideas(count=n_ideas, exclude=existing)
                    inserted = TopicRepo(s).bulk_insert_new(ideas) if ideas else 0
                    s.close()
                    _bust_cache()
                    if ideas:
                        st.success(f"Added {inserted} fresh topics to the queue.")
                        st.dataframe(pd.DataFrame(ideas)[["keyword", "category"]]
                                     .rename(columns=str.title), hide_index=True, width="stretch")
                    else:
                        st.error("Gemini returned no ideas — check API keys on the System page.")
                except Exception as e:
                    st.error(f"Idea generation failed: {e}")


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


def _delete_video(video_id: int, remove_files: bool) -> None:
    s = get_session()
    try:
        v = s.get(Video, video_id)
        if not v:
            return
        if remove_files:
            for p in (v.video_path, v.thumbnail_path, v.audio_path):
                if p and Path(p).exists():
                    try:
                        Path(p).unlink()
                    except OSError:
                        pass
        VideoRepo(s).delete(video_id)
    finally:
        s.close()


def page_videos() -> None:
    page_header("Videos", "Preview, upload, retry and manage every rendered Short.")
    tab1, tab2, tab3 = st.tabs(["⬆️ Ready to upload", "📚 Library", "🗂️ Table"])

    with tab1:
        pending = d_pending()
        if not pending:
            st.success("Nothing waiting — every rendered video is uploaded.")
        else:
            st.info(f"{len(pending)} video(s) ready (includes failed uploads for retry).")
            for v in pending:
                col = st.columns([4, 1, 1])
                size = f"{v['size_mb']:.1f} MB" if v["size_mb"] else "-"
                col[0].markdown(
                    f'<div class="card"><div class="row"><span class="title">{v["title"][:48]}</span>'
                    f'{badge(v["status"])}</div><div class="row" style="margin-top:6px">'
                    f'<span class="meta">{size}</span>'
                    f'<span class="meta">{v["error"][:60]}</span></div></div>',
                    unsafe_allow_html=True)
                if col[1].button("Upload", key=f"up_{v['id']}", type="primary"):
                    if _upload_single(v["id"]):
                        _bust_cache(); st.rerun()
            if st.button("⬆️ Upload all", type="primary"):
                ok = sum(_upload_single(v["id"]) for v in pending)
                _bust_cache()
                st.toast(f"Uploaded {ok}/{len(pending)}", icon="✅")
                st.rerun()

    with tab2:
        videos = d_videos(limit=60)
        if not videos:
            st.info("No videos yet.")
        else:
            statuses = sorted({v["status"] for v in videos})
            pick = st.multiselect("Filter status", statuses, default=statuses)
            videos = [v for v in videos if v["status"] in pick]
            for v in videos:
                with st.expander(f"{v['title'][:70]}  ·  {v['status'].upper()}"):
                    c1, c2 = st.columns([1, 2], gap="large")
                    with c1:
                        if v["video_path"] and Path(v["video_path"]).exists():
                            st.video(v["video_path"])
                        elif v["thumbnail_path"] and Path(v["thumbnail_path"]).exists():
                            st.image(v["thumbnail_path"], width=240)
                        else:
                            st.caption("No local media file.")
                    with c2:
                        meta_bits = []
                        if v["created_at"]:
                            meta_bits.append(f"created {v['created_at']:%d %b %H:%M}")
                        if v["size_mb"]:
                            meta_bits.append(f"{v['size_mb']:.1f} MB")
                        if v["cost"]:
                            meta_bits.append(f"${v['cost']:.4f}")
                        st.caption(" · ".join(meta_bits))
                        if v["youtube_url"]:
                            st.link_button("▶ Watch on YouTube", v["youtube_url"])
                        if v["script"]:
                            st.markdown("**Script**")
                            st.write(v["script"])
                        if v["description"]:
                            st.markdown("**Description**")
                            st.text(v["description"][:500])
                        if v["hashtags"]:
                            st.caption(" ".join(v["hashtags"][:10]))
                        if v["error"]:
                            st.error(v["error"][:300])

                        b1, b2, b3 = st.columns(3)
                        if v["status"] in ("generated", "upload_failed"):
                            if b1.button("⬆️ Upload", key=f"lib_up_{v['id']}"):
                                if _upload_single(v["id"]):
                                    _bust_cache(); st.rerun()
                        if b2.button("🗑️ Delete record", key=f"lib_del_{v['id']}"):
                            _delete_video(v["id"], remove_files=False)
                            _bust_cache(); st.rerun()
                        if b3.button("🗑️ Delete + files", key=f"lib_delf_{v['id']}"):
                            _delete_video(v["id"], remove_files=True)
                            _bust_cache(); st.rerun()

    with tab3:
        videos = d_videos(limit=200)
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


@st.cache_data(ttl=120, show_spinner=False)
def d_channel() -> dict:
    try:
        from src.youtube_uploader import YouTubeUploader
        up = YouTubeUploader()
        info = up.get_channel_info()
        recent = up.list_recent_uploads(max_results=6)
        return {"info": info, "recent": recent}
    except Exception as e:
        return {"info": {}, "recent": [], "error": str(e)}


def page_analytics() -> None:
    page_header("Analytics", "Pipeline metrics and your live YouTube channel.")
    tab1, tab2 = st.tabs(["📈 Pipeline (30 days)", "📺 YouTube Channel"])

    with tab1:
        history = d_history(30)
        if not history:
            st.info("No analytics yet. Run the pipeline to start tracking.")
        else:
            df = pd.DataFrame(history)
            total_cost = df["cost"].sum()
            total_gen = int(df["videos_generated"].sum())
            total_up = int(df["videos_uploaded"].sum())
            total_err = int(df["api_errors"].sum())
            kpi_grid([
                {"label": "Generated (30d)", "value": total_gen, "accent": _BLUE},
                {"label": "Uploaded (30d)", "value": total_up, "accent": _GREEN},
                {"label": "API Errors (30d)", "value": total_err, "accent": _RED},
                {"label": "API Cost (30d)", "value": f"${total_cost:.4f}", "accent": _AMBER},
                {"label": "Cost / Video", "value": f"${total_cost / max(total_gen, 1):.4f}", "accent": _ACCENT},
            ])

            st.write("")
            st.markdown("##### Daily activity")
            fig = go.Figure()
            fig.add_bar(x=df["date"], y=df["videos_generated"], name="Generated", marker_color=_BLUE)
            fig.add_bar(x=df["date"], y=df["videos_uploaded"], name="Uploaded", marker_color=_GREEN)
            fig.add_bar(x=df["date"], y=df["api_errors"], name="Errors", marker_color=_RED)
            fig.update_layout(barmode="group", legend=dict(orientation="h", y=1.1))
            styled_chart(fig, height=300)

            if df["cost"].sum() > 0:
                st.markdown("##### Daily API spend")
                fig2 = go.Figure(go.Scatter(
                    x=df["date"], y=df["cost"], fill="tozeroy", mode="lines",
                    line=dict(color=_ACCENT, width=2), fillcolor="rgba(124,92,255,0.2)"))
                styled_chart(fig2, height=240)

    with tab2:
        ch = d_channel()
        if ch.get("error") or not ch["info"]:
            st.warning("Could not reach the YouTube API — check credentials in Settings. "
                       + str(ch.get("error", "")))
        else:
            info = ch["info"]
            kpi_grid([
                {"label": "Channel", "value": info.get("title", "-"), "accent": _ACCENT},
                {"label": "Subscribers", "value": f"{int(info.get('subscribers', 0)):,}", "accent": _GREEN},
                {"label": "Total Views", "value": f"{int(info.get('views', 0)):,}", "accent": _BLUE},
                {"label": "Videos", "value": info.get("videos", "-"), "accent": _AMBER},
            ])
            st.write("")
            st.markdown("##### Latest uploads")
            for r in ch["recent"]:
                cols = st.columns([1, 4])
                if r.get("thumbnail"):
                    cols[0].image(r["thumbnail"], width=120)
                cols[1].markdown(
                    f'<div class="card"><div class="row">'
                    f'<span class="title">{r["title"][:70]}</span>'
                    f'<span class="meta">{r.get("published_at", "")[:10]}</span></div></div>',
                    unsafe_allow_html=True)
                cols[1].link_button("▶ Open", r["url"])
        if st.button("🔄 Refresh channel data"):
            st.cache_data.clear()
            st.rerun()


def page_logs() -> None:
    page_header("Logs", "Pipeline logs — app log plus per-run background logs.")

    log_files = {"app.log (main)": config.LOGS_DIR / "app.log"}
    for p in sorted(_runs_dir().glob("run_*.log"), reverse=True)[:10]:
        log_files[p.name] = p

    c1, c2, c3, c4 = st.columns([2, 1, 1, 1])
    pick = c1.selectbox("Log file", list(log_files.keys()), label_visibility="collapsed")
    level = c2.selectbox("Level", ["ALL", "ERROR", "WARNING", "INFO"], label_visibility="collapsed")
    tail_kb = c3.selectbox("Tail", ["50 KB", "200 KB", "1 MB"], label_visibility="collapsed")
    if c4.button("🔄 Refresh"):
        st.rerun()

    path = log_files[pick]
    if not path.exists():
        st.info("Log file does not exist yet.")
        return

    nbytes = {"50 KB": 50_000, "200 KB": 200_000, "1 MB": 1_000_000}[tail_kb]
    raw = path.read_text(encoding="utf-8", errors="replace")
    text = raw[-nbytes:]
    lines = text.splitlines()
    if level == "ERROR":
        lines = [l for l in lines if "ERROR" in l or "CRITICAL" in l or "Traceback" in l]
    elif level == "WARNING":
        lines = [l for l in lines if "WARNING" in l or "ERROR" in l or "CRITICAL" in l]
    elif level == "INFO":
        lines = [l for l in lines if "INFO" in l]
    search = st.text_input("Search in log", placeholder="filter lines…")
    if search:
        lines = [l for l in lines if search.lower() in l.lower()]

    st.markdown(f'<div class="logbox">{_colorize_log(chr(10).join(lines[-800:]))}</div>',
                unsafe_allow_html=True)
    st.caption(f"{path} · {path.stat().st_size / 1024:.0f} KB on disk · showing last {len(lines[-800:])} lines")
    st.download_button("⬇️ Download full log", raw, file_name=path.name)


def _dir_size_mb(p: Path) -> float:
    try:
        return sum(f.stat().st_size for f in p.rglob("*") if f.is_file()) / 1_048_576
    except OSError:
        return 0.0


def page_system() -> None:
    page_header("System", "Health checks, storage, API keys and content cache.")

    # ── Health ────────────────────────────────────────────────────────────
    from src.key_manager import key_status
    ks = key_status()
    ffmpeg_ok = False
    try:
        import imageio_ffmpeg
        ffmpeg_ok = bool(imageio_ffmpeg.get_ffmpeg_exe())
    except Exception:
        ffmpeg_ok = bool(shutil.which("ffmpeg"))
    missing = config.validate()
    yt_ok = all([config.YOUTUBE_CLIENT_ID, config.YOUTUBE_CLIENT_SECRET,
                 config.YOUTUBE_REFRESH_TOKEN]) or config.YOUTUBE_TOKEN_FILE.exists()

    kpi_grid([
        {"label": "Gemini Keys", "value": ks.get("total_keys", 0),
         "accent": _GREEN if ks.get("total_keys") else _RED,
         "delta": f"active: #{ks.get('active_key_index', 0)}", "delta_flat": True},
        {"label": "YouTube Auth", "value": "OK" if yt_ok else "missing",
         "accent": _GREEN if yt_ok else _RED},
        {"label": "FFmpeg", "value": "OK" if ffmpeg_ok else "missing",
         "accent": _GREEN if ffmpeg_ok else _RED},
        {"label": "Config", "value": "OK" if not missing else f"{len(missing)} missing",
         "accent": _GREEN if not missing else _RED},
        {"label": "Model", "value": config.GEMINI_MODEL.replace("gemini-", ""), "accent": _ACCENT},
    ])
    if missing:
        st.error("Missing required env vars: " + ", ".join(missing))

    st.write("")
    tab1, tab2 = st.tabs(["💽 Storage", "🧠 Content Cache"])

    with tab1:
        rows = []
        for label, p in [("Videos", config.VIDEOS_DIR), ("Images", config.IMAGES_DIR),
                         ("Audio", config.AUDIO_DIR), ("Thumbnails", config.THUMBNAILS_DIR),
                         ("Logs", config.LOGS_DIR)]:
            n_files = sum(1 for f in p.glob("*") if f.is_file())
            rows.append({"Folder": label, "Files": n_files,
                         "Size (MB)": round(_dir_size_mb(p), 1), "Path": str(p)})
        db_mb = config.DB_PATH.stat().st_size / 1_048_576 if config.DB_PATH.exists() else 0
        rows.append({"Folder": "Database", "Files": 1, "Size (MB)": round(db_mb, 2),
                     "Path": str(config.DB_PATH)})
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")

        st.markdown("##### Cleanup")
        a, b, c = st.columns(3)
        if a.button("🧹 Delete rendered videos"):
            n = 0
            for f in config.VIDEOS_DIR.glob("*.mp4"):
                f.unlink(missing_ok=True); n += 1
            st.success(f"Deleted {n} video file(s). DB records stay.")
        if b.button("🧹 Delete scene images"):
            n = 0
            for f in config.IMAGES_DIR.glob("*"):
                if f.is_file():
                    f.unlink(missing_ok=True); n += 1
            st.success(f"Deleted {n} image(s).")
        if c.button("🧹 Delete audio files"):
            n = 0
            for f in config.AUDIO_DIR.glob("*"):
                if f.is_file():
                    f.unlink(missing_ok=True); n += 1
            st.success(f"Deleted {n} audio file(s).")

    with tab2:
        cache_dir = config.DATA_DIR / "content_cache"
        files = sorted(cache_dir.glob("*.json")) if cache_dir.exists() else []
        st.caption(f"{len(files)} cached topic(s) — cache hits cost 0 Gemini tokens. "
                   f"Status: {'ON' if config.CONTENT_CACHE else 'OFF'}")
        if files:
            pick = st.selectbox("Cached topic", [f.stem for f in files])
            sel = cache_dir / f"{pick}.json"
            try:
                st.json(json.loads(sel.read_text(encoding="utf-8")))
            except Exception as e:
                st.error(f"Could not read cache file: {e}")
            a, b = st.columns(2)
            if a.button("🗑️ Delete this entry"):
                sel.unlink(missing_ok=True)
                st.rerun()
            if b.button("🗑️ Clear entire cache"):
                for f in files:
                    f.unlink(missing_ok=True)
                st.rerun()
        else:
            st.info("Cache is empty.")


def page_settings() -> None:
    page_header("Settings", "Keys, OAuth, channel and automation. Secrets live in .env only.")
    tab1, tab2, tab3, tab4 = st.tabs(["🔑 API Keys", "🔐 YouTube OAuth", "📺 Channel & Video", "🤖 Automation"])

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
            langs = ["en", "hi", "hinglish"]
            lang = st.selectbox("Voiceover language", langs,
                                index=langs.index(config.TTS_LANGUAGE) if config.TTS_LANGUAGE in langs else 0)
            c, d_ = st.columns(2)
            cname = c.text_input("Channel name", value=config.CHANNEL_NAME)
            wmark = d_.text_input("Watermark", value=config.CHANNEL_WATERMARK)
            if st.form_submit_button("💾 Save settings", type="primary"):
                _save_env({"TRENDS_GEO": geo, "SHORTS_PER_RUN": str(spr),
                           "YOUTUBE_PRIVACY_STATUS": priv, "TTS_LANGUAGE": lang,
                           "CHANNEL_NAME": cname, "CHANNEL_WATERMARK": wmark})
                st.toast("Settings saved — restart to apply", icon="✅")

    with tab4:
        st.markdown(
            "The cloud pipeline runs on **GitHub Actions every 4 hours** "
            "(`.github/workflows/auto_shorts.yml`). To control it from this dashboard, "
            "add a GitHub token:")
        with st.form("gh_token"):
            tok = st.text_input("GitHub Token (repo + workflow scopes)", type="password",
                                value="set" if (_gh_token()) else "")
            if st.form_submit_button("💾 Save token", type="primary"):
                _save_env({"GITHUB_TOKEN": tok})
                st.cache_data.clear()
                st.toast("Token saved — restart to apply", icon="✅")
        st.caption("Tip: change the schedule by editing the cron line in the workflow file. "
                   "Cloud secrets (API keys used by Actions) live in GitHub → Settings → "
                   "Secrets and variables → Actions.")


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
            # Update the live process too so cached helpers (e.g. _gh_token)
            # pick the new value up right after a cache clear, no restart.
            os.environ[k] = v
    env_path.write_text("".join(f"{k}={v}\n" for k, v in existing.items()), encoding="utf-8")


def _test_gemini() -> None:
    with st.spinner("Testing Gemini…"):
        try:
            from google import genai as genai_client
            from src.key_manager import get_active_key
            client = genai_client.Client(api_key=get_active_key())
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
        n_running = sum(1 for r in st.session_state.get("bg_runs", [])
                        if r["proc"].poll() is None)
        if n_running:
            st.caption(f"⏳ {n_running} background run(s) active")
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
        "Automation": page_automation,
        "Create Video": page_create_video,
        "Topics": page_topics,
        "Videos": page_videos,
        "Analytics": page_analytics,
        "Logs": page_logs,
        "System": page_system,
        "Settings": page_settings,
    }[page]()


if __name__ == "__main__":
    main()
