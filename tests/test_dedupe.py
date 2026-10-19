from newsroom.dedupe import dedupe, normalize_url, similar, strip_tracking


def test_strip_tracking_keeps_the_rest_of_the_link():
    assert strip_tracking("http://www.site.com/a/?utm_source=rss&id=3&fbclid=x#part") == "http://www.site.com/a/?id=3#part"
from newsroom.models import Story
from newsroom.sources import parse_feed


def test_normalize_url_strips_noise():
    assert normalize_url("http://www.Example.com/a/b/?utm_source=x&id=7#top") == "https://example.com/a/b?id=7"
    assert normalize_url("https://example.com/") == "https://example.com/"


def test_similar_titles():
    assert similar("Open-weights model tops coding leaderboard", "Open-weights model tops the coding leaderboard!")
    assert not similar("MCP spec adds streaming tool calls", "Benchmark found leaking into training data")


def test_dedupe_across_feeds(rss_bytes, atom_bytes):
    stories = parse_feed(rss_bytes, "RSS") + parse_feed(atom_bytes, "Atom")
    unique = dedupe(stories)
    titles = [s.title for s in unique]
    assert len(stories) == 5
    assert len(unique) == 4  # the reworded open-weights story is dropped
    assert titles[0] == "Open-weights model tops coding leaderboard"  # first copy wins
    assert "Benchmark found leaking into training data" in titles


def test_dedupe_same_url_different_tracking():
    a = Story(title="A story", url="https://site.com/x?utm_campaign=1", source="one")
    b = Story(title="Completely different headline", url="https://www.site.com/x/", source="two")
    assert dedupe([a, b]) == [a]
