"""
Blog writer — turns a single keyword into an SEO-optimized blog post using the
Claude API, grounded strictly in the scraped brand context so it never invents
facts about the company.
"""

from __future__ import annotations

import os
import re

import anthropic
import yaml

_SYSTEM_TEMPLATE = """\
You are the senior SEO content strategist for the brand described in the \
BRAND CONTEXT below. You write blog posts that rank well on Google AND read \
like an expert wrote them.

=================== BRAND CONTEXT (your only source of truth) ===================
{brand_context}
================================================================================

GROUNDING RULES — these are absolute:
- Every claim about the brand (its services, technology, projects, mission, \
numbers, geographies, partners) MUST be supported by the BRAND CONTEXT above.
- If the BRAND CONTEXT does not contain a fact, DO NOT invent it. Write around \
it using general, defensible industry knowledge instead, or omit it.
- Never fabricate statistics, client names, certifications, awards, or quotes.
- Match the brand's real tone, terminology, and positioning as shown in the \
context. Use the brand's own product/service names exactly as written there.

SEO REQUIREMENTS for every post:
- Target the focus keyword naturally in the title, first 100 words, at least \
one H2, the meta description, and the slug. No keyword stuffing.
- 1,100–1,600 words of genuinely useful content.
- Clear structure: one H1 (the title), several H2 sections, H3s where helpful.
- Include a short intro, scannable sections, and a concluding call-to-action \
that points the reader toward the brand.
- Write for humans first: concrete, specific, no fluff or AI throat-clearing.
"""

_USER_TEMPLATE = """\
Write a complete SEO blog post targeting this focus keyword: "{keyword}"
{extra}

Return the post as a single Markdown document that begins with a YAML \
frontmatter block, exactly in this shape:

---
title: "A compelling, click-worthy H1 (~60 chars, includes the keyword)"
meta_description: "150–160 char meta description that includes the keyword"
slug: "url-friendly-slug-with-keyword"
focus_keyword: "{keyword}"
tags: ["tag1", "tag2", "tag3"]
image_prompt: "A vivid, literal description of a single hero image for this \
post — photographic, professional, on-brand, no text in the image"
---

Then the article body in clean Markdown (start with the H1 as `# Title`).
Do not output anything before the opening `---` or after the article.
"""


def _client() -> anthropic.Anthropic:
    if not os.getenv("ANTHROPIC_API_KEY"):
        raise RuntimeError("ANTHROPIC_API_KEY is not set. Add it to your .env file.")
    return anthropic.Anthropic()


def split_frontmatter(markdown: str) -> tuple[dict, str]:
    """Parse the leading YAML frontmatter; returns (meta_dict, body_markdown)."""
    m = re.match(r"^\s*---\s*\n(.*?)\n---\s*\n(.*)$", markdown, re.DOTALL)
    if not m:
        return {}, markdown.strip()
    try:
        meta = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError:
        meta = {}
    return (meta if isinstance(meta, dict) else {}), m.group(2).strip()


def write_blog(
    keyword: str,
    brand_context: str,
    *,
    extra_instructions: str = "",
    model: str | None = None,
    effort: str | None = None,
) -> dict:
    """
    Generate one grounded SEO blog for `keyword`.

    Returns a dict: {keyword, markdown, meta, body, image_prompt}.
    """
    client = _client()
    model = model or os.getenv("CLAUDE_MODEL", "claude-opus-4-8")
    effort = effort or os.getenv("CLAUDE_EFFORT", "high")

    system = [
        {
            "type": "text",
            "text": _SYSTEM_TEMPLATE.format(brand_context=brand_context),
            # Cache the (large, identical-per-run) brand context so every
            # keyword after the first is far cheaper and faster.
            "cache_control": {"type": "ephemeral"},
        }
    ]
    extra = f"\nExtra guidance: {extra_instructions}" if extra_instructions else ""
    user = _USER_TEMPLATE.format(keyword=keyword, extra=extra)

    # Stream so the large max_tokens never trips an HTTP timeout.
    with client.messages.stream(
        model=model,
        max_tokens=8000,
        thinking={"type": "adaptive"},
        output_config={"effort": effort},
        system=system,
        messages=[{"role": "user", "content": user}],
    ) as stream:
        message = stream.get_final_message()

    markdown = "".join(
        block.text for block in message.content if block.type == "text"
    ).strip()

    meta, body = split_frontmatter(markdown)
    return {
        "keyword": keyword,
        "markdown": markdown,
        "meta": meta,
        "body": body,
        "image_prompt": meta.get("image_prompt", ""),
        "usage": {
            "input_tokens": message.usage.input_tokens,
            "output_tokens": message.usage.output_tokens,
            "cache_read": getattr(message.usage, "cache_read_input_tokens", 0),
        },
    }
