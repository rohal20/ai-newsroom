"""v0.2: one agent reads the day's stories and writes a short briefing.

The model never writes URLs. It picks stories by id, and every link in the
edition comes from our own fetched data, so a briefing cannot contain an
invented source. (The critic agent in v0.4 builds on this.)
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import date
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

from .dedupe import strip_tracking
from .llm import LLM, LLMError, extract_json
from .models import Story

log = logging.getLogger(__name__)


class BriefItem(BaseModel):
    story_id: str
    title: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    why_it_matters: str = Field(min_length=1)


class Briefing(BaseModel):
    one_line: str = Field(min_length=1, description="The day in one sentence")
    items: list[BriefItem]


SYSTEM_PROMPT = """You are the editor of a short daily news briefing about {topic}.
You will receive a numbered list of today's stories, each with an id.

Pick the {n} stories that matter most to working engineers and researchers.
Prefer real releases, results and changes over opinion and hype.
Avoid picking several stories about the same thing.

Rules:
- Use ONLY information in the story text you are given. Do not add facts.
- Refer to stories by their exact id. Do not write URLs.
- summary: 1-2 plain sentences. why_it_matters: one sentence.

Reply with JSON only, in this shape:
{{"one_line": "...", "items": [{{"story_id": "...", "title": "...", "summary": "...", "why_it_matters": "..."}}]}}"""


def select_candidates(stories: list[Story], max_total: int = 40, max_per_source: int = 8) -> list[Story]:
    """Newest first, taken round-robin across sources so one busy feed (arXiv) can't crowd out the rest."""
    by_source: dict[str, list[Story]] = defaultdict(list)
    for s in sorted(stories, key=lambda s: s.published.timestamp() if s.published else 0, reverse=True):
        if len(by_source[s.source]) < max_per_source:
            by_source[s.source].append(s)
    picked: list[Story] = []
    queues = list(by_source.values())
    while queues and len(picked) < max_total:
        for q in list(queues):
            if q:
                picked.append(q.pop(0))
                if len(picked) == max_total:
                    break
            if not q:
                queues.remove(q)
    return picked


def build_user_prompt(stories: list[Story]) -> str:
    lines = []
    for i, s in enumerate(stories, 1):
        snippet = s.summary[:300] if s.summary else "(no description)"
        lines.append(f"{i}. id={s.id} | {s.source} | {s.title}\n   {snippet}")
    return "Today's stories:\n\n" + "\n".join(lines)


def validate(briefing: Briefing, stories: list[Story], n: int) -> Briefing:
    """Drop items that point at unknown or repeated stories, and keep at most n."""
    known = {s.id for s in stories}
    seen: set[str] = set()
    items = []
    for item in briefing.items:
        if item.story_id not in known:
            log.warning("Dropping item with unknown story id %r (%s)", item.story_id, item.title)
            continue
        if item.story_id in seen:
            continue
        seen.add(item.story_id)
        items.append(item)
    if not items:
        raise LLMError("The model did not return any valid stories.")
    return Briefing(one_line=briefing.one_line, items=items[:n])


def write_briefing(llm: LLM, stories: list[Story], topic: str, n: int = 5, attempts: int = 2) -> Briefing:
    """Ask the model for a briefing, retrying once with the error if the JSON is unusable."""
    system = SYSTEM_PROMPT.format(topic=topic, n=n)
    user = build_user_prompt(stories)
    last_error = ""
    for _ in range(attempts):
        prompt = user if not last_error else f"{user}\n\nYour previous reply could not be used ({last_error}). Reply with valid JSON only."
        raw = llm.complete(system, prompt)
        try:
            return validate(Briefing.model_validate(extract_json(raw)), stories, n)
        except (json.JSONDecodeError, ValidationError, LLMError) as exc:
            last_error = str(exc).splitlines()[0][:200]
            log.warning("Unusable briefing: %s", last_error)
    raise LLMError(f"Could not get a usable briefing after {attempts} attempts: {last_error}")


def render_markdown(briefing: Briefing, stories: list[Story], topic: str, day: date) -> str:
    by_id = {s.id: s for s in stories}
    out = [
        f"# {topic} briefing · {day:%A %d %B %Y}",
        "",
        f"> {briefing.one_line}",
        "",
    ]
    for i, item in enumerate(briefing.items, 1):
        story = by_id[item.story_id]
        out += [
            f"## {i}. {item.title}",
            "",
            item.summary,
            "",
            f"**Why it matters:** {item.why_it_matters}",
            "",
            f"Source: [{story.source}]({strip_tracking(str(story.url))})",
            "",
        ]
    out.append(f"<sub>Chosen from {len(stories)} stories. Generated by ai-newsroom. Summaries are based on the feed text; follow the links for the full story.</sub>")
    return "\n".join(out) + "\n"


def save_edition(markdown: str, day: date, out_dir: str | Path = "editions") -> Path:
    path = Path(out_dir) / f"{day.isoformat()}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(markdown, encoding="utf-8")
    return path
