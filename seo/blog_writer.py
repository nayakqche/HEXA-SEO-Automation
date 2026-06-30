"""
Blog writer — turns one keyword into a structured-JSON blog post that matches
the Hexa Climate CMS schema exactly. Grounded in two source tiers:

  PRIMARY   → the only place facts ABOUT Hexa may come from.
  SECONDARY → trusted industry references for general claims & stats.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re

import anthropic

_SYSTEM = """\
You are the senior SEO content strategist for Hexa Climate. You write blog \
posts that rank well on Google AND read like a domain expert wrote them — \
specifically about the Indian renewable energy / decarbonisation space.

================== PRIMARY SOURCES (truth about Hexa Climate) ==================
You may make brand-specific claims (Hexa's services, projects, technology, \
team, mission, geographies, partners) ONLY when they are supported by these \
PRIMARY sources. If something isn't here, don't invent it.

{primary_context}
================================================================================

================ SECONDARY SOURCES (industry context / RAG refs) ===============
Use these for general industry facts, regulations, trends, market stats and \
to enrich the post with credible context. NEVER cite these for Hexa-specific \
claims.

{secondary_context}
================================================================================

GROUNDING RULES — absolute:
- Facts about Hexa Climate: PRIMARY sources only.
- General industry claims: only what's defensible from SECONDARY sources or \
your general expertise. If a number/stat isn't supported, omit it.
- Never invent client names, certifications, awards, partner names, or quotes.
- Match Hexa's real positioning and terminology shown in the primary context. \
Use Hexa's own product/service names exactly as written.

SEO REQUIREMENTS:
- Focus keyword used naturally in: title, first paragraph, at least one H2, \
the meta description, and the slug. No keyword stuffing.
- 1,100–1,600 words of genuinely useful content for an Indian C&I audience.
- Clear structure: short intro, multiple H2/H3 sections, scannable lists, and \
a concluding CTA that points readers toward Hexa Climate.
- Write for humans first. Concrete, specific, no fluff.
"""

_USER = """\
Write a complete SEO blog post for this focus keyword: "{keyword}"
Today's date is {today}.{extra}

Return ONLY a single JSON object — no prose before or after, no markdown \
fencing. The JSON MUST match this schema exactly:

{{
  "slug": "kebab-case-slug-with-keyword",
  "seo": {{
    "title": "Click-worthy SEO title (~60 chars, includes keyword) | Hexa Climate",
    "description": "150–160 char meta description that includes the keyword.",
    "keywords": ["focus keyword", "related kw 1", "related kw 2", "..."]
  }},
  "meta": {{
    "title": "Same title as seo.title but WITHOUT the ' | Hexa Climate' suffix",
    "subtitle": "One-line subtitle (≈120 chars) — what the reader will learn",
    "author": "Hexa Climate Editorial Team",
    "readTimeMinutes": 7,
    "publishedDate": "{today}",
    "category": "Policy & Regulations | Renewable Energy | Decarbonisation | C&I Procurement | ESG & Reporting",
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
    {{ "id": "intro-paragraph-1", "type": "paragraph", "text": "..." }},
    {{ "id": "section-x-heading", "type": "heading", "level": 3, "text": "..." }},
    {{ "id": "section-x-paragraph-1", "type": "paragraph", "text": "..." }},
    {{ "id": "section-x-list-1", "type": "list", "style": "unordered", "items": ["...", "..."] }},
    {{ "id": "diagram-...", "type": "image",
       "src": "/assets/blogs/<slug>/diagram-foo.png",
       "alt": "Specific, literal description of the diagram (used as Gemini prompt)",
       "caption": "Short reader-facing caption" }},
    ...
    {{ "id": "cta-heading", "type": "heading", "level": 3, "text": "Ready to ...?" }},
    {{ "id": "cta-paragraph-1", "type": "paragraph", "text": "..." }}
  ]
}}

CONTENT BLOCK RULES:
- `id` is a unique kebab-case slug per block.
- `type` is one of: "heading" | "paragraph" | "list" | "image".
- `heading.level` is 2 or 3.
- `list.style` is "unordered" or "ordered".
- Include EXACTLY 2 in-body image blocks (e.g. a diagram + an infographic), \
in addition to the hero. Their `src` MUST be `/assets/blogs/<slug>/<id>.png`. \
Write their `alt` as a vivid, literal Gemini image prompt — what should the \
image look like (subject, composition, mood, no text/logos/watermarks).
- Use H2 ("level": 2) for the opening section and major sections; use H3 \
("level": 3) for sub-sections.
- Always end with a CTA heading + paragraph.

Return ONLY the JSON object.\
"""


def _client() -> anthropic.Anthropic:
    if not os.getenv("ANTHROPIC_API_KEY"):
        raise RuntimeError("ANTHROPIC_API_KEY is not set. Add it to your .env file.")
    return anthropic.Anthropic()


def _extract_json(text: str) -> dict:
    """Pull the JSON object out of Claude's response, tolerant of stray prose."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```\s*$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Fall back: take the substring between the first { and the matching last }.
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError(f"Couldn't find JSON in Claude response: {text[:200]}…")
    return json.loads(text[start:end + 1])


def write_blog(
    keyword: str,
    primary_context: str,
    secondary_context: str,
    *,
    extra_instructions: str = "",
    model: str | None = None,
    effort: str | None = None,
) -> dict:
    """Generate one blog post as a Python dict matching the CMS schema."""
    client = _client()
    model = model or os.getenv("CLAUDE_MODEL", "claude-opus-4-8")
    effort = effort or os.getenv("CLAUDE_EFFORT", "high")
    today = dt.date.today().isoformat()

    system = [
        {
            "type": "text",
            "text": _SYSTEM.format(
                primary_context=primary_context,
                secondary_context=secondary_context,
            ),
            # The grounding context is identical across the batch — cache it
            # so every keyword after the first is far cheaper/faster.
            "cache_control": {"type": "ephemeral"},
        }
    ]
    extra = f"\nExtra guidance: {extra_instructions}" if extra_instructions else ""
    user = _USER.format(keyword=keyword, today=today, extra=extra)

    with client.messages.stream(
        model=model,
        max_tokens=8000,
        thinking={"type": "adaptive"},
        output_config={"effort": effort},
        system=system,
        messages=[{"role": "user", "content": user}],
    ) as stream:
        message = stream.get_final_message()

    raw = "".join(b.text for b in message.content if b.type == "text").strip()
    post = _extract_json(raw)

    # Belt-and-suspenders: ensure required top-level keys exist.
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
