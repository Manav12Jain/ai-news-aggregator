import argparse
import json
import logging
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from sqlalchemy import func, or_, select
from sqlalchemy.exc import SQLAlchemyError

from app.database.connection import get_session
from app.database.models import Digest
from app.services.process_email import generate_email_digest

from .jobs import PipelineJob
from .signals import load_signal_briefing, make_signal_briefing, read_signals, refresh_signals, signal_briefings
from .world_monitor import WorldMonitorError, load_world_events, world_monitor_configured
from .storage import initialize_dashboard, list_editions, load_edition, save_edition

ASSETS = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/dashboard.css": ("dashboard.css", "text/css; charset=utf-8"),
    "/dashboard.js": ("dashboard.js", "text/javascript; charset=utf-8"),
}
SOURCES = ("openai", "anthropic", "youtube")
WINDOWS = (1, 3, 7, 14, 30)
PAGE_SIZE = 12
logger = logging.getLogger(__name__)


def read_library(query):
    source = query.get("source", [""])[0]
    search = query.get("q", [""])[0].strip()[:200]
    days = int(query.get("days", ["0"])[0])
    page = int(query.get("page", ["1"])[0])
    if source not in ("", *SOURCES) or days not in (0, *WINDOWS) or not 1 <= page <= 100_000:
        raise ValueError("Invalid library filters.")

    filters = []
    if source:
        filters.append(Digest.article_type == source)
    if search:
        filters.append(or_(Digest.title.icontains(search, autoescape=True),
                           Digest.summary.icontains(search, autoescape=True)))
    if days:
        cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)
        filters.append(Digest.created_at >= cutoff)

    with get_session() as session:
        total = session.scalar(select(func.count()).select_from(Digest).where(*filters))
        rows = session.scalars(
            select(Digest).where(*filters)
            .order_by(Digest.created_at.desc(), Digest.id)
            .offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE)
        ).all()
        return {
            "total": total,
            "page": page,
            "page_size": PAGE_SIZE,
            "items": [
                {
                    "article_type": row.article_type,
                    "title": row.title,
                    "summary": row.summary,
                    "url": row.url,
                    "date": row.created_at.replace(tzinfo=timezone.utc).isoformat()
                    if row.created_at else None,
                }
                for row in rows
            ],
        }


