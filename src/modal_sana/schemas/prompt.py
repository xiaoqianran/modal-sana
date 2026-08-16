from __future__ import annotations

from pydantic import BaseModel, Field


class ParsedPromptFile(BaseModel):
    path: str
    format: str
    prompts: list[str] = Field(default_factory=list)
    count: int = 0
