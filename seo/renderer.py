"""
Renderer — converts the canonical blog JSON into Markdown, HTML, PDF, and DOCX.

The JSON is the source of truth (perfect for direct CMS import). The other
formats are generated from it so they always stay in sync.
"""

from __future__ import annotations

import io
import os
import re
from pathlib import Path
from typing import Iterable

from docx import Document
from docx.shared import Inches, Pt, RGBColor
from xhtml2pdf import pisa

# ── Helpers ────────────────────────────────────────────────────────────────

def _basename(src: str) -> str:
    """Strip a leading /assets/blogs/<slug>/ from the CMS src to a filename."""
    return os.path.basename(src) if src else ""


def _esc(text: str) -> str:
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ── Markdown ───────────────────────────────────────────────────────────────

def render_markdown(post: dict) -> str:
    meta = post.get("meta", {})
    seo = post.get("seo", {})
    hero = post.get("hero", {}).get("image", {})

    lines: list[str] = []
    lines.append("---")
    lines.append(f'title: "{seo.get("title", meta.get("title", ""))}"')
    lines.append(f'meta_description: "{seo.get("description", "")}"')
    lines.append(f'slug: "{post.get("slug", "")}"')
    if meta.get("category"):
        lines.append(f'category: "{meta["category"]}"')
    if meta.get("publishedDate"):
        lines.append(f'date: "{meta["publishedDate"]}"')
    if meta.get("author"):
        lines.append(f'author: "{meta["author"]}"')
    tags = meta.get("tags") or []
    if tags:
        lines.append("tags: [" + ", ".join(f'"{t}"' for t in tags) + "]")
    keywords = seo.get("keywords") or []
    if keywords:
        lines.append("keywords: [" + ", ".join(f'"{k}"' for k in keywords) + "]")
    lines.append("---")
    lines.append("")

    if hero.get("src"):
        lines.append(f'![{hero.get("alt", "")}]({_basename(hero["src"])})')
        lines.append("")

    title = meta.get("title") or seo.get("title", "")
    if title:
        lines.append(f"# {title}")
    if meta.get("subtitle"):
        lines.append(f"\n_{meta['subtitle']}_")
    lines.append("")

    for block in post.get("content", []):
        t = block.get("type")
        if t == "heading":
            level = max(2, min(6, int(block.get("level", 2))))
            lines.append("#" * level + " " + block.get("text", ""))
            lines.append("")
        elif t == "paragraph":
            lines.append(block.get("text", ""))
            lines.append("")
        elif t == "list":
            marker_fn = (lambda i: f"{i + 1}.") if block.get("style") == "ordered" else (lambda i: "-")
            for i, item in enumerate(block.get("items", [])):
                lines.append(f"{marker_fn(i)} {item}")
            lines.append("")
        elif t == "image":
            lines.append(f"![{block.get('alt', '')}]({_basename(block.get('src', ''))})")
            if block.get("caption"):
                lines.append(f"\n*{block['caption']}*")
            lines.append("")

    return "\n".join(lines).rstrip() + "\n"


# ── HTML ───────────────────────────────────────────────────────────────────

_HTML_CSS = """
  body{font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;
       max-width:780px;margin:0 auto;padding:2rem 1.25rem;color:#0f172a;line-height:1.7}
  .hero{width:100%;border-radius:14px;margin-bottom:1.25rem}
  .subtitle{color:#475569;font-size:1.1rem;margin-top:-0.5rem}
  .byline{color:#64748b;font-size:.88rem;margin-bottom:1.25rem}
  h1{font-size:2rem;line-height:1.2;margin-bottom:.25rem}
  h2{margin-top:2rem;color:#1d4ed8} h3{margin-top:1.4rem;color:#1e40af}
  a{color:#1d4ed8}
  ul,ol{padding-left:1.4rem} li{margin:.35rem 0}
  figure{margin:1.5rem 0} figure img{width:100%;border-radius:12px}
  figcaption{color:#475569;font-size:.88rem;margin-top:.4rem;text-align:center}
  .tags{margin-top:2rem;display:flex;flex-wrap:wrap;gap:.4rem}
  .tag{font-size:.74rem;background:#e5edff;color:#1d4ed8;
       padding:.16rem .55rem;border-radius:999px}
"""


def _html_blocks(blocks: Iterable[dict]) -> list[str]:
    out: list[str] = []
    for b in blocks:
        t = b.get("type")
        if t == "heading":
            lvl = max(2, min(6, int(b.get("level", 2))))
            out.append(f"<h{lvl}>{_esc(b.get('text', ''))}</h{lvl}>")
        elif t == "paragraph":
            out.append(f"<p>{_esc(b.get('text', ''))}</p>")
        elif t == "list":
            tag = "ol" if b.get("style") == "ordered" else "ul"
            items = "".join(f"<li>{_esc(i)}</li>" for i in b.get("items", []))
            out.append(f"<{tag}>{items}</{tag}>")
        elif t == "image":
            src = _basename(b.get("src", ""))
            alt = _esc(b.get("alt", ""))
            cap = _esc(b.get("caption", ""))
            cap_html = f"<figcaption>{cap}</figcaption>" if cap else ""
            out.append(f'<figure><img src="{src}" alt="{alt}">{cap_html}</figure>')
    return out


