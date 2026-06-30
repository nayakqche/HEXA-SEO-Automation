"""
Pipeline — orchestrates a full SEO run.

  primary URLs + secondary URLs  →  grounding context + URL inventory
                                 →  per keyword: Claude writes JSON blog
                                 →  links validated against URL inventory
                                 →  per image block: Gemini hero + diagrams
                                 →  outputs/<run>/<post>/{json,md,html,pdf,docx,*.png}

Yields server-sent events so the web UI can show a live log.
"""

from __future__ import annotations

import csv
import datetime as dt
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from . import image_gen, renderer
from .blog_writer import validate_and_clean_links, write_blog
from .scraper import GroundingContext, build_context

OUTPUT_DIR = Path("outputs")


# ── Input parsing ──────────────────────────────────────────────────────────

_HEADER_CELLS = {"keyword", "keywords", "term", "query"}


def _dedupe_clean(cells) -> list[str]:
    keywords: list[str] = []
    seen = set()
    for cell in cells:
        kw = (cell or "").strip().strip('"')
        if not kw or kw.lower() in _HEADER_CELLS:
            continue
        key = kw.lower()
        if key not in seen:
            seen.add(key)
            keywords.append(kw)
    return keywords


def _keywords_from_xlsx(data: bytes) -> list[str]:
    """Read keywords from an Excel .xlsx file (first non-empty cell of each row)."""
    import io as _io
    from openpyxl import load_workbook

    wb = load_workbook(_io.BytesIO(data), read_only=True, data_only=True)
    cells: list[str] = []
    for ws in wb.worksheets:
        for row in ws.iter_rows(values_only=True):
            for value in row:
                if value is None:
                    continue
                cells.append(str(value))
                break  # only the first non-empty cell in each row
    wb.close()
    return _dedupe_clean(cells)


