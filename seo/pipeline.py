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
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from . import image_gen, renderer, uploads
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
        # Guard: NUL bytes or lots of replacement chars mean binary junk.
        if "\x00" in text or text.count("�") > max(5, len(text) * 0.02):
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

    # If Pexels isn't configured, silently disable image lookup so the
    # whole pipeline doesn't fail per-keyword. The blog still produces.
    if make_images and not os.getenv("PEXELS_API_KEY"):
        make_images = False
        yield {"event": "warn", "message":
               "PEXELS_API_KEY not set — skipping stock photo lookup. "
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

    # Fold the upload container into PRIMARY context. Uploaded text/tables become
    # grounding material; uploaded images/tables get embedded into each post.
    upload_text, up_images, up_tables = uploads.resource_context()
    if upload_text:
        primary_text += "\n" + upload_text
    if up_images or up_tables or upload_text:
        yield {"event": "status",
               "message": f"Included {len(up_images)} uploaded image(s), "
                          f"{len(up_tables)} table(s), and pasted/other files as "
                          f"primary resources."}

    # Shared, read-only config passed to each worker.
    job = {
        "run_id": run_id, "run_dir": run_dir,
        "primary_text": primary_text, "secondary_text": secondary_text,
        "prim_inv": prim_inv, "sec_inv": sec_inv,
        "allow_primary": allow_primary, "allow_secondary": allow_secondary,
        "extra_instructions": extra_instructions, "fmt": fmt,
        "target_words": target_words, "make_images": make_images,
        "up_images": up_images, "up_tables": up_tables,
        "media_brief": _media_brief(up_images, up_tables),
    }

    concurrency = max(1, min(int(os.getenv("CONCURRENCY", "1")), 8))
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
        media_brief=job.get("media_brief", ""),
    )

    post = written["post"]
    slug = _slugify(post.get("slug") or post.get("meta", {}).get("title") or keyword)
    post["slug"] = slug

    # Guarantee every uploaded image/table is present with the real data — this
    # runs BEFORE the image cap so uploaded images are never counted or stripped.
    _ensure_uploaded_media(post, job.get("up_images", []), job.get("up_tables", []))

    # Enforce the "exactly 2 Pexels in-body images + 1 hero" rule regardless of
    # what Claude emitted. Uploaded images (id "upload-img-…") are exempt.
    _cap_image_blocks(post, limit=2)

    link_stats = validate_and_clean_links(post, job["allow_primary"], job["allow_secondary"])

    base = f"{i:03d}-{slug}"
    post_dir = run_dir / base
    post_dir.mkdir(parents=True, exist_ok=True)

    image_errors: list[str] = []

    # Copy uploaded images into the post dir (these bypass Pexels entirely).
    _copy_uploaded_images(post, slug, post_dir, job.get("up_images", []), image_errors)

    # Collect the remaining image blocks (hero + Pexels in-body) to fetch.
    image_blocks: list[dict] = []
    hero_block = post.get("hero", {}).get("image")
    if hero_block:
        hero_block.setdefault("id", "hero")
        hero_block.setdefault("src", f"/assets/blogs/{slug}/hero.png")
        image_blocks.append({"_target": hero_block, "id": "hero",
                             "alt": hero_block.get("alt", ""), "caption": ""})
    for b in post.get("content", []):
        if b.get("type") == "image" and not str(b.get("id", "")).startswith("upload-img-"):
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


def _cap_image_blocks(post: dict, *, limit: int = 2) -> None:
    """Keep at most `limit` Pexels in-body image blocks; strip the rest in place.

    Uploaded images (id "upload-img-…") are user-supplied and always kept.
    """
    content = post.get("content", [])
    seen = 0
    kept: list[dict] = []
    for b in content:
        if b.get("type") == "image" and not str(b.get("id", "")).startswith("upload-img-"):
            if seen >= limit:
                continue
            seen += 1
        kept.append(b)
    post["content"] = kept


# ── Uploaded-media embedding ───────────────────────────────────────────────

