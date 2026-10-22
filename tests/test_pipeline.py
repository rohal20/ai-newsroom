from fakes import NewsroomLLM, fake_reader, story

from newsroom import pipeline
from newsroom.config import Config, Feed


def config(critic=None, **briefing):
    return Config(topic="AI", feeds=[Feed(name="x", url="https://x.example/rss")], briefing=briefing,
                  critic=critic or {})


def news(n=5):
    return [story(i, "arXiv", domain="arxiv.org", hours_ago=i) for i in range(6)] + [story(10 + i, "News", hours_ago=i) for i in range(4)]


def test_full_run_with_critic_catching_and_fixing_a_planted_error():
    llm = NewsroomLLM()
    out: list[str] = []
    run = pipeline.run(llm, news(), config(stories=5, max_papers=2), read=fake_reader, say=out.append)

    assert llm.calls == ["editor"] + ["researcher"] * 5 + ["writer", "critic", "revise", "critic"]
    assert len(run.briefing.items) == 5
    assert sum(s.story.is_paper for s in run.sheets) <= 2                 # mix rule held
    assert all(len(s.dropped) == 1 for s in run.sheets)                   # researcher's invented fact never got through

    first = run.critic_rounds[0].issues[run.briefing.items[0].story_id]
    assert {i.by for i in first} == {"numbers", "critic"}                 # both layers caught their kind of error
    assert {i.text for i in first} == {"$9 billion", "Acme Corp"}
    assert "Acme Corp" in run.first_drafts[0].summary                    # the draft really had it...
    assert all("Acme" not in i.summary and "$9" not in i.summary for i in run.briefing.items)   # ...the edition doesn't

    statuses = [r.status for r in run.results]
    assert statuses == ["rewritten", "approved", "approved", "approved", "approved"]
    assert run.results[0].rewrites == 1
    assert set(run.seconds) == {"editor", "researcher", "writer", "critic"}
    assert any("Critic (round 1): 4 of 5 approved" in line for line in out)
    assert any("✗ \"$9 billion\"" in line for line in out)


def test_story_that_never_gets_fixed_falls_back_to_verified_facts():
    llm = NewsroomLLM(revise_fixes=False)
    run = pipeline.run(llm, news(), config(stories=5, critic={"max_rewrites": 2}), read=fake_reader, say=lambda _: None)
    assert llm.calls.count("revise") == 2 and llm.calls.count("critic") == 3   # 2 rewrites, then give up
    first = run.briefing.items[0]
    assert run.results[0].status == "facts-only"
    assert "Acme" not in first.summary and first.why_it_matters == ""
    assert first.title == run.sheets[0].story.title                       # the source's own headline


def test_clean_drafts_need_no_rewrite():
    llm = NewsroomLLM(plant_error=False)
    run = pipeline.run(llm, news(), config(stories=3), read=fake_reader, say=lambda _: None)
    assert llm.calls.count("critic") == 1 and "revise" not in llm.calls
    assert {r.status for r in run.results} == {"approved"}


def test_numbers_are_still_checked_when_the_ai_critic_is_down():
    llm = NewsroomLLM(critic_down=True)
    run = pipeline.run(llm, news(), config(stories=3), read=fake_reader, say=lambda _: None)
    assert run.critic_rounds[0].ai_checked is False
    assert [i.text for i in run.critic_rounds[0].issues[run.first_drafts[0].story_id]] == ["$9 billion"]
    assert all("$9" not in i.summary for i in run.briefing.items)          # the code check alone still fixed it
    assert {r.status for r in run.results} == {"numbers-only"}


def test_critic_can_be_turned_off():
    llm = NewsroomLLM()
    run = pipeline.run(llm, news(), config(stories=2, critic={"enabled": False}), read=fake_reader, say=lambda _: None)
    assert "critic" not in llm.calls and {r.status for r in run.results} == {"unchecked"}


def test_run_log_is_json_serializable():
    run = pipeline.run(NewsroomLLM(), [story(i) for i in range(3)], config(stories=2), read=fake_reader, say=lambda _: None)
    data = run.model_dump(mode="json")
    assert data["picks"][0]["story"]["title"].startswith("Story")
    assert {"facts", "dropped"} <= set(data["sheets"][0])
    assert data["critic_rounds"][0]["issues"] and data["results"][0]["status"] == "rewritten"
