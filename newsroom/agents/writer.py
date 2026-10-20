"""Writer agent: turns the researcher's verified facts into the briefing."""

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
    why_it_matters: str = Field(min_length=1)


class Briefing(BaseModel):
    one_line: str = Field(min_length=1, description="The day in one sentence")
    items: list[BriefItem]


SYSTEM = """You are the writer of a short daily briefing about {topic} for busy engineers and researchers.
You will receive fact sheets, one per story. Each fact has already been checked against its source.

For every story write:
- title: a clear, specific headline (no clickbait, no colons if you can avoid them).
- summary: 2 sentences, built only from that story's facts.
- why_it_matters: 1 concrete sentence about the practical consequence for the reader. Avoid empty phrases
  such as "highlights the importance of" or "marks a significant step".
Then write one_line: the whole day in one sentence.

Use ONLY the facts given. Do not add numbers, names or claims that are not in the facts.
Keep the stories in the order given. Reply with JSON only:
{{"one_line": "...", "items": [{{"story_id": "...", "title": "...", "summary": "...", "why_it_matters": "..."}}]}}"""


def build_prompt(sheets: list[FactSheet]) -> str:
    blocks = []
    for i, sheet in enumerate(sheets, 1):
        facts = "\n".join(f"   - {f.claim}" for f in sheet.facts)
        blocks.append(f"{i}. id={sheet.story.id} | {sheet.story.display_source} | {sheet.story.title}\n{facts}")
    return "Fact sheets:\n\n" + "\n\n".join(blocks)


def run(llm: LLM, sheets: list[FactSheet], topic: str) -> Briefing:
    briefing = ask_json(llm, SYSTEM.format(topic=topic), build_prompt(sheets), Briefing)
    order = [s.story.id for s in sheets]
    by_id = {i.story_id: i for i in briefing.items if i.story_id in order}
    missing = [sid for sid in order if sid not in by_id]
    if missing:
        log.warning("Writer skipped %d stor(ies): %s", len(missing), ", ".join(missing))
    items = [by_id[sid] for sid in order if sid in by_id]
    if not items:
        raise LLMError("The writer did not return any of the stories.")
    return Briefing(one_line=briefing.one_line, items=items)