def parse_keywords(file_storage_or_text) -> list[str]:
    """
    Accept a CSV, an Excel .xlsx upload, or pasted text. Returns clean,
    de-duplicated keywords. Raises ValueError with a clear message for
    unsupported binary files (old .xls, .pdf, images, etc.).
    """
    if hasattr(file_storage_or_text, "read"):
        raw = file_storage_or_text.read()
    else:
        raw = str(file_storage_or_text)

    if isinstance(raw, bytes):
        # Excel .xlsx (and .docx etc.) are ZIP archives → magic bytes "PK\x03\x04".
        if raw[:4] == b"PK\x03\x04":
            try:
                return _keywords_from_xlsx(raw)
            except Exception as exc:  # noqa: BLE001
                raise ValueError(
                    f"Couldn't read that Excel file ({exc}). "
                    "Re-save it as .xlsx or export to .csv."
                ) from exc
        # Old binary .xls (BIFF) starts with this OLE2 signature.
        if raw[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
            raise ValueError(
                "That's an old-format .xls file. Please 'Save As' .xlsx or .csv "
                "and upload again."
            )
        text = raw.decode("utf-8-sig", errors="replace")
        # Guard: if decoding produced lots of replacement chars, it's binary junk.
        if text.count("�") > max(5, len(text) * 0.02):
            raise ValueError(
                "That file doesn't look like a CSV or Excel sheet. Upload a .csv "
                "or .xlsx with one keyword per row, or paste keywords as text."
            )
    else:
        text = raw

    # splitlines() handles \n, \r\n and lone \r (Excel/Mac CSVs) uniformly.
    cells = []
    for row in csv.reader(text.splitlines()):
        if row:
            cells.append(row[0])  # first column = keyword
    return _dedupe_clean(cells)


def parse_urls(text: str) -> list[str]:
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


# ── URL inventory (what Claude is allowed to link to) ──────────────────────

def _build_inventory(ctx: GroundingContext, extra_urls: list[str]) -> tuple[str, str, list[str], list[str]]:
    """
    Build the (primary_inventory_text, secondary_inventory_text,
              primary_url_list, secondary_url_list) tuple.
    """
    prim_pages = [(p.url, p.title) for p in ctx.primary]
    # Include any extra primary URLs the user passed even if we couldn't scrape them.
    for u in extra_urls:
        if u and not any(u == p[0] for p in prim_pages):
            prim_pages.append((u, urlpath_title(u)))
    sec_pages = [(p.url, p.title) for p in ctx.secondary]

    def fmt(pairs):
        if not pairs:
            return "(none)"
        return "\n".join(f"- {url}  →  \"{title[:80]}\"" for url, title in pairs)

    prim_urls = [u for u, _ in prim_pages]
    sec_urls = [u for u, _ in sec_pages]
    return fmt(prim_pages), fmt(sec_pages), prim_urls, sec_urls


def urlpath_title(url: str) -> str:
    """Derive a readable title from a URL when we couldn't fetch the page."""
    from urllib.parse import urlparse
    p = urlparse(url)
    return (p.netloc + p.path).rstrip("/") or url


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
    fmt: str = "paragraph",
    target_words: int = 1400,
):
    OUTPUT_DIR.mkdir(exist_ok=True)
    run_id = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = OUTPUT_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    # If Gemini isn't configured, silently disable image generation so the
    # whole pipeline doesn't fail per-keyword. The blog still produces.
    if make_images and not os.getenv("GEMINI_API_KEY"):
        make_images = False
        yield {"event": "warn", "message":
               "GEMINI_API_KEY not set — skipping image generation. "
               "Blog text + JSON/MD/PDF/DOCX will still be produced "
               "with image placeholders."}

    yield {"event": "start", "run_id": run_id, "total": len(keywords),
           "make_images": make_images}

    # 1. Build grounding context + URL inventory.
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

    prim_inv, sec_inv, allow_primary, allow_secondary = _build_inventory(
        ctx, [brand_website] + primary_urls
    )

    yield {
        "event": "grounded",
        "primary_pages": len(ctx.primary),
        "secondary_pages": len(ctx.secondary),
        "chars": ctx.char_count,
        "logo_url": ctx.logo_url,
        "fmt": fmt,
        "target_words": target_words,
        "message": (
            f"Grounded on {len(ctx.primary)} primary + {len(ctx.secondary)} secondary "
            f"page(s) ({ctx.char_count:,} chars). "
            f"Link inventory: {len(allow_primary)} internal, {len(allow_secondary)} citation. "
            f"Format: {fmt}, ~{target_words} words/post."
        ),
    }

    primary_text = ctx.primary_text()
    secondary_text = ctx.secondary_text()

    # Shared, read-only config passed to each worker.
    job = {
        "run_id": run_id, "run_dir": run_dir,
        "primary_text": primary_text, "secondary_text": secondary_text,
        "prim_inv": prim_inv, "sec_inv": sec_inv,
        "allow_primary": allow_primary, "allow_secondary": allow_secondary,
        "extra_instructions": extra_instructions, "fmt": fmt,
        "target_words": target_words, "make_images": make_images,
    }

    concurrency = max(1, min(int(os.getenv("CONCURRENCY", "3")), 8))
    yield {"event": "status",
           "message": f"Writing {len(keywords)} post(s), {concurrency} at a time…"}

    results: list[dict] = []
    done_count = 0
    # Run keywords in parallel — the work is I/O-bound (waiting on the LLM /
    # image API), so a thread pool gives a big speedup without extra CPU.
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = {
            executor.submit(_process_keyword, i, kw, job): (i, kw)
            for i, kw in enumerate(keywords, start=1)
        }
        for future in as_completed(futures):
            i, kw = futures[future]
            try:
                record = future.result()
            except Exception as exc:  # noqa: BLE001 — never let one keyword kill the run
                done_count += 1
                yield {"event": "keyword_error", "index": i, "keyword": kw,
                       "stage": "blog", "message": f"{type(exc).__name__}: {exc}",
                       "done": done_count, "total": len(keywords)}
                continue
            done_count += 1
            results.append(record)
            yield {"event": "keyword_done", "done": done_count,
                   "total": len(keywords), **record}

    results.sort(key=lambda r: r["index"])
    (run_dir / "index.json").write_text(json.dumps(results, indent=2))
    yield {"event": "done", "run_id": run_id, "count": len(results),
           "message": f"Finished {len(results)} of {len(keywords)} posts."}


