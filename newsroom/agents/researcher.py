"""Researcher agent: reads each chosen article and extracts facts, each backed by a quote.

Every quote is checked against the article text in code. A fact whose quote is
not actually in the article is dropped, so the writer only sees facts that a
person could find on the page.
"""

from __future__ import annotations

import logging
import re

from pydantic import BaseModel, Field

from ..llm import LLM, LLMError, ask_json
from ..models import Story

log = logging.getLogger(__name__)


class Fact(BaseModel):
    claim: str = Field(min_length=1)
    quote: str = Field(min_length=1, description="Exact words from the article that support the claim")


class FactList(BaseModel):
    facts: list[Fact]


class FactSheet(BaseModel):
    story: Story
    text_source: str = Field(description="article, feed or feed (abstract)")
    facts: list[Fact]
    dropped: list[Fact] = []


SYSTEM = """You are a careful research assistant for a news briefing.
You will receive one article. Extract the 3 to 6 most important facts a reader needs:
what happened, who is involved, key numbers, and what changes as a result.

Rules:
- Each fact must be supported by a short quote copied word for word from the article (5 to 30 words).
- Do not use outside knowledge. If the article does not say it, leave it out.

Reply with JSON only:
{"facts": [{"claim": "...", "quote": "..."}]}"""

_QUOTES = str.maketrans({"“": '"', "”": '"', "‘": "'", "’": "'", "–": "-", "—": "-", " ": " "})
_NON_WORD = re.compile(r"[^\w%$.]+")


def normalize(text: str) -> str:
    """Lowercase, unify quotes and dashes, and collapse punctuation and whitespace to single spaces."""
    return _NON_WORD.sub(" ", text.translate(_QUOTES).lower()).strip()


def quote_in_text(quote: str, text: str, min_chars: int = 12) -> bool:
    q = normalize(quote).strip(" .")
    return len(q) >= min_chars and q in normalize(text)


def verify(facts: list[Fact], text: str) -> tuple[list[Fact], list[Fact]]:
    kept, dropped = [], []
    for f in facts:
        (kept if quote_in_text(f.quote, text) else dropped).append(f)
    return kept, dropped


def research(llm: LLM, story: Story, text: str, text_source: str, max_chars: int, min_facts: int) -> FactSheet:
    body = text[:max_chars]
    user = f"Headline: {story.title}\nSource: {story.display_source}\n\nArticle:\n{body}"
    try:
        found = ask_json(llm, SYSTEM, user, FactList).facts
    except LLMError as exc:
        log.warning("Researcher failed on %s: %s", story.id, exc)
        found = []
    kept, dropped = verify(found, body)
    if dropped:
        log.info("%s: dropped %d unsupported fact(s)", story.id, len(dropped))
    if len(kept) < min_facts:
        # Not enough checkable facts: fall back to the feed text itself as the single source of truth.
        log.info("%s: only %d verified fact(s); using the feed text", story.id, len(kept))
        fallback = story.summary or story.title
        kept = kept + [Fact(claim=fallback, quote=fallback[:200])]
    return FactSheet(story=story, text_source=text_source, facts=kept, dropped=dropped)
