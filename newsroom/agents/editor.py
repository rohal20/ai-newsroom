"""Editor agent: ranks the candidates, then code enforces the mix rules.

The model is good at judging newsworthiness. Code is better at following hard
rules every time, so "at most 2 papers" is applied here, not left to the prompt.
"""

from __future__ import annotations

import logging

from pydantic import BaseModel

from ..llm import LLM, LLMError, ask_json
from ..models import Story

log = logging.getLogger(__name__)


class Pick(BaseModel):
    story_id: str
    reason: str = ""


class Ranking(BaseModel):
    ranked: list[Pick]


class EditorChoice(BaseModel):
    story: Story
    reason: str


SYSTEM = """You are the editor of a short daily news briefing about {topic} for working engineers and researchers.
You will receive today's candidate stories, each with an id and a type (news or paper).

Rank the {k} most important stories, best first.
- Prefer real releases, results, launches, policy and industry moves over opinion, tutorials and hype.
- Do not rank several stories about the same event; keep the best one.
- For each, give a short reason (under 15 words).

Reply with JSON only:
{{"ranked": [{{"story_id": "...", "reason": "..."}}]}}"""


def build_prompt(stories: list[Story]) -> str:
    lines = []
    for i, s in enumerate(stories, 1):
        kind = "paper" if s.is_paper else "news"
        snippet = s.summary[:220] if s.summary else "(no description)"
        lines.append(f"{i}. id={s.id} | {kind} | {s.display_source} | {s.title}\n   {snippet}")
    return "Candidates:\n\n" + "\n".join(lines)


def apply_mix(ranked: list[Pick], stories: list[Story], n: int, max_papers: int) -> list[EditorChoice]:
    """Walk the ranking, skip unknown ids and repeats, and cap papers. Top up with papers if news runs out."""
    by_id = {s.id: s for s in stories}
    chosen: list[EditorChoice] = []
    held_back: list[EditorChoice] = []
    papers = 0
    seen: set[str] = set()
    for pick in ranked:
        story = by_id.get(pick.story_id)
        if story is None:
            log.warning("Editor picked an unknown id %r; ignoring it", pick.story_id)
            continue
        if story.id in seen:
            continue
        seen.add(story.id)
        choice = EditorChoice(story=story, reason=pick.reason)
        if story.is_paper and papers >= max_papers:
            held_back.append(choice)
            continue
        papers += story.is_paper
        chosen.append(choice)
        if len(chosen) == n:
            return chosen
    return (chosen + held_back)[:n]


def run(llm: LLM, candidates: list[Story], topic: str, n: int, max_papers: int) -> list[EditorChoice]:
    k = min(len(candidates), n + 5)  # ask for a few spares so the mix rule has room to work
    ranking = ask_json(llm, SYSTEM.format(topic=topic, k=k), build_prompt(candidates), Ranking)
    chosen = apply_mix(ranking.ranked, candidates, n, max_papers)
    if not chosen:
        raise LLMError("The editor did not pick any valid stories.")
    return chosen
