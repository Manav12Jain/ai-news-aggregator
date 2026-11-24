const byId = (id) => document.getElementById(id);
const sourceNames = { openai: "OpenAI", anthropic: "Anthropic", youtube: "YouTube" };
const formatDate = new Intl.DateTimeFormat(undefined, { dateStyle: "medium" });

function textElement(tag, content, className = "") {
  const element = document.createElement(tag);
  element.textContent = content;
  if (className) element.className = className;
  return element;
}

function storyCard(story) {
  const card = document.createElement("article");
  card.className = "story";
  const body = document.createElement("div");
  const meta = document.createElement("div");
  meta.className = "story-meta";

  const source = textElement("span", story.publisher ?? sourceNames[story.article_type] ?? "Other", "source");
  if (story.article_type) source.dataset.source = story.article_type;
  meta.append(source);
  if (story.topic_label) meta.append(textElement("span", story.topic_label));
  if (story.date) {
    const date = textElement("time", formatDate.format(new Date(story.date)));
    date.dateTime = story.date;
    meta.append(date);
  }
  if (story.rank && typeof story.relevance_score === "number") {
    meta.append(textElement("span", "Rank " + story.rank + " · " + story.relevance_score.toFixed(1) + "/10"));
  }

  body.append(meta, textElement("h3", story.title));
  if (story.summary) body.append(textElement("p", story.summary));
  card.append(body);
  try {
    const url = new URL(story.url);
    if (url.protocol === "https:" || url.protocol === "http:") {
      const link = textElement("a", "Read original", "story-link");
      link.href = url.href;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      link.setAttribute("aria-label", "Read original: " + story.title);
      card.append(link);
    }
  } catch {
    // Some stored stories have no usable outbound URL.
  }
  return card;
}

function renderStories(container, stories) {
  container.replaceChildren(...stories.map(storyCard));
}

async function getJson(url, options) {
  const response = await fetch(url, options);
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || "The dashboard request failed.");
  return result;
}

function postJson(url, body) {
  return getJson(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

const previewForm = byId("preview-form");
const previewButton = byId("preview-button");
const previewStatus = byId("preview-status");
let currentMarkdown = "";

previewForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const fields = new FormData(previewForm);
  const options = { days: Number(fields.get("days")), count: Number(fields.get("count")) };
  if (event.submitter === runButton) {
    await startRun({ ...options, email: fields.has("email") });
    return;
  }
  previewActive = true;
  setButtonStates();
  previewButton.textContent = "Generating…";
  previewStatus.hidden = false;
  previewStatus.classList.remove("error");
  previewStatus.textContent = "Ranking saved stories with Gemini…";
  try {
    const { digest, markdown } = await postJson("/api/preview", options);
    currentMarkdown = markdown;
    byId("preview-count").textContent =
      digest.articles.length + " shown · " + (
        digest.ranking_method === "newest" ? "Newest first" : digest.total_ranked + " ranked"
      );
    byId("preview-greeting").textContent = digest.introduction.greeting;
    byId("preview-introduction").textContent = digest.introduction.introduction;
    renderStories(byId("preview-stories"), digest.articles);
    byId("preview-panel").hidden = false;
    previewStatus.hidden = true;
    if (!byId("briefing-view").hidden) byId("preview-title").focus();
  } catch (error) {
    previewStatus.classList.add("error");
    previewStatus.textContent = error.message || "The preview could not be generated.";
  } finally {
    previewActive = false;
    setButtonStates();
    previewButton.textContent = "Generate preview";
  }
});

