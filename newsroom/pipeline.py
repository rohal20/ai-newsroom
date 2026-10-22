"""Runs the newsroom: editor → researcher → writer ⇄ critic, and records what each step did."""

from __future__ import annotations

import logging
import time
from typing import Callable, Literal

from pydantic import BaseModel

from .agents import critic, editor, researcher, writer
from .agents.critic import Issue
from .agents.editor import EditorChoice
from .agents.researcher import FactSheet
from .agents.writer import Briefing, BriefItem
from .article import read_article
from .config import Config
from .llm import LLM, LLMError
from .briefing import select_candidates
from .models import Story

log = logging.getLogger(__name__)

ArticleReader = Callable[[Story], tuple[str, str]]
Status = Literal["approved", "rewritten", "facts-only", "numbers-only", "unchecked"]


class CriticRound(BaseModel):
    round: int
    ai_checked: bool
    issues: dict[str, list[Issue]]


class StoryResult(BaseModel):
    story_id: str
    status: Status
    rewrites: int = 0
    sentences_checked: int = 0


class RunLog(BaseModel):
    """Everything needed to see why the briefing came out the way it did (saved next to the stories)."""

    candidates: int
    picks: list[EditorChoice]
    sheets: list[FactSheet]
    first_drafts: list[BriefItem] = []
    critic_rounds: list[CriticRound] = []
    results: list[StoryResult] = []
    briefing: Briefing
    seconds: dict[str, float]


def check_and_revise(llm: LLM, briefing: Briefing, sheets: list[FactSheet], topic: str, max_rewrites: int,
                     say: Callable[[str], None]) -> tuple[Briefing, list[CriticRound], list[StoryResult]]:
    """Critic reviews every draft; flagged stories go back to the writer until clean or out of rewrites."""
    drafts = {i.story_id: i for i in briefing.items}
    rewrites = {sid: 0 for sid in drafts}
    sentences = {sid: critic.sentences_in(item) for sid, item in drafts.items()}
    status: dict[str, Status] = {}
    rounds: list[CriticRound] = []
    pending = list(drafts)                       # stories still waiting for a clean review

    for rnd in range(max_rewrites + 1):
        to_check = [drafts[sid] for sid in pending]
        result = critic.review(llm, to_check, sheets)
        rounds.append(CriticRound(round=rnd + 1, ai_checked=result.ai_checked, issues=result.issues))
        flagged = {sid: issues for sid, issues in result.issues.items() if issues}
        n_issues = sum(len(v) for v in flagged.values())
        say(f"Critic (round {rnd + 1}): {len(to_check) - len(flagged)} of {len(to_check)} approved"
            + (f", {n_issues} problem(s) in {len(flagged)} stor(ies)" if flagged else "")
            + ("" if result.ai_checked else " (AI critic unavailable: numbers checked only)"))
        for sid, issues in flagged.items():
            for issue in issues:
                say(f"    ✗ \"{issue.text}\": {issue.problem}")

        for sid in pending:
            if sid not in flagged:
                if not result.ai_checked:
                    status[sid] = "numbers-only"
                else:
                    status[sid] = "rewritten" if rewrites[sid] else "approved"
        pending = list(flagged)
        if not pending:
            break
        if rnd == max_rewrites:
            break
        try:
            fixed = writer.revise(llm, [drafts[sid] for sid in pending], sheets, flagged, topic)
        except LLMError as exc:
            say(f"Writer could not revise ({exc}); using verified facts for flagged stories")
            break
        for item in fixed:
            drafts[item.story_id] = item
            rewrites[item.story_id] += 1
            sentences[item.story_id] += critic.sentences_in(item)
        say(f"Writer: rewrote {len(fixed)} stor(ies)")

    by_id = {s.story.id: s for s in sheets}
    for sid in pending:  # still failing after the last rewrite: publish verified facts only
        drafts[sid] = writer.facts_only(by_id[sid])
        status[sid] = "facts-only"
        say(f"Critic: story {sid} still had problems; publishing its verified facts only")

    items = [drafts[i.story_id] for i in briefing.items]
    results = [StoryResult(story_id=i.story_id, status=status[i.story_id], rewrites=rewrites[i.story_id],
                           sentences_checked=sentences[i.story_id]) for i in items]
    return Briefing(one_line=briefing.one_line, items=items), rounds, results


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
    first_drafts = list(briefing.items)

    rounds: list[CriticRound] = []
    if config.critic.enabled:
        t = time.perf_counter()
        briefing, rounds, results = check_and_revise(llm, briefing, sheets, config.topic,
                                                     config.critic.max_rewrites, say)
        timings["critic"] = time.perf_counter() - t
    else:
        results = [StoryResult(story_id=i.story_id, status="unchecked") for i in briefing.items]

    kept = {i.story_id for i in briefing.items}
    return RunLog(
        candidates=len(candidates),
        picks=picks,
        sheets=[s for s in sheets if s.story.id in kept],
        first_drafts=first_drafts,
        critic_rounds=rounds,
        results=results,
        briefing=briefing,
        seconds={k: round(v, 1) for k, v in timings.items()},
    )
