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


def test_brief_writes_an_edition_and_a_run_log(tmp_path, rss_bytes, atom_bytes, capsys, monkeypatch):
    from fakes import NewsroomLLM, fake_reader

    (tmp_path / "config.yaml").write_text(CONFIG)
    f = _stories_file(tmp_path, rss_bytes, atom_bytes)
    monkeypatch.setattr(cli, "make_llm", lambda config: NewsroomLLM())
    monkeypatch.setattr(cli, "make_reader", lambda: fake_reader)

    out_dir, data_dir = tmp_path / "editions", tmp_path / "data"
    args = ["brief", "--input", str(f), "--out", str(out_dir), "--data", str(data_dir), "--config", str(tmp_path / "config.yaml")]
    assert cli.main(args) == 0

    edition = next(out_dir.glob("*.md")).read_text(encoding="utf-8")
    assert "A focused day in AI." in edition and "facts verified against their sources" in edition
    log = json.loads(next(data_dir.glob("run-*.json")).read_text(encoding="utf-8"))
    assert {"candidates", "picks", "sheets", "briefing", "seconds"} <= set(log)
    out = capsys.readouterr().out
    assert "Editor: choosing" in out and "Researcher:" in out and "Saved →" in out
    index = (out_dir / "README.md").read_text(encoding="utf-8")       # the archive page is updated too
    assert "A focused day in AI." in index and "Index →" in out


def test_index_command(tmp_path, capsys):
    (tmp_path / "config.yaml").write_text(CONFIG)
    eds = tmp_path / "editions"
    eds.mkdir()
    (eds / "2026-10-22.md").write_text("# AI briefing\n\n> A day.\n\n## 1. One\n", encoding="utf-8")
    assert cli.main(["index", "--out", str(eds), "--config", str(tmp_path / "config.yaml")]) == 0
    assert "Index of 1 editions" in capsys.readouterr().out
    assert "A day." in (eds / "README.md").read_text(encoding="utf-8")


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


def test_make_llm_builds_a_fallback_chain_and_skips_fallbacks_without_keys(monkeypatch, capsys):
    from newsroom.config import Config

    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    config = Config.model_validate({
        "topic": "AI", "feeds": [{"name": "x", "url": "https://x.example/rss"}],
        "llm": {"provider": "gemini", "model": "main", "reasoning_effort": "low",
                "fallbacks": [{"provider": "gemini", "model": "lite"}, {"provider": "groq", "model": "g"}]},
    })
    llm = cli.make_llm(config)
    assert [c.name for c in llm.clients] == ["gemini/main", "gemini/lite"]
    assert llm.clients[0].reasoning_effort == "low"
    assert "Skipping fallback groq/g" in capsys.readouterr().out
