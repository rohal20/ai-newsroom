from fakes import NewsroomLLM, fake_reader, story

from newsroom import pipeline
from newsroom.config import Config, Feed


def config(**briefing):
    return Config(topic="AI", feeds=[Feed(name="x", url="https://x.example/rss")], briefing=briefing)


def test_full_run_editor_researcher_writer():
    stories = [story(i, "arXiv", domain="arxiv.org", hours_ago=i) for i in range(6)] + [story(10 + i, "News", hours_ago=i) for i in range(4)]
    llm = NewsroomLLM()
    out = []
    run = pipeline.run(llm, stories, config(stories=5, max_papers=2), read=fake_reader, say=out.append)

    assert llm.calls == ["editor"] + ["researcher"] * 5 + ["writer"]
    assert len(run.briefing.items) == 5
    assert sum(s.story.is_paper for s in run.sheets) <= 2                 # mix rule held
    assert all(len(s.dropped) == 1 for s in run.sheets)                   # the invented $9B fact never got through
    assert all("$9 billion" not in f.claim for s in run.sheets for f in s.facts)
    assert set(run.seconds) == {"editor", "researcher", "writer"}
    assert any(line.startswith("Editor: choosing 5 of 10") for line in out)


def test_run_log_is_json_serializable():
    run = pipeline.run(NewsroomLLM(), [story(i) for i in range(3)], config(stories=2), read=fake_reader, say=lambda _: None)
    data = run.model_dump(mode="json")
    assert data["picks"][0]["story"]["title"].startswith("Story")
    assert "facts" in data["sheets"][0]
