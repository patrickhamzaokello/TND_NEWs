# From Scrape to Digest: How NWITQ Processes a News Article

**Purpose of this document:** trace one article's full journey through the system — every
stage, every model, every threshold and prompt that shapes what a reader ultimately sees. This
is meant to be read, questioned, and revised; every parameter below is a deliberate but
adjustable choice, not a law of nature.

---

## 1. High-level pipeline

```
┌──────────┐   ┌────────────┐   ┌───────────┐   ┌──────────────┐   ┌───────────────┐
│  Scrape  │ → │  Enrich    │ → │  Embed    │ → │  Cluster /   │ → │  Digest /      │
│ (source  │   │ (per-      │   │ (semantic │   │  Story       │   │  Story Card /  │
│ scrapers)│   │  article   │   │  vector)  │   │  Engine      │   │  Twitter /     │
│          │   │  LLM call) │   │           │   │              │   │  Email         │
└──────────┘   └────────────┘   └───────────┘   └──────────────┘   └───────────────┘
```

Each stage is a separate Celery task, run on its own schedule, reading the output of the
previous stage from Postgres. Nothing is synchronous — an article can sit at any stage for
minutes to hours depending on when the next scheduled task picks it up.

---

## 2. Stage 1 — Scraping

**Code:** `tnd_apps/news_scrapping/*_scrapper.py`, one file per source.

Every source (Daily Monitor, Observer, NilePost, Chimp Reports, UBC, Kawowo, Pulse Uganda,
Uganda Radio Network) has its own scraper class, but they share a common shape:

| Step | What happens |
|---|---|
| **Listing fetch** | Load the source's news/section listing page (or, for URN, hit its JSON API directly). Prefer plain `requests`; fall back to headless Chromium (Selenium) if the page requires JS or blocks bots. |
| **Article discovery** | Extract candidate article URLs from the listing (JSON-LD `ItemList`, CSS card selectors, or raw anchor tags as a last resort). |
| **Dedup check** | `Article.find_existing()` — matches by URL, external ID, or content hash, so the same article is never inserted twice even if a source republishes it under a slightly different URL. |
| **Detail fetch** | Visit the article's own page for the full body, using JSON-LD (`NewsArticle`/`Article` schema) first, then falling back to CSS selectors for title, body paragraphs, author, published date, featured image, category, tags. |
| **Boilerplate stripping** | Each scraper has a source-specific list of junk phrases ("subscribe", "also read", "click here", etc.) filtered out of extracted paragraphs. |
| **Save** | `Article` row created/updated with `content`, `excerpt`, `word_count`, `has_full_content` (true only if word count clears a minimum threshold per source), `published_at`, `featured_image_url`. |

### Special cases worth knowing

- **Uganda Radio Network (URN):** its archive page is Cloudflare-protected with a *managed
  challenge* that defeats plain requests and vanilla headless Chromium. The scraper talks
  directly to URN's internal JSON API (`/a/json/archive.php`), which — unlike the public page —
  returns the **full, unpaywalled article body**. Requests are routed through a **FlareSolverr**
  sidecar container that solves the Cloudflare challenge with a real browser when the direct
  call gets a 403. URN's API also ignores all pagination parameters and always returns the
  latest ~14 stories, so it's scraped every 2 hours rather than paginated deeply.
- **URN radio-script artifacts:** URN's body text contains broadcast cue markers
  (`//Cue in: ... Cue out ...//`) referencing the accompanying audio clip — stripped by regex
  before the text is saved.
- **Pulse Uganda:** reuses the Observer scraper's machinery (JSON-LD extraction, Selenium
  fallback) with Pulse-specific URL patterns (`/story/<slug>-<timestamp>`) and boilerplate.

### Scheduling

Scrapers run on a 3-hour cycle (some sources on a 2-hour cycle), with each source's minute
offset staggered so no two Selenium-heavy scrapers run at the same moment on the same 4GB
server. See `TNDNEWS/celery.py` `beat_schedule` for the exact minute/hour of each source.

---

## 3. Stage 2 — Enrichment

**Code:** `tnd_apps/newsintelligence/agents.py`, `prompts.py` (`ARTICLE_ANALYSIS_*`),
`openai_client.py`.

**Trigger:** `enrich_new_articles` task, hourly at `:15`. Picks up to `batch_size` (default 50)
articles where `has_full_content=True` and no `ArticleEnrichment` row exists yet (or a prior
attempt failed and is under the retry limit).

