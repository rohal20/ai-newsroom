"""Critic agent: checks every written sentence against the story's verified facts.

Two layers:
1. A number check in code. Every number in the draft (89%, $45B, 17 of 211...)
   must appear in that story's facts or source text. Strict, free, and never
   misses a changed number.
2. An AI critic that reads each draft against its facts and flags anything
   the facts do not support: added names, dates, causes, comparisons, hype.
   It must quote the exact phrase it objects to.

If the AI critic is unavailable (quota, outage), the number check still runs
and the edition says the story was only partly checked.
"""

from __future__ import annotations

import logging
import re

from pydantic import BaseModel, Field

from ..llm import LLM, LLMError, ask_json
from .researcher import FactSheet
from .writer import BriefItem

log = logging.getLogger(__name__)


class Issue(BaseModel):
    text: str = Field(description="The exact words in the draft that are not supported")
    problem: str = Field(default="", description="What is wrong, in a few words")
    by: str = Field(default="critic", description="critic (AI) or numbers (code)")


class StoryReview(BaseModel):
    story_id: str
    issues: list[Issue] = []


class Review(BaseModel):
    stories: list[StoryReview]


# ---------------------------------------------------------------------------- number check (code)

_WORD_NUMBERS = {w: str(i) for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve".split())}
_WORD_NUMBERS.update({w: str(10 * i) for i, w in enumerate(
    "twenty thirty forty fifty sixty seventy eighty ninety".split(), start=2)})
_WORD_RE = re.compile(r"\b(" + "|".join(_WORD_NUMBERS) + r")\b", re.IGNORECASE)
_NUM_RE = re.compile(
    r"(?<![\w.])(\$|€|£)?(\d+(?:[.,]\d+)*)"
    r"(\s?(?:%|percent|per cent|trillion|billion|million|thousand|bn|[kmbt]\b))?",
    re.IGNORECASE,
)
_SCALE = {"k": 1e3, "thousand": 1e3, "m": 1e6, "million": 1e6, "b": 1e9, "bn": 1e9, "billion": 1e9,
          "t": 1e12, "trillion": 1e12}


def numbers_with_text(text: str, words: bool = True) -> dict[str, str]:
    """Map each number's normalized value to how it was written: {'45000000000': '$45 billion', '89%': '89 percent'}.

    words=True also reads spelled-out numbers ("fifty"). We do that for the facts, so a draft saying "50"
    matches facts saying "fifty", but not for drafts, where "the two companies" is ordinary prose.
    """
    if words:
        text = _WORD_RE.sub(lambda m: _WORD_NUMBERS[m.group(1).lower()], text)
    found: dict[str, str] = {}
    for m in _NUM_RE.finditer(text):
        _, raw, unit = m.groups()
        try:
            value = float(raw.replace(",", ""))
        except ValueError:
            continue
        unit = (unit or "").strip().lower()
        if unit in ("%", "percent", "per cent"):
            key = f"{value:g}%"
        else:
            value *= _SCALE.get(unit, 1)
            if value <= 1:  # "one model", "0 errors": too common to be worth flagging
                continue
            key = f"{value:.6g}"
        found.setdefault(key, m.group(0).strip())
    return found


def numbers_in(text: str) -> set[str]:
    """Normalized numbers in a text, so '$45 billion', '45B' and '45bn' compare equal."""
    return set(numbers_with_text(text))


def source_text(sheet: FactSheet) -> str:
    facts = " ".join(f"{f.claim} {f.quote}" for f in sheet.facts)
    return f"{facts} {sheet.story.title} {sheet.story.summary}"


def number_issues(item: BriefItem, sheet: FactSheet) -> list[Issue]:
    allowed = numbers_in(source_text(sheet))
    issues = []
    seen: set[str] = set()
    for part in (item.title, item.summary, item.why_it_matters):
        for key, written in numbers_with_text(part, words=False).items():
            if key not in allowed and key not in seen:
                seen.add(key)
                issues.append(Issue(text=written, problem="number not found in the verified facts", by="numbers"))
    return issues


# ---------------------------------------------------------------------------- AI critic

SYSTEM = """You are the fact-checking critic for a daily news briefing.
For each story you get the VERIFIED FACTS (the only things we know are true) and a DRAFT
(title, summary, why_it_matters) written from them.

Flag every statement in the draft that the facts do not support, including:
- names of companies, people, products or places that are not in the facts
- numbers, dates, prices or amounts that are not in the facts
- causes, comparisons or firsts ("the first", "the biggest", "because of") the facts do not state
- certainty or scope the facts do not give (e.g. facts say "plans to", draft says "has launched")

Do NOT flag:
- rewording or summarizing that keeps the same meaning
- why_it_matters giving a reasonable practical consequence, as long as it adds no new facts

For each problem, copy the exact words from the draft into "text". If a story is fine, give it an empty list.
Reply with JSON only:
{"stories": [{"story_id": "...", "issues": [{"text": "...", "problem": "..."}]}]}"""


def build_prompt(drafts: list[BriefItem], sheets: list[FactSheet]) -> str:
    by_id = {s.story.id: s for s in sheets}
    blocks = []
    for item in drafts:
        sheet = by_id[item.story_id]
        facts = "\n".join(f"   - {f.claim}" for f in sheet.facts)
        blocks.append(
            f"story_id={item.story_id}\n"
            f"VERIFIED FACTS:\n{facts}\n"
            f"DRAFT:\n   title: {item.title}\n   summary: {item.summary}\n   why_it_matters: {item.why_it_matters}"
        )
    return "\n\n".join(blocks)


def sentences_in(item: BriefItem) -> int:
    parts = [item.title, item.summary, item.why_it_matters]
    return sum(len([s for s in re.split(r"(?<=[.!?])\s+", p.strip()) if s]) for p in parts if p.strip())


class CriticResult(BaseModel):
    issues: dict[str, list[Issue]]
    ai_checked: bool = Field(description="False if the AI critic was unavailable and only numbers were checked")


def review(llm: LLM, drafts: list[BriefItem], sheets: list[FactSheet]) -> CriticResult:
    by_id = {s.story.id: s for s in sheets}
    issues: dict[str, list[Issue]] = {d.story_id: number_issues(d, by_id[d.story_id]) for d in drafts}

    ai_checked = True
    try:
        result = ask_json(llm, SYSTEM, build_prompt(drafts, sheets), Review)
    except LLMError as exc:
        log.warning("AI critic unavailable (%s); only numbers were checked", exc)
        ai_checked = False
    else:
        for story in result.stories:
            if story.story_id not in issues:
                continue
            seen = {i.text.lower() for i in issues[story.story_id]}
            for issue in story.issues:
                if issue.text.strip() and issue.text.lower() not in seen:
                    issues[story.story_id].append(Issue(text=issue.text.strip(), problem=issue.problem, by="critic"))
                    seen.add(issue.text.lower())
    return CriticResult(issues=issues, ai_checked=ai_checked)