def _process_keyword(i: int, keyword: str, job: dict) -> dict:
    """
    Generate one blog end-to-end (write → validate links → images → all
    output formats) and return its UI record. Runs inside a worker thread.
    Raises on a fatal blog-write failure so the caller can report it.
    """
    run_id = job["run_id"]
    run_dir = job["run_dir"]

    written = write_blog(
        keyword, job["primary_text"], job["secondary_text"],
        job["prim_inv"], job["sec_inv"],
        extra_instructions=job["extra_instructions"],
        fmt=job["fmt"], target_words=job["target_words"],
    )

    post = written["post"]
    slug = _slugify(post.get("slug") or post.get("meta", {}).get("title") or keyword)
    post["slug"] = slug

    link_stats = validate_and_clean_links(post, job["allow_primary"], job["allow_secondary"])

    base = f"{i:03d}-{slug}"
    post_dir = run_dir / base
    post_dir.mkdir(parents=True, exist_ok=True)

    # Collect every image block (hero + in-body).
    image_errors: list[str] = []
    image_blocks: list[dict] = []
    hero_block = post.get("hero", {}).get("image")
    if hero_block:
        hero_block.setdefault("id", "hero")
        hero_block.setdefault("src", f"/assets/blogs/{slug}/hero.png")
        image_blocks.append({"_target": hero_block, "id": "hero",
                             "alt": hero_block.get("alt", ""), "caption": ""})
    for b in post.get("content", []):
        if b.get("type") == "image":
            image_blocks.append({"_target": b, "id": b.get("id", "image"),
                                 "alt": b.get("alt", ""), "caption": b.get("caption", "")})

    if job["make_images"]:
        for img in image_blocks:
            filename = f"{img['id']}.png"
            target = img["_target"]
            try:
                img_bytes, mime = image_gen.generate_image(_image_prompt(img, slug))
                if "jpeg" in mime:
                    filename = filename.replace(".png", ".jpg")
                (post_dir / filename).write_bytes(img_bytes)
                target["src"] = f"/assets/blogs/{slug}/{filename}"
            except Exception as exc:  # noqa: BLE001
                image_errors.append(f"{img['id']}: {exc}")

    # Persist all output formats.
    (post_dir / "post.json").write_text(
        json.dumps(post, indent=2, ensure_ascii=False), encoding="utf-8")
    (post_dir / "post.md").write_text(renderer.render_markdown(post), encoding="utf-8")
    (post_dir / "post.html").write_text(
        renderer.render_html(post, asset_dir=post_dir), encoding="utf-8")
    try:
        renderer.render_pdf(post, post_dir / "post.pdf", asset_dir=post_dir)
    except Exception as exc:  # noqa: BLE001
        image_errors.append(f"pdf: {exc}")
    try:
        renderer.render_docx(post, post_dir / "post.docx", asset_dir=post_dir)
    except Exception as exc:  # noqa: BLE001
        image_errors.append(f"docx: {exc}")

    meta = post.get("meta", {})
    seo = post.get("seo", {})
    hero_file = (
        f"{run_id}/{base}/hero.png" if (post_dir / "hero.png").exists()
        else f"{run_id}/{base}/hero.jpg" if (post_dir / "hero.jpg").exists()
        else None
    )
    return {
        "index": i, "keyword": keyword, "slug": slug,
        "title": meta.get("title") or seo.get("title", ""),
        "subtitle": meta.get("subtitle", ""),
        "description": seo.get("description", ""),
        "tags": meta.get("tags", []),
        "category": meta.get("category", ""),
        "word_count": _word_count(post),
        "links": link_stats,
        "hero_image": hero_file,
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


def _image_prompt(block: dict, slug: str) -> str:
    alt = (block.get("alt") or "").strip()
    cap = (block.get("caption") or "").strip()
    base = alt or cap or f"hero image for an article about {slug.replace('-', ' ')}"
    if cap and cap != alt:
        base = f"{base}. Context: {cap}"
    return base


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
