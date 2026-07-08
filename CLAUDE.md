# HEXA SEO Automation — Project Guide

## What this is

A Flask web platform that generates grounded, SEO-optimized blog posts for **Hexa Climate** (Indian renewable energy consulting). It scrapes the brand website + trusted industry sources, feeds them as RAG context to Claude, and produces CMS-ready JSON blog posts with Markdown/HTML/PDF/DOCX exports and Pexels stock photos.

Key principle: **no hallucination**. Every fact about Hexa comes from scraped primary sources; industry stats come from secondary sources. Links are validated against a URL inventory — anything Claude invents gets stripped.

---

## Architecture

```
User (browser)
  │
  ├─ Form: brand URL, primary URLs, secondary URLs, keywords, format, word count
  │
  └─ POST /api/generate  →  NDJSON stream
       │
       ├─ 1. Scraper: deep-crawl brand site + fetch primary + secondary URLs
       │     (fallback chain: direct → Jina Reader → Wayback Machine)
       │
       ├─ 2. Build grounding context + URL inventory
       │
       ├─ 3. Per keyword (ThreadPoolExecutor):
       │     ├─ Claude writes JSON blog (system prompt with sources + inventory)
       │     ├─ validate_and_clean_links() strips unverified URLs
       │     ├─ Pexels fetches stock photos (1 hero + 2 in-body)
       │     └─ Render to JSON, MD, HTML, PDF, DOCX
       │
       └─ 4. Stream progress events back to browser
```

## File layout

```
app.py                  Flask server, routes, NDJSON streaming
seo/
  __init__.py
  scraper.py            Source fetcher — 3-tier fallback, deep crawl, logo detection
  blog_writer.py        Claude API call — system prompt, JSON extraction, link validation
  image_gen.py          Pexels stock photo search
  pipeline.py           Orchestrator — keyword parsing, batching, concurrent generation
  renderer.py           JSON → Markdown, HTML, PDF (xhtml2pdf), DOCX (python-docx)
  db.py                 Lazy SQLAlchemy engine from DATABASE_URL
templates/
  index.html            Jinja2 template — form, progress panel, result cards
static/
  style.css             Hexa navy/blue theme
  app.js                Form persistence (localStorage), NDJSON stream reader, UI
  logo.svg              Hexa Climate logo (SVG fallback)
render.yaml             Render Blueprint for one-click deploy
Procfile                gunicorn config (1 worker, 4 threads, 600s timeout)
requirements.txt        Python dependencies
.env.example            All env vars documented
.gitignore              Ignores .env, outputs/, __pycache__, etc.
outputs/                Generated blogs land here (ephemeral on Render)
```

## Environment variables

