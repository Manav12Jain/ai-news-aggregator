import hashlib
import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, time, timedelta, timezone
from threading import Lock
from urllib.parse import urlsplit, urlunsplit

import feedparser
import requests
from bs4 import BeautifulSoup
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from app.agent.curator_agent import CuratorAgent
from app.agent.gemini import GeminiUnavailable
from app.database.connection import get_session
from app.profiles.user_profile import USER_PROFILE

from .storage import Signal, SignalBriefing, SignalRefresh, read_preferences

TOPICS = {
    "research": "AI research",
    "policy": "AI policy",
    "chips": "Chips & supply chains",
    "tech": "Developer tools",
}
WINDOWS = (7, 14, 30, 90)
PAGE_SIZE = 12
SOURCE_LIMIT = 30
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
RSS_FEEDS = {
    "research": ("arXiv", "https://rss.arxiv.org/rss/cs.AI+cs.LG", "arxiv.org"),
    "tech": ("Hugging Face Blog", "https://huggingface.co/blog/feed.xml", "huggingface.co"),
}
REGISTER_QUERIES = {
    "policy": ('"artificial intelligence"', re.compile(
        r"artificial intelligence|machine learning|\bAI (?:systems?|models?|tools?)\b", re.I
    )),
    "chips": ("semiconductor", re.compile(
        r"semiconductor|integrated circuits?|\b(?:CHIPS Act|DRAM|HBM)\b", re.I
    )),
}
logger = logging.getLogger(__name__)
refresh_lock = Lock()


def source_response(url, *, params=None):
    with requests.get(
        url, params=params, timeout=(5, 12), stream=True,
        headers={"User-Agent": "AI-News-Aggregator/0.1 (public research feed reader)"},
    ) as response:
        response.raise_for_status()
        data = bytearray()
        for chunk in response.iter_content(64 * 1024):
            data.extend(chunk)
            if len(data) > MAX_RESPONSE_BYTES:
                raise ValueError("Feed exceeded the response size limit.")
        return bytes(data)


def plain_text(value, limit):
    if not isinstance(value, str):
        return ""
    text = " ".join(BeautifulSoup(value, "html.parser").get_text(" ", strip=True).split())
    if len(text) > limit:
        return text[:limit - 1].rsplit(" ", 1)[0] + "…"
    return text


def source_url(value, expected_host):
    if not isinstance(value, str):
        return ""
    parts = urlsplit(value)
    if parts.netloc.casefold() not in (expected_host, f"www.{expected_host}") or parts.scheme not in ("http", "https"):
        return ""
    host = "www.federalregister.gov" if expected_host == "federalregister.gov" else expected_host
    return urlunsplit(("https", host, parts.path.rstrip("/"), parts.query, ""))


def signal_row(topic, publisher, title, summary, link, published):
    if not title or not link or not published:
        return None
    return {
        "id": hashlib.sha256(link.encode()).hexdigest(),
        "topic": topic,
        "publisher": publisher,
        "title": plain_text(title, 360),
        "summary": plain_text(summary, 240),
        "url": link,
        "published_at": published,
    }


def read_rss(topic):
    publisher, url, host = RSS_FEEDS[topic]
    feed = feedparser.parse(source_response(url))
    if feed.bozo and not feed.entries:
        raise ValueError("Invalid RSS feed.")
    rows = []
    for item in feed.entries:
        published = item.get("published_parsed") or item.get("updated_parsed")
        row = signal_row(
            topic, publisher, item.get("title"), item.get("summary", ""),
            source_url(item.get("link"), host),
            datetime(*published[:6], tzinfo=timezone.utc) if published else None,
        )
        if row:
            rows.append(row)
        if len(rows) == SOURCE_LIMIT:
            break
    return rows


def read_register(topic):
    term, relevant = REGISTER_QUERIES[topic]
    data = json.loads(source_response("https://www.federalregister.gov/api/v1/documents.json", params=[
        ("conditions[term]", term), ("order", "newest"), ("per_page", 50),
        *[("fields[]", field) for field in ("title", "abstract", "html_url", "publication_date")],
    ]))
    rows = []
    for item in data["results"]:
        title, abstract = item.get("title") or "", item.get("abstract") or ""
        if not relevant.search(f"{title} {abstract}"):
            continue
        date_text = item.get("publication_date")
        row = signal_row(
            topic, "U.S. Federal Register", title, abstract,
            source_url(item.get("html_url"), "federalregister.gov"),
            datetime.combine(date.fromisoformat(date_text), time(12), timezone.utc) if date_text else None,
        )
        if row:
            rows.append(row)
        if len(rows) == SOURCE_LIMIT:
            break
    return rows


def fetch_topic(topic):
    try:
        return topic, read_rss(topic) if topic in RSS_FEEDS else read_register(topic)
    except (requests.RequestException, ValueError, TypeError, KeyError):
        logger.warning("Could not refresh Signals source for %s", topic, exc_info=True)
        return topic, None


