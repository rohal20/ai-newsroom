"""Load and validate config.yaml."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field, HttpUrl


class Feed(BaseModel):
    name: str
    url: HttpUrl
    filter: bool = Field(default=False, description="Keep only stories matching the keywords")
    aggregator: bool = Field(default=False, description="Links point to other sites (e.g. Hacker News)")
    max_age_hours: int | None = Field(default=None, description="Overrides the global limit for this feed")


class ModelSpec(BaseModel):
    provider: str
    model: str


class LLMSettings(BaseModel):
    provider: str = "gemini"
    model: str = "gemini-3.8-flash"
    temperature: float = 0.3
    timeout_seconds: float = 120
    reasoning_effort: str | None = Field(default=None, description="low / medium / high; low is much faster on Gemini 3")
    min_interval_seconds: float = Field(default=0, ge=0, description="Pause between calls to stay under rate limits")
    max_retries: int = Field(default=2, ge=0)
    fallbacks: list[ModelSpec] = Field(default_factory=list, description="Tried in order if the main model fails")


class BriefingSettings(BaseModel):
    stories: int = Field(default=5, ge=1, le=10, description="How many stories go in the briefing")
    max_candidates: int = Field(default=40, ge=5, description="How many stories the editor chooses from")
    max_per_source: int = Field(default=8, ge=1)
    max_papers: int = Field(default=2, ge=0, description="At most this many research papers per edition")


class ResearchSettings(BaseModel):
    max_article_chars: int = Field(default=6000, ge=500, description="How much of each article the researcher reads")
    min_facts: int = Field(default=2, ge=1, description="Below this, fall back to the feed text")


class CriticSettings(BaseModel):
    enabled: bool = True
    max_rewrites: int = Field(default=2, ge=0, le=5, description="Rewrites before a story falls back to facts only")


class SiteSettings(BaseModel):
    title: str = Field(default="Morning Context", description="Name of the briefing on the website")
    base_url: str = Field(default="", description="Public address of the site, used for the RSS feed")
    repo_url: str = Field(default="", description="Link to the source code")
    schedule: str = Field(default="", description="Shown on the dashboard, e.g. Daily · 07:07 PKT")
    timezone: str = Field(default="UTC", description="Clock times on the site, e.g. Asia/Karachi")


class Config(BaseModel):
    topic: str
    max_age_hours: int = 36
    keywords: list[str] = []
    feeds: list[Feed]
    llm: LLMSettings = LLMSettings()
    briefing: BriefingSettings = BriefingSettings()
    research: ResearchSettings = ResearchSettings()
    critic: CriticSettings = CriticSettings()
    site: SiteSettings = SiteSettings()


def load_config(path: str | Path = "config.yaml") -> Config:
    with open(path, encoding="utf-8") as f:
        return Config.model_validate(yaml.safe_load(f))
