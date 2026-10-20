"""Download a story's web page and extract the readable article text."""

from __future__ import annotations

import logging

import requests
import trafilatura

from .models import Story
from .sources import USER_AGENT

log = logging.getLogger(__name__)

# The feed already carries the full abstract for these, and their servers rate-limit scrapers.
SKIP_DOMAINS = {"arxiv.org"}


def extract_text(html: str) -> str | None:
    """Main article text from an HTML page, without menus, ads or comments."""
    return trafilatura.extract(html, include_comments=False, include_tables=False, favor_precision=True)


def read_article(story: Story, session: requests.Session | None = None, timeout: float = 15) -> tuple[str, str]:
    """Return (text, where_it_came_from). Falls back to the feed text if the page can't be read."""
    if story.domain in SKIP_DOMAINS:
        return story.summary, "feed (abstract)"
    http = session or requests.Session()
    try:
        resp = http.get(str(story.url), headers={"User-Agent": USER_AGENT}, timeout=timeout)
        resp.raise_for_status()
        text = extract_text(resp.text)
    except requests.RequestException as exc:
        log.warning("Could not read %s: %s", story.url, exc)
        text = None
    if text and len(text) > len(story.summary):
        return text, "article"
    return story.summary, "feed"
