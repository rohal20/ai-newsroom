"""Test doubles shared by several test files: stories, a fake model, and fake article pages."""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone

from newsroom.models import Story

BASE = datetime(2026, 10, 19, 7, 0, tzinfo=timezone.utc)


def story(i: int, source: str = "Feed", hours_ago: int = 0, domain: str | None = None, summary: str | None = None) -> Story:
    host = domain or f"{source.lower().replace(' ', '-')}.example.com"
    return Story(
        title=f"Story {i}",
        url=f"https://{host}/{i}",
        source=source,
        published=BASE - timedelta(hours=hours_ago),
        summary=summary if summary is not None else f"Details about story {i}. It launched today with 3 new features.",
    )


class ScriptedLLM:
    """Returns canned replies in order and records every prompt."""

    def __init__(self, *replies: str):
        self.replies, self.prompts = list(replies), []

    def complete(self, system: str, user: str) -> str:
        self.prompts.append((system, user))
        return self.replies.pop(0)


class NewsroomLLM:
    """Plays all three roles by looking at the system prompt, like the real model would."""

    def __init__(self, invent_fact: bool = True, editor_order: list[str] | None = None):
        self.calls: list[str] = []
        self.invent_fact = invent_fact
        self.editor_order = editor_order

    def complete(self, system: str, user: str) -> str:
        if "editor" in system:
            self.calls.append("editor")
            ids = re.findall(r"id=(\w+)", user)
            return json.dumps({"ranked": [{"story_id": i, "reason": "important"} for i in (self.editor_order or ids)]})
        if "research assistant" in system:
            self.calls.append("researcher")
            article = user.split("Article:\n", 1)[1]
            first = article.split(".")[0]
            facts = [{"claim": f"Claim: {first}", "quote": first}]
            if self.invent_fact:
                facts.append({"claim": "It costs $9 billion", "quote": "the company said it costs $9 billion"})
            return json.dumps({"facts": facts})
        if "writer" in system:
            self.calls.append("writer")
            ids = re.findall(r"id=(\w+)", user)
            return "```json\n" + json.dumps({
                "one_line": "A focused day in AI.",
                "items": [{"story_id": i, "title": f"Headline {n}", "summary": "Two sentences. Here.", "why_it_matters": "It saves time."}
                          for n, i in enumerate(ids)],
            }) + "\n```"
        raise AssertionError(f"unexpected system prompt: {system[:60]}")


def fake_reader(story: Story) -> tuple[str, str]:
    return (f"{story.title} was announced on Monday by the team. It ships with three new features and a free tier. "
            "Engineers can try it today.", "article")
