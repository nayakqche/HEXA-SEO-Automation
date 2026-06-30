"""
Image generator — creates a hero image for each blog post via the Google
Gemini API. Returns raw PNG/JPEG bytes that the pipeline saves next to the post.

Uses the generateContent endpoint with IMAGE response modality, which is how
Gemini's image-capable models return inline image data.
"""

from __future__ import annotations

import base64
import os

import requests

_API_ROOT = "https://generativelanguage.googleapis.com/v1beta/models"


class ImageGenError(RuntimeError):
    pass


def generate_image(prompt: str, *, model: str | None = None, timeout: int = 90) -> tuple[bytes, str]:
    """
    Generate one image from `prompt`.

    Returns (image_bytes, mime_type). Raises ImageGenError on failure so the
    pipeline can continue with a placeholder instead of crashing the run.
    """
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise ImageGenError("GEMINI_API_KEY is not set. Add it to your .env file.")

    model = model or os.getenv("GEMINI_IMAGE_MODEL", "gemini-2.5-flash-image-preview")
    url = f"{_API_ROOT}/{model}:generateContent"

    full_prompt = (
        f"{prompt}\n\nStyle: clean, professional, photorealistic editorial hero "
        "image suitable for a corporate sustainability/climate blog. "
        "No text, no logos, no watermarks. 16:9 composition."
    )

    payload = {
        "contents": [{"parts": [{"text": full_prompt}]}],
        "generationConfig": {"responseModalities": ["IMAGE", "TEXT"]},
    }

    try:
        resp = requests.post(
            url,
            params={"key": api_key},
            json=payload,
            timeout=timeout,
            headers={"Content-Type": "application/json"},
        )
    except requests.RequestException as exc:
        raise ImageGenError(f"Gemini request failed: {exc}") from exc

    if resp.status_code != 200:
        raise ImageGenError(
            f"Gemini returned {resp.status_code}: {resp.text[:300]}. "
            "Check GEMINI_API_KEY and GEMINI_IMAGE_MODEL."
        )

    data = resp.json()
    try:
        parts = data["candidates"][0]["content"]["parts"]
    except (KeyError, IndexError) as exc:
        raise ImageGenError(f"Unexpected Gemini response shape: {data}") from exc

    for part in parts:
        inline = part.get("inlineData") or part.get("inline_data")
        if inline and inline.get("data"):
            mime = inline.get("mimeType") or inline.get("mime_type") or "image/png"
            return base64.b64decode(inline["data"]), mime

    raise ImageGenError(
        "Gemini returned no image data — the model may not support image "
        "output on your account. Try GEMINI_IMAGE_MODEL="
        "gemini-2.0-flash-preview-image-generation"
    )