class DashboardHandler(BaseHTTPRequestHandler):
    def send_content(self, status, body, content_type):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'",
        )
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, status, payload):
        self.send_content(status, json.dumps(payload).encode("utf-8"), "application/json")

    def error(self, status, message):
        self.send_json(status, {"error": message})

    def check_host(self):
        port = self.server.server_port
        if self.headers.get("Host") in (f"127.0.0.1:{port}", f"localhost:{port}"):
            return True
        self.error(HTTPStatus.FORBIDDEN, "Use the local dashboard address.")
        return False

    def do_GET(self):
        if not self.check_host():
            return
        request = urlsplit(self.path)
        if request.path in ASSETS:
            filename, content_type = ASSETS[request.path]
            body = Path(__file__).with_name(filename).read_bytes()
            self.send_content(HTTPStatus.OK, body, content_type)
            return
        query = parse_qs(request.query)
        try:
            if request.path == "/api/library":
                payload = read_library(query)
            elif request.path == "/api/history":
                page = int(query.get("page", ["1"])[0])
                if not 1 <= page <= 100_000:
                    raise ValueError("Invalid history page.")
                payload = list_editions(page)
            elif request.path.startswith("/api/history/"):
                edition_id = int(request.path.removeprefix("/api/history/"))
                if not 1 <= edition_id <= 2_147_483_647:
                    raise ValueError("Invalid digest ID.")
                payload = load_edition(edition_id)
            elif request.path == "/api/signals":
                payload = read_signals(
                    query.get("topic", [""])[0],
                    int(query.get("days", ["30"])[0]),
                    int(query.get("page", ["1"])[0]),
                )
                payload["world_monitor_configured"] = world_monitor_configured()
            elif request.path == "/api/signals/briefings":
                payload = signal_briefings()
            elif request.path.startswith("/api/signals/briefings/"):
                briefing_id = int(request.path.removeprefix("/api/signals/briefings/"))
                if not 1 <= briefing_id <= 2_147_483_647:
                    raise ValueError("Invalid Signals briefing ID.")
                payload = load_signal_briefing(briefing_id)
            elif request.path == "/api/run":
                payload = self.server.job.snapshot()
            else:
                payload = None
        except ValueError:
            self.error(HTTPStatus.BAD_REQUEST, "Invalid dashboard query.")
            return
        except SQLAlchemyError:
            self.database_error()
            return
        if payload is None:
            self.error(HTTPStatus.NOT_FOUND, "Page or saved digest not found.")
        else:
            self.send_json(HTTPStatus.OK, payload)

    def database_error(self):
        logger.exception("Dashboard database request failed")
        self.error(HTTPStatus.SERVICE_UNAVAILABLE, "Could not access saved data. Check that PostgreSQL is running.")

    def json_body(self):
        size = int(self.headers.get("Content-Length", "0"))
        if self.headers.get_content_type() != "application/json" or not 0 < size <= 24_000:
            raise ValueError("Expected a JSON request.")
        options = json.loads(self.rfile.read(size))
        if not isinstance(options, dict):
            raise ValueError("Expected a JSON object.")
        return options

    def signal_action(self, options):
        if self.path == "/api/signals/refresh":
            status = refresh_signals()
            if status is None:
                self.error(HTTPStatus.CONFLICT, "A Signals refresh is already in progress.")
            else:
                self.send_json(HTTPStatus.OK, status)
        elif self.path == "/api/signals/briefings":
            briefing = make_signal_briefing(options.get("topic", ""), options.get("days"))
            if briefing is None:
                self.error(HTTPStatus.NOT_FOUND, "No saved signals in this window. Refresh the sources or choose a longer lookback.")
            else:
                self.send_json(HTTPStatus.CREATED, briefing)
        elif self.path == "/api/signals/world-monitor":
            self.send_json(HTTPStatus.OK, load_world_events())


    def do_POST(self):
        if not self.check_host():
            return
        if self.path not in (
            "/api/preview", "/api/run",
            "/api/signals/refresh", "/api/signals/briefings", "/api/signals/world-monitor",
        ):
            self.error(HTTPStatus.NOT_FOUND, "Page not found.")
            return
        if self.headers.get("Origin") != f"http://{self.headers['Host']}":
            self.error(HTTPStatus.FORBIDDEN, "Open the local dashboard to make changes.")
            return

        try:
            options = self.json_body()
            if self.path.startswith("/api/signals/"):
                self.signal_action(options)
                return
            days, count = options.get("days"), options.get("count")
            email = options.get("email", False)
            if type(email) is not bool:
                raise ValueError("Choose whether to email the digest.")
            if type(days) is not int or type(count) is not int or days not in WINDOWS or count not in (3, 5, 10):
                raise ValueError("Choose a supported lookback window and story count.")
        except WorldMonitorError as exc:
            self.error(HTTPStatus.BAD_GATEWAY, str(exc))
            return
        except ValueError as exc:
            self.error(HTTPStatus.BAD_REQUEST, str(exc))
            return
        except SQLAlchemyError:
            self.database_error()
            return

        if self.path == "/api/run":
            if not self.server.job.start(days, count, email):
                self.error(HTTPStatus.CONFLICT, "A pipeline run is already in progress.")
                return
            self.send_json(HTTPStatus.ACCEPTED, self.server.job.snapshot())
            return

        try:
            preview = generate_email_digest(hours=days * 24, top_n=count, fallback_to_recent=True)
            payload = save_edition(preview, days * 24, "preview")
        except SQLAlchemyError:
            self.database_error()
        except ValueError as exc:
            if str(exc) == "No digests available":
                self.error(HTTPStatus.NOT_FOUND, "No saved summaries in that window. Try a longer lookback or run the pipeline.")
            else:
                logger.warning("Could not generate the digest preview: %s", exc)
                self.error(HTTPStatus.BAD_GATEWAY, "Could not generate a preview. Check your Gemini configuration or try again.")
        else:
            self.send_json(HTTPStatus.OK, payload)


def main():
    parser = argparse.ArgumentParser(description="Run the AI News Aggregator dashboard locally.")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    with ThreadingHTTPServer(("127.0.0.1", args.port), DashboardHandler) as server:
        try:
            initialize_dashboard()
        except SQLAlchemyError as exc:
            logger.error("Could not initialize dashboard storage: %s", exc)
            parser.exit(1, "Start PostgreSQL and retry.\n")
        server.job = PipelineJob()
        print(f"Dashboard available at http://127.0.0.1:{server.server_port}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
