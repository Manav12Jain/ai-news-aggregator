from .scrapers.youtube import YouTubeScraper
from .scrapers.openai import OpenAIScraper
from .scrapers.anthropic import AnthropicScraper
from .dashboard.storage import read_preferences
from .database.connection import get_session
from .database.repository import Repository


def run_scrapers(hours: int = 24) -> dict:
    with get_session() as session:
        settings = read_preferences(session)

    youtube_scraper = YouTubeScraper()
    
    youtube_videos = []
    video_dicts = []
    for channel_id in settings["youtube_channels"]:
        videos = youtube_scraper.get_latest_videos(channel_id, hours=hours)
        youtube_videos.extend(videos)
        video_dicts.extend([
            {
                "video_id": v.video_id,
                "title": v.title,
                "url": v.url,
                "channel_id": channel_id,
                "published_at": v.published_at,
                "description": v.description,
                "transcript": v.transcript
            }
            for v in videos
        ])
    
    openai_articles = OpenAIScraper().get_articles(hours=hours) if settings["openai_enabled"] else []
    anthropic_articles = AnthropicScraper().get_articles(hours=hours) if settings["anthropic_enabled"] else []
    
    with get_session() as session:
        repo = Repository(session)
        if video_dicts:
            repo.bulk_create_youtube_videos(video_dicts)

        if openai_articles:
            article_dicts = [
                {
                    "guid": a.guid,
                    "title": a.title,
                    "url": a.url,
                    "published_at": a.published_at,
                    "description": a.description,
                    "category": a.category
                }
                for a in openai_articles
            ]
            repo.bulk_create_openai_articles(article_dicts)

        if anthropic_articles:
            article_dicts = [
                {
                    "guid": a.guid,
                    "title": a.title,
                    "url": a.url,
                    "published_at": a.published_at,
                    "description": a.description,
                    "category": a.category
                }
                for a in anthropic_articles
            ]
            repo.bulk_create_anthropic_articles(article_dicts)

    return {
        "youtube": youtube_videos,
        "openai": openai_articles,
        "anthropic": anthropic_articles,
    }


if __name__ == "__main__":
    results = run_scrapers(hours=24)
    print(f"YouTube videos: {len(results['youtube'])}")
    print(f"OpenAI articles: {len(results['openai'])}")
    print(f"Anthropic articles: {len(results['anthropic'])}")