function downloadMarkdown(markdown, filename = "ai-news-digest.md") {
  const url = URL.createObjectURL(new Blob([markdown], { type: "text/markdown;charset=utf-8" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}
byId("export-button").addEventListener("click", () => downloadMarkdown(currentMarkdown));

const libraryForm = byId("library-form");
const libraryStatus = byId("library-status");
let selectedPage = 1;
let libraryController;

async function loadLibrary() {
  libraryController?.abort();
  const controller = new AbortController();
  libraryController = controller;
  const query = new URLSearchParams(new FormData(libraryForm));
  query.set("page", String(selectedPage));
  libraryStatus.textContent = "Loading saved stories…";
  byId("pagination").hidden = true;
  try {
    const { items, total, page, page_size } = await getJson("/api/library?" + query, { signal: controller.signal });
    const pages = Math.ceil(total / page_size);
    if (total && page > pages) {
      selectedPage = pages;
      return loadLibrary();
    }
    renderStories(byId("library-stories"), items);
    libraryStatus.textContent = total === 0 ? "No saved stories match these filters." : total + (total === 1 ? " saved story" : " saved stories");
    byId("page-label").textContent = "Page " + page + " of " + pages;
    byId("previous-page").disabled = page <= 1;
    byId("next-page").disabled = page >= pages;
    byId("pagination").hidden = pages <= 1;
  } catch (error) {
    if (error.name === "AbortError") return;
    libraryStatus.textContent = error.message || "Saved stories could not be loaded.";
    byId("library-stories").replaceChildren();
  }
}

function applyFilters() {
  selectedPage = 1;
  loadLibrary();
}
libraryForm.addEventListener("submit", (event) => {
  event.preventDefault();
  applyFilters();
});
libraryForm.addEventListener("change", (event) => {
  if (event.target.matches("select")) applyFilters();
});
libraryForm.addEventListener("reset", () => window.setTimeout(applyFilters, 0));
byId("previous-page").addEventListener("click", () => { selectedPage -= 1; loadLibrary(); });
byId("next-page").addEventListener("click", () => { selectedPage += 1; loadLibrary(); });

async function loadRecent() {
  const status = byId("recent-status");
  try {
    const { items, total } = await getJson("/api/library");
    renderStories(byId("recent-stories"), items.slice(0, 5));
    status.textContent = total ? total + (total === 1 ? " saved summary" : " saved summaries") : "Run the aggregator to populate your library.";
  } catch (error) {
    status.textContent = error.message || "Recent stories could not be loaded.";
  }
}

const runButton = byId("run-button");
const stages = [
  "Fetching news from your sources",
  "Processing Anthropic articles",
  "Fetching YouTube transcripts",
  "Creating story summaries with Gemini",
  "Ranking stories and saving the digest",
  "Sending the digest by email",
];
let runActive = false;
let runStarting = false;
let previewActive = false;
let pollTimer;

function setButtonStates() {
  const busy = runActive || runStarting || previewActive;
  previewButton.disabled = busy;
  runButton.disabled = busy;
}

function showRun(state) {
  const previous = runActive;
  runActive = state.status === "running";
  setButtonStates();
  byId("run-panel").hidden = state.status === "idle";
  const stepCount = state.email ? 6 : 5;
  const progress = byId("run-progress");
  progress.max = stepCount;
  progress.value = state.status === "complete" ? stepCount : Math.max(0, state.stage - 1);
  const status = byId("run-status");
  status.classList.toggle("run-error", state.status === "failed");
  const message = runActive
    ? "Stage " + state.stage + " of " + stepCount + " · " + stages[state.stage - 1]
    : state.status === "failed" ? state.message
    : state.email_sent ? "Digest saved and emailed."
    : "Digest saved to History.";
  const note = state.status !== "running" && state.ranking_method === "newest"
    ? " Gemini was temporarily unavailable, so the digest uses newest-first order."
    : "";
  if (status.textContent !== message + note) status.textContent = message + note;
  const link = byId("run-edition");
  link.hidden = !state.edition_id;
  if (state.edition_id) link.href = "#history/" + state.edition_id;
  const stats = byId("run-stats");
  stats.hidden = !state.stats;
  if (state.stats) {
    const { fetched, summarized, failed, transcripts_unavailable: unavailable } = state.stats;
    const parts = [fetched + " fetched", summarized + " newly summarized"];
    if (failed) parts.push(failed + " processing failures");
    if (unavailable) parts.push(unavailable + " transcripts unavailable");
    stats.textContent = parts.join(" · ");
  }
  window.clearTimeout(pollTimer);
  if (runActive) pollTimer = window.setTimeout(pollRun, 1500);
  else if (previous && !byId("briefing-view").hidden) loadRecent();
}

async function pollRun() {
  try {
    showRun(await getJson("/api/run"));
  } catch {
    if (runActive) {
      byId("run-status").textContent = "Reconnecting to the pipeline status…";
      pollTimer = window.setTimeout(pollRun, 3000);
    }
  }
}

async function startRun(options) {
  runStarting = true;
  setButtonStates();
  previewStatus.hidden = true;
  try {
    showRun(await postJson("/api/run", options));
  } catch (error) {
    previewStatus.hidden = false;
    previewStatus.classList.add("error");
    previewStatus.textContent = error.message;
    pollRun();
  } finally {
    runStarting = false;
    setButtonStates();
  }
}

const editions = { preview: "Preview", pipeline: "Pipeline run", email: "Sent email" };
const formatEditionDate = new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" });
let historyPage = 1;
let historyMarkdown = "";
let historyRequest = 0;

function editionSummary(edition) {
  const stories = edition.story_count + (edition.story_count === 1 ? " story" : " stories");
  const lookback = edition.hours % 24 === 0 && edition.hours > 24
    ? edition.hours / 24 + " days" : edition.hours + " hours";
  return [editions[edition.kind] || "Saved digest", stories, lookback + " lookback"].join(" · ");
}

async function loadHistory(editionId) {
  const request = ++historyRequest;
  const status = byId("history-status");
  const listing = byId("history-list");
  byId("history-detail").hidden = true;
  byId("history-pagination").hidden = true;
  listing.hidden = Boolean(editionId);
  listing.replaceChildren();
  status.textContent = "Loading digest history…";
  try {
    if (editionId) {
      const { edition, digest, markdown } = await getJson("/api/history/" + editionId);
      if (request !== historyRequest) return;
      historyMarkdown = markdown;
      byId("history-detail-title").textContent = formatEditionDate.format(new Date(edition.date));
      byId("history-detail-meta").textContent = editionSummary(edition)
        + (digest.ranking_method === "newest" ? " · Newest first" : "");
      byId("history-greeting").textContent = digest.introduction.greeting;
      byId("history-introduction").textContent = digest.introduction.introduction;
      renderStories(byId("history-stories"), digest.articles);
      byId("history-detail").hidden = false;
      status.textContent = "";
      return;
    }
    const { items, page_size, total } = await getJson("/api/history?page=" + historyPage);
    if (request !== historyRequest) return;
    for (const item of items) {
      const row = document.createElement("article");
      row.className = "story";
      const body = document.createElement("div");
      body.append(
        textElement("h3", formatEditionDate.format(new Date(item.date))),
        textElement("p", editionSummary(item)),
      );
      const link = textElement("a", "Open digest", "story-link");
      link.href = "#history/" + item.id;
      link.setAttribute("aria-label", "Open digest from " + formatEditionDate.format(new Date(item.date)));
      row.append(body, link);
      listing.append(row);
    }
    const pages = Math.ceil(total / page_size);
    status.textContent = total
      ? total + (total === 1 ? " saved edition" : " saved editions")
      : "Generate a preview or run the pipeline to save your first edition.";
    byId("history-page").textContent = "Page " + historyPage + " of " + pages;
    byId("history-previous").disabled = historyPage <= 1;
    byId("history-next").disabled = historyPage >= pages;
    byId("history-pagination").hidden = pages <= 1;
  } catch (error) {
    if (request === historyRequest) status.textContent = error.message;
  }
}

byId("history-previous").addEventListener("click", () => { historyPage -= 1; loadHistory(); });
byId("history-next").addEventListener("click", () => { historyPage += 1; loadHistory(); });
byId("history-export").addEventListener("click", () => downloadMarkdown(historyMarkdown));

const signalsForm = byId("signals-form");
const signalsStatus = byId("signals-status");
const signalNotice = byId("signals-action");
const signalBriefingSelect = byId("signal-briefing-select");
let signalsPage = 1;
let signalQueryRequest = 0;
let signalBriefingRequest = 0;
let signalBriefing = null;

function signalOptions() {
  const fields = new FormData(signalsForm);
  return { topic: fields.get("topic"), days: Number(fields.get("days")) };
}

function showSignalNotice(text, error = false) {
  signalNotice.textContent = text;
  signalNotice.classList.toggle("error", error);
  signalNotice.hidden = !text;
}

async function loadSignals() {
  const request = ++signalQueryRequest;
  const query = new URLSearchParams({ ...signalOptions(), page: signalsPage });
  signalsStatus.textContent = "Loading signals…";
  try {
    const data = await getJson("/api/signals?" + query);
    if (request !== signalQueryRequest) return;
    byId("world-monitor-load").hidden = !data.world_monitor_configured;
    renderStories(byId("signals-stories"), data.items);
    signalsStatus.textContent = data.total
      ? data.total + (data.total === 1 ? " source item" : " source items")
      : data.refresh ? "No signals in this window. Look further back or change the topic."
        : "Refresh the sources to collect your first signals.";
    const sourceStatus = byId("signals-source-status");
    sourceStatus.hidden = !data.refresh;
    if (data.refresh) {
      const counts = data.refresh.sources.map(source =>
        source.label + " " + (source.count === null ? "unavailable" : source.count)
      );
      sourceStatus.textContent = "Checked " + formatEditionDate.format(new Date(data.refresh.date))
        + " · " + counts.join(" · ");
    }
    const pages = Math.ceil(data.total / data.page_size);
    byId("signals-previous").disabled = signalsPage <= 1;
    byId("signals-next").disabled = signalsPage >= pages;
    byId("signals-page").textContent = "Page " + signalsPage + " of " + pages;
    byId("signals-pagination").hidden = pages <= 1;
  } catch (error) {
    if (request === signalQueryRequest) signalsStatus.textContent = error.message;
  }
}

async function loadSignalBriefing(id) {
  const request = ++signalBriefingRequest;
  const panel = byId("signal-briefing-view");
  panel.hidden = true;
  try {
    const editions = await getJson("/api/signals/briefings");
    if (request !== signalBriefingRequest) return;
    const initial = new Option("Choose a briefing", "");
    signalBriefingSelect.replaceChildren(initial, ...editions.map(edition => new Option(
      formatEditionDate.format(new Date(edition.date)) + " · " + edition.topic, edition.id
    )));
    byId("signal-briefing-history").hidden = !editions.length;
    signalBriefingSelect.value = id || "";
    if (!id) return;
    const briefing = await getJson("/api/signals/briefings/" + id);
    if (request !== signalBriefingRequest) return;
    signalBriefing = briefing;
    byId("signal-briefing-meta").textContent =
      briefing.topic + " · " + briefing.days + " days · " + formatEditionDate.format(new Date(briefing.date))
      + (briefing.ranking === "gemini" ? " · Gemini-ranked" : " · Newest first (Gemini ranking unavailable)");
    renderStories(byId("signal-briefing-stories"), briefing.items);
    panel.hidden = false;
  } catch (error) {
    if (request === signalBriefingRequest) showSignalNotice(error.message, true);
  }
}

signalsForm.addEventListener("change", () => {
  signalsPage = 1;
  loadSignals();
});
signalBriefingSelect.addEventListener("change", () => {
  const value = signalBriefingSelect.value;
  location.hash = value ? "#signals/" + value : "#signals";
});
byId("signals-previous").addEventListener("click", () => { signalsPage--; loadSignals(); });
byId("signals-next").addEventListener("click", () => { signalsPage++; loadSignals(); });

byId("signals-refresh").addEventListener("click", async () => {
  const button = byId("signals-refresh");
  button.disabled = true;
  showSignalNotice("Checking the public feeds…");
  try {
    const { sources } = await postJson("/api/signals/refresh", {});
    const failures = sources.filter(source => source.count === null);
    showSignalNotice(failures.length
      ? "Could not refresh " + failures.map(source => source.label).join(", ") + ". Previously saved signals are still available."
      : "Public sources refreshed.", failures.length === sources.length);
    signalsPage = 1;
    await loadSignals();
  } catch (error) {
    showSignalNotice(error.message, true);
  } finally {
    button.disabled = false;
  }
});

signalsForm.addEventListener("submit", async event => {
  event.preventDefault();
  const button = byId("signals-generate");
  button.disabled = true;
  showSignalNotice("Preparing a briefing from saved signals…");
  try {
    const briefing = await postJson("/api/signals/briefings", signalOptions());
    showSignalNotice("Briefing saved.");
    if (!byId("signals-view").hidden) location.hash = "#signals/" + briefing.id;
  } catch (error) {
    showSignalNotice(error.message, true);
  } finally {
    button.disabled = false;
  }
});

byId("signal-briefing-export").addEventListener("click", () => {
  if (!signalBriefing) return;
  const { date, days, topic, ranking, items } = signalBriefing;
  const escape = text => text.replace(/[<>\\\[\]*_~\x60]/g, "\\$&");
  const intro = [
    "# Signal briefing",
    topic + " · " + days + " days · " + date.slice(0, 10),
    ranking === "gemini" ? "Gemini-ranked" : "Newest first (Gemini ranking unavailable)",
  ];
  const entries = items.map(item => [
    "## " + escape(item.title),
    escape(item.publisher) + " · " + item.date.slice(0, 10),
    escape(item.summary),
    "[Original source](<" + item.url + ">)",
  ].join("\n\n"));
  downloadMarkdown([...intro, ...entries].join("\n\n") + "\n", "signal-briefing.md");
});

byId("world-monitor-load").addEventListener("click", async () => {
  const button = byId("world-monitor-load");
  const status = byId("world-monitor-status");
  const list = byId("world-monitor-events");
  const notices = byId("world-monitor-notices");
  button.disabled = true;
  status.hidden = false;
  status.textContent = "Loading tech events…";
  try {
    const data = await postJson("/api/signals/world-monitor", {});
    renderStories(list, data.events.map(event => ({
      ...event,
      date: event.start_date ? event.start_date + "T12:00:00Z" : null,
      summary: [event.location, event.end_date && event.end_date !== event.start_date
        ? "Ends " + event.end_date : ""].filter(Boolean).join(" · "),
    })));
    list.hidden = !data.events.length;
    const annotations = [data.attribution, ...data.events.map(event => event.attribution)].filter(Boolean);
    notices.hidden = !annotations.length;
    byId("world-monitor-attribution").textContent = annotations.map(value =>
      typeof value === "string" ? value : JSON.stringify(value, null, 2)
    ).join("\n\n");
    const cached = Date.parse(data.cached_at);
    status.textContent = (data.events.length ? "World Monitor calendar" : "No calendar events returned")
      + (data.stale ? " · Provider marked this snapshot as stale" : "")
      + (Number.isFinite(cached) ? " · Updated " + formatEditionDate.format(new Date(cached)) : "");
  } catch (error) {
    status.textContent = error.message;
  } finally {
    button.disabled = false;
  }
});

function showView(moveFocus = false) {
  const views = ["briefing", "library", "signals", "history"];
  const [section, editionId] = location.hash.slice(1).split("/");
  const active = views.includes(section) ? section : "briefing";
  for (const view of views) byId(view + "-view").hidden = view !== active;
  for (const link of document.querySelectorAll("[data-tab]")) {
    if (link.dataset.tab === active) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  }
  if (active === "library") loadLibrary();
  else if (active === "history") loadHistory(editionId?.match(/^\d+$/) ? editionId : undefined);
  else if (active === "signals") {
    loadSignals();
    loadSignalBriefing(editionId?.match(/^\d+$/) ? editionId : undefined);
  }
  else loadRecent();
  if (moveFocus) {
    const heading = byId(active + "-title");
    heading.tabIndex = -1;
    heading.focus();
  }
}
window.addEventListener("hashchange", () => showView(true));
showView();
pollRun();
