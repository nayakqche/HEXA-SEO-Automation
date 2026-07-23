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
posts that rank well on Google AND read like a domain expert wrote them, \
specifically about the Indian renewable energy / decarbonisation space.

========================= HEXA CLIMATE BUSINESS SCOPE =========================
This is the single most important rule. Every blog, and ESPECIALLY every
heading, must be anchored to WHAT HEXA CLIMATE DOES (below). Use the focus
keyword for SEO relevance and weave it in naturally, but the substance and
headings must revolve around Hexa's actual offerings. If the keyword is about a
residential, hardware, manufacturing, or pricing topic that Hexa does not do,
pivot it to how it connects to Hexa's Commercial & Industrial, utility-scale,
open-access, PPA, or RTC work. NEVER position or imply Hexa does anything in the
DOES NOT list.

HEXA DOES:
- Develops utility-scale solar, wind, and solar-wind hybrid projects
- Develops FDRE (Firm & Dispatchable Renewable Energy) projects
- Develops Battery Energy Storage Systems (BESS)
- Owns and operates renewable energy assets; acquires and develops RE portfolios
- Delivers 24x7 / round-the-clock (RTC) renewable power
- Supplies renewable power through Open Access (CTU/STU)
- Structures Captive and Group Captive power arrangements
- Signs long-term Power Purchase Agreements (PPAs)
- Serves Commercial & Industrial (C&I) customers, and sells power to DISCOMs,
  government buyers, and PSUs under long-term PPAs
- Sells uncontracted power on the exchange (IEX)
- Provides end-to-end project development: land, permits, transmission, EPC
  coordination, and operations
- Facilitates Renewable Energy Certificates (I-RECs) and carbon offsets
- Helps businesses cut electricity costs and emissions; supports ESG and
  Net-Zero strategies

HEXA DOES NOT (never imply Hexa does any of these):
- Manufacture solar panels, wind turbines, batteries, inverters, or any
  renewable-energy hardware / equipment
- Sell electricity directly to homes, or serve primarily residential consumers
- Build consumer rooftop solar as a primary business
- Operate as a DISCOM or a traditional utility company
- Own or operate transmission infrastructure (STU/CTU handle this)
- Operate coal, oil, or gas power plants, or mine coal or critical minerals
- Manufacture EVs / EV components, or build EV charging as a core business
- Act as an EPC contractor for third parties

TERMINOLOGY (strict):
- Always say "carbon offsets", never "carbon credits".
- Never mention Biochar anywhere.
================================================================================

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
- If a PRIMARY source contains a data table (marked "TABLE:"), you MAY reproduce
  that real data as a `table` content block. Copy the numbers exactly; never
  invent rows, columns, or figures that aren't in the source.

UPLOADED MEDIA (user-supplied — these MUST appear in the post):
{media_brief}

WRITING STYLE — absolute:
- Write the ENTIRE post in fluent English. The focus keyword may be given in
  Hindi or Devanagari script; treat it purely as the topic to cover and never
  output any Hindi word or sentence. Every heading, paragraph, list item, tag,
  caption, and metadata field is in English.
- NEVER use an em dash or en dash anywhere in any field. Use a comma, a colon,
  or split into two sentences instead. This is a hard, non-negotiable rule.
- No AI throat-clearing, no "in today's world", no filler.

SEO REQUIREMENTS:
- Focus keyword used naturally in: title, first paragraph, at least one H2, the
  meta description, and the slug. No stuffing.
