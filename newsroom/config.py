"""Load and validate config.yaml."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field, HttpUrl


class Feed(BaseModel):
    name: str
    url: HttpUrl
    filter: bool = Field(default=False, description="Keep only stories matching the keywords")


class LLMSettings(BaseModel):
    provider: str = "gemini"
    model: str = "gemini-3.8-flash"
    temperature: float = 0.3


class BriefingSettings(BaseModel):
    stories: int = Field(default=5, ge=1, le=10, description="How many stories go in the briefing")
    max_candidates: int = Field(default=40, ge=5, description="How many stories the model gets to choose from")
    max_per_source: int = Field(default=8, ge=1)


class Config(BaseModel):
    topic: str
    max_age_hours: int = 36
    keywords: list[str] = []
    feeds: list[Feed]
    llm: LLMSettings = LLMSettings()
    briefing: BriefingSettings = BriefingSettings()


def load_config(path: str | Path = "config.yaml") -> Config:
    with open(path, encoding="utf-8") as f:
        return Config.model_validate(yaml.safe_load(f))
