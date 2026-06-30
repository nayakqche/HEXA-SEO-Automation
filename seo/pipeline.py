"""
Pipeline — ties the pieces together:

    keywords.csv + brand site  →  grounding context (scraped once)
                               →  per keyword: Claude blog + Gemini hero image
                               →  saved to outputs/ as .md + .html + .png

Designed to stream progress events so the web UI can show a live log.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import json
import os
import re
from pathlib import Path

import markdown as md

from . import image_gen
from .blog_writer import write_blog
from .scraper import BrandContext, crawl

OUTPUT_DIR = Path("outputs")
CACHE_DIR = Path(".brand_cache")


def parse_keywords(file_storage_or_text) -> list[str]:
    """Accept an uploaded CSV (file-like) or raw text; return clean keywords."""
    if hasattr(file_storage_or_text, "read"):
        raw = file_storage_or_text.read()
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8-sig", errors="replace")
    else:
        raw = str(file_storage_or_text)

    keywords: list[str] = []
    seen = set()
    for row in csv.reader(io.StringIO(raw)):
        for cell in row:
            kw = cell.strip().strip('"')
            if not kw:
                continue
            # Skip an obvious header cell.
            if kw.lower() in {"keyword", "keywords", "term", "query"}:
                continue
            key = kw.lower()
            if key not in seen:
                seen.add(key)
                keywords.append(kw)
    return keywords


def _slugify(text: str) -> str:
    text = re.sub(r"[^\w\s-]", "", text.lower()).strip()
    return re.sub(r"[\s_-]+", "-", text)[:60] or "post"


def build_brand_context(website: str, max_pages: int, use_cache: bool = True) -> BrandContext:
    """Crawl the brand site (or load a cached crawl) into a BrandContext."""
    CACHE_DIR.mkdir(exist_ok=True)
    cache_file = CACHE_DIR / f"{_slugify(website)}.json"

    if use_cache and cache_file.exists():
        data = json.loads(cache_file.read_text())
        ctx = BrandContext(website=data["website"])
        ctx.pages = data["pages"]
        ctx.logo_url = data.get("logo_url")
        return ctx

    ctx = crawl(website, max_pages=max_pages)
    cache_file.write_text(
        json.dumps(
            {"website": ctx.website, "pages": ctx.pages, "logo_url": ctx.logo_url}
        )
    )
    return ctx


def run(keywords: list[str], website: str, *, extra_instructions: str = "",
        make_images: bool = True, max_pages: int = 12):
    """
    Generator yielding progress dicts. Each yield is one event:
        {"event": "...", ...}
    so the Flask layer can stream it to the browser as server-sent events.
    """
    OUTPUT_DIR.mkdir(exist_ok=True)
    run_id = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = OUTPUT_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    yield {"event": "start", "run_id": run_id, "total": len(keywords)}

    # 1. Grounding context (scrape once, reuse for every keyword).
    yield {"event": "status", "message": f"Reading {website} for brand grounding…"}
    try:
        ctx = build_brand_context(website, max_pages=max_pages, use_cache=False)
    except Exception as exc:  # noqa: BLE001 — surface any scrape failure to the UI
        yield {"event": "error", "fatal": True, "message": str(exc)}
        return

    yield {
        "event": "grounded",
        "pages": len(ctx.pages),
        "chars": ctx.char_count,
        "logo_url": ctx.logo_url,
        "message": f"Grounded on {len(ctx.pages)} pages "
                   f"({ctx.char_count:,} chars) from {website}.",
    }

    results = []
    for i, keyword in enumerate(keywords, start=1):
        yield {"event": "keyword_start", "index": i, "keyword": keyword}

        # 2. Write the blog (grounded).
        try:
            blog = write_blog(
                keyword, ctx.text, extra_instructions=extra_instructions
            )
        except Exception as exc:  # noqa: BLE001
            yield {"event": "keyword_error", "index": i, "keyword": keyword,
                   "stage": "blog", "message": str(exc)}
            continue

        meta = blog["meta"]
        slug = _slugify(meta.get("slug") or meta.get("title") or keyword)
        base = f"{i:02d}-{slug}"

        # 3. Generate the hero image (non-fatal if it fails).
        image_file = None
        image_error = None
        if make_images and blog["image_prompt"]:
            try:
                img_bytes, mime = image_gen.generate_image(blog["image_prompt"])
                ext = "jpg" if "jpeg" in mime else "png"
                image_file = f"{base}.{ext}"
                (run_dir / image_file).write_bytes(img_bytes)
            except Exception as exc:  # noqa: BLE001
                image_error = str(exc)

        # 4. Persist markdown + standalone HTML preview.
        (run_dir / f"{base}.md").write_text(blog["markdown"], encoding="utf-8")
        html_body = md.markdown(
            blog["body"], extensions=["extra", "sane_lists", "toc"]
        )
        html_doc = _html_page(meta, html_body, image_file)
        (run_dir / f"{base}.html").write_text(html_doc, encoding="utf-8")

        record = {
            "index": i,
            "keyword": keyword,
            "title": meta.get("title", keyword),
            "meta_description": meta.get("meta_description", ""),
            "slug": slug,
            "tags": meta.get("tags", []),
            "word_count": len(blog["body"].split()),
            "files": {
                "markdown": f"{run_id}/{base}.md",
                "html": f"{run_id}/{base}.html",
                "image": f"{run_id}/{image_file}" if image_file else None,
            },
            "image_error": image_error,
            "usage": blog["usage"],
        }
        results.append(record)
        yield {"event": "keyword_done", **record}

    (run_dir / "index.json").write_text(json.dumps(results, indent=2))
    yield {"event": "done", "run_id": run_id, "count": len(results),
           "message": f"Finished {len(results)} of {len(keywords)} posts."}


def _html_page(meta: dict, body_html: str, image_file: str | None) -> str:
    title = meta.get("title", "Blog post")
    desc = meta.get("meta_description", "")
    hero = (f'<img class="hero" src="{image_file}" alt="{title}">'
            if image_file else "")
    return f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<meta name="description" content="{desc}">
<style>
  body{{font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;
  max-width:760px;margin:0 auto;padding:2rem 1.25rem;color:#0f172a;line-height:1.7}}
  .hero{{width:100%;border-radius:14px;margin-bottom:1.5rem}}
  h1{{font-size:2rem;line-height:1.2}} h2{{margin-top:2rem;color:#1e40af}}
  a{{color:#1e40af}} code{{background:#eef2fb;padding:2px 5px;border-radius:4px}}
  blockquote{{border-left:4px solid #3b82f6;margin:1.5rem 0;padding:.4rem 1rem;
  background:#f1f6ff;color:#334155}}
</style></head>
<body>{hero}{body_html}</body></html>"""
