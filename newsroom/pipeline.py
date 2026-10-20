"""Runs the newsroom: editor → researcher → writer, and records what each step did."""

from __future__ import annotations

import logging
import time
from typing import Callable

from pydantic import BaseModel

from .agents import editor, researcher, writer
from .agents.editor import EditorChoice
from .agents.researcher import FactSheet
from .agents.writer import Briefing
from .article import read_article
from .briefing import select_candidates
from .config import Config
from .llm import LLM
from .models import Story

log = logging.getLogger(__name__)

ArticleReader = Callable[[Story], tuple[str, str]]


class RunLog(BaseModel):
    """Everything needed to see why the briefing came out the way it did (saved next to the stories)."""

    candidates: int
    picks: list[EditorChoice]
    sheets: list[FactSheet]
    briefing: Briefing
    seconds: dict[str, float]


def run(llm: LLM, stories: list[Story], config: Config, read: ArticleReader = read_article,
        say: Callable[[str], None] = print) -> RunLog:
    b, r = config.briefing, config.research
    timings: dict[str, float] = {}

    candidates = select_candidates(stories, max_total=b.max_candidates, max_per_source=b.max_per_source)
    say(f"Editor: choosing {min(b.stories, len(candidates))} of {len(candidates)} candidates "
        f"from {len({s.source for s in candidates})} sources")
    t = time.perf_counter()
    picks = editor.run(llm, candidates, config.topic, n=b.stories, max_papers=b.max_papers)
    timings["editor"] = time.perf_counter() - t
    for p in picks:
        say(f"  • {p.story.title}  ({p.story.display_source})")

    t = time.perf_counter()
    sheets: list[FactSheet] = []
    for p in picks:
        text, where = read(p.story)
        sheet = researcher.research(llm, p.story, text, where, r.max_article_chars, r.min_facts)
        sheets.append(sheet)
        say(f"Researcher: {len(sheet.facts)} facts from the {where}"
            + (f", {len(sheet.dropped)} dropped" if sheet.dropped else "") + f"  [{p.story.domain}]")
    timings["researcher"] = time.perf_counter() - t

    t = time.perf_counter()
    briefing = writer.run(llm, sheets, config.topic)
    timings["writer"] = time.perf_counter() - t
    say(f"Writer: {len(briefing.items)} stories written")

    kept = {i.story_id for i in briefing.items}
    return RunLog(
        candidates=len(candidates),
        picks=picks,
        sheets=[s for s in sheets if s.story.id in kept],
        briefing=briefing,
        seconds={k: round(v, 1) for k, v in timings.items()},
    )
