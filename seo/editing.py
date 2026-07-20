"""
Server-side support for the CMS-style blog editor.

The editor sends back the canonical post JSON after a human has edited it. This
module sanitises that payload (edited paragraphs/list items may carry a small
set of inline HTML tags), writes it back to post.json, and re-renders every
export (Markdown / HTML / PDF / DOCX) so the downloads stay in sync.

Everything is path-guarded to the outputs/ tree so an edit request can never
read or write outside a real post directory.
"""

from __future__ import annotations

import json
import re
from html import escape, unescape
from html.parser import HTMLParser
from pathlib import Path

from . import renderer

OUTPUT_DIR = Path("outputs")

# Inline tags a human may use inside a paragraph or list item.
_ALLOWED_TAGS = {"b", "strong", "i", "em", "u", "a", "br"}


def _safe_href(href: str) -> bool:
    low = (href or "").strip().lower()
    return bool(low) and not low.startswith(("javascript:", "data:", "vbscript:"))


class _Sanitizer(HTMLParser):
    """Keep only bold/italic/underline/link/break; escape everything else."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self._a_emitted: list[bool] = []
        self._skip = 0   # depth inside a tag whose text must be dropped entirely

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "iframe", "object", "embed"):
            self._skip += 1
            return
        if tag == "br":
            self.out.append("<br>")
        elif tag == "a":
            href = dict(attrs).get("href", "").strip()
            if _safe_href(href):
                self.out.append(f'<a href="{escape(href, quote=True)}">')
                self._a_emitted.append(True)
            else:
                self._a_emitted.append(False)
        elif tag in _ALLOWED_TAGS:
            self.out.append(f"<{tag}>")
        # any other tag: dropped, its text is kept via handle_data

    def handle_endtag(self, tag):
        if tag in ("script", "style", "iframe", "object", "embed"):
            self._skip = max(0, self._skip - 1)
            return
        if tag == "br":
            return
        if tag == "a":
            if self._a_emitted and self._a_emitted.pop():
                self.out.append("</a>")
        elif tag in _ALLOWED_TAGS:
            self.out.append(f"</{tag}>")

    def handle_data(self, data):
        if self._skip:
            return
        self.out.append(escape(data))


def sanitize_inline_html(value: str) -> str:
    if not value:
        return ""
    p = _Sanitizer()
    p.feed(value)
    return "".join(p.out)


def _strip_tags(value: str) -> str:
    return unescape(re.sub(r"<[^>]+>", "", value or "")).strip()


def _plain(value) -> str:
    return _strip_tags(str(value)) if value is not None else ""


def sanitize_post(post: dict) -> dict:
    """Coerce and sanitise an edited post in place, returning it."""
    # Plain-text metadata.
    post["slug"] = _plain(post.get("slug"))
    seo = post.get("seo") or {}
    seo["title"] = _plain(seo.get("title"))
    seo["description"] = _plain(seo.get("description"))
    seo["keywords"] = [_plain(k) for k in (seo.get("keywords") or []) if _plain(k)]
    post["seo"] = seo

    meta = post.get("meta") or {}
    for key in ("title", "subtitle", "author", "category", "publishedDate"):
        meta[key] = _plain(meta.get(key))
    meta["tags"] = [_plain(t) for t in (meta.get("tags") or []) if _plain(t)]
    post["meta"] = meta

    hero = (post.get("hero") or {})
    hero_img = hero.get("image") or {}
    hero_img["src"] = _plain(hero_img.get("src"))
    hero_img["alt"] = _plain(hero_img.get("alt"))
    hero["image"] = hero_img
    post["hero"] = hero

    clean: list[dict] = []
    for i, b in enumerate(post.get("content") or []):
        if not isinstance(b, dict):
            continue
        t = b.get("type")
        b["id"] = _plain(b.get("id")) or f"block-{i}"
        if t == "heading":
            b["text"] = _plain(b.get("text") or b.get("html"))
            b.pop("html", None)
            try:
                b["level"] = 2 if int(b.get("level", 2)) < 3 else 3
            except (TypeError, ValueError):
                b["level"] = 2
        elif t == "paragraph":
            b["html"] = sanitize_inline_html(b.get("html") or escape(b.get("text", "")))
            b["text"] = _strip_tags(b["html"])
            b.pop("links", None)
            if not b["text"]:
                continue
        elif t == "list":
            src = b.get("itemsHtml")
            if src is None:
                src = [escape(x) for x in (b.get("items") or [])]
            items_html = [sanitize_inline_html(x) for x in src if _strip_tags(x).strip()]
            b["itemsHtml"] = items_html
            b["items"] = [_strip_tags(x) for x in items_html]
            b["style"] = "ordered" if b.get("style") == "ordered" else "unordered"
            b.pop("links", None)
            if not items_html:
                continue
        elif t == "image":
            b["src"] = _plain(b.get("src"))
            b["alt"] = _plain(b.get("alt"))
            b["caption"] = _plain(b.get("caption"))
            href = str(b.get("href") or "").strip()
            b["href"] = href if _safe_href(href) else ""
        elif t == "table":
            b["rows"] = [[_plain(c) for c in (row or [])] for row in (b.get("rows") or [])]
            if b.get("headers"):
                b["headers"] = [_plain(h) for h in b["headers"]]
            b["caption"] = _plain(b.get("caption"))
            if not any(any(c for c in r) for r in b["rows"]):
                continue
        else:
            continue
        clean.append(b)
    post["content"] = clean
    return post


def safe_post_dir(rel: str) -> Path:
    """Resolve outputs/<rel> and confirm it is a real post directory."""
    base = OUTPUT_DIR.resolve()
    target = (OUTPUT_DIR / (rel or "")).resolve()
    if target != base and base not in target.parents:
        raise ValueError("Path is outside the outputs directory.")
    if not (target / "post.json").exists():
        raise ValueError("No editable post was found at that location.")
    return target


def load_post(rel: str) -> dict:
    post_dir = safe_post_dir(rel)
    return json.loads((post_dir / "post.json").read_text(encoding="utf-8"))


def save_post(rel: str, post: dict) -> dict:
    """Sanitise, persist, and re-render all export formats for one post."""
    post_dir = safe_post_dir(rel)
    sanitize_post(post)
    (post_dir / "post.json").write_text(
        json.dumps(post, indent=2, ensure_ascii=False), encoding="utf-8")
    (post_dir / "post.md").write_text(renderer.render_markdown(post), encoding="utf-8")
    (post_dir / "post.html").write_text(
        renderer.render_html(post, asset_dir=post_dir), encoding="utf-8")
    renderer.render_pdf(post, post_dir / "post.pdf", asset_dir=post_dir)
    renderer.render_docx(post, post_dir / "post.docx", asset_dir=post_dir)
    return post


def save_asset(rel: str, filename: str, data: bytes) -> str:
    """Store an uploaded image next to the post; return its safe basename."""
    post_dir = safe_post_dir(rel)
    safe = re.sub(r"[^\w.\-]+", "_", filename or "image")[:80] or "image"
    (post_dir / safe).write_bytes(data)
    return safe