**Model:** `gpt-4o-mini` (cheap enough for bulk processing — roughly $0.15/$0.60 per 1M
input/output tokens).

### What the LLM produces per article (`ArticleEnrichment` model)

| Field | Purpose |
|---|---|
| `summary` | 2–3 sentence neutral summary — what happened, who, immediate significance. |
| `neutral_title` | A **rewritten** headline in the platform's own words — never copies the publisher's. Used directly as the story card title for single-article stories (see §5). |
| `why_it_matters` | One dense sentence of concrete stakes — no "this matters because" framing, no abstract phrases like "public trust". |
| `sentiment`, `sentiment_score` | positive / negative / neutral / mixed, plus a -1.0–1.0 float. |
| `importance_score` | 1–10 newsworthiness. Calibrated so most articles land 3–6; 7+ requires real national consequences; 9–10 reserved for historic-scale events. |
| `themes` | 1–4 tags from a fixed vocabulary (governance, economy, sports, crime, etc.) |
| `key_facts` | Concrete bullet facts with names/numbers/places — becomes the story card's highlight bullets for single-article stories. |
| `key_highlights` | Verbatim phrases from the article body, tagged `fact`/`figure`/`claim`/`link` — used by clients to underline important passages inside the raw article text. |
| `claims` | Attributed factual claims with a confidence score (lower if single-sourced/unverified). |
| `entities_people` / `_organizations` / `_locations` | Named entity extraction — feeds embeddings, story clustering, and clickable entity tags. |
| `local_impact` | `{regions, affected_groups, time_horizon, impact_note}` — who specifically feels this and how, phrased as concrete fact rather than commentary. |
| `bias_or_framing_notes` | Only genuinely observable patterns (single-source reporting, PR-as-news, sensationalism, missing context) — not political-lean judgments. |
| `follow_up_worthy`, `controversy_flag`, `is_breaking_candidate` | Booleans used by the story-alert system. |

### Tone contract (applies to every LLM-generated field platform-wide)

The system prompt (`ARTICLE_ANALYSIS_SYSTEM`) targets **young Ugandans, 18–35** — students,
early-career professionals, digitally native. Concretely:

- Dense and factual over conversational — every sentence carries information (names, actions,
  figures, dates), not commentary.
- Never addresses the reader, never says "this matters because," "raises concerns," "public
  trust," "keep an eye out" — these are explicitly banned filler phrases.
- Consequences are stated as facts ("leaves 4M NSSF contributors unable to withdraw until Q3"),
  never as reader-directed warnings.
- Connects to daily life where genuinely relevant (jobs, transport, mobile money, school fees) —
  but only when the article supports it, never forced.

This exact tone contract is reused, nearly verbatim, in the digest and story-synthesis prompts
(§5, §6) so the whole platform reads as one voice regardless of which pipeline stage produced
the text.

### Editorial image generation (optional, on-demand)

Separately from enrichment text, `editorial_image_service.py` can generate an AI "engraving/
stippling" style illustration from an article's featured image via `gpt-image-1`
(image-edit endpoint). Not automatic per-article — triggered manually per article or in batch
via the admin action, since it costs real money per image. Tracks
`editorial_image_status` (`generated` / `skipped` / `moderation` / `download_error` /
`api_error` / `error`) so failures are visible in the admin list view rather than silent.

---

## 4. Stage 3 — Embedding

**Code:** `story_engine.py` → `embed_pending_articles()`.

**Trigger:** part of the hourly `process_story_engine` task (`:45`).

**Model:** `text-embedding-3-small` (1536 dimensions, ~$0.02/1M tokens — negligible cost).

**What gets embedded:** `title + cleaned article body (first ~6000 chars) + extracted entities
+ themes`. Falls back to the AI summary only if the scraper didn't capture a full body.

We embed the **body**, not the AI summary, because:
1. `Article.content` is already cleaned at save time (boilerplate stripped), so it's safe to
   embed directly.
2. Embedding the body is independent of enrichment quality — two outlets' differently-styled
   prose about the same event still lands close together in vector space based on the actual
   facts, not on how well the summarizer did its job.
3. It's cheap enough (6000 chars ≈ 1500 tokens) that truncation costs little signal — a news
   article's opening carries the who/what/where.

