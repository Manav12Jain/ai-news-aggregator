import logging
from datetime import datetime
from dotenv import load_dotenv
from sqlalchemy.exc import SQLAlchemyError

load_dotenv()

from app.agent.email_agent import EmailAgent, EmailDigestResponse, EmailIntroduction, RankedArticleDetail
from app.agent.curator_agent import CuratorAgent
from app.agent.gemini import GeminiUnavailable
from app.profiles.user_profile import USER_PROFILE
from app.dashboard.storage import read_preferences, save_edition
from app.database.connection import get_session
from app.database.repository import Repository
from app.services.email import send_email, digest_to_html

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)


def newest_first_digest(digests: list[dict], name: str, top_n: int) -> EmailDigestResponse:
    current_date = datetime.now().strftime("%B %d, %Y")
    articles = [
        RankedArticleDetail(
            digest_id=story["id"],
            title=story["title"],
            summary=story["summary"],
            url=story["url"],
            article_type=story["article_type"],
            rank=position,
            relevance_score=None,
        )
        for position, story in enumerate(digests[:top_n], 1)
    ]
    return EmailDigestResponse(
        introduction=EmailIntroduction(
            greeting=f"Hey {name}, here is your daily digest of AI news for {current_date}.",
            introduction="Gemini was temporarily unavailable. This edition shows the newest saved stories in your selected window.",
        ),
        articles=articles,
        total_ranked=0,
        top_n=top_n,
        ranking_method="newest",
    )


def generate_email_digest(
    hours: int = 24, top_n: int = 10, *, fallback_to_recent: bool = False
) -> EmailDigestResponse:
    with get_session() as session:
        digests = Repository(session).get_recent_digests(hours=hours)
        settings = read_preferences(session) if digests else None
    total = len(digests)
    
    if total == 0:
        logger.warning(f"No digests found from the last {hours} hours")
        raise ValueError("No digests available")
    
    profile = {**USER_PROFILE, "name": settings["name"], "interests": settings["interests"]}
    curator = CuratorAgent(profile)
    logger.info(f"Ranking {total} digests for email generation")
    try:
        ranked_articles = curator.rank_digests(digests)
    except GeminiUnavailable as exc:
        if not fallback_to_recent:
            raise ValueError("Gemini is temporarily unavailable. Try again later.") from exc
        logger.warning("%s Using newest saved stories for this edition.", exc)
        return newest_first_digest(digests, profile["name"], top_n)
    
    if not ranked_articles:
        logger.error("Failed to rank digests")
        raise ValueError("Failed to rank articles")
    
    logger.info(f"Generating email digest with top {top_n} articles")
    
    article_details = [
        RankedArticleDetail(
            digest_id=a.digest_id,
            rank=a.rank,
            relevance_score=a.relevance_score,
            reasoning=a.reasoning,
            title=next((d["title"] for d in digests if d["id"] == a.digest_id), ""),
            summary=next((d["summary"] for d in digests if d["id"] == a.digest_id), ""),
            url=next((d["url"] for d in digests if d["id"] == a.digest_id), ""),
            article_type=next((d["article_type"] for d in digests if d["id"] == a.digest_id), "")
        )
        for a in ranked_articles
    ]
    
    email_digest = EmailAgent(profile).create_email_digest_response(
        ranked_articles=article_details,
        total_ranked=len(ranked_articles),
        limit=top_n
    )
    
    logger.info("Email digest generated successfully")
    logger.info(f"\n=== Email Introduction ===")
    logger.info(email_digest.introduction.greeting)
    logger.info(f"\n{email_digest.introduction.introduction}")
    
    return email_digest


def deliver_email_digest(digest: EmailDigestResponse) -> str:
    plain = digest.to_markdown()
    html = digest_to_html(digest)
    date = digest.introduction.greeting.rsplit("for ", 1)
    subject = f"Daily AI News Digest - {date[-1] if len(date) > 1 else 'Today'}"
    send_email(subject=subject, body_text=plain, body_html=html)
    logger.info("Email sent successfully!")
    return subject


def send_digest_email(hours: int = 24, top_n: int = 10) -> dict:
    try:
        result = generate_email_digest(hours=hours, top_n=top_n)
        subject = deliver_email_digest(result)
        try:
            save_edition(result, hours, "email", require_history=False)
        except SQLAlchemyError:
            logger.exception("Email sent, but the digest could not be recorded in dashboard history")
        return {
            "success": True,
            "subject": subject,
            "articles_count": len(result.articles)
        }
    except ValueError as e:
        logger.error(f"Error sending email: {e}")
        return {
            "success": False,
            "error": str(e)
        }


if __name__ == "__main__":
    result = send_digest_email(hours=24, top_n=10)
    if result["success"]:
        print("\n=== Email Digest Sent ===")
        print(f"Subject: {result['subject']}")
        print(f"Articles: {result['articles_count']}")
    else:
        print(f"Error: {result['error']}")

