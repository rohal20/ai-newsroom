from datetime import date

from newsroom.archive import list_editions, read_edition, render_index, write_index


def edition(day: str, one_line: str, stories: int, rewritten: int = 0, facts_only: int = 0) -> str:
    parts = [f"# AI briefing · {day}", "", f"> {one_line}", ""]
    for i in range(1, stories + 1):
        badge = "✓ critic approved"
        if i <= rewritten:
            badge = "✓ approved after 1 rewrite"
        elif i <= rewritten + facts_only:
            badge = "⚠ draft failed fact-check; showing verified facts only"
        parts += [f"## {i}. Story {i}", "", "Summary.", "", f"Source: [x](https://x) · <sub>3 facts · {badge}</sub>", ""]
    return "\n".join(parts)


def test_read_edition(tmp_path):
    p = tmp_path / "2026-10-22.md"
    p.write_text(edition("Thursday", "Walmart opens its doors to AI shoppers.", 5, rewritten=2, facts_only=1), encoding="utf-8")
    info = read_edition(p)
    assert info.day == date(2026, 10, 22) and info.stories == 5
    assert info.one_line == "Walmart opens its doors to AI shoppers."
    assert (info.rewritten, info.facts_only) == (2, 1)


def test_index_is_newest_first_and_skips_other_files(tmp_path):
    (tmp_path / "2026-10-19.md").write_text(edition("Mon", "First day.", 5), encoding="utf-8")
    (tmp_path / "2026-10-22.md").write_text(edition("Thu", "Agents | shopping.", 4, rewritten=1), encoding="utf-8")
    (tmp_path / "2026-10-21.md").write_text(edition("Wed", "Middle day.", 5), encoding="utf-8")
    (tmp_path / "README.md").write_text("old index", encoding="utf-8")
    (tmp_path / "notes.md").write_text("not an edition", encoding="utf-8")

    assert [e.file for e in list_editions(tmp_path)] == ["2026-10-22.md", "2026-10-21.md", "2026-10-19.md"]

    path = write_index(tmp_path, "AI")
    md = path.read_text(encoding="utf-8")
    assert path.name == "README.md" and md.startswith("# AI briefing archive")
    assert "**Latest:** [Thursday 22 October 2026](2026-10-22.md): Agents | shopping." in md
    assert "| [Thu 22 Oct 2026](2026-10-22.md) | Agents \\| shopping. | 4 | 1 rewritten |" in md   # pipes escaped in the table
    assert md.index("2026-10-22") < md.index("2026-10-21") < md.index("2026-10-19")
    assert "notes.md" not in md


def test_empty_archive(tmp_path):
    assert "No editions yet" in render_index([], "AI")
