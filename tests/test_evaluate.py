import json
import re
from datetime import date
from pathlib import Path

from newsroom import evaluate
from newsroom.llm import LLMError

CASES = Path(__file__).parent.parent / "evals" / "critic_cases.json"


class CriticDown:
    def complete(self, system, user):
        raise LLMError("offline")


class PerfectCritic:
    """Flags exactly the planted phrase in each draft, so we can check the scoring."""

    def __init__(self, cases):
        self.planted = {c.title: c.planted for c in cases}

    def complete(self, system, user):
        stories = []
        for block in user.split("\n\n"):
            sid = re.search(r"story_id=(\w+)", block).group(1)
            draft = block.split("DRAFT:", 1)[1]
            hit = next((p for p in self.planted.values() if p and p in draft), None)
            stories.append({"story_id": sid, "issues": [{"text": hit, "problem": "unsupported"}] if hit else []})
        return json.dumps({"stories": stories})


def test_case_file_is_valid_and_balanced():
    cases = evaluate.load_cases(CASES)
    assert len(cases) == 13 and len({c.id for c in cases}) == 13
    assert sum(1 for c in cases if c.planted) == 8
    for c in cases:
        if c.planted:   # every planted phrase really is in its draft
            assert c.planted in " ".join(c.draft.values()), c.id


def test_number_check_alone_baseline():
    """Without the AI critic: changed numbers are caught, nothing clean is flagged, wording errors are missed."""
    results = evaluate.run_eval(CriticDown(), evaluate.load_cases(CASES))
    outcome = {r.case.id: r.outcome for r in results}
    assert outcome["changed-percentage"] == "caught"
    assert outcome["invented-amount-and-certainty"] == "caught"
    assert outcome["added-company"] == "missed"          # names need the AI critic
    assert outcome["changed-date-words"] == "missed"     # 2027 is right, the month is not
    s = evaluate.summarize(results)
    assert (s["caught"], s["false_alarms"], s["ai_checked"]) == (2, 0, False)


def test_scoring_with_a_perfect_critic():
    cases = evaluate.load_cases(CASES)
    results = evaluate.run_eval(PerfectCritic(cases), cases)
    s = evaluate.summarize(results)
    assert (s["caught"], s["located"], s["false_alarms"]) == (8, 8, 0)
    # when both layers flag the same phrase it is recorded once, under the number check
    assert s["caught_by_numbers"] == 2 and s["caught_by_ai"] == 6


def test_report_markdown():
    cases = evaluate.load_cases(CASES)
    md = evaluate.report_markdown(evaluate.run_eval(PerfectCritic(cases), cases), "gemini/test", date(2026, 10, 22))
    assert "Planted errors caught: 8 of 8" in md and "False alarms on clean drafts: 0 of 5" in md
    assert "| added-company | name | Google and Meta partner | caught |" in md


def test_eval_critic_command(tmp_path, monkeypatch, capsys):
    from newsroom import __main__ as cli

    (tmp_path / "config.yaml").write_text("topic: AI\nfeeds:\n  - {name: x, url: 'https://x.example/rss'}\n")
    cases = evaluate.load_cases(CASES)
    monkeypatch.setattr(cli, "make_llm", lambda config: PerfectCritic(cases))
    assert cli.main(["eval-critic", "--config", str(tmp_path / "config.yaml"), "--cases", str(CASES),
                     "--out", str(tmp_path / "evals")]) == 0
    assert "Caught 8/8 planted errors" in capsys.readouterr().out
    assert next((tmp_path / "evals").glob("results-*.md")).read_text(encoding="utf-8").startswith("# Critic evaluation")
