"""SQLAlchemy models and database helpers."""

from __future__ import annotations

import json
from datetime import datetime, date
from pathlib import Path
from typing import Optional

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Session, relationship, sessionmaker

from src.config import config
from src.utils import get_logger

log = get_logger(__name__)

_ENGINE = None
_Session = None


def get_engine():
    global _ENGINE
    if _ENGINE is None:
        if config.DATABASE_URL:
            # Postgres/Supabase: pre_ping survives pooler-dropped connections;
            # recycle avoids stale connections behind the connection pooler.
            _ENGINE = create_engine(
                config.DATABASE_URL, pool_pre_ping=True, pool_recycle=300,
            )
            log.debug("Database engine created (external: %s)",
                      config.DATABASE_URL.split("@")[-1])
        else:
            _ENGINE = create_engine(
                f"sqlite:///{config.DB_PATH}",
                connect_args={"check_same_thread": False},
            )
            log.debug("Database engine created at %s", config.DB_PATH)
    return _ENGINE


def is_sqlite() -> bool:
    return not config.DATABASE_URL


def get_session() -> Session:
    global _Session
    if _Session is None:
        _Session = sessionmaker(bind=get_engine(), expire_on_commit=False)
    return _Session()


class Base(DeclarativeBase):
    pass


# ── Models ────────────────────────────────────────────────────────────────────

class Topic(Base):
    __tablename__ = "topics"

    id = Column(Integer, primary_key=True, autoincrement=True)
    keyword = Column(String(500), nullable=False, unique=True)
    category = Column(String(100))
    geo = Column(String(10), default="IN")
    score = Column(Float, default=0.0)
    rank = Column(Integer, default=0)
    source = Column(String(50), default="google_trends")
    is_processed = Column(Boolean, default=False)
    is_uploaded = Column(Boolean, default=False)
    fail_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
    processed_at = Column(DateTime, nullable=True)

    videos = relationship("Video", back_populates="topic")

    def __repr__(self) -> str:
        return f"<Topic id={self.id} keyword={self.keyword!r} score={self.score}>"


class Video(Base):
    __tablename__ = "videos"

    id = Column(Integer, primary_key=True, autoincrement=True)
    topic_id = Column(Integer, ForeignKey("topics.id"), nullable=True)
    title = Column(String(500))
    description = Column(Text)
    script = Column(Text)
    hashtags = Column(Text)        # JSON list
    thumbnail_text = Column(String(200))
    audio_path = Column(String(500))
    video_path = Column(String(500))
    thumbnail_path = Column(String(500))
    youtube_id = Column(String(50), nullable=True)
    youtube_url = Column(String(200), nullable=True)
    status = Column(String(30), default="pending")
    # status: pending | generating | generated | uploading | uploaded | failed
    kind = Column(String(20), default="trends")   # "kids" | "trends"
    story_id = Column(Integer, ForeignKey("stories.id"), nullable=True)
    error_message = Column(Text, nullable=True)
    gemini_input_tokens = Column(Integer, default=0)
    gemini_output_tokens = Column(Integer, default=0)
    estimated_cost_usd = Column(Float, default=0.0)
    created_at = Column(DateTime, default=datetime.utcnow)
    uploaded_at = Column(DateTime, nullable=True)

    topic = relationship("Topic", back_populates="videos")

    @property
    def hashtags_list(self) -> list[str]:
        try:
            return json.loads(self.hashtags or "[]")
        except (json.JSONDecodeError, TypeError):
            return []

    @hashtags_list.setter
    def hashtags_list(self, value: list[str]) -> None:
        self.hashtags = json.dumps(value)


class DailyAnalytics(Base):
    __tablename__ = "daily_analytics"

    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(Date, default=date.today, unique=True)
    topics_fetched = Column(Integer, default=0)
    videos_generated = Column(Integer, default=0)
    videos_uploaded = Column(Integer, default=0)
    api_errors = Column(Integer, default=0)
    total_cost_usd = Column(Float, default=0.0)
    notes = Column(Text, nullable=True)


# ── Kids-cartoon domain ─────────────────────────────────────────────────────--

