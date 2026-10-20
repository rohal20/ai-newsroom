"""Data models shared by every stage of the newsroom."""

from __future__ import annotations

import hashlib
from datetime import datetime
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, HttpUrl, computed_field


class Story(BaseModel):
    """One news item, as collected from a feed."""

    title: str = Field(min_length=1)
    url: HttpUrl
    source: str = Field(description="Name of the feed the story came from")
    published: datetime | None = None
    summary: str = ""
    aggregated: bool = Field(default=False, description="Found through an aggregator such as Hacker News")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def id(self) -> str:
        """Stable short id derived from the URL, handy for logs and file names."""
        return hashlib.sha1(str(self.url).encode()).hexdigest()[:12]

    @property
    def domain(self) -> str:
        return (urlsplit(str(self.url)).hostname or "").removeprefix("www.")

    @property
    def is_paper(self) -> bool:
        return self.domain == "arxiv.org" or self.source.lower().startswith("arxiv")

    @property
    def display_source(self) -> str:
        """'TechCrunch AI', or 'arxiv.org via Hacker News' for stories found through an aggregator."""
        return f"{self.domain} via {self.source}" if self.aggregated else self.source