def refresh_signals():
    if not refresh_lock.acquire(blocking=False):
        return None
    try:
        with ThreadPoolExecutor(max_workers=len(TOPICS)) as workers:
            feeds = dict(workers.map(fetch_topic, TOPICS))
        now = datetime.now(timezone.utc)
        items = {}
        for rows in feeds.values():
            for row in rows or []:
                items.setdefault(row["id"], row)
        status = {
            "date": now.isoformat(),
            "sources": [
                {"label": TOPICS[topic], "count": len(rows) if rows is not None else None}
                for topic, rows in feeds.items()
            ],
        }
        with get_session() as session:
            if items:
                statement = insert(Signal).values(list(items.values()))
                session.execute(statement.on_conflict_do_update(
                    index_elements=[Signal.id],
                    set_={key: getattr(statement.excluded, key) for key in ("title", "summary", "published_at")},
                ))
            joined = insert(SignalRefresh).values(id=1, payload=status)
            session.execute(joined.on_conflict_do_update(
                index_elements=[SignalRefresh.id], set_={"payload": status}
            ))
            session.commit()
        return status
    finally:
        refresh_lock.release()


def check_filters(topic, days):
    if topic not in ("", *TOPICS) or type(days) is not int or days not in WINDOWS:
        raise ValueError("Choose a supported topic and lookback window.")


def signal_query(days):
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    return select(Signal).where(Signal.published_at >= cutoff)


def signal_item(row):
    return {
        "id": row.id,
        "topic": row.topic,
        "topic_label": TOPICS[row.topic],
        "publisher": row.publisher,
        "title": row.title,
        "summary": row.summary,
        "url": row.url,
        "date": row.published_at.isoformat(),
    }


def read_signals(topic, days, page):
    check_filters(topic, days)
    if not 1 <= page <= 100_000:
        raise ValueError("Invalid Signals page.")
    query = signal_query(days)
    if topic:
        query = query.where(Signal.topic == topic)
    with get_session() as session:
        last = session.get(SignalRefresh, 1)
        total = session.scalar(select(func.count()).select_from(query.subquery()))
        rows = session.scalars(query.order_by(Signal.published_at.desc(), Signal.id)
                               .offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE)).all()
        return {
            "refresh": last.payload if last else None,
            "items": [signal_item(row) for row in rows],
            "total": total,
            "page_size": PAGE_SIZE,
        }


def make_signal_briefing(topic, days):
    check_filters(topic, days)
    with get_session() as session:
        query = signal_query(days).order_by(Signal.published_at.desc(), Signal.id)
        candidates = [
            signal_item(row)
            for name in ([topic] if topic else TOPICS)
            for row in session.scalars(query.where(Signal.topic == name).limit(24 if topic else 6))
        ]
        if not candidates:
            return None
        preferences = read_preferences(session)

    candidates.sort(key=lambda entry: entry["date"], reverse=True)
    by_id = {f"{entry['topic']}:{index}": entry for index, entry in enumerate(candidates, 1)}
    count = min(len(candidates), 5)
    chosen = []
    try:
        profile = {**USER_PROFILE, "name": preferences["name"], "interests": preferences["interests"]}
        ranked = CuratorAgent(profile).rank_digests([
            {**entry, "id": identifier, "article_type": entry["topic"]}
            for identifier, entry in by_id.items()
        ], limit=count)
        seen = set()
        for rank in sorted(ranked, key=lambda item: item.rank):
            if rank.digest_id in by_id and rank.digest_id not in seen:
                chosen.append({**by_id[rank.digest_id], "rank": len(chosen) + 1, "relevance_score": rank.relevance_score})
                seen.add(rank.digest_id)
    except (GeminiUnavailable, ValueError) as exc:
        logger.info("Gemini ranking unavailable for Signals: %s", exc)

    method = "gemini" if len(chosen) >= count else "newest"
    if method == "newest":
        logger.info("Signals received %s usable rankings for %s stories. Using newest-first order.", len(chosen), count)
    items = (chosen if method == "gemini" else candidates)[:count]
    created = datetime.now(timezone.utc)
    payload = {
        "topic": TOPICS[topic] if topic else "All topics",
        "days": days, "ranking": method, "items": items, "date": created.isoformat(),
    }
    with get_session() as session:
        briefing = SignalBriefing(created_at=created, payload=payload)
        session.add(briefing)
        session.flush()
        result = {"id": briefing.id, **payload}
        session.commit()
        return result


def signal_briefings():
    with get_session() as session:
        rows = session.scalars(select(SignalBriefing).order_by(SignalBriefing.id.desc()).limit(12))
        return [
            {"id": row.id, "date": row.created_at.isoformat(), "topic": row.payload["topic"]}
            for row in rows
        ]


def load_signal_briefing(briefing_id):
    with get_session() as session:
        row = session.get(SignalBriefing, briefing_id)
        return {"id": row.id, **row.payload} if row else None
