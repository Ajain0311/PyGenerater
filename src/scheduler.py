"""
Upload scheduler — enforces the manual/automatic upload policy.

Rules (per product spec):
  1. MANUAL uploads are always allowed — no 24h restriction, ever.
  2. AUTOMATIC uploads must never occur within `interval_hours` (default 24h) of
     the last SUCCESSFUL automatic upload. After a successful auto upload the
     next one is due exactly `interval_hours` later.
  3. Manual uploads are INDEPENDENT — they never reset or delay the auto timer.
     Only successful automatic uploads advance `last_auto_at`.

All times are stored as naive UTC (consistent with the rest of the codebase);
`to_local_str` / `fmt_duration` are display helpers for the dashboard.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone


def fmt_duration(seconds: float) -> str:
    """'23h 12m', '4m 03s', 'now'."""
    seconds = int(max(0, seconds))
    if seconds == 0:
        return "now"
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m:02d}m"
    if m:
        return f"{m}m {s:02d}s"
    return f"{s}s"


def to_local_str(dt: datetime | None, fmt: str = "%Y-%m-%d %H:%M") -> str:
    """Format a naive-UTC datetime in the machine's local timezone."""
    if dt is None:
        return "—"
    try:
        local = dt.replace(tzinfo=timezone.utc).astimezone()
        return local.strftime(fmt)
    except Exception:
        return dt.strftime(fmt)


class UploadScheduler:
    def __init__(self, session=None):
        from src.database import get_session
        self.session = session or get_session()

    # ── singleton state row ────────────────────────────────────────────────
    def _row(self):
        from src.database import UploadSchedule
        row = self.session.get(UploadSchedule, 1)
        if row is None:
            row = UploadSchedule(id=1, auto_enabled=True, interval_hours=24.0)
            self.session.add(row)
            self.session.commit()
        return row

    @property
    def interval(self) -> timedelta:
        return timedelta(hours=float(self._row().interval_hours or 24.0))

    # ── settings ───────────────────────────────────────────────────────────
    def is_auto_enabled(self) -> bool:
        return bool(self._row().auto_enabled)

    def set_auto_enabled(self, on: bool) -> None:
        r = self._row()
        r.auto_enabled = bool(on)
        r.updated_at = datetime.utcnow()
        self.session.commit()

    def set_interval_hours(self, hours: float) -> None:
        r = self._row()
        r.interval_hours = max(0.0, float(hours))
        r.updated_at = datetime.utcnow()
        self.session.commit()

    # ── timer maths ────────────────────────────────────────────────────────
    def next_auto_at(self) -> datetime | None:
        """When the next AUTOMATIC upload becomes due. None → eligible now
        (no prior auto upload yet)."""
        r = self._row()
        if r.last_auto_at is None:
            return None
        return r.last_auto_at + self.interval

    def seconds_until_next_auto(self, now: datetime | None = None) -> float:
        now = now or datetime.utcnow()
        nxt = self.next_auto_at()
        if nxt is None:
            return 0.0
        return max(0.0, (nxt - now).total_seconds())

    def can_auto_upload(self, now: datetime | None = None) -> tuple[bool, str]:
        """Gate for AUTOMATIC uploads only (manual bypasses this entirely)."""
        now = now or datetime.utcnow()
        r = self._row()
        if not r.auto_enabled:
            return False, "Automatic upload is disabled."
        if r.last_auto_at is None:
            return True, "No previous automatic upload — eligible now."
        elapsed = now - r.last_auto_at
        if elapsed >= self.interval:
            return True, f"{fmt_duration(elapsed.total_seconds())} since last auto upload — due."
        remaining = (self.interval - elapsed).total_seconds()
        return False, (f"Only {fmt_duration(elapsed.total_seconds())} since last auto "
                       f"upload; wait {fmt_duration(remaining)}.")

    # ── record events ──────────────────────────────────────────────────────
    def record_manual_upload(self, when: datetime | None = None, video_id: int | None = None) -> None:
        """Stamp a manual upload. Deliberately does NOT touch the auto timer."""
        r = self._row()
        r.last_manual_at = when or datetime.utcnow()
        r.updated_at = datetime.utcnow()
        self.session.commit()

    def record_auto_upload(self, when: datetime | None = None, video_id: int | None = None) -> None:
        """Stamp a SUCCESSFUL automatic upload — advances the 24h gate."""
        r = self._row()
        w = when or datetime.utcnow()
        r.last_auto_at = w
        r.last_auto_video_id = video_id
        r.updated_at = w
        self.session.commit()

    # ── dashboard view ─────────────────────────────────────────────────────
    def status(self, now: datetime | None = None) -> dict:
        now = now or datetime.utcnow()
        r = self._row()
        can, reason = self.can_auto_upload(now)
        nxt = self.next_auto_at()
        secs = self.seconds_until_next_auto(now)
        return {
            "auto_enabled": bool(r.auto_enabled),
            "interval_hours": float(r.interval_hours or 24.0),
            "last_manual_at": r.last_manual_at,
            "last_auto_at": r.last_auto_at,
            "next_auto_at": nxt,                       # None → ready now
            "seconds_until_next_auto": secs,
            "countdown": "ready now" if secs <= 0 else fmt_duration(secs),
            "can_auto_now": can,
            "reason": reason,
            # pre-formatted local strings for direct display
            "last_manual_str": to_local_str(r.last_manual_at),
            "last_auto_str": to_local_str(r.last_auto_at),
            "next_auto_str": ("ready now" if nxt is None and r.auto_enabled
                              else "disabled" if not r.auto_enabled
                              else to_local_str(nxt)),
        }
