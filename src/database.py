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
        db_url = f"sqlite:///{config.DB_PATH}"
        _ENGINE = create_engine(db_url, connect_args={"check_same_thread": False})
        log.debug("Database engine created at %s", config.DB_PATH)
    return _ENGINE


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


# ── Bootstrap ─────────────────────────────────────────────────────────────────

def init_db() -> None:
    """Create all tables if they don't exist, then apply column migrations."""
    engine = get_engine()
    Base.metadata.create_all(engine)
    _migrate_columns(engine)
    log.info("Database initialised at %s", config.DB_PATH)


def _migrate_columns(engine) -> None:
    """create_all() never alters existing tables, and the CI database is
    restored from cache — so new columns must be added by hand."""
    migrations = {
        "topics": {"fail_count": "INTEGER DEFAULT 0"},
    }
    with engine.connect() as conn:
        for table, columns in migrations.items():
            existing = {row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info({table})")}
            for col, ddl in columns.items():
                if existing and col not in existing:
                    conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")
                    log.info("DB migration: added %s.%s", table, col)
        conn.commit()


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