Vector similarity search is done **in-process** (plain Python cosine similarity over active
cluster centroids) rather than a dedicated vector database — at this platform's volume
(~50–200 articles/day, low hundreds of active stories), that's faster than a network round-trip
and needs no new infrastructure.

---

## 5. Stage 4 — Story Engine: Event Detection & Clustering

**Code:** `story_engine.py`, models `StoryCluster` / `StoryClusterArticle` /
`StoryClusterRelation` / `StoryVersion`.

This is the most involved stage — it decides whether an article is reporting on an **existing**
story or a **brand-new** one, in three tiers.

### 5.1 — Stage 1: match against currently-active stories

For each embedded, unassigned article:

1. Candidate stories = `StoryCluster` rows with `status='active'` and `last_seen_at` within the
   last **14 days** (`EVENT_WINDOW_DAYS`).
2. **Temporal proximity gate** *(added after a real incident — see §5.5)*: the article's own
   `published_at` must be within 14 days of the candidate's `last_seen_at`. A candidate cluster
   being "currently active" is not enough — the article's real event date must plausibly belong
   to that story's timeline. Articles with no confirmed publish date are treated as unproven and
   can only attach via a very strong direct match.
3. **Cosine similarity** between the article's embedding and the cluster's **centroid**
   (mean of member embeddings):
   - `≥ 0.86` (`COSINE_STRONG_MATCH`) → same event, attach immediately, no further checks.
   - `< 0.52` (`COSINE_FLOOR`) → never attach, reject outright.
   - In between → must also clear an **entity-overlap gate**: Jaccard similarity of
     (person/org/location) sets between the article and the cluster's 10 most recent members.
     An article sharing **zero** named entities with a story can never join it — this is what
     stops two unrelated stories in the same broad topic (e.g. two different sports events, two
     different corruption cases) from merging just because they're thematically similar.
   - Combined score = `0.70 × cosine + 0.30 × entity_overlap`; must clear **0.66**
     (`SEMANTIC_ATTACH_THRESHOLD`) to attach.

If no active story qualifies, fall through to stage 2.

### 5.2 — Stage 2: revival (old stories resurfacing)

Handles the case of a follow-up article months after the original coverage — e.g. a court
ruling long after an arrest that's no longer "active."

1. Search stories that fell **outside** the 14-day active window, going back up to **365 days**.
2. **Direct revival:** cosine ≥ 0.80 *and* entity overlap ≥ 0.10 → the old story is revived
   (`status` flips back to `active`), no LLM call needed.
3. **Borderline band (cosine 0.62–0.80):** the top 2 candidates go to an LLM adjudicator
   (`_adjudicate`, using `gpt-4o-mini` with the `STORY_ADJUDICATION_*` prompt), which judges
   `same_story` / `related_story` / `unrelated` based on shared specific entities and causal
   continuity ("this happened *because of* / as the *next step of* that"). Capped at 2 calls per
   article, ~150 tokens each — cheap, and it only fires for articles that already failed the
   free stage-1 check.
   - `same_story` → revive the old cluster, attach the article.
   - `related_story` → create a **new** story, but link it to the old one via
     `StoryClusterRelation` (a lightweight story graph: "continuation" vs "related" edges,
     surfaced on the story page as "Related stories").
   - `unrelated` → falls through to stage 3.

### 5.3 — Stage 3: new story

If nothing matched, a brand-new `StoryCluster` is created, seeded with the article's own
embedding as its initial centroid.

### 5.4 — Card population (avoiding wasted LLM calls)

