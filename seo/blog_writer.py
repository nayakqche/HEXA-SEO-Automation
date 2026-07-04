"""
Blog writer — turns one keyword into a structured-JSON blog post that matches
the Hexa Climate CMS schema. Grounded in two source tiers and constrained to
only emit hyperlinks pointing at URLs from our verified inventory.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
from urllib.parse import urlparse

import anthropic

_SYSTEM = """\
You are the senior SEO content strategist for Hexa Climate. You write blog \
posts that rank well on Google AND read like a domain expert wrote them — \
specifically about the Indian renewable energy / decarbonisation space.

================== PRIMARY SOURCES (truth about Hexa Climate) ==================
Use these to ground any claim ABOUT Hexa (services, projects, technology, team,
mission, geographies). If something isn't here, don't invent it.

{primary_context}
================================================================================

================ SECONDARY SOURCES (industry context / RAG refs) ===============
Use these for general industry facts, regulations, trends, market stats.
NEVER cite these for Hexa-specific claims.

{secondary_context}
================================================================================

============================ URL INVENTORY (LINKS) =============================
The ONLY URLs you may link to are listed below. Any link you emit MUST be in
this list OR be on the same domain as a listed URL. Never invent paths.

INTERNAL (Hexa Climate properties — use for internal links):
{primary_inventory}

CITATION (trusted external sources — use for external links):
{secondary_inventory}
================================================================================

