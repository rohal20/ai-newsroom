"""The editions index: editions/README.md lists every briefing, newest first.

GitHub shows a folder's README.md when you open the folder, so this page is
what visitors see when they click on editions/ in the repo.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from pydantic import BaseModel

_NAME_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})\.md$")


class EditionInfo(BaseModel):
    day: date
    file: str
    one_line: str
    stories: int
    rewritten: int = 0
    facts_only: int = 0


def read_edition(path: Path) -> EditionInfo | None:
    m = _NAME_RE.match(path.name)
    if not m:
        return None
    text = path.read_text(encoding="utf-8")
    one_line = next((line[2:].strip() for line in text.splitlines() if line.startswith("> ")), "")
    return EditionInfo(
        day=date(int(m[1]), int(m[2]), int(m[3])),
        file=path.name,
        one_line=one_line,
        stories=len(re.findall(r"^## \d+\. ", text, re.MULTILINE)),
        rewritten=text.count("✓ approved after"),
        facts_only=text.count("⚠ draft failed fact-check"),
    )


def list_editions(editions_dir: str | Path) -> list[EditionInfo]:
    infos = [read_edition(p) for p in Path(editions_dir).glob("*.md")]
    return sorted((i for i in infos if i), key=lambda i: i.day, reverse=True)


def _cell(text: str) -> str:
    return text.replace("|", "\\|")


def render_index(editions: list[EditionInfo], topic: str) -> str:
    lines = [
        f"# {topic} briefing archive",
        "",
        "Every morning's briefing, newest first. Each one is written by the newsroom's agents "
        "and checked by the critic before it is published.",
        "",
    ]
    if not editions:
        return "\n".join(lines + ["No editions yet. Run `python -m newsroom brief` to write the first one.", ""])
    latest = editions[0]
    lines += [f"**Latest:** [{latest.day:%A %d %B %Y}]({latest.file}): {latest.one_line}", "",
              "| Date | The day in one line | Stories | Critic |", "|---|---|---|---|"]
    for e in editions:
        notes = []
        if e.rewritten:
            notes.append(f"{e.rewritten} rewritten")
        if e.facts_only:
            notes.append(f"{e.facts_only} facts only")
        critic = ", ".join(notes) or "all approved"
        lines.append(f"| [{e.day:%a %d %b %Y}]({e.file}) | {_cell(e.one_line)} | {e.stories} | {critic} |")
    return "\n".join(lines) + "\n"


def write_index(editions_dir: str | Path, topic: str) -> Path:
    path = Path(editions_dir) / "README.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_index(list_editions(editions_dir), topic), encoding="utf-8")
    return path
