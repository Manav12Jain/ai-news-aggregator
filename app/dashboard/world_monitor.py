import json
import os
from datetime import date
from urllib.parse import urlsplit

import requests

WORLD_MONITOR_ENDPOINT = "https://worldmonitor.app/mcp"


class WorldMonitorError(ValueError):
    pass


def world_monitor_configured():
    return bool(os.getenv("WORLDMONITOR_API_KEY"))


def calendar_date(value):
    if not isinstance(value, str) or not value:
        return None
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        return None


def decode_events(payload):
    if not isinstance(payload, dict):
        raise WorldMonitorError("World Monitor returned an unreadable event response.")
    result = payload.get("result")
    if payload.get("error") or not isinstance(result, dict) or result.get("isError"):
        raise WorldMonitorError("World Monitor did not return events. Check the key and plan access.")
    data = result.get("structuredContent")
    if not isinstance(data, dict):
        data = next(
            (json.loads(block["text"]) for block in result.get("content", [])
             if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str)),
            None,
        )
    if not isinstance(data, dict) or not isinstance(data.get("data"), dict):
        raise WorldMonitorError("World Monitor returned an unreadable event response.")

    bootstrap = data["data"].get("tech-events-bootstrap")
    if not isinstance(bootstrap, dict) or not isinstance(bootstrap.get("events"), list):
        raise WorldMonitorError("World Monitor's event snapshot is unavailable.")
    events = []
    for entry in bootstrap["events"][:8]:
        if not isinstance(entry, dict):
            continue
        title, publisher, link = (entry.get(field) for field in ("title", "source", "url"))
        if not all(isinstance(value, str) and value.strip() for value in (title, publisher, link)):
            continue
        try:
            url = urlsplit(link)
        except ValueError:
            continue
        if url.scheme != "https" or not url.hostname or url.username or url.password:
            continue
        events.append({
            "title": title, "publisher": publisher, "url": link,
            "start_date": calendar_date(entry.get("startDate")),
            "end_date": calendar_date(entry.get("endDate")),
            "location": entry.get("location") if isinstance(entry.get("location"), str) else "",
            "attribution": entry.get("_attribution") or entry.get("attribution"),
        })
    return {
        "events": events,
        "stale": bool(data.get("stale")),
        "cached_at": data.get("cached_at"),
        "attribution": data.get("_attribution") or data["data"].get("_attribution") or bootstrap.get("_attribution"),
    }


def load_world_events():
    key = os.getenv("WORLDMONITOR_API_KEY")
    if not key:
        raise WorldMonitorError("Set WORLDMONITOR_API_KEY in .env to load events here.")
    try:
        response = requests.post(
            WORLD_MONITOR_ENDPOINT,
            headers={
                "Accept": "application/json",
                "User-Agent": "ai-news-aggregator/0.1",
                "X-WorldMonitor-Key": key,
            },
            json={
                "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": "get_research_signals", "arguments": {"type": "conference", "limit": 8}},
            },
            timeout=(5, 15),
            allow_redirects=False,
        )
        if response.status_code in (401, 403):
            raise WorldMonitorError("This World Monitor key or plan cannot access tech events.")
        if response.status_code == 429:
            raise WorldMonitorError("World Monitor's request limit was reached. Try again later.")
        if not 200 <= response.status_code < 300:
            raise WorldMonitorError("World Monitor is temporarily unavailable.")
        return decode_events(response.json())
    except (KeyError, TypeError, json.JSONDecodeError):
        raise WorldMonitorError("World Monitor returned an unreadable event response.") from None
    except requests.RequestException:
        raise WorldMonitorError("Could not connect to World Monitor.") from None
