# 🎬 YouTube Shorts Auto-Generator

A fully automated AI-powered system that fetches trending topics from Google Trends, generates complete YouTube Shorts (script → images → voiceover → video → upload) using the Gemini API, and uploads them to YouTube — all on autopilot via GitHub Actions every 4 hours.

---

## ✨ Features

| Feature | Details |
|---------|---------|
| **Trending Topics** | Google Trends (India + any region), duplicate-filtered, scored |
| **AI Content** | Gemini 1.5 Pro: script, title, description, hashtags, image prompts |
| **Images** | Gemini imagen → Unsplash fallback → gradient fallback |
| **Voiceover** | gTTS (free, Indian English accent) |
| **Video** | MoviePy — 1080×1920 Shorts, subtitles, transitions |
| **Thumbnail** | Pillow-generated, branded, topic-aware |
| **Upload** | YouTube Data API v3, OAuth2, resumable upload |
| **Database** | SQLite (SQLAlchemy) — topics, videos, analytics |
| **Dashboard** | Streamlit control center — automation, topics, videos, analytics, logs, system |
| **Automation** | GitHub Actions — every 4 hours, manual dispatch, dashboard trigger |
| **Reliability** | Retry logic, error handling, quota awareness |
| **Cost Tracking** | Token usage and USD cost per video |

---

## 📁 Project Structure

```
PyGenerater/
├── src/
│   ├── config.py              # All config from env vars
│   ├── database.py            # SQLAlchemy models & repositories
│   ├── trends.py              # Google Trends integration
│   ├── content_generator.py   # Gemini AI content generation
│   ├── image_generator.py     # Scene image generation
│   ├── voiceover.py           # gTTS voiceover
│   ├── video_generator.py     # MoviePy video rendering
│   ├── thumbnail_generator.py # Pillow thumbnail creation
│   ├── youtube_uploader.py    # YouTube Data API v3
│   ├── analytics.py           # Scoring & cost tracking
│   └── utils.py               # Logging, retry decorators
├── dashboard/
│   └── app.py                 # Streamlit dashboard
├── scripts/
│   ├── setup_youtube_auth.py  # One-time OAuth setup
│   └── test_pipeline.py       # Integration tests
├── .github/
│   └── workflows/
│       └── auto_shorts.yml    # GitHub Actions cron workflow
├── data/                      # SQLite database
├── logs/                      # Application logs
├── generated/
│   ├── audio/                 # MP3 voiceovers
│   ├── images/                # Scene images
│   ├── videos/                # Final MP4 Shorts
│   └── thumbnails/            # JPEG thumbnails
├── main.py                    # CLI entrypoint
├── requirements.txt
└── .env.example
```

---

## 🚀 Quick Start — Local Setup

### 1. Clone and Install

```bash
git clone https://github.com/YOUR_USERNAME/PyGenerater.git
cd PyGenerater
python -m venv venv

# Windows
venv\Scripts\activate

# Linux/Mac
source venv/bin/activate

pip install -r requirements.txt
```

### 2. Install System Dependencies

