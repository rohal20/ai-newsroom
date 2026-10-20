from datetime import date

from fakes import story

from newsroom.agents.researcher import Fact, FactSheet
from newsroom.agents.writer import BriefItem, Briefing
from newsroom.briefing import render_markdown, save_edition, select_candidates


def test_select_candidates_balances_sources():
    stories = [story(i, "arXiv", hours_ago=i) for i in range(30)] + [story(i, "Lab Blog", hours_ago=i) for i in range(3)]
    picked = select_candidates(stories, max_total=10, max_per_source=8)
    sources = [s.source for s in picked]
    assert len(picked) == 10
    assert sources.count("Lab Blog") == 3        # the small feed is fully represented
    assert sources.count("arXiv") == 7
    assert picked[0].title == "Story 0"           # newest first


def test_markdown_edition(tmp_path):
    s1 = story(1, "TechCrunch AI")
    s2 = story(2, "Hacker News", domain="arxiv.org").model_copy(update={"aggregated": True})
    sheets = [
        FactSheet(story=s1, text_source="article", facts=[Fact(claim="a", quote="a"), Fact(claim="b", quote="b")],
                  dropped=[Fact(claim="x", quote="x")]),
        FactSheet(story=s2, text_source="feed (abstract)", facts=[Fact(claim="c", quote="c")]),
    ]
    b = Briefing(one_line="A busy day.", items=[
        BriefItem(story_id=s1.id, title="One", summary="S1.", why_it_matters="W1."),
        BriefItem(story_id=s2.id, title="Two", summary="S2.", why_it_matters="W2."),
    ])
    md = render_markdown(b, sheets, "AI", date(2026, 10, 19), candidates=40)
    assert md.startswith("# AI briefing · Monday 19 October 2026")
    assert "> A busy day." in md
    assert f"[TechCrunch AI]({s1.url})" in md
    assert "[arxiv.org via Hacker News]" in md                  # aggregator stories show where they really are
    assert "2 facts checked against the article" in md
    assert "1 facts checked against the abstract" in md
    assert "Chosen from 40 stories · 3 facts verified against their sources, 1 unsupported facts removed" in md

    path = save_edition(md, date(2026, 10, 19), tmp_path / "editions")
    assert path.name == "2026-10-19.md" and path.read_text(encoding="utf-8") == md
