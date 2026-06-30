"""
Brand scraper — builds the "grounding context" the blog writer is allowed to
draw facts from. By crawling the real Hexa Climate site and feeding only that
text to Claude, the model writes about the *actual* company instead of
inventing services, numbers, or claims.
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

# A real browser UA — many marketing sites 403 the default python-requests UA.
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# Pages whose paths hint they describe the company well — crawled first.
_PRIORITY_HINTS = (
    "about", "what-we-do", "solution", "service", "product", "platform",
    "project", "technology", "sustainab", "decarbon", "energy", "esg",
    "team", "company", "mission",
)

# Paths we never want in grounding context.
_SKIP_HINTS = (
    "privacy", "terms", "cookie", "login", "career", "contact",
    "policy", "legal", "/blog", "/news", ".pdf", ".jpg", ".png",
)


@dataclass
class BrandContext:
    website: str
    pages: list[dict] = field(default_factory=list)
    logo_url: str | None = None

    @property
    def text(self) -> str:
        """The full grounding document fed to Claude."""
        chunks = [f"BRAND SOURCE WEBSITE: {self.website}\n"]
        for p in self.pages:
            chunks.append(f"\n===== PAGE: {p['title']} ({p['url']}) =====\n{p['text']}")
        return "\n".join(chunks)

    @property
    def char_count(self) -> int:
        return len(self.text)


def _clean_text(soup: BeautifulSoup) -> str:
    for tag in soup(["script", "style", "noscript", "svg", "header", "footer", "nav"]):
        tag.decompose()
    text = soup.get_text(separator="\n")
    # Collapse runs of blank lines / whitespace.
    lines = [ln.strip() for ln in text.splitlines()]
    lines = [ln for ln in lines if ln]
    text = "\n".join(lines)
    return re.sub(r"\n{3,}", "\n\n", text)


def _find_logo(soup: BeautifulSoup, base_url: str) -> str | None:
    # 1. og:image is usually the brand's hero/logo.
    og = soup.find("meta", property="og:image")
    if og and og.get("content"):
        return urljoin(base_url, og["content"])
    # 2. An <img> whose attrs mention "logo".
    for img in soup.find_all("img"):
        attrs = " ".join(
            str(img.get(a, "")) for a in ("src", "alt", "class", "id")
        ).lower()
        if "logo" in attrs and img.get("src"):
            return urljoin(base_url, img["src"])
    # 3. Apple touch / favicon as a last resort.
    for rel in ("apple-touch-icon", "icon", "shortcut icon"):
        link = soup.find("link", rel=lambda v: v and rel in v)
        if link and link.get("href"):
            return urljoin(base_url, link["href"])
    return None


def _same_host(a: str, b: str) -> bool:
    return urlparse(a).netloc.replace("www.", "") == urlparse(b).netloc.replace("www.", "")


def _score(url: str) -> int:
    low = url.lower()
    return sum(2 for h in _PRIORITY_HINTS if h in low)


def crawl(website: str, max_pages: int = 12, timeout: int = 20) -> BrandContext:
    """Crawl `website`, returning a BrandContext with text + a logo URL."""
    website = website.rstrip("/")
    ctx = BrandContext(website=website)
    session = requests.Session()
    session.headers.update(_HEADERS)

    try:
        resp = session.get(website, timeout=timeout)
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise RuntimeError(
            f"Could not reach {website}: {exc}. "
            "Check the URL and that your network allows outbound HTTPS to it."
        ) from exc

    home = BeautifulSoup(resp.text, "html.parser")
    ctx.logo_url = _find_logo(home, website)

    # Collect candidate internal links from the homepage.
    candidates: list[str] = [website]
    seen = {website}
    for a in home.find_all("a", href=True):
        link = urljoin(website, a["href"].split("#")[0]).rstrip("/")
        if not link or link in seen:
            continue
        if not _same_host(link, website):
            continue
        if any(s in link.lower() for s in _SKIP_HINTS):
            continue
        seen.add(link)
        candidates.append(link)

    # Homepage first, then highest-scoring internal pages.
    ordered = [website] + sorted(
        [c for c in candidates if c != website], key=_score, reverse=True
    )

    for url in ordered[:max_pages]:
        try:
            r = url == website and resp or session.get(url, timeout=timeout)
            if r is not resp:
                r.raise_for_status()
            soup = BeautifulSoup(r.text, "html.parser")
            title = (soup.title.string.strip() if soup.title and soup.title.string
                     else urlparse(url).path or "Home")
            body = _clean_text(soup)
            if len(body) < 120:  # skip near-empty pages
                continue
            ctx.pages.append({"url": url, "title": title, "text": body[:8000]})
        except requests.RequestException:
            continue
        if url != website:
            time.sleep(0.4)  # be polite

    if not ctx.pages:
        raise RuntimeError(
            f"Reached {website} but extracted no readable content. "
            "The site may be fully JavaScript-rendered."
        )
    return ctx


def fetch_logo_bytes(logo_url: str, timeout: int = 20) -> tuple[bytes, str] | None:
    """Download the brand logo; returns (bytes, content_type) or None."""
    try:
        r = requests.get(logo_url, headers=_HEADERS, timeout=timeout)
        r.raise_for_status()
        return r.content, r.headers.get("Content-Type", "image/png")
    except requests.RequestException:
        return None