- **Single-article stories** get their card fields (title, summary, highlights, entities, "why
  it matters") copied **directly from that article's enrichment** — `neutral_title` becomes the
  story title, `key_facts` become highlight bullets, etc. **Zero extra LLM calls.** This matters
  because the majority of stories never grow beyond one source, so re-running a full synthesis
  on every singleton would be pure waste.
- **Once a second article joins** (`SYNTHESIS_MIN_ARTICLES = 2`), full synthesis takes over (see
  §5.6) and **every subsequent new article re-triggers synthesis** (`SYNTHESIS_GROWTH_TRIGGER =
  1`) — deliberately aggressive, because a stale title sitting next to a fresh "updated N hours
  ago" badge is a much worse failure than one extra cheap `gpt-4o-mini` call.

### 5.5 — Incident that shaped the temporal-proximity gate

Real example encountered in production: a story titled *"Messi Scores in Argentina's 3-1
Victory Over Jordan"* kept showing "updated 4 hours ago" — but the actual Jordan match was
**three weeks old**. What had happened: a brand-new article (a World Cup Final preview,
published today) about the same recurring entities (Messi, Argentina, World Cup) matched the
existing story purely on topic similarity and got attached, bumping `last_seen_at` to "now"
without the *title* ever catching up to reflect that the real news had moved on.

Two fixes landed from this:
1. `SYNTHESIS_GROWTH_TRIGGER` dropped from 2 → 1 (§5.4) so titles never lag behind
   `last_seen_at`.
2. The temporal-proximity gate (§5.1, step 2) was added so an article's *own* publish date must
   be plausible for the story it's joining — not just "the story happens to be active right
   now." A follow-up management command, `split_contaminated_stories`, was written to detect and
   retroactively split any existing clusters whose member articles span a date gap wider than
   the event window.

### 5.6 — Full synthesis (2+ articles)

**Model:** `gpt-4o-mini`, prompt `STORY_SYNTHESIS_*`.

Given every member article's title, summary, key facts, and importance score (oldest → newest),
plus any linked earlier stories for historical context, the LLM produces:

| Field | What it is |
|---|---|
| `title` | Rewritten, neutral, describes the **current** state of the event — never copies a source headline, no length cap (cards clip visually to 2 lines instead). |
| `short_summary` | 2–3 sentences: what happened, consensus facts across sources, duplicates removed. |
| `long_summary` | 2–4 paragraphs, the full chronological story so far. |
| `overview` | 4–6 short paragraphs: detailed chronology + a final "why this matters / what this changes" paragraph, grounded in linked earlier stories where relevant. |
| `why_it_matters` | One dense sentence of concrete stakes. |
| `key_highlights` | 3–6 facts, each tagged with `sources_count` — how many of the input articles independently support it (this is the actual point of multi-source synthesis: surfacing what's *corroborated* vs. single-sourced). |
| `entities` | Every person/org/location appearing **verbatim** in the generated text — validated so clients can substring-match and render clickable entity tags reliably. |

Every synthesis run:
- Increments `StoryCluster.version` and snapshots an immutable `StoryVersion` row (title,
  summaries, highlights, article count, a one-line `change_note`) — full audit trail of how the
  story's understanding evolved.
- Is guarded by `select_for_update()` + a DB-computed next-version number, so two concurrent
  workers (e.g. the hourly scheduled pass and a manual backfill) can't create duplicate version
  numbers.

### 5.7 — On-demand "Explain Like I'm 5"

A separate, purely cached feature: clicking the button on a story page calls
`get_or_generate_eli5()`, which generates a plain-language explanation (via `gpt-4o-mini`,
`ELI5_*` prompts) **once per story version** and caches it on the cluster. Every subsequent
click — by any user — gets the same cached text, until the story's `version` advances (new
synthesis), at which point it regenerates. The prompt follows the same tone contract as
everything else: no jargon left unexplained, no condescending "let me explain" framing, local
comparisons (boda rides, market stalls) only when genuinely clarifying.

---

## 6. Stage 5 — Daily Digest

**Code:** `agents.py` (digest agent), `prompts.py` (`DAILY_DIGEST_*`), `email_service.py`,
`views.py` (`digest_home`), `tasks.py`.

**Trigger:** `generate_daily_digest`, scheduled 4×/day (morning/midday/evening/night — only
morning triggers the email + Twitter push; the others keep the API/website content fresh
through the day). Runs a small enrichment top-up first to catch anything the hourly enrichment
task hasn't reached yet, then synthesizes.

**Model:** `gpt-4o-mini`, fed every enriched article for the target date ordered by importance,
plus 7-day trending entities.

### Output (`DailyDigest` model)

| Field | Purpose |
|---|---|
| `digest_text` | 1–4 paragraphs (length scales with article volume): lead story → other notable stories → developing situations with concrete next steps → one under-covered story affecting daily life. |
| `top_stories` | 3–5 stories, each with `why_it_matters` (one dense stacked-stakes sentence). |
| `under_radar_story` | One deliberately under-covered story with a concrete-consequence `reason`. |
| `key_concern` / `key_concern_short` | The day's single most consequential development — `_short` is the ≤180-char version used as the tweet-thread opener and social share hook. |
| `trending_entities` | Top 5, each with a factual `trend_note` (the specific events driving mentions — not vague "signals" language). |
| `sector_sentiment` | -1.0–1.0 per sector (governance, economy, health, etc.) for trend charts. |
| `story_threads` | Groups articles sharing a `related_themes` arc name into a running thread. |
| `illustration` / `illustration_caption` | AI-generated editorial-style image based on the top story, with a fallback chain: try image-edit on the top story's photo → if moderation-blocked, try the next top story → if all fail, text-to-image from the story's title/context. Caption always names which story inspired it. |

### Where it's read

1. **Email** — two sends/day (morning full digest to all active subscribers except
   evening-only; evening roundup to `morning_evening` + `evening` subscribers). Responsive HTML
   with real `<table>` layout (Gmail/Outlook-safe), images sized for both web and mobile.
2. **Website** (`newsapi.mwonya.com`) — homepage renders today's digest; if not yet published,
   shows a "brief is on its way" state with the 6 most recently updated stories instead of stale
   content. A `/stories/` browser and per-story pages expose the full story-engine output
   (search, entity tags, timeline, coverage list, ELI5).
3. **Twitter/X** — a thread built from `digest_text` split into tweet-sized chunks (packed to
   fill 280 chars per tweet, never mid-sentence), opened with `key_concern_short` and the day's
   illustration, closed with a link back to the full brief.
4. **Mobile API** — full JSON via DRF serializers, including `illustration_url`,
   `key_highlights`, and story-cluster data for the app's feed.

---

## 7. Key parameters at a glance

| Parameter | Value | File |
|---|---|---|
| Enrichment model | `gpt-4o-mini` | `openai_client.py` |
| Digest / synthesis model | `gpt-4o-mini` | `openai_client.py` |
| Embedding model | `text-embedding-3-small` (1536-dim) | `story_engine.py` |
| `COSINE_STRONG_MATCH` | 0.86 | `story_engine.py` |
| `COSINE_FLOOR` | 0.52 | `story_engine.py` |
| `MIN_ENTITY_OVERLAP` | 0.04 | `story_engine.py` |
| `SEMANTIC_ATTACH_THRESHOLD` | 0.66 | `story_engine.py` |
| `EVENT_WINDOW_DAYS` (active-story window + temporal gate) | 14 | `story_engine.py` |
| `REVIVAL_LOOKBACK_DAYS` | 365 | `story_engine.py` |
| `REVIVAL_COSINE_DIRECT` | 0.80 | `story_engine.py` |
| `ADJUDICATION_COSINE_MIN` / max candidates | 0.62 / 2 | `story_engine.py` |
| `SYNTHESIS_MIN_ARTICLES` | 2 | `story_engine.py` |
| `SYNTHESIS_GROWTH_TRIGGER` | 1 (every new article re-synthesizes) | `story_engine.py` |
| `SYNTHESIS_IMPORTANCE_TRIGGER` | 7 | `story_engine.py` |
| Enrichment schedule | hourly, `:15` | `celery.py` |
| Story engine schedule | hourly, `:45` | `celery.py` |
| Digest generation | 4×/day | `celery.py` |
| Email sends | 2×/day (morning, evening) | `celery.py` |

---

## 8. Open questions worth discussing

- **Clustering granularity for recurring entities.** Sports/entertainment stories involving the
  same recurring actors (a team across a tournament) are more prone to over-clustering than,
  say, two unrelated corruption cases with different defendants — entity overlap alone doesn't
  distinguish "same ongoing campaign" from "same people, different event." Currently mitigated
  by the temporal-proximity gate and the story graph (`StoryClusterRelation`), but not fully
  solved.
- **`SYNTHESIS_GROWTH_TRIGGER = 1` cost tradeoff.** Correctness-first choice; worth monitoring
  actual daily LLM spend as story volume grows, since every multi-source story now re-synthesizes
  on every single new article rather than batching updates.
- **Missing `published_at` articles.** Currently logged as a warning and allowed through with a
  `scraped_at` fallback (needed for *some* event_date), but only permitted to join a story via a
  very strong direct match. Worth deciding whether these should instead be quarantined for
  manual review, given they were the root cause of the Messi/Jordan incident.
- **URN's Cloudflare dependency.** The FlareSolverr bypass is inherently fragile — Cloudflare
  could tighten the challenge tier at any time, at which point URN scraping breaks until the
  bypass is re-tuned.