def _media_brief(images: list[dict], tables: list[dict]) -> str:
    """Instruction block telling Claude exactly which uploaded media to place."""
    if not images and not tables:
        return ""
    lines: list[str] = []
    if images:
        lines.append(
            "UPLOADED IMAGES — add ONE image block for EACH of these using the "
            "EXACT id shown. Write a fitting English alt and caption. These are "
            "the user's own photos/charts/tables; do NOT send them to stock search:"
        )
        for im in images:
            desc = im.get("description") or im["name"]
            lines.append(f'  - id "upload-img-{im["id"]}"  (from {im["name"]}) : {desc}')
    if tables:
        lines.append(
            "UPLOADED TABLES — reproduce each as a `table` block using the EXACT "
            "id shown and the REAL rows from the matching UPLOADED TABLE data above. "
            "Do not alter the numbers:"
        )
        for tb in tables:
            desc = tb.get("description") or tb["name"]
            lines.append(f'  - id "upload-table-{tb["id"]}"  (from {tb["name"]}) : {desc}')
    return "\n".join(lines)


def _insert_before_cta(content: list[dict], block: dict) -> None:
    """Insert `block` just before the final heading (usually the CTA)."""
    for idx in range(len(content) - 1, -1, -1):
        if content[idx].get("type") == "heading":
            content.insert(idx, block)
            return
    content.append(block)


def _ensure_uploaded_media(post: dict, images: list[dict], tables: list[dict]) -> None:
    """Guarantee every uploaded image/table appears as a block with real data."""
    content = post.get("content", [])
    slug = post.get("slug", "")

    for tb in tables:
        bid = f"upload-table-{tb['id']}"
        block = next((b for b in content if b.get("id") == bid), None)
        if block is None:
            _insert_before_cta(content, {
                "id": bid, "type": "table", "rows": tb["rows"],
                "caption": tb.get("description") or tb["name"],
            })
        else:
            # Override whatever Claude produced with the verified source rows.
            block["type"] = "table"
            block["rows"] = tb["rows"]
            block.pop("headers", None)
            block.setdefault("caption", tb.get("description") or tb["name"])

    for im in images:
        bid = f"upload-img-{im['id']}"
        block = next((b for b in content if b.get("id") == bid), None)
        if block is None:
            _insert_before_cta(content, {
                "id": bid, "type": "image",
                "src": f"/assets/blogs/{slug}/{im['stored_as']}",
                "alt": im.get("description") or im["name"],
                "caption": im.get("description") or "",
            })
    post["content"] = content


def _copy_uploaded_images(post: dict, slug: str, post_dir: Path,
                          images: list[dict], image_errors: list[str]) -> None:
    """Copy each uploaded image file next to the post and point its block at it."""
    by_id = {f"upload-img-{im['id']}": im for im in images}
    for b in post.get("content", []):
        if b.get("type") != "image":
            continue
        im = by_id.get(b.get("id"))
        if not im:
            continue
        src_path = uploads.stored_path(im["stored_as"])
        if src_path.exists():
            shutil.copyfile(src_path, post_dir / im["stored_as"])
            b["src"] = f"/assets/blogs/{slug}/{im['stored_as']}"
        else:
            image_errors.append(f"upload-img-{im['id']}: stored file missing")


_PEXELS_STYLE_HINTS = (
    "renewable energy, clean energy, professional photography, editorial, "
    "sustainability, industry, real-world scene"
)


def _image_prompt(block: dict, slug: str) -> str:
    """Build a Pexels-friendly query — real photographable subject + style hint."""
    alt = (block.get("alt") or "").strip()
    cap = (block.get("caption") or "").strip()
    base = alt or cap or f"renewable energy article about {slug.replace('-', ' ')}"
    # Strip diagram/infographic language so Pexels doesn't match irrelevant schematics.
    base = re.sub(
        r"\b(diagram|infographic|chart|flow(chart)?|schematic|illustration|graphic)\b",
        "", base, flags=re.IGNORECASE,
    ).strip(" ,.")
    return f"{base}. {_PEXELS_STYLE_HINTS}"


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