class Character(Base):
    """A reusable, ORIGINAL cartoon character. The appearance_prompt + seed are
    what keep the same character looking consistent across scenes and videos."""

    __tablename__ = "characters"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False, unique=True)
    slug = Column(String(120), unique=True)
    species = Column(String(120))          # "baby elephant", "parrot", "little girl"
    personality = Column(Text)             # drives the Dialogue agent
    description = Column(Text)             # short bio shown in the dashboard
    clothes = Column(Text)                # wardrobe — part of visual identity
    appearance_prompt = Column(Text)       # CONSISTENCY tokens for image gen
    negative_prompt = Column(Text, default="")
    seed = Column(Integer, default=0)      # fixed SD seed → stable look
    reference_image = Column(String(500))  # portrait/turnaround path
    # Voice identity (per-character, used by the Voice agent)
    voice_engine = Column(String(40), default="edge")     # edge | piper | coqui
    voice_id = Column(String(120), default="")            # e.g. hi-IN-MadhurNeural
    voice_rate = Column(String(12), default="+0%")
    voice_pitch = Column(String(12), default="+0Hz")
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    def __repr__(self) -> str:
        return f"<Character id={self.id} name={self.name!r} species={self.species!r}>"


class Story(Base):
    """A generated kids-cartoon story. package_json holds the full StoryPackage
    (the single source of truth the renderer + dashboard read back)."""

    __tablename__ = "stories"

    id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(String(300))
    category = Column(String(60))
    language = Column(String(10), default="hi")
    logline = Column(Text)
    moral = Column(Text)
    characters = Column(Text)         # JSON list of character names
    package_json = Column(Text)       # full StoryPackage as JSON
    status = Column(String(30), default="draft")   # draft | ready | used
    created_at = Column(DateTime, default=datetime.utcnow)

    @property
    def characters_list(self) -> list[str]:
        try:
            return json.loads(self.characters or "[]")
        except (json.JSONDecodeError, TypeError):
            return []


class PipelineRun(Base):
    """One end-to-end generation attempt. Persisted per-step so a crashed or
    partial run can RESUME, and any single failed step can be REGENERATED."""

    __tablename__ = "pipeline_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_uid = Column(String(80), unique=True, nullable=False)
    mode = Column(String(20), default="kids")
    video_id = Column(Integer, ForeignKey("videos.id"), nullable=True)
    story_id = Column(Integer, ForeignKey("stories.id"), nullable=True)
    status = Column(String(30), default="running")  # running|done|failed|partial
    current_step = Column(String(40))
    steps_json = Column(Text)         # [{name,status,artifact,error,ts}, …]
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

    @property
    def steps(self) -> list[dict]:
        try:
            return json.loads(self.steps_json or "[]")
        except (json.JSONDecodeError, TypeError):
            return []


class UploadSchedule(Base):
    """Singleton (id=1) holding the upload scheduler state.

    Manual and automatic uploads are tracked INDEPENDENTLY: only a successful
    *automatic* upload advances `last_auto_at` (and therefore the 24h gate);
    manual uploads only stamp `last_manual_at` and never affect the auto timer.
    """

    __tablename__ = "upload_schedule"

    id = Column(Integer, primary_key=True, autoincrement=True)
    auto_enabled = Column(Boolean, default=True)
    interval_hours = Column(Float, default=24.0)
    last_manual_at = Column(DateTime, nullable=True)
    last_auto_at = Column(DateTime, nullable=True)     # last SUCCESSFUL auto upload
    last_auto_video_id = Column(Integer, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow)


# ── Bootstrap ─────────────────────────────────────────────────────────────────

def init_db() -> None:
    """Create all tables if they don't exist, then apply column migrations."""
    engine = get_engine()
    Base.metadata.create_all(engine)
    _migrate_columns(engine)
    log.info("Database initialised at %s", config.DB_PATH)


def _migrate_columns(engine) -> None:
    """create_all() never alters existing tables, so additive columns are added
    by hand. DB-agnostic: column detection uses SQLAlchemy's inspector (works on
    both SQLite and Postgres); the ALTER DDL below is valid on both. On a fresh
    Postgres DB create_all already made every column, so this is a no-op there."""
    from sqlalchemy import inspect as sa_inspect

    migrations = {
        "topics": {"fail_count": "INTEGER DEFAULT 0"},
        # Kids-mode tags the video kind and links it to its Story (additive,
        # so legacy rows simply default to 'trends'/NULL).
        "videos": {
            "kind": "VARCHAR(20) DEFAULT 'trends'",
            "story_id": "INTEGER",
        },
    }
    insp = sa_inspect(engine)
    tables = set(insp.get_table_names())
    for table, columns in migrations.items():
        if table not in tables:
            continue
        existing = {c["name"] for c in insp.get_columns(table)}
        for col, ddl in columns.items():
            if col not in existing:
                with engine.begin() as conn:
                    conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")
                log.info("DB migration: added %s.%s", table, col)


# ── Repository helpers ────────────────────────────────────────────────────────

