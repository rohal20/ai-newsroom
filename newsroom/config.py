"""Load and validate config.yaml."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field, HttpUrl


class Feed(BaseModel):
    name: str
    url: HttpUrl
    filter: bool = Field(default=False, description="Keep only stories matching the keywords")


class Config(BaseModel):
    topic: str
    max_age_hours: int = 36
    keywords: list[str] = []
    feeds: list[Feed]


def load_config(path: str | Path = "config.yaml") -> Config:
    with open(path, encoding="utf-8") as f:
        return Config.model_validate(yaml.safe_load(f))
