"""Measure the critic: run it on drafts with known, planted errors and count what it catches.

    python -m newsroom eval-critic

Reports how many planted errors were caught (and by which layer: the code number
check or the AI critic), and how many clean drafts were wrongly flagged.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from pydantic import BaseModel

from .agents import critic
from .agents.critic import Issue
from .agents.researcher import Fact, FactSheet
from .agents.writer import BriefItem
from .llm import LLM
from .models import Story


class Case(BaseModel):
    id: str
    kind: str
    title: str
    facts: list[str]
    draft: dict
    planted: str | None = None


class CaseResult(BaseModel):
    case: Case
    issues: list[Issue]
    ai_checked: bool

    @property
    def flagged(self) -> bool:
        return bool(self.issues)

    @property
    def located(self) -> bool:
        """Did any issue point at (part of) the planted phrase?"""
        if not self.case.planted:
            return False
        p = self.case.planted.lower()
        return any(i.text.lower() in p or p in i.text.lower() for i in self.issues)

    @property
    def outcome(self) -> str:
        if self.case.planted:
            return "caught" if self.flagged else "missed"
        return "false alarm" if self.flagged else "correctly passed"


def load_cases(path: str | Path) -> list[Case]:
    return [Case.model_validate(c) for c in json.loads(Path(path).read_text(encoding="utf-8"))["cases"]]


def to_inputs(case: Case) -> tuple[BriefItem, FactSheet]:
    story = Story(title=case.title, url=f"https://eval.example/{case.id}", source="eval")
    sheet = FactSheet(story=story, text_source="eval", facts=[Fact(claim=f, quote=f) for f in case.facts])
    item = BriefItem(story_id=story.id, **case.draft)
    return item, sheet


def run_eval(llm: LLM, cases: list[Case], batch: int = 5) -> list[CaseResult]:
    results: list[CaseResult] = []
    for start in range(0, len(cases), batch):
        chunk = cases[start:start + batch]
        inputs = [to_inputs(c) for c in chunk]
        review = critic.review(llm, [i for i, _ in inputs], [s for _, s in inputs])
        for case, (item, _) in zip(chunk, inputs):
            results.append(CaseResult(case=case, issues=review.issues[item.story_id], ai_checked=review.ai_checked))
    return results


def summarize(results: list[CaseResult]) -> dict:
    planted = [r for r in results if r.case.planted]
    clean = [r for r in results if not r.case.planted]
    return {
        "planted": len(planted),
        "caught": sum(r.flagged for r in planted),
        "located": sum(r.located for r in planted),
        "caught_by_numbers": sum(any(i.by == "numbers" for i in r.issues) for r in planted),
        "caught_by_ai": sum(any(i.by == "critic" for i in r.issues) for r in planted),
        "clean": len(clean),
        "false_alarms": sum(r.flagged for r in clean),
        "ai_checked": all(r.ai_checked for r in results),
    }


def report_markdown(results: list[CaseResult], model: str, day: date) -> str:
    s = summarize(results)
    lines = [
        f"# Critic evaluation · {day.isoformat()}",
        "",
        f"Model: `{model}` · {len(results)} cases ({s['planted']} with a planted error, {s['clean']} clean)",
        "",
        f"- **Planted errors caught: {s['caught']} of {s['planted']}** "
        f"({s['located']} pointed at the exact phrase)",
        f"  - by the number check (code): {s['caught_by_numbers']}",
        f"  - by the AI critic: {s['caught_by_ai']}",
        f"- **False alarms on clean drafts: {s['false_alarms']} of {s['clean']}**",
    ]
    if not s["ai_checked"]:
        lines.append("- ⚠ The AI critic was unavailable for some cases; only the number check ran for those.")
    lines += ["", "| Case | Kind | Planted error | Result | What the critic flagged |", "|---|---|---|---|---|"]
    for r in results:
        flagged = "; ".join(f"“{i.text}” ({i.by})" for i in r.issues) or "nothing"
        lines.append(f"| {r.case.id} | {r.case.kind} | {r.case.planted or '-'} | {r.outcome} | {flagged} |")
    return "\n".join(lines) + "\n"
