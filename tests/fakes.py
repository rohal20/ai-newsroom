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


PLANTED = " Backed by Acme Corp with $9 billion."


class NewsroomLLM:
    """Plays every role by looking at the system prompt, like the real model would.

    plant_error: the writer's first draft of the first story adds a made-up company and amount.
    revise_fixes: whether the writer's rewrite actually removes them.
    critic_down: the AI critic is unavailable (only the code number check runs).
    """

    def __init__(self, invent_fact: bool = True, editor_order: list[str] | None = None,
                 plant_error: bool = True, revise_fixes: bool = True, critic_down: bool = False):
        self.calls: list[str] = []
        self.invent_fact = invent_fact
        self.editor_order = editor_order
        self.plant_error, self.revise_fixes, self.critic_down = plant_error, revise_fixes, critic_down

    def complete(self, system: str, user: str) -> str:
        if "fact-checking critic" in system:
            self.calls.append("critic")
            if self.critic_down:
                from newsroom.llm import LLMError
                raise LLMError("critic model overloaded")
            stories = []
            for block in user.split("\n\n"):
                m = re.search(r"story_id=(\w+)", block)
                if m:
                    draft = block.split("DRAFT:", 1)[1]
                    issues = [{"text": "Acme Corp", "problem": "company not in the facts"}] if "Acme Corp" in draft else []
                    stories.append({"story_id": m.group(1), "issues": issues})
            return json.dumps({"stories": stories})
        if "fact-checker reviewed" in system:
            self.calls.append("revise")
            items = []
            for block in user.split("\n\nid=")[0:]:
                m = re.search(r"id=(\w+)", block if block.startswith("id=") else "id=" + block)
                if not m:
                    continue
                summary = "Two sentences. Here." if self.revise_fixes else "Two sentences. Here." + PLANTED
                items.append({"story_id": m.group(1), "title": "Fixed headline", "summary": summary,
                              "why_it_matters": "It saves time."})
            return json.dumps({"items": items})
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
                "items": [{"story_id": i, "title": f"Headline {chr(65 + n)}",
                           "summary": "Two sentences. Here." + (PLANTED if (self.plant_error and n == 0) else ""),
                           "why_it_matters": "It saves time."}
                          for n, i in enumerate(ids)],
            }) + "\n```"
        raise AssertionError(f"unexpected system prompt: {system[:60]}")


def fake_reader(story: Story) -> tuple[str, str]:
    return (f"{story.title} was announced on Monday by the team. It ships with three new features and a free tier. "
            "Engineers can try it today.", "article")
