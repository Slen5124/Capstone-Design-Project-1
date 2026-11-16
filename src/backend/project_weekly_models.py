"""Request models for the opt-in weekly report API."""
from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator
from project_models import Input


class ReportRequest(Input):
    id: UUID
    week_start: date
    mode: Literal['draft', 'final'] = 'draft'

    @model_validator(mode='after')
    def valid(self):
        if self.week_start.weekday() != 0:
            raise ValueError('주간 시작일은 월요일이어야 합니다.')
        return self


class ReportResult(Input):
    claim_id: UUID
    status: Literal['validated', 'failed']
    input_digest: str = Field(pattern=r'^[0-9a-f]{64}$')
    report_file_id: UUID | None = None
    error: str = Field(default='', max_length=2000)
    validation: dict | None = None

    @model_validator(mode='after')
    def valid(self):
        if self.status == 'validated' and self.report_file_id is None:
            raise ValueError('검증한 보고서 파일이 필요합니다.')
        if self.status == 'failed' and (not self.error or self.report_file_id):
            raise ValueError('실패 사유를 입력하고 결과 파일은 생략하세요.')
        return self


class VisionDelivery(Input):
    id: UUID
    week_start: date
    revision: int = Field(ge=1)
    observation_ids: list[UUID] = Field(default_factory=list, max_length=10000)

    @model_validator(mode='after')
    def valid(self):
        if self.week_start.weekday() != 0:
            raise ValueError('주차 시작일은 월요일이어야 합니다.')
        if len(self.observation_ids) != len(set(self.observation_ids)):
            raise ValueError('관측 ID가 중복되었습니다.')
        return self
