# AI News Aggregator

A local AI news dashboard maintained by **Manav Jain**. It collects stories from OpenAI News, Anthropic News, and selected YouTube channels, then uses Gemini to summarize and rank them. The dashboard supports browsing saved stories, generating digests, and sending them through Gmail.

## Run locally on Windows

Use Docker Desktop for PostgreSQL and uv for the Python environment. With Docker Desktop running, open PowerShell in the project folder and run:

```powershell
uv python install 3.12
uv sync --locked --no-dev
if (-not (Test-Path .env)) { Copy-Item .\app\example.env .\.env }
docker compose --env-file .env -f .\docker\docker-compose.yml up -d --wait
uv run --no-sync python -m app.database.create_tables
```

Set `GEMINI_API_KEY` in `.env` to enable AI summaries and ranking. For email delivery, set `MY_EMAIL` and `APP_PASSWORD` to the Gmail or Google Workspace address and its Google App Password.

Start the dashboard:

```powershell
uv run --no-sync python -m app.dashboard
```

Open [the local dashboard](http://127.0.0.1:8765). Use **History** to reopen or download saved digests. Fresh installations use the profile and source defaults in `app/profiles/user_profile.py` and `app/config.py`.

To run the full pipeline and email up to five stories from the past seven days:

```powershell
uv run --no-sync python main.py 168 5
```

## Signals

Open **Signals → Refresh sources** for publicly available updates from [arXiv](https://info.arxiv.org/help/rss.html), the [U.S. Federal Register](https://www.federalregister.gov/developers/documentation/api/v1), and the [Hugging Face Blog](https://huggingface.co/blog). The topic and date filters apply to the source list and to **Generate briefing**. Each briefing saves up to five stories with publisher attribution and source URLs. Open a saved briefing from the Signals tab to download it as Markdown.

**Open Tech Monitor** links to [World Monitor's technology dashboard](https://tech.worldmonitor.app/dashboard). To show its tech-event calendar inside Signals, add `WORLDMONITOR_API_KEY` to `.env` using a key with access to the documented [research signals tool](https://www.worldmonitor.app/docs/mcp-tools/environment-health#get-research-signals), then restart the dashboard. **Load tech events** fetches the calendar on demand. World Monitor events are displayed with their source links and stay out of saved briefings, downloads, and email. The provider's [usage terms](https://www.worldmonitor.app/docs/eula) apply to this connection.

## Credits

This project is build on DataLumina’s AI News Aggregator tutorial. This local edition adds the dashboard, Gemini integration, digest history, public-source Signals, and a Swiss Editorial theme.