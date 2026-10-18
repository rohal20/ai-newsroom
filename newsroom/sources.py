"""Sources agent (v0.1: no LLM yet): fetch RSS/Atom feeds and turn them into Story objects."""

from __future__ import annotations

import html
import logging
import re
from calendar import timegm
from datetime import datetime, timedelta, timezone

import feedparser
import requests
from pydantic import ValidationError

from .config import Config, Feed
from .models import Story

log = logging.getLogger(__name__)

USER_AGENT = "ai-newsroom/0.1 (+https://github.com/your-username/ai-newsroom)"
TIMEOUT_SECONDS = 15
_TAG_RE = re.compile(r"<[^>]+>")
_SPACE_RE = re.compile(r"\s+")


def clean_text(raw: str, limit: int = 600) -> str:
    """Strip HTML tags and entities, collapse whitespace, and cap the length."""
    text = _SPACE_RE.sub(" ", html.unescape(_TAG_RE.sub(" ", raw or ""))).strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _published(entry) -> datetime | None:
    for key in ("published_parsed", "updated_parsed"):
        value = entry.get(key)
        if value:
            return datetime.fromtimestamp(timegm(value), tz=timezone.utc)
    return None


def parse_feed(content: bytes | str, source: str) -> list[Story]:
    """Parse raw feed XML into stories. Pure function, so it is easy to test."""
    parsed = feedparser.parse(content)
    stories: list[Story] = []
    for entry in parsed.entries:
        try:
            stories.append(
                Story(
                    title=clean_text(entry.get("title", ""), limit=300),
                    url=entry.get("link", ""),
                    source=source,
                    published=_published(entry),
                    summary=clean_text(entry.get("summary", "")),
                )
            )
        except ValidationError:
            log.debug("Skipping malformed entry in %s: %r", source, entry.get("title"))
    return stories


def fetch_feed(feed: Feed, session: requests.Session | None = None) -> list[Story]:
    """Download one feed. Network errors are logged and return an empty list."""
    http = session or requests.Session()
    try:
        resp = http.get(str(feed.url), headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT_SECONDS)
        resp.raise_for_status()
    except requests.RequestException as exc:
        log.warning("Could not fetch %s: %s", feed.name, exc)
        return []
    return parse_feed(resp.content, feed.name)


def matches_keywords(story: Story, keywords: list[str]) -> bool:
    """Whole-word, case-insensitive keyword match on title and summary."""
    text = f"{story.title} {story.summary}"
    return any(re.search(rf"\b{re.escape(k)}\b", text, re.IGNORECASE) for k in keywords)


def is_recent(story: Story, max_age_hours: int, now: datetime | None = None) -> bool:
    """Stories without a date are kept; dated ones must be newer than the cutoff."""
    if story.published is None:
        return True
    now = now or datetime.now(timezone.utc)
    return story.published >= now - timedelta(hours=max_age_hours)


def collect(config: Config, session: requests.Session | None = None) -> tuple[list[Story], dict[str, int]]:
    """Fetch every feed, apply keyword and age filters, and report per-feed counts."""
    http = session or requests.Session()
    stories: list[Story] = []
    counts: dict[str, int] = {}
    for feed in config.feeds:
        items = fetch_feed(feed, http)
        if feed.filter:
            items = [s for s in items if matches_keywords(s, config.keywords)]
        items = [s for s in items if is_recent(s, config.max_age_hours)]
        counts[feed.name] = len(items)
        log.info("%-18s %3d stories", feed.name, len(items))
        stories.extend(items)
    return stories, counts
