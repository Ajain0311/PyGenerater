"""
setup_youtube_auth.py
Run this script ONCE locally to perform YouTube OAuth and save your refresh token.
Usage:
  python scripts/setup_youtube_auth.py --secrets path/to/client_secrets.json
  python scripts/setup_youtube_auth.py  # uses YOUTUBE_CLIENT_ID/SECRET env vars
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import config
from src.youtube_uploader import run_oauth_flow, run_oauth_flow_from_config


def main():
    parser = argparse.ArgumentParser(description="Set up YouTube OAuth authentication")
    parser.add_argument("--secrets", type=str, default=None, help="Path to client_secrets.json")
    args = parser.parse_args()

    print("=" * 60)
    print("  YouTube OAuth Setup")
    print("=" * 60)

    if args.secrets:
        secrets_path = Path(args.secrets)
        if not secrets_path.exists():
            print(f"ERROR: File not found: {secrets_path}")
            sys.exit(1)
        print(f"Using secrets file: {secrets_path}")
        creds = run_oauth_flow(str(secrets_path))
    else:
        print("Using YOUTUBE_CLIENT_ID / YOUTUBE_CLIENT_SECRET from environment…")
        if not config.YOUTUBE_CLIENT_ID or not config.YOUTUBE_CLIENT_SECRET:
            print("ERROR: YOUTUBE_CLIENT_ID and YOUTUBE_CLIENT_SECRET must be set in .env")
            sys.exit(1)
        creds = run_oauth_flow_from_config()

    if not creds:
        print("ERROR: Authentication failed.")
        sys.exit(1)

    print()
    print("✅ Authentication successful!")
    print()
    print("Your refresh token (add to GitHub Secrets as YOUTUBE_REFRESH_TOKEN):")
    print("-" * 60)
    print(creds.refresh_token)
    print("-" * 60)
    print()
    print(f"Token also saved to: {config.YOUTUBE_TOKEN_FILE}")
    print()
    print("Next steps:")
    print("  1. Copy the refresh token above.")
    print("  2. Add it to GitHub Secrets → Settings → Secrets → Actions")
    print("  3. Name it: YOUTUBE_REFRESH_TOKEN")
    print("  4. The GitHub Actions workflow will use it automatically.")


if __name__ == "__main__":
    main()
