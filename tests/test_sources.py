from datetime import datetime, timezone

import requests

from newsroom.config import Config, Feed
from newsroom.models import Story
from newsroom.sources import clean_text, collect, is_recent, matches_keywords, parse_feed

NOW = datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc)


def test_parse_rss_skips_untitled_entries(rss_bytes):
    stories = parse_feed(rss_bytes, "Sample")
    assert [s.title for s in stories] == [
        "Open-weights model tops coding leaderboard",
        "MCP spec adds streaming tool calls",
        "Local bakery wins regional award",
    ]
    assert all(s.source == "Sample" for s in stories)


def test_parse_rss_cleans_html_and_reads_dates(rss_bytes):
    first = parse_feed(rss_bytes, "Sample")[0]
    assert first.summary == "The release beats several closed models & runs on one GPU."
    assert first.published == datetime(2026, 10, 17, 8, 0, tzinfo=timezone.utc)


def test_parse_atom(atom_bytes):
    stories = parse_feed(atom_bytes, "Lab")
    assert len(stories) == 2
    assert str(stories[1].url) == "https://lab.example.org/posts/benchmark-leak"


def test_clean_text_removes_arxiv_boilerplate():
    raw = "arXiv:2610.22515v1 Announce Type: new Abstract: We describe our system for subtask 1b."
    assert clean_text(raw) == "We describe our system for subtask 1b."


def test_aggregator_stories_are_marked(rss_bytes):
    stories = parse_feed(rss_bytes, "Hacker News", aggregated=True)
    assert all(s.aggregated for s in stories)
    assert stories[0].display_source == "example.com via Hacker News"


def test_per_feed_age_limit(rss_bytes, monkeypatch):
    seen = []
    monkeypatch.setattr("newsroom.sources.is_recent", lambda s, h: seen.append(h) or True)
    config = Config(topic="AI", max_age_hours=36, feeds=[
        Feed(name="Blog", url="https://blog.example/rss", max_age_hours=96),
        Feed(name="News", url="https://news.example/rss"),
    ])
    session = FakeSession({"https://blog.example/rss": FakeResponse(rss_bytes), "https://news.example/rss": FakeResponse(rss_bytes)})
    collect(config, session)  # type: ignore[arg-type]
    assert set(seen) == {96, 36}


def test_clean_text_truncates():
    assert clean_text("word " * 200, limit=20).endswith("…")
    assert len(clean_text("word " * 200, limit=20)) <= 20


def test_keyword_filter_is_whole_word():
    ai = Story(title="New AI model released", url="https://e.com/a", source="x")
    said = Story(title="She said the plan was fine", url="https://e.com/b", source="x")
    assert matches_keywords(ai, ["AI"])
    assert not matches_keywords(said, ["AI"])  # "said" contains "ai" but is not a match


def test_is_recent():
    old = Story(title="Old", url="https://e.com/o", source="x", published=datetime(2026, 10, 10, tzinfo=timezone.utc))
    new = Story(title="New", url="https://e.com/n", source="x", published=datetime(2026, 10, 17, tzinfo=timezone.utc))
    undated = Story(title="Undated", url="https://e.com/u", source="x")
    assert not is_recent(old, 36, now=NOW)
    assert is_recent(new, 36, now=NOW)
    assert is_recent(undated, 36, now=NOW)


class FakeResponse:
    def __init__(self, content: bytes, status: int = 200):
        self.content, self.status_code = content, status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}")


class FakeSession:
    """Serves fixture files instead of hitting the network."""

    def __init__(self, routes: dict[str, FakeResponse]):
        self.routes = routes

    def get(self, url, **kwargs):
        if url not in self.routes:
            raise requests.ConnectionError("offline")
        return self.routes[url]


def test_collect_filters_and_survives_broken_feeds(rss_bytes, atom_bytes, monkeypatch):
    monkeypatch.setattr("newsroom.sources.is_recent", lambda s, h: True)
    config = Config(
        topic="AI",
        keywords=["machine learning", "model", "MCP"],
        feeds=[
            Feed(name="General", url="https://general.example/rss", filter=True),
            Feed(name="Lab", url="https://lab.example/atom"),
            Feed(name="Down", url="https://down.example/rss"),
        ],
    )
    session = FakeSession({
        "https://general.example/rss": FakeResponse(rss_bytes),
        "https://lab.example/atom": FakeResponse(atom_bytes),
    })
    stories, counts = collect(config, session)  # type: ignore[arg-type]
    # The bakery story mentions "machine learning", so the whole-word filter keeps it; all 3 match.
    assert counts == {"General": 3, "Lab": 2, "Down": 0}
    assert len(stories) == 5
