"""
Pipeline — orchestrates a full SEO run.

  primary URLs + secondary URLs  →  grounding context (scraped once)
                                 →  per keyword: Claude writes JSON blog
                                 →  per image block: Gemini hero + diagrams
                                 →  outputs/<run>/<post>/{json,md,html,pdf,docx,*.png}

Yields server-sent events so the web UI can show a live log.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import json
import os
import re
from pathlib import Path

from . import image_gen, renderer
from .blog_writer import write_blog
from .scraper import GroundingContext, build_context

OUTPUT_DIR = Path("outputs")


# ── Input parsing ──────────────────────────────────────────────────────────

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
            if kw.lower() in {"keyword", "keywords", "term", "query"}:
                continue
            key = kw.lower()
            if key not in seen:
                seen.add(key)
                keywords.append(kw)
    return keywords


def parse_urls(text: str) -> list[str]:
    """One URL per line (commas/whitespace tolerated)."""
    if not text:
        return []
    parts = re.split(r"[\s,]+", text.strip())
    seen, out = set(), []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        if not p.startswith(("http://", "https://")):
            p = "https://" + p
        if p in seen:
            continue
        seen.add(p)
        out.append(p)
    return out


# ── Slugs ──────────────────────────────────────────────────────────────────

def _slugify(text: str) -> str:
    text = re.sub(r"[^\w\s-]", "", (text or "").lower()).strip()
    return re.sub(r"[\s_-]+", "-", text)[:60] or "post"


def _image_prompt(block: dict, slug: str) -> str:
    """Compose the Gemini prompt for one image block from its alt + caption."""
    alt = (block.get("alt") or "").strip()
    cap = (block.get("caption") or "").strip()
    base = alt or cap or f"hero image for an article about {slug.replace('-', ' ')}"
    if cap and cap != alt:
        base = f"{base}. Context: {cap}"
    return base


# ── Main run ───────────────────────────────────────────────────────────────

def run(
    keywords: list[str],
    brand_website: str,
    *,
    primary_urls: list[str],
    secondary_urls: list[str],
    extra_instructions: str = "",
    make_images: bool = True,
    max_pages: int = 12,
):
    OUTPUT_DIR.mkdir(exist_ok=True)
    run_id = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = OUTPUT_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    yield {"event": "start", "run_id": run_id, "total": len(keywords)}

    # 1. Build grounding context.
    yield {"event": "status",
           "message": f"Scraping {brand_website} + "
                      f"{len(primary_urls)} primary, {len(secondary_urls)} secondary source(s)…"}
    try:
        ctx = build_context(brand_website, primary_urls, secondary_urls, max_pages=max_pages)
    except Exception as exc:  # noqa: BLE001
        yield {"event": "error", "fatal": True, "message": str(exc)}
        return

    for note in ctx.notes:
        yield {"event": "warn", "message": note}

    yield {
        "event": "grounded",
        "primary_pages": len(ctx.primary),
        "secondary_pages": len(ctx.secondary),
        "chars": ctx.char_count,
        "logo_url": ctx.logo_url,
        "message": f"Grounded on {len(ctx.primary)} primary + "
                   f"{len(ctx.secondary)} secondary page(s), "
                   f"{ctx.char_count:,} chars total.",
    }

    results: list[dict] = []
    primary_text = ctx.primary_text()
    secondary_text = ctx.secondary_text()

    for i, keyword in enumerate(keywords, start=1):
        yield {"event": "keyword_start", "index": i, "keyword": keyword}

        # 2. Write the structured blog.
        try:
            written = write_blog(
                keyword, primary_text, secondary_text,
                extra_instructions=extra_instructions,
            )
        except Exception as exc:  # noqa: BLE001
            yield {"event": "keyword_error", "index": i, "keyword": keyword,
                   "stage": "blog", "message": str(exc)}
            continue

        post = written["post"]
        slug = _slugify(post.get("slug") or post.get("meta", {}).get("title") or keyword)
        post["slug"] = slug  # canonicalize after slugify

        # Per-post output directory.
        base = f"{i:02d}-{slug}"
        post_dir = run_dir / base
        post_dir.mkdir(parents=True, exist_ok=True)

        # 3. Generate every image block (hero + in-body), if enabled.
        image_errors: list[str] = []
        image_blocks: list[dict] = []
        hero_block = post.get("hero", {}).get("image")
        if hero_block:
            hero_block.setdefault("id", "hero")
            hero_block.setdefault("src", f"/assets/blogs/{slug}/hero.png")
            image_blocks.append({"_target": hero_block, "id": "hero",
                                 "alt": hero_block.get("alt", ""),
                                 "caption": ""})
        for b in post.get("content", []):
            if b.get("type") == "image":
                image_blocks.append({"_target": b, "id": b.get("id", "image"),
                                     "alt": b.get("alt", ""),
                                     "caption": b.get("caption", "")})

        if make_images:
            for img in image_blocks:
                filename = f"{img['id']}.png"
                target = img["_target"]
                prompt = _image_prompt(img, slug)
                try:
                    img_bytes, mime = image_gen.generate_image(prompt)
                    if "jpeg" in mime:
                        filename = filename.replace(".png", ".jpg")
                    (post_dir / filename).write_bytes(img_bytes)
                    # JSON src uses the CMS path; preview HTML uses the filename.
                    target["src"] = f"/assets/blogs/{slug}/{filename}"
                except Exception as exc:  # noqa: BLE001
                    image_errors.append(f"{img['id']}: {exc}")

        # 4. Write all output formats.
        (post_dir / "post.json").write_text(
            json.dumps(post, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        (post_dir / "post.md").write_text(renderer.render_markdown(post), encoding="utf-8")
        (post_dir / "post.html").write_text(renderer.render_html(post), encoding="utf-8")
        try:
            renderer.render_pdf(post, post_dir / "post.pdf", asset_dir=post_dir)
        except Exception as exc:  # noqa: BLE001
            image_errors.append(f"pdf: {exc}")
        try:
            renderer.render_docx(post, post_dir / "post.docx", asset_dir=post_dir)
        except Exception as exc:  # noqa: BLE001
            image_errors.append(f"docx: {exc}")

        # 5. Build the result record consumed by the UI.
        meta = post.get("meta", {})
        seo = post.get("seo", {})
        record = {
            "index": i,
            "keyword": keyword,
            "slug": slug,
            "title": meta.get("title") or seo.get("title", ""),
            "subtitle": meta.get("subtitle", ""),
            "description": seo.get("description", ""),
            "tags": meta.get("tags", []),
            "category": meta.get("category", ""),
            "word_count": _word_count(post),
            "hero_image": (
                f"{run_id}/{base}/hero.png"
                if (post_dir / "hero.png").exists() else
                next((f"{run_id}/{base}/hero.jpg" for _ in [1]
                      if (post_dir / "hero.jpg").exists()), None)
            ),
            "downloads": {
                "json":     f"{run_id}/{base}/post.json",
                "markdown": f"{run_id}/{base}/post.md",
                "html":     f"{run_id}/{base}/post.html",
                "pdf":      f"{run_id}/{base}/post.pdf",
                "docx":     f"{run_id}/{base}/post.docx",
            },
            "image_errors": image_errors,
            "usage": written["usage"],
        }
        results.append(record)
        yield {"event": "keyword_done", **record}

    (run_dir / "index.json").write_text(json.dumps(results, indent=2))
    yield {"event": "done", "run_id": run_id, "count": len(results),
           "message": f"Finished {len(results)} of {len(keywords)} posts."}


def _word_count(post: dict) -> int:
    n = 0
    for b in post.get("content", []):
        if b.get("type") == "paragraph":
            n += len((b.get("text") or "").split())
        elif b.get("type") == "list":
            n += sum(len((i or "").split()) for i in b.get("items", []))
        elif b.get("type") == "heading":
            n += len((b.get("text") or "").split())
    return n