LINK RULES — absolute:
- INTERNAL: Add 2–3 internal links per blog. Each link's `href` must be a Hexa
  URL from the INTERNAL list (or a deep path on the same domain). Anchor text
  must be a natural phrase already in the paragraph (e.g. "our green energy
  solutions"), not generic ("click here").
- CITATION: Add 2–4 external citation links per blog at points where you
  reference a stat, regulation, or named programme (e.g. "the MNRE's Green
  Open Access Rules"). The `href` MUST come from the CITATION list (or a
  deep path on the same domain).
- Every link is declared in a `links` array attached to its block. Do NOT add
  inline markdown like [text](url) inside `text`.

GROUNDING RULES — absolute:
- Facts about Hexa: PRIMARY sources only.
- Industry stats: only what's defensible from SECONDARY sources or general
  expertise. Never fabricate numbers, names, or quotes.

SEO REQUIREMENTS:
- Focus keyword used naturally in: title, first paragraph, ≥1 H2, the meta
  description, and the slug. No stuffing.
- Audience: Indian C&I procurement, sustainability, and ESG decision-makers.
- Concrete, specific, no fluff or AI throat-clearing.
"""

_USER = """\
Write a complete SEO blog post for this focus keyword: "{keyword}"
Today's date is {today}.

FORMAT: {format_directive}
TARGET LENGTH: ~{target_words} words (acceptable range: {min_words}–{max_words}).{extra}

Return ONLY a single JSON object — no prose before or after, no markdown
fencing. The JSON MUST match this schema exactly:

{{
  "slug": "kebab-case-slug-with-keyword",
  "seo": {{
    "title": "Click-worthy SEO title (~60 chars, includes keyword) | Hexa Climate",
    "description": "150–160 char meta description that includes the keyword.",
    "keywords": ["focus keyword", "related kw 1", "related kw 2", "..."]
  }},
  "meta": {{
    "title": "Same as seo.title WITHOUT ' | Hexa Climate'",
    "subtitle": "One-line subtitle (~120 chars) — what the reader will learn",
    "author": "Hexa Climate Editorial Team",
    "readTimeMinutes": 7,
    "publishedDate": "{today}",
    "category": "one of: Policy & Regulations | Renewable Energy | Decarbonisation | C&I Procurement | ESG & Reporting",
    "tags": ["3–6 short topic tags"]
  }},
  "hero": {{
    "image": {{
      "src": "/assets/blogs/<slug>/hero.png",
      "alt": "Vivid alt text describing the hero photo (used as the Gemini prompt)"
    }},
    "showOverlayTitle": true
  }},
  "content": [
    {{ "id": "intro-heading", "type": "heading", "level": 2, "text": "..." }},
    {{ "id": "intro-paragraph-1", "type": "paragraph", "text": "...",
       "links": [
         {{ "anchor": "exact substring of text", "href": "https://...",
            "kind": "internal" }}
       ] }},
    {{ "id": "section-x-heading", "type": "heading", "level": 3, "text": "..." }},
    {{ "id": "section-x-paragraph-1", "type": "paragraph", "text": "...",
       "links": [
         {{ "anchor": "the MNRE's Green Open Access Rules", "href": "https://mnre.gov.in/...",
            "kind": "citation" }}
       ] }},
    {{ "id": "section-x-list-1", "type": "list", "style": "unordered",
       "items": ["...", "..."], "links": [] }},
    {{ "id": "diagram-...", "type": "image",
       "src": "/assets/blogs/<slug>/diagram-foo.png",
       "alt": "Specific, literal description of the diagram (Gemini prompt)",
       "caption": "Short reader-facing caption" }},
    {{ "id": "cta-heading", "type": "heading", "level": 3, "text": "Ready to ...?" }},
    {{ "id": "cta-paragraph-1", "type": "paragraph", "text": "...",
       "links": [
         {{ "anchor": "talk to our team", "href": "https://hexaclimate.com/contact",
            "kind": "internal" }}
       ] }}
  ]
}}

CONTENT BLOCK RULES:
- `id` is a unique kebab-case slug per block.
- `type` ∈ heading | paragraph | list | image.
- `heading.level` is 2 or 3.
- `list.style` is "unordered" or "ordered".
- `links` is OPTIONAL on paragraph and list blocks. Omit if no links. When
  present, each `anchor` MUST be a verbatim substring of `text` (or of one of
  the list `items`). `kind` is "internal" or "citation".

IMAGE RULES (strict — we source stock photos from Pexels, not AI):
- 1 hero image (in `hero.image`, alt = photographable subject).
- EXACTLY 2 in-body `image` blocks — no more, no less.
- Every image `alt` MUST describe a real, photographable subject from the
  renewable energy space — e.g. "solar panels on a warehouse roof", "wind
  turbines against a blue sky", "battery energy storage container at a
  substation", "high-voltage transmission towers at sunset", "workers
  installing rooftop solar", "electric vehicles charging", "green hydrogen
  facility".
- Do NOT ask for diagrams, infographics, charts, flowcharts, illustrations,
  logos, or text-in-image. Pexels is stock photography — those returns
  never exist. Ask for concrete physical scenes.
- Image `src` MUST be `/assets/blogs/<slug>/<id>.png`.
- Image `caption` is a short reader-facing line — different from `alt`.
- Always end with a CTA heading + paragraph that contains at least one internal
  link to a Hexa contact / services page.

LINK DISTRIBUTION:
- 2–3 INTERNAL links total across the blog (Hexa pages).
- 2–4 CITATION links total across the blog (secondary sources).
- Spread them across different blocks. Don't pile multiple links into one
  paragraph.

Return ONLY the JSON object.\
"""


def _client() -> anthropic.Anthropic:
    if not os.getenv("ANTHROPIC_API_KEY"):
        raise RuntimeError("ANTHROPIC_API_KEY is not set. Add it to your .env file.")
    return anthropic.Anthropic()


def _extract_json(text: str, *, stop_reason: str | None = None) -> dict:
    text = (text or "").strip()
    if not text:
        raise ValueError(
            "Claude returned an empty text block"
            + (f" (stop_reason={stop_reason}). max_tokens was exhausted by thinking "
               "before any output was produced — raise CLAUDE_MAX_TOKENS."
               if stop_reason == "max_tokens" else ".")
        )
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```\s*$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError(
            "Claude response contained no JSON object"
            + (" (stop_reason=max_tokens — truncated). Raise CLAUDE_MAX_TOKENS or "
               "lower CLAUDE_EFFORT." if stop_reason == "max_tokens" else "")
            + f". First 300 chars: {text[:300]!r}"
        )
    snippet = text[start:end + 1]
    try:
        return json.loads(snippet)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"JSON in Claude response was malformed: {exc}. "
            f"Snippet: {snippet[:300]!r}"
        ) from exc


def _format_directive(fmt: str) -> str:
    f = (fmt or "paragraph").lower()
    if f == "listicle":
        return (
            "LISTICLE — short, scannable, list-driven. Use H2s like "
            "\"1. <Point Title>\", \"2. <Point Title>\". Each numbered point "
            "has a brief paragraph plus an unordered list of 3–5 bullets. "
            "Aim for 6–10 numbered points. Keep paragraphs ≤3 sentences."
        )
    return (
        "PARAGRAPH (long-form) — traditional flowing prose with clear H2 "
        "sections and H3 sub-sections. Use lists sparingly (1–3 across the "
        "whole post) to break up dense topics. Substantive paragraphs of "
        "3–6 sentences each."
    )


def write_blog(
    keyword: str,
    primary_context: str,
    secondary_context: str,
    primary_inventory: str,
    secondary_inventory: str,
    *,
    extra_instructions: str = "",
    fmt: str = "paragraph",
    target_words: int = 1400,
    model: str | None = None,
    effort: str | None = None,
) -> dict:
    client = _client()
    model = model or os.getenv("CLAUDE_MODEL", "claude-opus-4-8")
    effort = effort or os.getenv("CLAUDE_EFFORT", "high")
    today = dt.date.today().isoformat()
    target_words = max(400, min(target_words, 3000))
    min_w = int(target_words * 0.85)
    max_w = int(target_words * 1.15)

    system = [
        {
            "type": "text",
            "text": _SYSTEM.format(
                primary_context=primary_context,
                secondary_context=secondary_context,
                primary_inventory=primary_inventory or "(none)",
                secondary_inventory=secondary_inventory or "(none)",
            ),
            "cache_control": {"type": "ephemeral"},
        }
    ]
    extra = f"\nExtra guidance: {extra_instructions}" if extra_instructions else ""
    user = _USER.format(
        keyword=keyword, today=today,
        format_directive=_format_directive(fmt),
        target_words=target_words, min_words=min_w, max_words=max_w,
        extra=extra,
    )

    max_tokens = int(os.getenv("CLAUDE_MAX_TOKENS", "16000"))
    with client.messages.stream(
        model=model,
        max_tokens=max_tokens,
        thinking={"type": "adaptive"},
        output_config={"effort": effort},
        system=system,
        messages=[{"role": "user", "content": user}],
    ) as stream:
        message = stream.get_final_message()

    raw = "".join(b.text for b in message.content if b.type == "text").strip()
    stop_reason = getattr(message, "stop_reason", None)
    post = _extract_json(raw, stop_reason=stop_reason)

    post.setdefault("slug", _fallback_slug(keyword))
    post.setdefault("seo", {})
    post.setdefault("meta", {})
    post.setdefault("hero", {})
    post.setdefault("content", [])

    return {
        "post": post,
        "usage": {
            "input_tokens": message.usage.input_tokens,
            "output_tokens": message.usage.output_tokens,
            "cache_read": getattr(message.usage, "cache_read_input_tokens", 0),
        },
    }


def _fallback_slug(keyword: str) -> str:
    s = re.sub(r"[^\w\s-]", "", keyword.lower()).strip()
    return re.sub(r"[\s_-]+", "-", s)[:60] or "post"


# ── Link validation (called from pipeline after generation) ────────────────

def _domain(url: str) -> str:
    try:
        return urlparse(url).netloc.replace("www.", "").lower()
    except Exception:
        return ""


def validate_and_clean_links(post: dict, primary_urls: list[str], secondary_urls: list[str]) -> dict:
    """
    Walk every block's `links` array and:
      • drop links whose href domain isn't in primary or secondary inventory
      • drop links whose anchor isn't a real substring of the block text
      • return stats about kept/dropped for the UI log
    """
    allowed_internal = {_domain(u) for u in primary_urls if u}
    allowed_citation = {_domain(u) for u in secondary_urls if u}
    kept = {"internal": 0, "citation": 0}
    dropped: list[str] = []

    for block in post.get("content", []):
        links = block.get("links") or []
        if not links:
            continue
        haystack = block.get("text", "")
        if block.get("type") == "list":
            haystack += "\n" + "\n".join(block.get("items", []))
        clean: list[dict] = []
        for link in links:
            anchor = (link.get("anchor") or "").strip()
            href = (link.get("href") or "").strip()
            if not anchor or not href:
                dropped.append(f"empty link in {block.get('id', '?')}")
                continue
            if anchor not in haystack:
                dropped.append(f"'{anchor[:40]}…' anchor not in {block.get('id', '?')}")
                continue
            dom = _domain(href)
            if dom in allowed_internal:
                link["kind"] = "internal"
            elif dom in allowed_citation:
                link["kind"] = "citation"
            else:
                dropped.append(f"'{href[:60]}…' domain not in inventory")
                continue
            kept[link["kind"]] += 1
            clean.append(link)
        if clean:
            block["links"] = clean
        elif "links" in block:
            del block["links"]

    return {"kept": kept, "dropped": dropped}
