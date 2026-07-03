"""
Stock photo lookup via the Pexels API.

Given an image `prompt` (the block's alt text), searches Pexels and returns
raw JPEG bytes plus the mime type. The pipeline treats this as a drop-in
replacement for the old AI generator: same signature, same return shape,
same graceful-failure semantics — if lookup fails, the caller records the
error and the renderer shows a placeholder.

Get a free API key: https://www.pexels.com/api/  (200 req/hr, 20k req/mo)
"""

from __future__ import annotations

import os
import re

import requests

_SEARCH_URL = "https://api.pexels.com/v1/search"


class ImageGenError(RuntimeError):
    pass


# Words we strip from the search query — filler that pulls Pexels toward
# generic matches instead of the actual subject.
_STOPWORDS = frozenset({
    "a", "an", "the", "of", "and", "or", "for", "with", "in", "on", "at",
    "to", "from", "by", "as", "is", "are", "be", "this", "that", "these",
    "those", "shows", "showing", "depicts", "depicting", "featuring",
    "illustrating", "under", "over", "into", "photo", "image", "picture",
    "photograph", "hero", "vivid", "professional", "editorial", "shot",
    "clean", "wide",
})


def _query_from_prompt(prompt: str, max_words: int) -> str:
    """Reduce a long alt sentence to a few punchy search keywords."""
    words = re.findall(r"[A-Za-z][A-Za-z'-]+", (prompt or "").lower())
    words = [w for w in words if w not in _STOPWORDS and len(w) > 2]
    return " ".join(words[:max_words]) or (prompt or "").strip()[:60]


def generate_image(
    prompt: str,
    *,
    orientation: str = "landscape",
    size_key: str = "landscape",
    timeout: int = 30,
) -> tuple[bytes, str]:
    """
    Search Pexels for a photo matching `prompt` and return (bytes, mime).

    Tries progressively shorter queries so we don't come back empty when the
    alt text is a full descriptive sentence. Raises ImageGenError if every
    query returns zero results or the download fails.
    """
    api_key = os.getenv("PEXELS_API_KEY")
    if not api_key:
        raise ImageGenError(
            "PEXELS_API_KEY is not set. Grab a free key at "
            "https://www.pexels.com/api/ and add it to your .env or Render env."
        )
    headers = {"Authorization": api_key}

    tried: list[str] = []
    last_error: str | None = None
    for max_words in (5, 3, 2):
        query = _query_from_prompt(prompt, max_words)
        if not query or query in tried:
            continue
        tried.append(query)
        try:
            resp = requests.get(
                _SEARCH_URL,
                params={"query": query, "per_page": 1, "orientation": orientation},
                headers=headers,
                timeout=timeout,
            )
        except requests.RequestException as exc:
            last_error = f"network error: {exc}"
            continue
        if resp.status_code == 401:
            raise ImageGenError("Pexels rejected the API key (401). Check PEXELS_API_KEY.")
        if resp.status_code == 429:
            raise ImageGenError("Pexels rate limit hit (429). Try again in a few minutes.")
        if resp.status_code != 200:
            last_error = f"Pexels {resp.status_code}: {resp.text[:200]}"
            continue

        photos = resp.json().get("photos", [])
        if not photos:
            continue

        photo = photos[0]
        src = photo.get("src", {}) or {}
        img_url = src.get(size_key) or src.get("large2x") or src.get("large") or src.get("original")
        if not img_url:
            continue
        try:
            img_resp = requests.get(img_url, timeout=timeout)
            img_resp.raise_for_status()
        except requests.RequestException as exc:
            raise ImageGenError(f"Downloading Pexels image failed: {exc}") from exc
        mime = img_resp.headers.get("Content-Type", "image/jpeg")
        return img_resp.content, mime

    detail = last_error or f"no photos matched {tried}"
    raise ImageGenError(f"No Pexels photo found ({detail}).")
