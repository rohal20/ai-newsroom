import json
from pathlib import Path

import pytest

from newsroom import __main__ as cli


@pytest.mark.parametrize("argv", [["fetch", "-v"], ["-v", "fetch"], ["fetch"]])
def test_fetch_writes_unique_stories(tmp_path, rss_bytes, atom_bytes, monkeypatch, argv):
    from newsroom.sources import parse_feed

    fake = parse_feed(rss_bytes, "RSS") + parse_feed(atom_bytes, "Atom")
    monkeypatch.setattr(cli, "collect", lambda config: (fake, {"RSS": 4, "Atom": 2}))

    config = tmp_path / "config.yaml"
    config.write_text("topic: AI\nfeeds:\n  - {name: RSS, url: 'https://e.com/rss'}\n")
    out = tmp_path / "data"

    assert cli.main([*argv, "--config", str(config), "--out", str(out)]) == 0

    files = list(Path(out).glob("stories-*.json"))
    assert len(files) == 1
    saved = json.loads(files[0].read_text())
    assert len(saved) == 4
    assert {"id", "title", "url", "source", "published", "summary"} <= set(saved[0])
