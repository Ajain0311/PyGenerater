"""
Headless smoke test for the Streamlit dashboard.

Uses Streamlit's AppTest harness to actually RUN dashboard/app.py in-process and
assert it raises no exception on initial load AND when navigating to every page.
This catches import errors, bad API calls (e.g. deprecated st args), template
mistakes, and detached-ORM bugs without a browser.

    python scripts/test_dashboard.py        # exit 0 = pass, 1 = fail
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from streamlit.testing.v1 import AppTest

APP = str(ROOT / "dashboard" / "app.py")
PAGES = ["Dashboard", "Automation", "Create Video", "Topics", "Videos",
         "Analytics", "Logs", "System", "Settings"]


def _exc(at) -> str | None:
    return f"{at.exception[0].type}: {at.exception[0].value}" if at.exception else None


def main() -> int:
    failures: list[str] = []

    # 1. Initial load (lands on Dashboard).
    at = AppTest.from_file(APP, default_timeout=90).run()
    if _exc(at):
        print(f"[FAIL] initial load -> {_exc(at)}")
        return 1
    print("[ok]   initial load (Dashboard)")

    # 2. Navigate to every page via the sidebar radio and assert clean render.
    radios = at.sidebar.radio
    if not radios:
        print("[FAIL] no sidebar radio found - navigation broken")
        return 1
    options = list(radios[0].options)

    for page in PAGES:
        match = next((o for o in options if page in o), None)
        if match is None:
            failures.append(f"page '{page}' missing from nav")
            continue
        at = at.sidebar.radio[0].set_value(match).run()
        err = _exc(at)
        if err:
            failures.append(f"{page} -> {err}")
            print(f"[FAIL] {page} -> {err}")
        else:
            print(f"[ok]   {page}")

    if failures:
        print(f"\nFAILED ({len(failures)}):")
        for f in failures:
            print("  -", f)
        return 1

    print("\nPASS - dashboard renders cleanly on all pages.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
