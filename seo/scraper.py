"""
Source scraper — builds the grounding context Claude is allowed to draw on.

Two tiers of sources:
  • PRIMARY: the brand's own properties (hexaclimate.com, the brand's LinkedIn,
    etc.). Treated as the only source of truth for facts ABOUT the brand —
    services, projects, team, claims. The brand website is deep-crawled;
    other primary URLs are fetched as single pages.
  • SECONDARY: trusted industry references (e.g. CEA, MNRE, IEEFA, MERCOM India).
    Used by Claude for general industry context — figures, trends, regulation
    descriptions — but NOT for claims about the brand.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

_PRIORITY_HINTS = (
    "about", "what-we-do", "solution", "service", "product", "platform",
    "project", "technology", "sustainab", "decarbon", "energy", "esg",
    "team", "company", "mission",
)

_SKIP_HINTS = (
    "privacy", "terms", "cookie", "login", "career",
    "policy", "legal", "/blog", "/news", ".pdf", ".jpg", ".png",
)


@dataclass
class SourcePage:
    url: str
    title: str
    text: str


@dataclass
class GroundingContext:
    """Holds primary + secondary source text and the brand logo URL."""
    brand_website: str
    primary: list[SourcePage] = field(default_factory=list)
    secondary: list[SourcePage] = field(default_factory=list)
    logo_url: str | None = None
    notes: list[str] = field(default_factory=list)   # warnings/skips for UI

    @property
    def char_count(self) -> int:
        return sum(len(p.text) for p in self.primary) + \
               sum(len(p.text) for p in self.secondary)

    def primary_text(self) -> str:
        if not self.primary:
            return "(no primary sources captured)"
        return "\n".join(
            f"\n===== PRIMARY: {p.title} ({p.url}) =====\n{p.text}"
            for p in self.primary
        )

    def secondary_text(self) -> str:
        if not self.secondary:
            return "(no secondary sources provided)"
        return "\n".join(
            f"\n===== SECONDARY: {p.title} ({p.url}) =====\n{p.text}"
            for p in self.secondary
        )


def _clean_text(soup: BeautifulSoup) -> str:
    for tag in soup(["script", "style", "noscript", "svg", "header", "footer", "nav"]):
        tag.decompose()
    text = soup.get_text(separator="\n")
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    text = "\n".join(lines)
    return re.sub(r"\n{3,}", "\n\n", text)


def _find_logo(soup: BeautifulSoup, base_url: str) -> str | None:
    og = soup.find("meta", property="og:image")
    if og and og.get("content"):
        return urljoin(base_url, og["content"])
    for img in soup.find_all("img"):
        attrs = " ".join(str(img.get(a, "")) for a in ("src", "alt", "class", "id")).lower()
        if "logo" in attrs and img.get("src"):
            return urljoin(base_url, img["src"])
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


def _fetch_page(url: str, session: requests.Session, timeout: int = 20) -> SourcePage | None:
    try:
        r = session.get(url, timeout=timeout)
        r.raise_for_status()
    except requests.RequestException:
        return None
    soup = BeautifulSoup(r.text, "html.parser")
    title = (soup.title.string.strip()
             if soup.title and soup.title.string
             else urlparse(url).path or url)
    body = _clean_text(soup)
    if len(body) < 80:
        return None
    return SourcePage(url=url, title=title[:140], text=body[:8000])


def _crawl_site(website: str, session: requests.Session, max_pages: int) -> tuple[list[SourcePage], str | None]:
    """Deep-crawl a brand site: homepage + best internal pages. Returns (pages, logo_url)."""
    website = website.rstrip("/")
    try:
        resp = session.get(website, timeout=20)
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise RuntimeError(f"Could not reach {website}: {exc}") from exc

    home_soup = BeautifulSoup(resp.text, "html.parser")
    logo_url = _find_logo(home_soup, website)

    candidates: list[str] = []
    seen = {website}
    for a in home_soup.find_all("a", href=True):
        link = urljoin(website, a["href"].split("#")[0]).rstrip("/")
        if not link or link in seen or not _same_host(link, website):
            continue
        if any(s in link.lower() for s in _SKIP_HINTS):
            continue
        seen.add(link)
        candidates.append(link)

    ordered = [website] + sorted(candidates, key=_score, reverse=True)
    pages: list[SourcePage] = []
    for url in ordered[:max_pages]:
        if url == website:
            soup = home_soup
            title = (soup.title.string.strip() if soup.title and soup.title.string
                     else "Home")
            body = _clean_text(soup)
            if len(body) >= 120:
                pages.append(SourcePage(url=url, title=title[:140], text=body[:8000]))
        else:
            page = _fetch_page(url, session)
            if page:
                pages.append(page)
            time.sleep(0.3)
    return pages, logo_url


def build_context(
    brand_website: str,
    primary_urls: list[str],
    secondary_urls: list[str],
    max_pages: int = 12,
) -> GroundingContext:
    """
    Build a GroundingContext from a brand website (deep-crawled) plus
    additional primary URLs (single-page fetch) and secondary URLs.
    """
    session = requests.Session()
    session.headers.update(_HEADERS)
    ctx = GroundingContext(brand_website=brand_website.rstrip("/"))

    # 1. Deep-crawl the brand site for primary context.
    try:
        primary, logo = _crawl_site(brand_website, session, max_pages)
        ctx.primary.extend(primary)
        ctx.logo_url = logo
    except RuntimeError as exc:
        ctx.notes.append(f"Brand site crawl failed: {exc}")

    # 2. Fetch any additional primary URLs as single pages.
    for url in primary_urls:
        url = url.strip()
        if not url or url == brand_website.rstrip("/"):
            continue
        page = _fetch_page(url, session)
        if page:
            ctx.primary.append(page)
        else:
            ctx.notes.append(f"Primary source skipped (couldn't fetch): {url}")
        time.sleep(0.3)

    # 3. Fetch secondary URLs.
    for url in secondary_urls:
        url = url.strip()
        if not url:
            continue
        page = _fetch_page(url, session)
        if page:
            ctx.secondary.append(page)
        else:
            ctx.notes.append(f"Secondary source skipped (couldn't fetch): {url}")
        time.sleep(0.3)

    if not ctx.primary and not ctx.secondary:
        raise RuntimeError(
            "No sources could be fetched. Check that the brand website "
            "and your URLs are reachable from your network."
        )
    return ctx


def fetch_logo_bytes(logo_url: str, timeout: int = 20) -> tuple[bytes, str] | None:
    try:
        r = requests.get(logo_url, headers=_HEADERS, timeout=timeout)
        r.raise_for_status()
        return r.content, r.headers.get("Content-Type", "image/png")
    except requests.RequestException:
        return None