| Variable | Required | Default | Notes |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | — | [console.anthropic.com](https://console.anthropic.com/) |
| `PEXELS_API_KEY` | No | — | [pexels.com/api](https://www.pexels.com/api/) — free, 200 req/hr. Without it, blogs still generate with image placeholders. |
| `DATABASE_URL` | No | — | Render Internal Database URL. App works without it. |
| `BRAND_WEBSITE` | No | `https://hexaclimate.com` | Pre-fills the form. |
| `CLAUDE_MODEL` | No | `claude-opus-4-8` | Any Anthropic model ID. |
| `CLAUDE_EFFORT` | No | `high` | Adaptive thinking effort: low/medium/high/max. |
| `CLAUDE_MAX_TOKENS` | No | `16000` | Must be high enough for thinking + full JSON output. |
| `MAX_CRAWL_PAGES` | No | `12` | Brand site pages to deep-crawl. |
| `CONCURRENCY` | No | `1` | Parallel keyword threads (1–8). |
| `JINA_API_KEY` | No | — | Optional — raises Jina Reader rate limit. |
| `PORT` | No | `5000` | Flask/gunicorn port. |

## Setup (local)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in ANTHROPIC_API_KEY at minimum
python app.py           # → http://localhost:5000
```

## Deploy (Render)

1. Push to GitHub.
2. Render → New → Blueprint → pick this repo → `render.yaml` auto-configures.
3. Set `ANTHROPIC_API_KEY` and `PEXELS_API_KEY` in Render → Environment.
4. Optionally attach a Postgres service and paste its Internal URL as `DATABASE_URL`.

## How the grounding system works

### Two-tier sources

- **PRIMARY** (brand truth): The brand website is deep-crawled (up to `MAX_CRAWL_PAGES` internal pages, priority-sorted by URL keywords like "about", "solution", "service"). Additional primary URLs (LinkedIn, press releases) are fetched as single pages. Claude may ONLY cite Hexa-specific facts from primary sources.

- **SECONDARY** (industry RAG): Trusted portals (CEA, MNRE, MERCOM India, IEEFA, etc.). Used for general industry context — stats, regulations, trends. Never used for claims about the brand.

### Fallback fetch chain

Many gov/industry sites block datacenter IPs. The scraper tries three methods in order:

1. **Direct fetch** with Chrome-realistic headers (Sec-Ch-Ua, Sec-Fetch-*, DNT).
2. **Jina Reader** (`r.jina.ai/<url>`) — a free reader proxy that fetches from its own infra and renders JS.
3. **Wayback Machine** — latest `archive.org` snapshot. Gov portals are archived frequently.

### URL inventory + link validation

Before calling Claude, the pipeline builds an inventory of all successfully-scraped URLs (primary = internal, secondary = citation). Claude's system prompt lists these as the ONLY allowed link targets.

After Claude returns the blog JSON, `validate_and_clean_links()` walks every block's `links` array and:
- Drops links whose `href` domain isn't in the inventory
- Drops links whose `anchor` text isn't a real substring of the block text
- Reports kept/dropped stats in the UI

### Link rules

- **Internal links** (Hexa pages): 2–3 per blog, natural anchor text
- **Citation links** (secondary sources): 2–4 per blog, at stat/regulation references
- Links are declared in a `links` array on each content block — no inline markdown

## CMS JSON schema

Each blog is a single JSON object with this structure:

```json
{
  "slug": "kebab-case-slug",
  "seo": {
    "title": "SEO title (~60 chars) | Hexa Climate",
    "description": "150-160 char meta description",
    "keywords": ["focus keyword", "related-1", "related-2"]
  },
  "meta": {
    "title": "Display title (no brand suffix)",
    "subtitle": "One-line subtitle (~120 chars)",
    "author": "Hexa Climate Editorial Team",
    "readTimeMinutes": 7,
    "publishedDate": "2026-01-15",
    "category": "Policy & Regulations | Renewable Energy | Decarbonisation | C&I Procurement | ESG & Reporting",
    "tags": ["3-6 topic tags"]
  },
  "hero": {
    "image": { "src": "/assets/blogs/<slug>/hero.png", "alt": "..." },
    "showOverlayTitle": true
  },
  "content": [
    { "id": "unique-id", "type": "heading", "level": 2, "text": "..." },
    { "id": "unique-id", "type": "paragraph", "text": "...",
      "links": [{ "anchor": "exact substring", "href": "https://...", "kind": "internal" }] },
    { "id": "unique-id", "type": "list", "style": "unordered", "items": ["..."] },
    { "id": "unique-id", "type": "image", "src": "/assets/blogs/<slug>/img.png",
      "alt": "photographable scene description", "caption": "short caption" }
  ]
}
```

Content block types: `heading` (level 2 or 3), `paragraph`, `list` (ordered/unordered), `image`.

## Image handling

- **Source**: Pexels stock photo API (not AI-generated).
- **Count**: 1 hero image + exactly 2 in-body images. `_cap_image_blocks()` enforces the limit.
- **Alt text rules**: Must describe a real, photographable subject (solar panels, wind turbines, battery storage). No diagrams, infographics, charts, or text-in-image — Pexels is photography only.
- **Search strategy**: Alt text is stripped of stopwords/filler, then searched at decreasing lengths (5, 3, 2 words) so descriptive sentences still match.
- **Graceful failure**: If `PEXELS_API_KEY` is unset, images are silently skipped. The renderer shows placeholder cards (dashed blue box with alt text in HTML/PDF, italic marker in DOCX).

## Batch queue system

- Keywords are saved in the browser via `localStorage` (key: `hexa-seo-form-v1`).
- Excel/CSV files are imported once via `/api/parse-keywords` into the textarea queue.
- Each run takes the first N keywords (configurable "Blogs per run", default 2).
- Successfully completed keywords are auto-removed from the queue.
- Failed keywords stay in the queue for retry on the next run.
- All form fields (URLs, settings, keywords) survive page reloads.

## Formats

Two blog formats, selectable in the UI:

- **Paragraph** (long-form): Flowing prose with H2/H3 sections, substantive paragraphs of 3–6 sentences, lists used sparingly.
- **Listicle**: Numbered points (6–10), each with a brief paragraph + bullet list. Scannable and skimmable.

Target word count is configurable (500–3000, default 1400).

## Output formats

Each keyword produces a directory `outputs/<run-id>/<NNN-slug>/` with:

| File | Purpose |
|---|---|
| `post.json` | Canonical CMS schema — import directly |
| `post.md` | Markdown with YAML frontmatter |
| `post.html` | Standalone HTML preview (styled, with inline CSS) |
| `post.pdf` | PDF via xhtml2pdf |
| `post.docx` | Word document with real hyperlinks (OOXML) |
| `hero.png/jpg` | Hero image from Pexels |
| `*.png/jpg` | In-body images from Pexels |

## API routes

| Route | Method | Purpose |
|---|---|---|
| `/` | GET | Main form UI |
| `/api/generate` | POST | NDJSON streaming blog generation |
| `/api/parse-keywords` | POST | One-time Excel/CSV keyword import |
| `/api/logo` | GET | Brand logo proxy (with SVG fallback) |
| `/api/health` | GET | Liveness check (Claude/Pexels/DB status) |
| `/outputs/<path>` | GET | Serve generated files |

## NDJSON event types

The `/api/generate` endpoint streams these events:

- `start` — run ID, total keyword count
- `status` — progress messages (scraping, writing)
- `warn` — non-fatal warnings (source fetch failures, missing API keys)
- `grounded` — source scraping complete, context stats
- `keyword_done` — one blog finished (includes full result record)
- `keyword_error` — one blog failed (keyword stays in queue)
- `done` — run complete
- `error` — fatal error with traceback

## Known issues / limitations

- **Render free tier**: filesystem is ephemeral — download outputs immediately. 700+ keywords need many batched runs (~4+ hours total).
- **Blocked sources**: Some gov/portal sites (CEA, POSOCO) block datacenter IPs. The 3-tier fallback (direct → Jina → Wayback) handles most cases, but very recently updated pages may only be available via Jina.
- **Claude max_tokens**: If `CLAUDE_MAX_TOKENS` is too low, adaptive thinking consumes the budget before producing output. Default 16000 works for ~1400-word blogs; increase for longer posts or `max` effort.
- **Image-text mismatch**: Pexels search is keyword-based, so very specific alt text may return loosely related photos. The progressive query shortening (5→3→2 words) mitigates this.

## Brand identity

- **Colors**: Navy `#1e40af` (primary), Blue `#3b82f6` (accent), Ink `#0f172a`
- **Logo**: Blue isometric hexagon wireframe with vertex nodes and center spokes, stacked "Hexa Climate" wordmark. SVG fallback in `static/logo.svg`; live logo auto-detected from brand site via og:image / img[alt~=logo] / favicon.
- **Audience**: Indian C&I procurement, sustainability, and ESG decision-makers.
- **Tone**: Confident, data-driven, domain-expert. No AI throat-clearing or fluff.
