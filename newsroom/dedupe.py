"""Remove duplicate stories: the same link shared by several feeds, or the same headline reworded slightly."""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .models import Story

TRACKING_PARAMS = re.compile(r"^(utm_|ref$|ref_src$|fbclid$|gclid$|mc_cid$|mc_eid$)")
TITLE_SIMILARITY = 0.88


def normalize_url(url: str) -> str:
    """Canonical form: https, no www, no tracking params, no fragment, no trailing slash."""
    parts = urlsplit(url.strip())
    host = parts.netloc.lower().removeprefix("www.")
    query = urlencode([(k, v) for k, v in parse_qsl(parts.query) if not TRACKING_PARAMS.match(k)])
    path = parts.path.rstrip("/") or "/"
    return urlunsplit(("https", host, path, query, ""))


def strip_tracking(url: str) -> str:
    """Remove tracking parameters only, keeping the link otherwise exactly as published."""
    parts = urlsplit(url)
    query = urlencode([(k, v) for k, v in parse_qsl(parts.query) if not TRACKING_PARAMS.match(k)])
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, parts.fragment))


def normalize_title(title: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", title.lower()).strip()


def similar(a: str, b: str, threshold: float = TITLE_SIMILARITY) -> bool:
    return SequenceMatcher(None, normalize_title(a), normalize_title(b)).ratio() >= threshold


def dedupe(stories: list[Story]) -> list[Story]:
    """Keep the first copy of each story, in input order.

    Same link → duplicate. Near-identical headline → duplicate only if it comes from a
    *different* feed: one feed never lists a story twice, but it often lists papers
    with similar titles ("X at Task A", "X at Task B") that are genuinely different.
    """
    seen_urls: set[str] = set()
    kept: list[Story] = []
    for story in stories:
        key = normalize_url(str(story.url))
        if key in seen_urls:
            continue
        if any(k.source != story.source and similar(story.title, k.title) for k in kept):
            continue
        seen_urls.add(key)
        kept.append(story)
    return kept
