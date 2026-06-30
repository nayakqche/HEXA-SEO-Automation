# HEXA Climate — SEO Blog Automation

A web platform that turns a **CSV of keywords** into a batch of **SEO-optimized
blog posts**, each with a generated hero image — all **grounded in the Hexa
Climate website** so the content stays factually accurate instead of
hallucinating.

- ✍️ **Claude (Opus 4.8)** writes each blog, constrained to facts scraped from
  your real website.
- 🖼️ **Gemini** generates a hero image for every post.
- 🔒 **No hallucination by design** — the writer's only source of truth is the
  text crawled from your site. If a fact isn't on the site, it won't be invented.
- 📊 Live progress, downloadable Markdown + standalone HTML previews + images.

---

## How grounding works

1. You give the platform your **brand website** (defaults to `hexaclimate.com`).
2. It **crawls** the homepage and the most informative internal pages
   (about, solutions, projects, technology…) and extracts the visible text.
3. That text becomes the **only source of truth** handed to Claude as a cached
   system prompt. The model is explicitly instructed to support every
   brand-specific claim with that context and to never fabricate
   statistics, clients, certifications, or quotes.
4. For each keyword, Claude writes a full SEO post; Gemini illustrates it.

---

## Quick start

### 1. Install

```bash
python -m venv .venv && source .venv/bin/activate   # optional but recommended
pip install -r requirements.txt
```

### 2. Add your API keys

```bash
cp .env.example .env
```

Edit `.env` and set:

| Variable            | Where to get it                              |
| ------------------- | -------------------------------------------- |
| `ANTHROPIC_API_KEY` | https://console.anthropic.com/               |
| `GEMINI_API_KEY`    | https://aistudio.google.com/apikey           |
| `BRAND_WEBSITE`     | already set to `https://hexaclimate.com`     |

### 3. Run

```bash
python app.py
```

Open **http://localhost:5000**, drop in `sample_keywords.csv` (or your own),
and click **Generate blogs**.

---

## Output

Everything lands in `outputs/<timestamp>/`:

```
outputs/20260630-143012/
├── 01-corporate-decarbonization-strategy.md     # Markdown + YAML frontmatter
├── 01-corporate-decarbonization-strategy.html   # styled standalone preview
├── 01-corporate-decarbonization-strategy.png    # Gemini hero image
├── 02-…
└── index.json                                   # machine-readable run summary
```

Each Markdown file starts with SEO frontmatter ready for your CMS:

```yaml
---
title: "..."
meta_description: "..."
slug: "..."
focus_keyword: "..."
tags: ["...", "..."]
image_prompt: "..."
---
```

---

## Project layout

```
app.py                 Flask server + streaming API
seo/
  scraper.py           crawls the brand site → grounding context (+ logo)
  blog_writer.py       Claude API → grounded SEO blog
  image_gen.py         Gemini API → hero image
  pipeline.py          orchestrates the per-keyword run
templates/index.html   dashboard UI
static/                styles, JS, fallback Hexa logo
outputs/               generated posts (git-ignored)
```

---

## Configuration (`.env`)

| Variable             | Default                              | Purpose                                  |
| -------------------- | ------------------------------------ | ---------------------------------------- |
| `ANTHROPIC_API_KEY`  | —                                    | Claude (required)                        |
| `GEMINI_API_KEY`     | —                                    | Gemini images (required for images)      |
| `BRAND_WEBSITE`      | `https://hexaclimate.com`            | Grounding source                         |
| `CLAUDE_MODEL`       | `claude-opus-4-8`                    | Blog-writing model                       |
| `GEMINI_IMAGE_MODEL` | `gemini-2.5-flash-image-preview`     | Image model                              |
| `CLAUDE_EFFORT`      | `high`                               | `low`/`medium`/`high`/`max`              |
| `MAX_CRAWL_PAGES`    | `12`                                 | Pages crawled for grounding              |
| `PORT`               | `5000`                               | Server port                              |

---

## Notes

- The Hexa logo in the header is pulled live from `hexaclimate.com`; a clean
  Hexa-style SVG is shown as a fallback if the site can't be reached.
- If your Gemini account doesn't have access to the default image model, set
  `GEMINI_IMAGE_MODEL=gemini-2.0-flash-preview-image-generation` in `.env`.
  Image failures are non-fatal — the blog text is still produced.
- Generated content is a strong first draft. Review before publishing.
