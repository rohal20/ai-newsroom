import json
from pathlib import Path

import pytest

from newsroom import __main__ as cli

CONFIG = "topic: AI\nfeeds:\n  - {name: RSS, url: 'https://e.com/rss'}\n"


def _stories_file(tmp_path, rss_bytes, atom_bytes) -> Path:
    from newsroom.sources import parse_feed

    stories = parse_feed(rss_bytes, "RSS") + parse_feed(atom_bytes, "Atom")
    f = tmp_path / "stories.json"
    f.write_text(json.dumps([s.model_dump(mode="json") for s in stories]))
    return f


def test_brief_dry_run_needs_no_key(tmp_path, rss_bytes, atom_bytes, capsys, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    (tmp_path / "config.yaml").write_text(CONFIG)
    f = _stories_file(tmp_path, rss_bytes, atom_bytes)
    assert cli.main(["brief", "--dry-run", "--input", str(f), "--config", str(tmp_path / "config.yaml")]) == 0
    out = capsys.readouterr().out
    assert "dry run" in out and "Open-weights model tops coding leaderboard" in out


def test_brief_writes_an_edition(tmp_path, rss_bytes, atom_bytes, capsys, monkeypatch):
    (tmp_path / "config.yaml").write_text(CONFIG)
    f = _stories_file(tmp_path, rss_bytes, atom_bytes)
    ids = [s["id"] for s in json.loads(f.read_text())][:2]

    class Fake:
        def complete(self, system, user):
            return json.dumps({"one_line": "Open models had a good day.", "items": [
                {"story_id": i, "title": f"Title {n}", "summary": "S.", "why_it_matters": "W."} for n, i in enumerate(ids)]})

    monkeypatch.setattr(cli, "make_llm", lambda config: Fake())
    out_dir = tmp_path / "editions"
    assert cli.main(["brief", "--input", str(f), "--out", str(out_dir), "--config", str(tmp_path / "config.yaml")]) == 0
    files = list(out_dir.glob("*.md"))
    assert len(files) == 1 and "Open models had a good day." in files[0].read_text(encoding="utf-8")
    assert "Saved →" in capsys.readouterr().out


def test_brief_reports_missing_key_cleanly(tmp_path, rss_bytes, atom_bytes, capsys, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(cli, "_load_dotenv", lambda: None)
    (tmp_path / "config.yaml").write_text(CONFIG)
    f = _stories_file(tmp_path, rss_bytes, atom_bytes)
    assert cli.main(["brief", "--input", str(f), "--config", str(tmp_path / "config.yaml")]) == 1
    assert "GEMINI_API_KEY" in capsys.readouterr().out


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
