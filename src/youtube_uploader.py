"""Upload videos to YouTube via Data API v3."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Optional

import google.oauth2.credentials
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

from src.config import config
from src.utils import api_retry, get_logger

log = get_logger(__name__)

SCOPES = ["https://www.googleapis.com/auth/youtube.upload", "https://www.googleapis.com/auth/youtube.readonly"]

CATEGORY_MAP = {
    "entertainment": "24",
    "news": "25",
    "science": "28",
    "education": "27",
    "people": "22",
    "gaming": "20",
}


def _build_credentials_from_token() -> Optional[Credentials]:
    """Build OAuth credentials from stored refresh token."""
    if not all([config.YOUTUBE_CLIENT_ID, config.YOUTUBE_CLIENT_SECRET, config.YOUTUBE_REFRESH_TOKEN]):
        log.error("YouTube OAuth credentials not configured.")
        return None

    creds = Credentials(
        token=None,
        refresh_token=config.YOUTUBE_REFRESH_TOKEN,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=config.YOUTUBE_CLIENT_ID,
        client_secret=config.YOUTUBE_CLIENT_SECRET,
        scopes=SCOPES,
    )
    try:
        creds.refresh(Request())
        return creds
    except Exception as e:
        log.error("Failed to refresh YouTube token: %s", e)
        return None


def _load_credentials_from_file() -> Optional[Credentials]:
    """Load from saved token file."""
    token_file = config.YOUTUBE_TOKEN_FILE
    if not token_file.exists():
        return None
    try:
        with open(token_file) as f:
            data = json.load(f)
        creds = Credentials(
            token=data.get("token"),
            refresh_token=data.get("refresh_token"),
            token_uri=data.get("token_uri", "https://oauth2.googleapis.com/token"),
            client_id=data.get("client_id"),
            client_secret=data.get("client_secret"),
            scopes=data.get("scopes"),
        )
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            _save_credentials(creds)
        return creds
    except Exception as e:
        log.warning("Could not load token file: %s", e)
        return None


def _save_credentials(creds: Credentials) -> None:
    data = {
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "scopes": list(creds.scopes or []),
    }
    config.YOUTUBE_TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(config.YOUTUBE_TOKEN_FILE, "w") as f:
        json.dump(data, f, indent=2)
    log.debug("YouTube token saved to %s", config.YOUTUBE_TOKEN_FILE)


def run_oauth_flow(client_secrets_file: str) -> Credentials:
    """Run local browser OAuth flow and return credentials."""
    flow = InstalledAppFlow.from_client_secrets_file(client_secrets_file, SCOPES)
    creds = flow.run_local_server(port=8080, prompt="consent", access_type="offline")
    _save_credentials(creds)
    log.info("YouTube OAuth completed. Token saved.")
    return creds


def run_oauth_flow_from_config() -> Optional[Credentials]:
    """Run OAuth flow using client ID/secret from env vars."""
    if not all([config.YOUTUBE_CLIENT_ID, config.YOUTUBE_CLIENT_SECRET]):
        return None

    client_config = {
        "installed": {
            "client_id": config.YOUTUBE_CLIENT_ID,
            "client_secret": config.YOUTUBE_CLIENT_SECRET,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost:8080"],
        }
    }
    flow = InstalledAppFlow.from_client_config(client_config, SCOPES)
    creds = flow.run_local_server(port=8080, prompt="consent", access_type="offline")
    _save_credentials(creds)
    return creds


class YouTubeUploader:
    def __init__(self):
        self._service = None

    def _get_service(self):
        if self._service:
            return self._service

        # Try saved token file first
        creds = _load_credentials_from_file()

        # Fall back to env-var refresh token
        if not creds:
            creds = _build_credentials_from_token()

        if not creds:
            raise RuntimeError(
                "YouTube credentials not available. "
                "Run scripts/setup_youtube_auth.py to authenticate."
            )

        self._service = build("youtube", "v3", credentials=creds)
        return self._service

    @api_retry(max_attempts=3, wait_min=10, wait_max=120, exceptions=(HttpError, Exception))
    def upload(
        self,
        video_path: Path,
        title: str,
        description: str,
        tags: list[str],
        thumbnail_path: Optional[Path] = None,
        category_id: str | None = None,
        privacy_status: str | None = None,
    ) -> dict:
        """Upload video to YouTube. Returns upload response dict."""
        service = self._get_service()
        cat_id = category_id or config.YOUTUBE_CATEGORY_ID
        privacy = privacy_status or config.YOUTUBE_PRIVACY_STATUS

        log.info("Uploading video: %s", video_path.name)

        body = {
            "snippet": {
                "title": title[:100],
                "description": description[:5000],
                "tags": tags[:500],
                "categoryId": cat_id,
                "defaultLanguage": "en",
                "defaultAudioLanguage": "en",
            },
            "status": {
                "privacyStatus": privacy,
                "selfDeclaredMadeForKids": False,
            },
        }

        media = MediaFileUpload(
            str(video_path),
            mimetype="video/mp4",
            resumable=True,
            chunksize=1024 * 1024 * 5,  # 5 MB chunks
        )

        request = service.videos().insert(
            part=",".join(body.keys()),
            body=body,
            media_body=media,
        )

        response = None
        retry_count = 0
        while response is None:
            try:
                status, response = request.next_chunk()
                if status:
                    pct = int(status.progress() * 100)
                    log.info("Upload progress: %d%%", pct)
            except HttpError as e:
                if e.resp.status in (500, 502, 503, 504):
                    retry_count += 1
                    if retry_count > 5:
                        raise
                    wait = min(2 ** retry_count, 64)
                    log.warning("Upload error %d, retrying in %ds…", e.resp.status, wait)
                    time.sleep(wait)
                else:
                    raise

        video_id = response.get("id")
        log.info("Upload complete! Video ID: %s", video_id)

        # Upload thumbnail if available
        if thumbnail_path and thumbnail_path.exists() and video_id:
            self._upload_thumbnail(service, video_id, thumbnail_path)

        return {
            "id": video_id,
            "url": f"https://www.youtube.com/shorts/{video_id}",
            "title": title,
        }

    def _upload_thumbnail(self, service, video_id: str, thumbnail_path: Path) -> None:
        try:
            service.thumbnails().set(
                videoId=video_id,
                media_body=MediaFileUpload(str(thumbnail_path), mimetype="image/jpeg"),
            ).execute()
            log.info("Thumbnail uploaded for video %s", video_id)
        except HttpError as e:
            log.warning("Thumbnail upload failed: %s", e)

    def get_channel_info(self) -> dict:
        try:
            service = self._get_service()
            response = service.channels().list(part="snippet,statistics", mine=True).execute()
            items = response.get("items", [])
            if items:
                item = items[0]
                return {
                    "id": item["id"],
                    "title": item["snippet"]["title"],
                    "subscribers": item["statistics"].get("subscriberCount", "0"),
                    "views": item["statistics"].get("viewCount", "0"),
                    "videos": item["statistics"].get("videoCount", "0"),
                }
        except Exception as e:
            log.warning("Could not fetch channel info: %s", e)
        return {}

    def list_recent_uploads(self, max_results: int = 10) -> list[dict]:
        try:
            service = self._get_service()
            channels = service.channels().list(part="contentDetails", mine=True).execute()
            if not channels.get("items"):
                return []
            playlist_id = channels["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]
            playlist_items = (
                service.playlistItems()
                .list(part="snippet", playlistId=playlist_id, maxResults=max_results)
                .execute()
            )
            results = []
            for item in playlist_items.get("items", []):
                snip = item["snippet"]
                vid_id = snip["resourceId"]["videoId"]
                results.append(
                    {
                        "id": vid_id,
                        "title": snip["title"],
                        "url": f"https://www.youtube.com/shorts/{vid_id}",
                        "published_at": snip.get("publishedAt", ""),
                        "thumbnail": snip.get("thumbnails", {}).get("high", {}).get("url", ""),
                    }
                )
            return results
        except Exception as e:
            log.warning("Could not list uploads: %s", e)
            return []
