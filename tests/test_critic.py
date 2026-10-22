import json

import pytest
from fakes import ScriptedLLM, story

from newsroom.agents import critic
from newsroom.agents.critic import number_issues, numbers_in, sentences_in
from newsroom.agents.researcher import Fact, FactSheet
from newsroom.agents.writer import BriefItem


def sheet(*claims, title="Acme ships Model X", summary=""):
    s = story(1, summary=summary).model_copy(update={"title": title})
    return FactSheet(story=s, text_source="article", facts=[Fact(claim=c, quote=c) for c in claims])


def item(summary, title="Acme ships Model X", why="Teams can try it today.", sid=None):
    return BriefItem(story_id=sid or story(1).id, title=title, summary=summary, why_it_matters=why)


@pytest.mark.parametrize("a,b", [
    ("a $45 billion deal", "a $45B deal"),
    ("45bn", "45 billion"),
    ("89 percent of models", "89% of models"),
    ("over fifty languages", "over 50 languages"),
    ("1,248 stories", "1248 stories"),
])
def test_same_number_written_differently_matches(a, b):
    assert numbers_in(a) == numbers_in(b)


def test_number_check_flags_changed_and_invented_numbers():
    s = sheet("Model X scores 71.2% on the benchmark", "It supports 50 languages")
    issues = number_issues(item("Model X scores 75% and supports 50 languages, raising $2 billion."), s)
    assert [i.text for i in issues] == ["75%", "$2 billion"]
    assert all(i.by == "numbers" for i in issues)


def test_spelled_out_numbers_in_drafts_are_ordinary_prose():
    s = sheet("Two companies signed the deal", "It supports fifty languages")
    assert number_issues(item("The two companies signed it; three teams will use it in 50 languages."), s) == []


def test_number_check_accepts_numbers_from_the_headline_and_feed_text():
    s = sheet("Released on Monday", title="Voxtral 3 arrives", summary="Now in 20 regions.")
    assert number_issues(item("Voxtral 3 is live in 20 regions."), s) == []


def test_ai_issues_are_merged_with_number_issues_without_duplicates():
    s = sheet("Model X scores 71.2% on the benchmark")
    draft = item("Model X, built with Google, scores 80% on the benchmark.")
    llm = ScriptedLLM(json.dumps({"stories": [
        {"story_id": s.story.id, "issues": [{"text": "built with Google", "problem": "Google not in facts"},
                                            {"text": "80%", "problem": "wrong number"}]},
        {"story_id": "unknown", "issues": [{"text": "x", "problem": "y"}]},
    ]}))
    result = critic.review(llm, [draft], [s])
    issues = result.issues[s.story.id]
    assert [(i.text, i.by) for i in issues] == [("80%", "numbers"), ("built with Google", "critic")]
    assert result.ai_checked and "unknown" not in result.issues


def test_prompt_shows_facts_and_draft_side_by_side():
    s = sheet("Model X scores 71.2% on the benchmark")
    prompt = critic.build_prompt([item("Scores 71.2%.")], [s])
    assert "VERIFIED FACTS:\n   - Model X scores 71.2% on the benchmark" in prompt
    assert "DRAFT:\n   title: Acme ships Model X\n   summary: Scores 71.2%." in prompt


def test_critic_outage_still_returns_number_issues():
    s = sheet("It supports 50 languages")
    result = critic.review(ScriptedLLM("garbage", "garbage again"), [item("It supports 60 languages.")], [s])
    assert result.ai_checked is False
    assert [i.text for i in result.issues[s.story.id]] == ["60"]


def test_sentence_count():
    assert sentences_in(item("One. Two! Three?", why="Four.")) == 5   # title + 3 + 1
