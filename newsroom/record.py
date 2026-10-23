"""A compact, public record of each run, saved next to the edition as editions/YYYY-MM-DD.json.

The website reads these, so every number on the site comes from a real run.
Unlike the full run log in data/, this file is committed to the repo.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

from pydantic import BaseModel

from .agents.critic import Issue
from .pipeline import RunLog


class FetchStats(BaseModel):
    fetched: int
    unique: int
    feeds_ok: int
    feeds_total: int
    per_feed: dict[str, int] = {}
    seconds: float = 0


class FactRecord(BaseModel):
    claim: str
    quote: str


class StoryRecord(BaseModel):
    id: str
    title: str
    summary: str
    why_it_matters: str
    url: str
    source: str
    domain: str
    is_paper: bool
    text_source: str
    editor_reason: str = ""
    status: str
    rewrites: int = 0
    sentences_checked: int = 0
    facts: list[FactRecord]
    dropped_facts: int = 0
    first_issues: list[Issue] = []


class EditionRecord(BaseModel):
    version: int = 1
    day: date
    started: datetime | None = None
    topic: str
    model: str
    one_line: str
    fetch: FetchStats | None = None
    candidates: int
    seconds: dict[str, float]
    critic_rounds: int = 0
    stories: list[StoryRecord]

    @property
    def facts_verified(self) -> int:
        return sum(len(s.facts) for s in self.stories)

    @property
    def rewritten(self) -> int:
        return sum(1 for s in self.stories if s.status == "rewritten")

    @property
    def issues_caught(self) -> int:
        return sum(len(s.first_issues) for s in self.stories)

    @property
    def total_seconds(self) -> float:
        fetch = self.fetch.seconds if self.fetch else 0
        return round(sum(self.seconds.values()) + fetch, 1)


def make_record(run: RunLog, topic: str, model: str, day: date, fetch: FetchStats | None,
                started: datetime | None = None) -> EditionRecord:
    sheets = {s.story.id: s for s in run.sheets}
    reasons = {p.story.id: p.reason for p in run.picks}
    results = {r.story_id: r for r in run.results}
    first = run.critic_rounds[0].issues if run.critic_rounds else {}
    stories = []
    for item in run.briefing.items:
        sheet = sheets[item.story_id]
        res = results.get(item.story_id)
        stories.append(StoryRecord(
            id=item.story_id, title=item.title, summary=item.summary, why_it_matters=item.why_it_matters,
            url=str(sheet.story.url), source=sheet.story.display_source, domain=sheet.story.domain,
            is_paper=sheet.story.is_paper, text_source=sheet.text_source, editor_reason=reasons.get(item.story_id, ""),
            status=res.status if res else "unchecked", rewrites=res.rewrites if res else 0,
            sentences_checked=res.sentences_checked if res else 0,
            facts=[FactRecord(claim=f.claim, quote=f.quote) for f in sheet.facts],
            dropped_facts=len(sheet.dropped), first_issues=first.get(item.story_id, []),
        ))
    return EditionRecord(day=day, started=started, topic=topic, model=model, one_line=run.briefing.one_line, fetch=fetch,
                         candidates=run.candidates, seconds=run.seconds, critic_rounds=len(run.critic_rounds),
                         stories=stories)


def save_record(record: EditionRecord, out_dir: str | Path = "editions") -> Path:
    path = Path(out_dir) / f"{record.day.isoformat()}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(record.model_dump_json(indent=2), encoding="utf-8")
    return path


def load_record(path: str | Path) -> EditionRecord:
    return EditionRecord.model_validate(json.loads(Path(path).read_text(encoding="utf-8")))
