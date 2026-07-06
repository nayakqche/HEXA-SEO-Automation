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

import os
import re
import time
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Ch-Ua": '"Chromium";v="122", "Not(A:Brand";v="24", "Google Chrome";v="122"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "DNT": "1",
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
    via: str = "direct"   # direct | reader proxy | archive.org snapshot


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


def _page_from_html(url: str, html: str, via: str) -> SourcePage | None:
    soup = BeautifulSoup(html, "html.parser")
    # Strip the Wayback Machine toolbar if present so it doesn't pollute text.
    for el in soup.find_all(id=re.compile(r"^wm-ipp")):
        el.decompose()
    title = (soup.title.string.strip()
             if soup.title and soup.title.string
             else urlparse(url).path or url)
    body = _clean_text(soup)
    if len(body) < 80:
        return None
    return SourcePage(url=url, title=title[:140], text=body[:16000], via=via)


def _fetch_direct(url: str, session: requests.Session, timeout: int) -> SourcePage | None:
    # Two attempts: many gov/portal sites are slow or rate-limit the first hit.
    for attempt in range(2):
        try:
            r = session.get(url, timeout=timeout, allow_redirects=True)
            r.raise_for_status()
            return _page_from_html(url, r.text, "direct")
        except requests.RequestException:
            if attempt == 0:
                time.sleep(1.0)
    return None


def _fetch_via_reader(url: str, timeout: int = 45) -> SourcePage | None:
    """
    Fallback #1: Jina Reader (r.jina.ai) — a free public reader proxy that
    fetches the page from its own infrastructure and renders JavaScript.
    Gets through most datacenter-IP blocks AND fixes JS-only sites.
    """
    headers = {"Accept": "text/plain", "User-Agent": _HEADERS["User-Agent"]}
    # Optional: a free key from https://jina.ai/reader raises the rate limit
    # from ~20/min (per IP) to 200/min. Works fine without one.
    jina_key = os.getenv("JINA_API_KEY")
    if jina_key:
        headers["Authorization"] = f"Bearer {jina_key}"
    try:
        r = requests.get(
            "https://r.jina.ai/" + url,
            headers=headers,
            timeout=timeout,
        )
        if r.status_code != 200:
            return None
        text = r.text.strip()
        if len(text) < 120:
            return None
        # Jina prefixes output with "Title: …" / "URL Source: …" lines.
        title = urlparse(url).netloc
        m = re.match(r"Title:\s*(.+)", text)
        if m:
            title = m.group(1).strip()
        return SourcePage(url=url, title=title[:140], text=text[:16000],
                          via="reader proxy")
    except requests.RequestException:
        return None


def _fetch_via_wayback(url: str, timeout: int = 30) -> SourcePage | None:
    """
    Fallback #2: latest Wayback Machine snapshot. Gov portals (CEA, POSOCO,
    MNRE) are archived frequently, and archive.org welcomes automated access.
    """
    try:
        avail = requests.get(
            "https://archive.org/wayback/available",
            params={"url": url},
            headers={"User-Agent": _HEADERS["User-Agent"]},
            timeout=20,
        ).json()
        snap = (avail.get("archived_snapshots") or {}).get("closest") or {}
        snap_url = snap.get("url")
        if not snap.get("available") or not snap_url:
            return None
        snap_url = snap_url.replace("http://", "https://", 1)
        r = requests.get(snap_url, headers=_HEADERS, timeout=timeout)
        r.raise_for_status()
        return _page_from_html(url, r.text, "archive.org snapshot")
    except (requests.RequestException, ValueError):
        return None


def _fetch_page(url: str, session: requests.Session, timeout: int = 25) -> SourcePage | None:
    """Fetch one page, falling back through reader proxy → archive snapshot."""
    return (_fetch_direct(url, session, timeout)
            or _fetch_via_reader(url)
            or _fetch_via_wayback(url))


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

    # 1. Deep-crawl the brand site for primary context. If the direct crawl
    #    fails entirely, still try to capture the homepage via the fallbacks.
    try:
        primary, logo = _crawl_site(brand_website, session, max_pages)
        ctx.primary.extend(primary)
        ctx.logo_url = logo
    except RuntimeError as exc:
        home = _fetch_via_reader(brand_website) or _fetch_via_wayback(brand_website)
        if home:
            ctx.primary.append(home)
            ctx.notes.append(
                f"Brand site direct crawl failed; captured homepage via {home.via}.")
        else:
            ctx.notes.append(f"Brand site crawl failed: {exc}")

    # 2. Fetch any additional primary URLs as single pages.
    for url in primary_urls:
        url = url.strip()
        if not url or url == brand_website.rstrip("/"):
            continue
        page = _fetch_page(url, session)
        if page:
            ctx.primary.append(page)
            if page.via != "direct":
                ctx.notes.append(f"Primary source fetched via {page.via}: {url}")
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
            if page.via != "direct":
                ctx.notes.append(f"Secondary source fetched via {page.via}: {url}")
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
