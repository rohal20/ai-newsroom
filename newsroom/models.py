"""Data models shared by every stage of the newsroom."""

from __future__ import annotations

import hashlib
from datetime import datetime

from pydantic import BaseModel, Field, HttpUrl, computed_field


class Story(BaseModel):
    """One news item, as collected from a feed."""

    title: str = Field(min_length=1)
    url: HttpUrl
    source: str = Field(description="Name of the feed the story came from")
    published: datetime | None = None
    summary: str = ""

    @computed_field  # type: ignore[prop-decorator]
    @property
    def id(self) -> str:
        """Stable short id derived from the URL, handy for logs and file names."""
        return hashlib.sha1(str(self.url).encode()).hexdigest()[:12]