- Audience: Indian C&I procurement, sustainability, and ESG decision-makers.
- Concrete, specific, no fluff.
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
    {{ "id": "section-x-table-1", "type": "table",
       "caption": "Short caption for a REAL data table from a source",
       "data": {{
         "headers": ["Column A", "Column B"],
         "rows": [["cell", "cell"], ["cell", "cell"]]
       }} }},
    {{ "id": "section-x-quote-1", "type": "quote",
       "text": "A single-sentence pull quote from a source, verbatim.",
       "cite": "Attribution (source or speaker)" }},
    {{ "id": "section-x-image", "type": "image",
       "src": "/assets/blogs/<slug>/section-x-image.png",
       "alt": "Real photographable renewable-energy scene",
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
- `type` ∈ heading | paragraph | list | image | table | quote.
- `heading.level` is 2 or 3.
- `list.style` is "unordered" or "ordered".
- `links` is OPTIONAL on paragraph and list blocks. Omit if no links. When
  present, each `anchor` MUST be a verbatim substring of `text` (or of one of
  the list `items`). `kind` is "internal" or "citation".
- `table` is for REAL tabular data only (from a "TABLE:" source block or an
  uploaded table). Put `headers` and `rows` INSIDE a `data` object:
  `"data": {{ "headers": [...], "rows": [[...], [...]] }}`. Never fabricate
  table data from thin air.
- `quote` blocks carry a verbatim `text` pull-quote (no invented quotes) and
  an optional `cite` attribution. Use them sparingly for real, source-backed
  quotations.

IMAGE RULES (strict — we source stock photos from Pexels, not AI):
- 1 hero image (in `hero.image`, alt = photographable subject).
- {image_directive}
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


_FORMAT_DIRECTIVES = {
    "paragraph": (
        "PARAGRAPH (long-form): traditional flowing prose with clear H2 "
        "sections and H3 sub-sections. Use lists sparingly (1 to 3 across the "
        "whole post) to break up dense topics. Substantive paragraphs of "
        "3 to 6 sentences each."
    ),
    "listicle": (
        "LISTICLE: short, scannable, list-driven. Use H2s like "
        "\"1. <Point Title>\", \"2. <Point Title>\". Each numbered point "
        "has a brief paragraph plus an unordered list of 3 to 5 bullets. "
        "Aim for 6 to 10 numbered points. Keep paragraphs to 3 sentences or less."
    ),
    "data": (
        "DATA-INTENSIVE / ANALYTICAL: lead with figures. Include at least one "
        "`table` block built ONLY from real numbers that appear in the sources "
        "(capacity, tariffs, emissions, year-on-year changes). Never invent a "
        "figure. Interpret the numbers in prose so a procurement lead can act "
        "on them, and cite the source for every stat."
    ),
    "hexa-update": (
        "HEXA DEVELOPMENTS UPDATE: cover ONLY Hexa Climate's own projects, "
        "milestones, announcements, partnerships, and capabilities, drawn "
        "strictly from the PRIMARY sources. Do not pad with generic industry "
        "background. If the primary sources do not support a detail, leave it "
        "out rather than inventing it. Write it as an authoritative company update."
    ),
    "how-to": (
        "HOW-TO GUIDE: practical and step-driven. Use an ordered list for the "
        "core steps, each with a short paragraph of context. Make it directly "
        "actionable for a C&I procurement or sustainability lead."
    ),
    "thought-leadership": (
        "THOUGHT LEADERSHIP: a confident expert point of view on where the "
        "market is heading. Prose-led with one clear argument, backed by cited "
        "statistics. No hedging, no filler."
    ),
    "comparison": (
        "COMPARISON: weigh the main options or approaches side by side. Include "
        "a `table` block comparing them on real criteria, then prose that "
        "interprets the trade-offs for Indian C&I buyers. Use only facts the "
        "sources support."
    ),
}


def _format_directive(fmt: str) -> str:
    return _FORMAT_DIRECTIVES.get((fmt or "paragraph").lower(),
                                  _FORMAT_DIRECTIVES["paragraph"])


def _image_directive(fmt: str) -> str:
    """How many in-body images to request, by format.

    Listicles get one image per numbered point (in addition to the hero) so
    every item is illustrated; other formats keep the tight 2-image rule.
    """
    if (fmt or "").lower() == "listicle":
        return (
            "Add ONE in-body `image` block for EACH numbered point (so 6 to 10 "
            "in-body images, one per point), placed right after that point's "
            "text. Give each a DIFFERENT photographable subject so no two images "
            "repeat."
        )
    return "Add EXACTLY 2 in-body `image` blocks, no more and no less."


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
    media_brief: str = "",
    model: str | None = None,
    effort: str | None = None,
) -> dict:
    client = _client()
    model = model or os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6")
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
                media_brief=media_brief or "(none uploaded — use Pexels photos only)",
            ),
            "cache_control": {"type": "ephemeral"},
        }
    ]
    extra = f"\nExtra guidance: {extra_instructions}" if extra_instructions else ""
    user = _USER.format(
        keyword=keyword, today=today,
        format_directive=_format_directive(fmt),
        image_directive=_image_directive(fmt),
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
    _sanitize_post(post)

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


# ── Output sanitiser (belt-and-braces on top of the prompt rules) ──────────

# Em dash, en dash, and the horizontal-bar variants Claude sometimes reaches for.
_DASH_RE = re.compile(r"\s*[‒–—―]\s*")
# House style: Hexa says "carbon offsets", never "carbon credits".
_CARBON_RE = re.compile(r"\bcarbon(\s+)credit(s?)\b", re.IGNORECASE)


def _clean_dashes(value: str) -> str:
    """Replace any em/en dash with a comma-space (or a plain hyphen mid-word)."""
    if not _DASH_RE.search(value):
        return value
    # "word — word" → "word, word"; "12–15" → "12-15" (no surrounding spaces).
    def repl(m: re.Match) -> str:
        return ", " if (m.group(0) != m.group(0).strip()) else "-"
    return _DASH_RE.sub(repl, value)


def _swap_terms(value: str) -> str:
    """Enforce Hexa house terminology: "carbon credit(s)" → "carbon offset(s)"."""
    def repl(m: re.Match) -> str:
        carbon = "Carbon" if m.group(0)[:1].isupper() else "carbon"
        return f"{carbon}{m.group(1)}offset{m.group(2)}"
    return _CARBON_RE.sub(repl, value)


def _sanitize_string(value: str) -> str:
    return _swap_terms(_clean_dashes(value)) if value else value


def _sanitize_post(node):
    """Recursively rewrite every string in the post dict/list, in place."""
    if isinstance(node, dict):
        for k, v in node.items():
            node[k] = _sanitize_post(v)
        return node
    if isinstance(node, list):
        return [_sanitize_post(v) for v in node]
    if isinstance(node, str):
        return _sanitize_string(node)
    return node


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
