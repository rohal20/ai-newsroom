"""Writer agent: turns the researcher's verified facts into the briefing, and rewrites
stories the critic sends back."""

from __future__ import annotations

import logging

from pydantic import BaseModel, Field

from ..llm import LLM, LLMError, ask_json
from .researcher import FactSheet

log = logging.getLogger(__name__)


class BriefItem(BaseModel):
    story_id: str
    title: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    why_it_matters: str = ""


class Briefing(BaseModel):
    one_line: str = Field(min_length=1, description="The day in one sentence")
    items: list[BriefItem]


class Rewrites(BaseModel):
    items: list[BriefItem]


RULES = """For every story write:
- title: a clear, specific headline (no clickbait).
- summary: 2 sentences, built only from that story's facts.
- why_it_matters: 1 concrete sentence about the practical consequence for the reader. Avoid empty phrases
  such as "highlights the importance of" or "marks a significant step".

Use ONLY the facts given. Do not add names, numbers, dates or claims that are not in the facts."""

SYSTEM = """You are the writer of a short daily briefing about {topic} for busy engineers and researchers.
You will receive fact sheets, one per story. Each fact has already been checked against its source.

""" + RULES + """
Then write one_line: the whole day in one sentence.

Keep the stories in the order given. Reply with JSON only:
{{"one_line": "...", "items": [{{"story_id": "...", "title": "...", "summary": "...", "why_it_matters": "..."}}]}}"""

REVISE_SYSTEM = """You are the writer of a short daily briefing about {topic}.
A fact-checker reviewed your drafts against the verified facts and found problems.
Rewrite each story so that every problem is fixed: remove or correct anything the facts do not support.
Keep what was fine. Do not introduce anything new that is not in the facts.

""" + RULES + """

Reply with JSON only:
{{"items": [{{"story_id": "...", "title": "...", "summary": "...", "why_it_matters": "..."}}]}}"""


def _facts(sheet: FactSheet) -> str:
    return "\n".join(f"   - {f.claim}" for f in sheet.facts)


def build_prompt(sheets: list[FactSheet]) -> str:
    blocks = [f"{i}. id={s.story.id} | {s.story.display_source} | {s.story.title}\n{_facts(s)}"
              for i, s in enumerate(sheets, 1)]
    return "Fact sheets:\n\n" + "\n\n".join(blocks)


def _ordered(items: list[BriefItem], order: list[str]) -> list[BriefItem]:
    by_id = {i.story_id: i for i in items if i.story_id in order}
    return [by_id[sid] for sid in order if sid in by_id]


def run(llm: LLM, sheets: list[FactSheet], topic: str) -> Briefing:
    briefing = ask_json(llm, SYSTEM.format(topic=topic), build_prompt(sheets), Briefing)
    order = [s.story.id for s in sheets]
    items = _ordered(briefing.items, order)
    missing = [sid for sid in order if sid not in {i.story_id for i in items}]
    if missing:
        log.warning("Writer skipped %d stor(ies): %s", len(missing), ", ".join(missing))
    if not items:
        raise LLMError("The writer did not return any of the stories.")
    return Briefing(one_line=briefing.one_line, items=items)


def build_revise_prompt(drafts: list[BriefItem], sheets: list[FactSheet], problems: dict[str, list]) -> str:
    by_id = {s.story.id: s for s in sheets}
    blocks = []
    for d in drafts:
        notes = "\n".join(f"   - \"{p.text}\": {p.problem}" for p in problems.get(d.story_id, []))
        blocks.append(
            f"id={d.story_id}\nVERIFIED FACTS:\n{_facts(by_id[d.story_id])}\n"
            f"YOUR DRAFT:\n   title: {d.title}\n   summary: {d.summary}\n   why_it_matters: {d.why_it_matters}\n"
            f"PROBLEMS FOUND:\n{notes}"
        )
    return "\n\n".join(blocks)


def revise(llm: LLM, drafts: list[BriefItem], sheets: list[FactSheet], problems: dict[str, list], topic: str) -> list[BriefItem]:
    """Rewrite only the flagged stories. Stories the model leaves out keep their old draft."""
    out = ask_json(llm, REVISE_SYSTEM.format(topic=topic), build_revise_prompt(drafts, sheets, problems), Rewrites)
    fixed = {i.story_id: i for i in out.items}
    return [fixed.get(d.story_id, d) for d in drafts]


def facts_only(sheet: FactSheet) -> BriefItem:
    """Last resort when a draft keeps failing review: the source's own headline and the verified facts, nothing added."""
    claims = [f.claim.rstrip(".") + "." for f in sheet.facts[:2]]
    return BriefItem(story_id=sheet.story.id, title=sheet.story.title, summary=" ".join(claims), why_it_matters="")
