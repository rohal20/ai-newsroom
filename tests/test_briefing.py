import json
from datetime import date, datetime, timedelta, timezone

import pytest

from newsroom.briefing import (
    Briefing,
    build_user_prompt,
    render_markdown,
    save_edition,
    select_candidates,
    write_briefing,
)
from newsroom.llm import LLMError
from newsroom.models import Story

BASE = datetime(2026, 10, 18, 7, 0, tzinfo=timezone.utc)


def story(i: int, source: str = "Feed", hours_ago: int = 0) -> Story:
    return Story(
        title=f"Story {i}",
        url=f"https://example.com/{source.lower().replace(' ', '-')}/{i}",
        source=source,
        published=BASE - timedelta(hours=hours_ago),
        summary=f"Details about story {i}.",
    )


class FakeLLM:
    """Returns canned replies in order and records what it was asked."""

    def __init__(self, *replies: str):
        self.replies, self.prompts = list(replies), []

    def complete(self, system: str, user: str) -> str:
        self.prompts.append((system, user))
        return self.replies.pop(0)


def reply(stories, ids=None, one_line="A busy day.") -> str:
    ids = ids or [s.id for s in stories[:3]]
    return json.dumps({
        "one_line": one_line,
        "items": [{"story_id": i, "title": f"T {i}", "summary": "S.", "why_it_matters": "W."} for i in ids],
    })


def test_select_candidates_balances_sources():
    stories = [story(i, "arXiv", hours_ago=i) for i in range(30)] + [story(i, "Lab Blog", hours_ago=i) for i in range(3)]
    picked = select_candidates(stories, max_total=10, max_per_source=8)
    sources = [s.source for s in picked]
    assert len(picked) == 10
    assert sources.count("Lab Blog") == 3        # the small feed is fully represented
    assert sources.count("arXiv") == 7
    assert picked[0].title == "Story 0"           # newest first


def test_prompt_lists_ids_not_just_titles():
    s = [story(1), story(2)]
    prompt = build_user_prompt(s)
    assert f"id={s[0].id}" in prompt and "Story 2" in prompt


def test_write_briefing_happy_path():
    s = [story(i) for i in range(6)]
    llm = FakeLLM(reply(s))
    b = write_briefing(llm, s, topic="AI", n=5)
    assert b.one_line == "A busy day."
    assert [i.story_id for i in b.items] == [x.id for x in s[:3]]
    assert "AI" in llm.prompts[0][0] and "5 stories" in llm.prompts[0][0]


def test_invented_story_ids_are_dropped():
    s = [story(i) for i in range(3)]
    llm = FakeLLM(reply(s, ids=[s[0].id, "made-up-id", s[0].id, s[1].id]))
    b = write_briefing(llm, s, topic="AI")
    assert [i.story_id for i in b.items] == [s[0].id, s[1].id]   # fake and duplicate removed


def test_caps_number_of_items():
    s = [story(i) for i in range(8)]
    b = write_briefing(FakeLLM(reply(s, ids=[x.id for x in s])), s, topic="AI", n=5)
    assert len(b.items) == 5


def test_retries_once_on_bad_json_and_tells_the_model_why():
    s = [story(i) for i in range(3)]
    llm = FakeLLM("not json at all", reply(s))
    b = write_briefing(llm, s, topic="AI")
    assert len(b.items) == 3
    assert "could not be used" in llm.prompts[1][1]


def test_gives_up_after_two_bad_replies():
    s = [story(1)]
    with pytest.raises(LLMError, match="usable briefing"):
        write_briefing(FakeLLM("nope", '{"one_line": "x", "items": []}'), s, topic="AI")


def test_markdown_links_come_from_our_data(tmp_path):
    s = [story(i) for i in range(3)]
    b = Briefing.model_validate(json.loads(reply(s)))
    md = render_markdown(b, s, "AI", date(2026, 10, 18))
    assert md.startswith("# AI briefing · Sunday 18 October 2026")
    assert "> A busy day." in md
    assert f"[Feed]({s[0].url})" in md
    assert md.count("**Why it matters:**") == 3
    path = save_edition(md, date(2026, 10, 18), tmp_path / "editions")
    assert path.name == "2026-10-18.md" and path.read_text(encoding="utf-8") == md
