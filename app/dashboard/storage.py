from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, Integer, String, func, inspect, select
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.config import YOUTUBE_CHANNELS
from app.database.connection import engine, get_session
from app.profiles.user_profile import USER_PROFILE


class DashboardBase(DeclarativeBase):
    pass


class LegacyPreferences(DashboardBase):
    __tablename__ = "dashboard_preferences"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    values: Mapped[dict] = mapped_column(JSON)


class Edition(DashboardBase):
    __tablename__ = "digest_editions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    kind: Mapped[str] = mapped_column(String(10))
    hours: Mapped[int]
    story_count: Mapped[int]
    payload: Mapped[dict] = mapped_column(JSON)


class Signal(DashboardBase):
    __tablename__ = "signals"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    topic: Mapped[str] = mapped_column(String(12), index=True)
    publisher: Mapped[str]
    title: Mapped[str]
    summary: Mapped[str]
    url: Mapped[str]
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class SignalRefresh(DashboardBase):
    __tablename__ = "signal_refresh"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    payload: Mapped[dict] = mapped_column(JSON)


class SignalBriefing(DashboardBase):
    __tablename__ = "signal_briefings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict] = mapped_column(JSON)


def initialize_dashboard():
    DashboardBase.metadata.create_all(
        engine,
        tables=[Edition.__table__, Signal.__table__, SignalRefresh.__table__, SignalBriefing.__table__],
    )


def default_preferences():
    return {
        "name": USER_PROFILE["name"],
        "interests": list(USER_PROFILE["interests"]),
        "youtube_channels": list(YOUTUBE_CHANNELS),
        "openai_enabled": True,
        "anthropic_enabled": True,
    }


def read_preferences(session):
    # Existing installations may already have saved profile and source choices.
    defaults = default_preferences()
    if inspect(session.connection()).has_table(LegacyPreferences.__tablename__):
        row = session.get(LegacyPreferences, 1)
        if row:
            defaults.update(row.values)
    return defaults


def save_edition(digest, hours, kind, *, require_history=True):
    payload = {"digest": digest.model_dump(mode="json"), "markdown": digest.to_markdown()}
    with get_session() as session:
        if not require_history and not inspect(session.connection()).has_table(Edition.__tablename__):
            return payload
        row = Edition(
            created_at=datetime.now(timezone.utc),
            kind=kind,
            hours=hours,
            story_count=len(digest.articles),
            payload=payload,
        )
        session.add(row)
        session.flush()
        metadata = edition_metadata(row)
        session.commit()
    return {**payload, "edition": metadata}


def edition_metadata(row):
    return {
        "id": row.id,
        "date": row.created_at.isoformat(),
        "kind": row.kind,
        "hours": row.hours,
        "story_count": row.story_count,
    }


def list_editions(page, page_size=12):
    with get_session() as session:
        total = session.scalar(select(func.count()).select_from(Edition))
        rows = session.execute(
            select(Edition.id, Edition.created_at, Edition.kind, Edition.hours, Edition.story_count)
            .order_by(Edition.id.desc())
            .offset((page - 1) * page_size).limit(page_size)
        )
        return {"items": [edition_metadata(row) for row in rows], "total": total, "page_size": page_size}


def load_edition(edition_id):
    with get_session() as session:
        row = session.get(Edition, edition_id)
        if row is not None:
            return {**row.payload, "edition": edition_metadata(row)}
    return None


def mark_edition_sent(edition_id):
    with get_session() as session:
        edition = session.get(Edition, edition_id)
        edition.kind = "email"
        session.commit()
