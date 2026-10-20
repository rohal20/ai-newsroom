import json

import pytest
from fakes import ScriptedLLM, story

from newsroom.agents import writer
from newsroom.agents.researcher import Fact, FactSheet
from newsroom.llm import LLMError


def sheets(n=3):
    return [FactSheet(story=story(i), text_source="article",
                      facts=[Fact(claim=f"fact {i}a", quote="q"), Fact(claim=f"fact {i}b", quote="q")]) for i in range(n)]


def item(sid, title="T"):
    return {"story_id": sid, "title": title, "summary": "S.", "why_it_matters": "W."}


def test_prompt_contains_facts_not_raw_articles():
    prompt = writer.build_prompt(sheets(2))
    assert "- fact 0a" in prompt and "- fact 1b" in prompt


def test_keeps_editor_order_and_drops_unknown_ids():
    sh = sheets(3)
    ids = [s.story.id for s in sh]
    reply = json.dumps({"one_line": "Day.", "items": [item(ids[2]), item("invented"), item(ids[0]), item(ids[1])]})
    b = writer.run(ScriptedLLM(reply), sh, "AI")
    assert [i.story_id for i in b.items] == ids


def test_tolerates_a_skipped_story():
    sh = sheets(3)
    reply = json.dumps({"one_line": "Day.", "items": [item(sh[0].story.id)]})
    assert len(writer.run(ScriptedLLM(reply), sh, "AI").items) == 1


def test_fails_when_no_story_matches():
    with pytest.raises(LLMError):
        writer.run(ScriptedLLM(json.dumps({"one_line": "Day.", "items": [item("nope")]})), sheets(1), "AI")


def test_retries_once_on_bad_json():
    sh = sheets(1)
    llm = ScriptedLLM("not json", json.dumps({"one_line": "Day.", "items": [item(sh[0].story.id)]}))
    assert writer.run(llm, sh, "AI").one_line == "Day."
    assert "could not be used" in llm.prompts[1][1]