class TopicRepo:
    def __init__(self, session: Session):
        self.session = session

    def exists(self, keyword: str) -> bool:
        return self.session.query(Topic).filter_by(keyword=keyword).first() is not None

    def bulk_insert_new(self, topics: list[dict]) -> int:
        """Insert topics that don't already exist. Returns count inserted."""
        valid = {c.name for c in Topic.__table__.columns}
        inserted = 0
        for t in topics:
            if not self.exists(t["keyword"]):
                self.session.add(Topic(**{k: v for k, v in t.items() if k in valid}))
                inserted += 1
        self.session.commit()
        return inserted

    def get_unprocessed(self, limit: int = 5) -> list[Topic]:
        return (
            self.session.query(Topic)
            .filter_by(is_processed=False)
            .order_by(Topic.score.desc())
            .limit(limit)
            .all()
        )

    def mark_processed(self, topic_id: int) -> None:
        topic = self.session.get(Topic, topic_id)
        if topic:
            topic.is_processed = True
            topic.processed_at = datetime.utcnow()
            self.session.commit()

    def mark_failed(self, topic_id: int, max_failures: int = 3) -> int:
        """Record a generation failure. After max_failures the topic is
        retired (marked processed) so it can't block the queue forever.
        Returns the new fail count."""
        topic = self.session.get(Topic, topic_id)
        if not topic:
            return 0
        topic.fail_count = (topic.fail_count or 0) + 1
        if topic.fail_count >= max_failures:
            topic.is_processed = True
            topic.processed_at = datetime.utcnow()
            log.warning("Topic %r retired after %d failures", topic.keyword, topic.fail_count)
        self.session.commit()
        return topic.fail_count

    def add_manual(self, keyword: str, score: float = 100.0, category: str = "manual") -> Optional[Topic]:
        """Add a user-supplied topic to the queue. Returns None if it exists."""
        if self.exists(keyword):
            return None
        topic = Topic(keyword=keyword, score=score, category=category,
                      geo=config.TRENDS_GEO, source="manual")
        self.session.add(topic)
        self.session.commit()
        return topic

    def requeue(self, topic_id: int) -> None:
        """Put a processed/retired topic back into the unprocessed queue."""
        topic = self.session.get(Topic, topic_id)
        if topic:
            topic.is_processed = False
            topic.fail_count = 0
            topic.processed_at = None
            self.session.commit()

    def delete(self, topic_id: int) -> None:
        topic = self.session.get(Topic, topic_id)
        if topic:
            self.session.delete(topic)
            self.session.commit()

    def count_unprocessed(self) -> int:
        return self.session.query(Topic).filter_by(is_processed=False).count()

    def all_keywords(self) -> set[str]:
        rows = self.session.query(Topic.keyword).all()
        return {r[0] for r in rows}

    def get_all(self, limit: int = 100, offset: int = 0) -> list[Topic]:
        return (
            self.session.query(Topic)
            .order_by(Topic.created_at.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )


class VideoRepo:
    def __init__(self, session: Session):
        self.session = session

    def create(self, **kwargs) -> Video:
        v = Video(**kwargs)
        self.session.add(v)
        self.session.commit()
        return v

    def update_status(self, video_id: int, status: str, **extra) -> None:
        v = self.session.get(Video, video_id)
        if v:
            v.status = status
            for k, val in extra.items():
                setattr(v, k, val)
            self.session.commit()

    def get_all(self, limit: int = 50) -> list[Video]:
        return (
            self.session.query(Video)
            .order_by(Video.created_at.desc())
            .limit(limit)
            .all()
        )

    def get_pending_uploads(self) -> list[Video]:
        return (
            self.session.query(Video)
            .filter(Video.status.in_(["generated", "upload_failed"]))
            .order_by(Video.created_at.asc())
            .all()
        )

    def get_by_id(self, video_id: int) -> Optional[Video]:
        return self.session.get(Video, video_id)

    def get_by_status(self, status: str, limit: int = 50) -> list[Video]:
        return (
            self.session.query(Video)
            .filter_by(status=status)
            .order_by(Video.created_at.desc())
            .limit(limit)
            .all()
        )

    def delete(self, video_id: int) -> None:
        v = self.session.get(Video, video_id)
        if v:
            self.session.delete(v)
            self.session.commit()

    def total_cost(self) -> float:
        return self.session.query(func.coalesce(func.sum(Video.estimated_cost_usd), 0.0)).scalar()

    def count_by_status(self) -> dict[str, int]:
        rows = (
            self.session.query(Video.status, func.count(Video.id))
            .group_by(Video.status)
            .all()
        )
        return dict(rows)


class AnalyticsRepo:
    def __init__(self, session: Session):
        self.session = session

    def today(self) -> DailyAnalytics:
        today = date.today()
        row = self.session.query(DailyAnalytics).filter_by(date=today).first()
        if not row:
            row = DailyAnalytics(date=today)
            self.session.add(row)
            self.session.commit()
        return row

    def increment(self, field: str, amount: float | int = 1) -> None:
        row = self.today()
        current = getattr(row, field, 0) or 0
        setattr(row, field, current + amount)
        self.session.commit()

    def get_history(self, days: int = 30) -> list[DailyAnalytics]:
        return (
            self.session.query(DailyAnalytics)
            .order_by(DailyAnalytics.date.desc())
            .limit(days)
            .all()
        )


# ── Kids-domain repositories ────────────────────────────────────────────────--

class CharacterRepo:
    def __init__(self, session: Session):
        self.session = session

    def create(self, **kwargs) -> Character:
        c = Character(**kwargs)
        self.session.add(c)
        self.session.commit()
        return c

    def upsert(self, **kwargs) -> Character:
        """Insert, or update an existing character matched by name (idempotent
        seeding)."""
        name = kwargs.get("name")
        existing = self.session.query(Character).filter_by(name=name).first()
        if existing:
            for k, v in kwargs.items():
                setattr(existing, k, v)
            self.session.commit()
            return existing
        return self.create(**kwargs)

    def get(self, character_id: int) -> Optional[Character]:
        return self.session.get(Character, character_id)

    def by_name(self, name: str) -> Optional[Character]:
        return self.session.query(Character).filter_by(name=name).first()

    def get_active(self) -> list[Character]:
        return (
            self.session.query(Character)
            .filter_by(is_active=True)
            .order_by(Character.name.asc())
            .all()
        )

    def get_all(self) -> list[Character]:
        return self.session.query(Character).order_by(Character.name.asc()).all()

    def update(self, character_id: int, **fields) -> Optional[Character]:
        c = self.session.get(Character, character_id)
        if c:
            for k, v in fields.items():
                setattr(c, k, v)
            self.session.commit()
        return c

    def delete(self, character_id: int) -> None:
        c = self.session.get(Character, character_id)
        if c:
            self.session.delete(c)
            self.session.commit()

    def count(self) -> int:
        return self.session.query(Character).count()


class StoryRepo:
    def __init__(self, session: Session):
        self.session = session

    def create(self, **kwargs) -> Story:
        s = Story(**kwargs)
        self.session.add(s)
        self.session.commit()
        return s

    def get(self, story_id: int) -> Optional[Story]:
        return self.session.get(Story, story_id)

    def update(self, story_id: int, **fields) -> Optional[Story]:
        s = self.session.get(Story, story_id)
        if s:
            for k, v in fields.items():
                setattr(s, k, v)
            self.session.commit()
        return s

    def get_all(self, limit: int = 100) -> list[Story]:
        return (
            self.session.query(Story)
            .order_by(Story.created_at.desc())
            .limit(limit)
            .all()
        )

    def delete(self, story_id: int) -> None:
        s = self.session.get(Story, story_id)
        if s:
            self.session.delete(s)
            self.session.commit()


class PipelineRunRepo:
    def __init__(self, session: Session):
        self.session = session

    def create(self, run_uid: str, mode: str = "kids", **extra) -> PipelineRun:
        r = PipelineRun(run_uid=run_uid, mode=mode, **extra)
        self.session.add(r)
        self.session.commit()
        return r

    def get(self, run_id: int) -> Optional[PipelineRun]:
        return self.session.get(PipelineRun, run_id)

    def by_uid(self, run_uid: str) -> Optional[PipelineRun]:
        return self.session.query(PipelineRun).filter_by(run_uid=run_uid).first()

    def save_steps(self, run_id: int, steps: list[dict], current_step: str = "",
                   status: str = "running", **extra) -> None:
        r = self.session.get(PipelineRun, run_id)
        if not r:
            return
        r.steps_json = json.dumps(steps, ensure_ascii=False)
        r.current_step = current_step
        r.status = status
        r.updated_at = datetime.utcnow()
        for k, v in extra.items():
            setattr(r, k, v)
        self.session.commit()

    def get_recent(self, limit: int = 20) -> list[PipelineRun]:
        return (
            self.session.query(PipelineRun)
            .order_by(PipelineRun.created_at.desc())
            .limit(limit)
            .all()
        )

    def latest(self) -> Optional[PipelineRun]:
        return (
            self.session.query(PipelineRun)
            .order_by(PipelineRun.created_at.desc())
            .first()
        )