def render_html(post: dict) -> str:
    meta = post.get("meta", {})
    seo = post.get("seo", {})
    hero = post.get("hero", {}).get("image", {})

    title = _esc(meta.get("title") or seo.get("title", ""))
    subtitle = _esc(meta.get("subtitle", ""))
    desc = _esc(seo.get("description", ""))
    hero_html = (
        f'<img class="hero" src="{_basename(hero["src"])}" alt="{_esc(hero.get("alt", ""))}">'
        if hero.get("src") else ""
    )
    byline_bits = []
    if meta.get("author"):
        byline_bits.append(_esc(meta["author"]))
    if meta.get("publishedDate"):
        byline_bits.append(_esc(meta["publishedDate"]))
    if meta.get("readTimeMinutes"):
        byline_bits.append(f"{meta['readTimeMinutes']} min read")
    byline = (' <span class="byline">' + " · ".join(byline_bits) + "</span>") if byline_bits else ""

    body = "\n".join(_html_blocks(post.get("content", [])))
    tags_html = ""
    if meta.get("tags"):
        chips = "".join(f'<span class="tag">{_esc(t)}</span>' for t in meta["tags"])
        tags_html = f'<div class="tags">{chips}</div>'

    return f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<meta name="description" content="{desc}">
<style>{_HTML_CSS}</style></head>
<body>
{hero_html}
<h1>{title}</h1>
{f'<p class="subtitle">{subtitle}</p>' if subtitle else ''}
{byline}
{body}
{tags_html}
</body></html>"""


# ── PDF (via xhtml2pdf) ────────────────────────────────────────────────────

def render_pdf(post: dict, output_path: Path, asset_dir: Path) -> None:
    """
    Convert the post to PDF. `asset_dir` is the folder containing image files
    so that <img src="hero.png"> resolves correctly.
    """
    html = render_html(post)

    def link_callback(uri: str, rel: str) -> str:
        # xhtml2pdf passes <img src> through here; resolve to local path.
        if uri.startswith(("http://", "https://", "data:")):
            return uri
        p = (asset_dir / uri).resolve()
        return str(p) if p.exists() else uri

    with open(output_path, "wb") as fh:
        pisa.CreatePDF(src=html, dest=fh, link_callback=link_callback)


# ── DOCX (via python-docx) ─────────────────────────────────────────────────

_BLUE = RGBColor(0x1D, 0x4E, 0xD8)
_SLATE = RGBColor(0x47, 0x55, 0x69)


def _add_hyperlink_safe_text(p, text: str) -> None:
    """Add plain text run; python-docx doesn't natively support inline links."""
    run = p.add_run(text or "")
    run.font.size = Pt(11)


def render_docx(post: dict, output_path: Path, asset_dir: Path) -> None:
    meta = post.get("meta", {})
    seo = post.get("seo", {})
    hero = post.get("hero", {}).get("image", {})

    doc = Document()

    # Title
    title = meta.get("title") or seo.get("title", "")
    t = doc.add_paragraph()
    tr = t.add_run(title)
    tr.bold = True
    tr.font.size = Pt(22)
    tr.font.color.rgb = _BLUE

    if meta.get("subtitle"):
        sp = doc.add_paragraph()
        sr = sp.add_run(meta["subtitle"])
        sr.italic = True
        sr.font.size = Pt(12)
        sr.font.color.rgb = _SLATE

    byline_bits = []
    if meta.get("author"): byline_bits.append(meta["author"])
    if meta.get("publishedDate"): byline_bits.append(meta["publishedDate"])
    if meta.get("readTimeMinutes"): byline_bits.append(f"{meta['readTimeMinutes']} min read")
    if byline_bits:
        bp = doc.add_paragraph()
        br = bp.add_run(" · ".join(byline_bits))
        br.font.size = Pt(9)
        br.font.color.rgb = _SLATE

    # Hero image
    hero_path = asset_dir / _basename(hero.get("src", "")) if hero.get("src") else None
    if hero_path and hero_path.exists():
        doc.add_picture(str(hero_path), width=Inches(6))

    # Body blocks
    for block in post.get("content", []):
        bt = block.get("type")
        if bt == "heading":
            lvl = max(1, min(4, int(block.get("level", 2))))
            doc.add_heading(block.get("text", ""), level=lvl)
        elif bt == "paragraph":
            p = doc.add_paragraph()
            _add_hyperlink_safe_text(p, block.get("text", ""))
        elif bt == "list":
            style = "List Number" if block.get("style") == "ordered" else "List Bullet"
            for item in block.get("items", []):
                doc.add_paragraph(item, style=style)
        elif bt == "image":
            img_path = asset_dir / _basename(block.get("src", ""))
            if img_path.exists():
                doc.add_picture(str(img_path), width=Inches(6))
            if block.get("caption"):
                cp = doc.add_paragraph()
                cr = cp.add_run(block["caption"])
                cr.italic = True
                cr.font.size = Pt(9)
                cr.font.color.rgb = _SLATE

    # Tags footer
    if meta.get("tags"):
        doc.add_paragraph()
        fp = doc.add_paragraph()
        fr = fp.add_run("Tags: " + ", ".join(meta["tags"]))
        fr.font.size = Pt(9)
        fr.font.color.rgb = _SLATE

    doc.save(str(output_path))
