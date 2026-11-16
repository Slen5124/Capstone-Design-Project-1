"""Request models for the read-only project copilot."""
from datetime import date
from typing import Literal
from uuid import UUID, uuid4

from pydantic import Field, model_validator
from project_models import Input


class CopilotTurn(Input):
    """A prior browser turn supplied only to preserve the current conversation."""
    role: Literal['user', 'assistant']
    content: str = Field(min_length=1, max_length=2000)


class CopilotQuestion(Input):
    """Read-only, site-scoped question for the Qwen site copilot."""
    message: str = Field(min_length=1, max_length=2000)
    request_id: UUID = Field(default_factory=uuid4)
    week_start: date | None = None
    history: list[CopilotTurn] = Field(default_factory=list, max_length=10)
    page: Literal['home', 'schedule', 'review', 'report', 'field'] | None = None

    @model_validator(mode='after')
    def valid(self):
        if self.week_start and self.week_start.weekday() != 0:
            raise ValueError('보고 주차 시작일은 월요일이어야 합니다.')
        return self