**Windows:**
- Download and install [ffmpeg](https://ffmpeg.org/download.html) and add to PATH.
- Download and install [ImageMagick](https://imagemagick.org/script/download.php) (for MoviePy text clips).

**Linux/Mac:**
```bash
sudo apt-get install ffmpeg imagemagick fonts-dejavu  # Ubuntu
brew install ffmpeg imagemagick                        # Mac
```

### 3. Configure Environment Variables

```bash
cp .env.example .env
```

Edit `.env` with your credentials:

```env
GEMINI_API_KEY=your_key_here
YOUTUBE_CLIENT_ID=your_client_id
YOUTUBE_CLIENT_SECRET=your_client_secret
YOUTUBE_REFRESH_TOKEN=your_refresh_token
UNSPLASH_ACCESS_KEY=your_unsplash_key  # optional
```

### 4. Run Integration Tests

```bash
python scripts/test_pipeline.py
```

All green? You're ready to go.

### 5. Run the Pipeline

```bash
# Auto-fetch trending topics and generate 1 Short
python main.py

# Generate for a specific topic
python main.py --topic "India Wins T20 World Cup"

# Generate 3 Shorts
python main.py --n 3

# Generate without uploading
python main.py --skip-upload

# Content-only dry run
python main.py --dry-run
```

### 6. Launch the Dashboard

```bash
streamlit run dashboard/app.py
```

Open [http://localhost:8501](http://localhost:8501)

---

## 🔑 API Setup Guides

### Gemini API Key

1. Go to [Google AI Studio](https://aistudio.google.com/apikey)
2. Click **Create API Key**
3. Copy and add to `.env` as `GEMINI_API_KEY`

> **Cost:** Gemini 1.5 Pro = ~$3.50/1M input tokens + $10.50/1M output tokens.
> Typical video: ~2000 tokens input, ~800 output ≈ **$0.015/video**

### Unsplash Access Key (Optional — Image Fallback)

1. Go to [Unsplash Developers](https://unsplash.com/developers)
2. Create an app → copy Access Key
3. Add to `.env` as `UNSPLASH_ACCESS_KEY`
4. Free tier: 50 requests/hour

### YouTube OAuth 2.0 Setup

#### Step 1: Create Google Cloud Project

1. Go to [Google Cloud Console](https://console.cloud.google.com)
2. Create a new project (e.g. `YouTube Shorts Bot`)
3. Enable the **YouTube Data API v3**:
   - APIs & Services → Enable APIs → search "YouTube Data API v3" → Enable

#### Step 2: Create OAuth Credentials

1. APIs & Services → Credentials → Create Credentials → **OAuth 2.0 Client IDs**
2. Application type: **Desktop app**
3. Name: `Shorts Bot`
4. Download the JSON file (or note the Client ID and Secret)

#### Step 3: Configure OAuth Consent Screen

1. APIs & Services → OAuth consent screen
2. User Type: **External**
3. Fill in App name, support email
4. Add scope: `.../auth/youtube.upload`
5. Add your Gmail as a **Test User**

#### Step 4: Get Refresh Token

**Option A — Using client_secrets.json:**
```bash
python scripts/setup_youtube_auth.py --secrets /path/to/client_secrets.json
```

**Option B — Using env vars:**
```bash
# First add CLIENT_ID and CLIENT_SECRET to .env, then:
python scripts/setup_youtube_auth.py
```

A browser window opens. Sign in and grant permissions.
The script prints your **refresh token** — copy it.

Add to `.env`:
```env
YOUTUBE_REFRESH_TOKEN=1//0abc...xyz
```

---

## ⚙️ GitHub Actions Automation

### Step 1: Push to GitHub

```bash
git init
git add .
git commit -m "Initial commit"
git remote add origin https://github.com/YOUR_USERNAME/PyGenerater.git
git push -u origin main
```

### Step 2: Add GitHub Secrets

Go to your repo → **Settings → Secrets and variables → Actions → New repository secret**

Add these secrets:

| Secret Name | Value |
|-------------|-------|
| `GEMINI_API_KEY` | Your Gemini API key |
| `YOUTUBE_CLIENT_ID` | Your YouTube OAuth client ID |
| `YOUTUBE_CLIENT_SECRET` | Your YouTube OAuth client secret |
| `YOUTUBE_REFRESH_TOKEN` | Refresh token from setup script |
| `UNSPLASH_ACCESS_KEY` | Unsplash key (optional) |

### Step 3: Add GitHub Variables (Optional)

Go to **Settings → Secrets and variables → Actions → Variables**

| Variable | Default | Description |
|----------|---------|-------------|
| `TRENDS_GEO` | `IN` | Trends region |
| `SHORTS_PER_RUN` | `1` | Videos per run |
| `YOUTUBE_PRIVACY_STATUS` | `public` | Upload privacy |
| `CHANNEL_NAME` | `TrendSnap AI` | Your channel name |
| `CHANNEL_WATERMARK` | `@TrendSnapAI` | Watermark text |

### Step 4: Enable Actions

1. Go to repo → **Actions** tab
2. Click **Enable Actions**
3. The workflow runs automatically every 4 hours
4. Or trigger manually: **Actions → Auto YouTube Shorts Generator → Run workflow**

---

## 🖥️ Streamlit Dashboard Guide

The dashboard is a full project control center.

| Page | Features |
|------|----------|
| **Dashboard** | KPI metrics, recent videos, last automation run + next scheduled run |
| **Automation** | Trigger GitHub Actions runs from the UI, monitor/cancel recent runs |
| **Create Video** | Manual topic entry, background (non-blocking) generation & upload |
| **Topics** | Queue management — fetch trends, add manual topics, Gemini topic ideas, requeue/retire/delete |
| **Videos** | Library with inline video preview, script view, retry failed uploads, bulk upload, delete |
| **Analytics** | Daily charts, cost tracking, error counts, live YouTube channel stats |
| **Logs** | App + per-run logs with level filter, search and download |
| **System** | Health checks (keys, ffmpeg, auth), storage usage, cleanup, content cache manager |
| **Settings** | API keys, YouTube OAuth, channel config, GitHub token for automation control |

> For the **Automation** page, set `GITHUB_TOKEN` in `.env` (classic token with
> `repo` + `workflow` scopes), or be logged in once via `gh auth login`.

### Hosting the Dashboard

**Option A — Streamlit Community Cloud (Free)**
1. Go to [share.streamlit.io](https://share.streamlit.io) → **Sign in with GitHub**
2. **Create app** → repo `Ajain0311/PyGenerater` · branch `main` · main file `dashboard/app.py`
3. **Advanced settings → Secrets** — paste (TOML format, quotes required):
   ```toml
   GEMINI_API_KEY = "your_gemini_key"
   GITHUB_TOKEN = "ghp_xxx"            # classic token, repo + workflow scopes
   GITHUB_REPOSITORY = "Ajain0311/PyGenerater"
   YOUTUBE_CLIENT_ID = "xxx.apps.googleusercontent.com"
   YOUTUBE_CLIENT_SECRET = "xxx"
   YOUTUBE_REFRESH_TOKEN = "xxx"
   UNSPLASH_ACCESS_KEY = "xxx"
   CHANNEL_NAME = "Your Channel"
   CHANNEL_WATERMARK = "@YourHandle"
   ```
4. **Deploy** — every push to `main` auto-redeploys the app.

> Hosted-mode note: the app's SQLite DB is ephemeral and separate from the CI
> database, so Topics/Videos history starts empty there. The pages that shine
> when hosted: **Automation** (trigger/monitor the 4-hour GitHub Actions
> pipeline — including custom-topic runs), **Analytics → YouTube Channel**
> (live channel stats), and **Logs/System**. For heavy local rendering, run
> the dashboard on your PC instead.

**Option B — Local network**
```bash
streamlit run dashboard/app.py --server.port 8501 --server.address 0.0.0.0
```

**Option C — Render / Railway / Fly.io**
Add a `Procfile`:
```
web: streamlit run dashboard/app.py --server.port $PORT --server.address 0.0.0.0
```

---

## 🧠 Architecture

```
main.py (CLI) / dashboard/app.py (UI)
          ↓
    ┌─────────────────┐
    │  TrendsFetcher  │ ← Google Trends API
    └────────┬────────┘
             ↓ topics
    ┌─────────────────┐
    │ContentGenerator │ ← Gemini 1.5 Pro
    └────────┬────────┘
             ↓ script, prompts, metadata
    ┌──────────────────┐  ┌──────────────┐
    │  ImageGenerator  │  │  Voiceover   │ ← gTTS
    │  Gemini/Unsplash │  │  Generator   │
    └────────┬─────────┘  └──────┬───────┘
             └─────────┬──────────┘
                       ↓ images + audio
             ┌──────────────────┐
             │ VideoGenerator   │ ← MoviePy
             │ ThumbnailGen     │ ← Pillow
             └────────┬─────────┘
                      ↓ MP4 + thumbnail
             ┌──────────────────┐
             │ YouTubeUploader  │ ← YouTube Data API v3
             └────────┬─────────┘
                      ↓
                  YouTube ✅
                      ↓
             ┌──────────────────┐
             │  SQLite Database │ ← Analytics, history
             └──────────────────┘
```

---

## 🛡️ Reliability Features

- **Retry logic**: All API calls use exponential backoff (tenacity)
- **Duplicate prevention**: Topics tracked in SQLite, never reprocessed
- **Fallback chain**: Gemini image → Unsplash → gradient background
- **Resume-safe**: Generated files cached; re-run skips completed steps
- **Quota awareness**: HTTP 429/503 errors trigger intelligent retry
- **Comprehensive logging**: Timestamped logs to file + coloured console

---

## 💰 Cost Estimate

Per video (typical):
- Gemini 1.5 Pro (content): ~$0.015
- Gemini imagen (optional): ~$0.04
- Unsplash (free tier): $0
- gTTS: Free
- YouTube API: Free (10,000 units/day quota)

**At 4 videos/day: ~$0.06–$0.22/day** (with Gemini images)
**At 4 videos/day: ~$0.06/day** (without Gemini images / using Unsplash)

---

## 🔧 Troubleshooting

| Problem | Solution |
|---------|---------|
| `ffmpeg not found` | Install ffmpeg and add to system PATH |
| `quota exceeded` | Reduce SHORTS_PER_RUN or wait 24h |
| `OAuth token expired` | Re-run `scripts/setup_youtube_auth.py` |
| `Video too short` | Check gTTS internet connectivity |
| `ImageMagick policy error` | Edit `/etc/ImageMagick-6/policy.xml`, remove `<policy domain="path" rights="none" pattern="@*"/>` |
| `ModuleNotFoundError` | Run `pip install -r requirements.txt` |

---

## 📄 License

MIT License — see LICENSE file.

---

## 🤝 Contributing

PRs welcome! See [CONTRIBUTING.md](CONTRIBUTING.md).

---

*Built with ❤️ using Gemini AI, MoviePy, and Streamlit.*
