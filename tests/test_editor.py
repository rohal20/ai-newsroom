import json

import pytest
from fakes import ScriptedLLM, story

from newsroom.agents import editor
from newsroom.agents.editor import Pick, apply_mix
from newsroom.llm import LLMError


def test_prompt_marks_papers_and_news():
    s = [story(1, "arXiv cs.CL", domain="arxiv.org"), story(2, "TechCrunch AI")]
    prompt = editor.build_prompt(s)
    assert f"id={s[0].id} | paper |" in prompt
    assert f"id={s[1].id} | news |" in prompt


def test_mix_rule_caps_papers_and_keeps_rank_order():
    papers = [story(i, "arXiv", domain="arxiv.org") for i in range(4)]
    news = [story(10 + i, "News") for i in range(3)]
    ranked = [Pick(story_id=s.id) for s in papers + news]   # model loves papers
    chosen = apply_mix(ranked, papers + news, n=5, max_papers=2)
    assert [c.story.title for c in chosen] == ["Story 0", "Story 1", "Story 10", "Story 11", "Story 12"]


def test_mix_rule_tops_up_with_papers_when_news_runs_out():
    papers = [story(i, "arXiv", domain="arxiv.org") for i in range(4)]
    news = [story(10, "News")]
    chosen = apply_mix([Pick(story_id=s.id) for s in papers + news], papers + news, n=4, max_papers=1)
    assert len(chosen) == 4 and sum(c.story.is_paper for c in chosen) == 3


def test_mix_rule_ignores_unknown_and_repeated_ids():
    s = [story(1), story(2)]
    ranked = [Pick(story_id="made-up"), Pick(story_id=s[0].id), Pick(story_id=s[0].id), Pick(story_id=s[1].id)]
    assert [c.story.id for c in apply_mix(ranked, s, n=5, max_papers=2)] == [s[0].id, s[1].id]


def test_run_asks_for_spares_and_keeps_reasons():
    s = [story(i) for i in range(10)]
    llm = ScriptedLLM(json.dumps({"ranked": [{"story_id": x.id, "reason": f"r{x.title}"} for x in s]}))
    chosen = editor.run(llm, s, topic="AI", n=5, max_papers=2)
    assert len(chosen) == 5 and chosen[0].reason == "rStory 0"
    assert "Rank the 10 most important" in llm.prompts[0][0]     # n + 5 spares


def test_run_fails_clearly_when_nothing_valid():
    with pytest.raises(LLMError, match="did not pick"):
        editor.run(ScriptedLLM('{"ranked": [{"story_id": "nope"}]}'), [story(1)], "AI", n=5, max_papers=2)
