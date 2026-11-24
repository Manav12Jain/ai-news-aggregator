import logging
from threading import Lock, Thread

from sqlalchemy.exc import SQLAlchemyError

from app.services.process_email import deliver_email_digest, generate_email_digest

from .storage import mark_edition_sent, save_edition

logger = logging.getLogger(__name__)


class PipelineJob:
    def __init__(self):
        self.lock = Lock()
        self.state = {"status": "idle", "stage": 0}

    def snapshot(self):
        with self.lock:
            return dict(self.state)

    def start(self, days, count, email=False):
        with self.lock:
            if self.state["status"] == "running":
                return False
            self.state = {"status": "running", "stage": 1, "days": days, "count": count, "email": email}
            Thread(target=self.run, args=(days, count, email), daemon=True).start()
            return True

    def update(self, **updates):
        with self.lock:
            self.state.update(updates)

    def run(self, days, count, email):
        hours = days * 24
        try:
            from app.daily_runner import run_daily_pipeline

            result = run_daily_pipeline(
                hours=hours, top_n=count, send_email=False,
                progress=lambda stage: self.update(stage=stage),
            )
            if not result["success"]:
                self.update(status="failed", message="The pipeline stopped. Check the PowerShell output for the error.")
                return
            reports = [*result["processing"].values(), result["digests"]]
            failed = sum(report.get("failed", 0) for report in reports)
            self.update(stage=5, stats={
                "fetched": sum(result["scraping"].values()),
                "summarized": result["digests"]["processed"],
                "failed": failed,
                "transcripts_unavailable": result["processing"]["youtube"]["unavailable"],
            })
            digest = generate_email_digest(hours=hours, top_n=count, fallback_to_recent=True)
            saved = save_edition(digest, hours, "pipeline")
            edition_id = saved["edition"]["id"]
            self.update(edition_id=edition_id, ranking_method=digest.ranking_method)
            if email:
                self.update(stage=6)
                try:
                    deliver_email_digest(digest)
                except Exception:
                    logger.exception("Digest saved, but email delivery failed")
                    self.update(status="failed", message="Digest saved, but email delivery failed. Check the PowerShell output.")
                    return
                try:
                    mark_edition_sent(edition_id)
                except SQLAlchemyError:
                    logger.exception("Email sent, but the history label could not be updated")
                self.update(email_sent=True)
            self.update(status="complete")
        except ValueError as exc:
            logger.warning("Dashboard pipeline could not generate a digest: %s", exc)
            message = (
                "No saved summaries in that window. Try a longer lookback."
                if str(exc) == "No digests available"
                else "Could not generate the digest. Check Gemini configuration and the PowerShell output."
            )
            self.update(status="failed", message=message)
        except Exception:
            logger.exception("Dashboard pipeline failed")
            self.update(status="failed", message="The pipeline stopped. Check the PowerShell output for the error.")
