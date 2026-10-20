import json

from fakes import ScriptedLLM, story

from newsroom.agents.researcher import Fact, normalize, quote_in_text, research, verify

ARTICLE = ("Acme released Model X on Monday. The model scores 71.2% on the coding benchmark, "
           "up from 64% for its predecessor. It is free for personal use — “no strings attached,” the CEO said.")


def test_normalize_ignores_case_quotes_dashes_and_spacing():
    assert normalize("“No  strings” — attached!") == normalize('"no strings" - attached')


def test_quote_matching():
    assert quote_in_text("scores 71.2% on the coding benchmark", ARTICLE)
    assert quote_in_text("it is free for personal use - \"no strings attached,\"", ARTICLE)   # different quote marks
    assert not quote_in_text("scores 75% on the coding benchmark", ARTICLE)                  # changed number
    assert not quote_in_text("Model X", ARTICLE)                                              # too short to prove anything


def test_verify_splits_supported_and_unsupported():
    facts = [
        Fact(claim="Acme released Model X", quote="Acme released Model X on Monday"),
        Fact(claim="It costs $20 a month", quote="costs $20 a month for teams"),
    ]
    kept, dropped = verify(facts, ARTICLE)
    assert [f.claim for f in kept] == ["Acme released Model X"]
    assert [f.claim for f in dropped] == ["It costs $20 a month"]


def test_research_keeps_only_verified_facts():
    llm = ScriptedLLM(json.dumps({"facts": [
        {"claim": "Released Monday", "quote": "Acme released Model X on Monday"},
        {"claim": "Scores 71.2%", "quote": "The model scores 71.2% on the coding benchmark"},
        {"claim": "Raised $1B", "quote": "Acme raised $1 billion last week"},
    ]}))
    sheet = research(llm, story(1), ARTICLE, "article", max_chars=6000, min_facts=2)
    assert [f.claim for f in sheet.facts] == ["Released Monday", "Scores 71.2%"]
    assert [f.claim for f in sheet.dropped] == ["Raised $1B"]
    assert "Article:\nAcme released" in llm.prompts[0][1]


def test_research_falls_back_to_feed_text_when_too_few_facts():
    s = story(1, summary="Acme shipped Model X today.")
    sheet = research(ScriptedLLM('{"facts": []}'), s, ARTICLE, "article", max_chars=6000, min_facts=2)
    assert sheet.facts[-1].claim == "Acme shipped Model X today."


def test_research_survives_a_broken_model():
    s = story(1, summary="Acme shipped Model X today.")
    sheet = research(ScriptedLLM("garbage", "still garbage"), s, ARTICLE, "article", max_chars=6000, min_facts=1)
    assert len(sheet.facts) == 1


def test_research_only_reads_up_to_the_limit():
    llm = ScriptedLLM('{"facts": []}')
    research(llm, story(1), "A" * 10_000, "article", max_chars=500, min_facts=1)
    assert llm.prompts[0][1].count("A") < 600
