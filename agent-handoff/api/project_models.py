"""Public request contracts. Identity and database scope never come from a body."""
from datetime import date, datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Input(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True, allow_inf_nan=False)


Percent = Annotated[float, Field(strict=True, ge=0, le=100)]


class Submission(Input):
    id: UUID
    photo_file_ids: list[UUID] = Field(min_length=1, max_length=10)
    captured_at: datetime
    progress_percent: Percent = 100
    body: str = Field(default='', max_length=4000)
    observation_id: UUID | None = None

    @model_validator(mode='after')
    def valid(self):
        if self.captured_at.utcoffset() is None:
            raise ValueError('촬영 시각에 시간대가 필요합니다.')
        if len(set(self.photo_file_ids)) != len(self.photo_file_ids):
            raise ValueError('사진을 중복 선택할 수 없습니다.')
        if self.progress_percent < 100 and not self.body:
            raise ValueError('남은 작업 사유가 필요합니다.')
        return self


class Review(Input):
    id: UUID
    outcome: Literal['accept', 'reject', 'complete']
    target_event_id: UUID | None = None
    observation_id: UUID | None = None
    body: str = Field(default='', max_length=4000)

    @model_validator(mode='after')
    def valid(self):
        if (self.target_event_id is None) == (self.observation_id is None):
            raise ValueError('제출 또는 관측 중 한 가지 검토 대상을 지정하세요.')
        if self.outcome == 'reject' and not self.body:
            raise ValueError('반려 사유가 필요합니다.')
        return self


class Observation(Input):
    id: UUID
    daily_record_id: UUID
    target_key: str = Field(min_length=1, max_length=128)
    view_key: str = Field(min_length=1, max_length=128)
    revision: int = Field(default=1, ge=1, le=100000)
    status: Literal['observed', 'unobserved', 'held', 'failed']
    installed_ratio: Annotated[float, Field(strict=True, ge=0, le=1)] | None = None
    hold_reason: str | None = Field(default=None, max_length=4000)
    overlay_file_id: UUID | None = None

    @model_validator(mode='after')
    def valid(self):
        if (self.status == 'observed') != (self.installed_ratio is not None):
            raise ValueError('관측 성공에만 설치 비율을 기록합니다. 가림은 0%가 아닙니다.')
        if self.status != 'observed' and not self.hold_reason:
            raise ValueError('관측 불가 사유가 필요합니다.')
        return self


class Classification(Input):
    processing_route: Literal['ai', 'field', 'manual_check']
    category_codes: list[Literal['wall', 'floor', 'ceiling', 'fixture', 'mep', 'admin', 'other']] = Field(min_length=1, max_length=7)
    surface_type: Literal['wall', 'floor', 'ceiling'] | None = None


class ChangeRequest(Input):
    id: UUID
    recipient_org_id: UUID
    request_type: Literal['schedule', 'extension', 'work']
    expected_start: date
    expected_finish: date
    proposed_start: date | None = None
    proposed_finish: date | None = None
    proposed_work: str | None = Field(default=None, max_length=4000)
    reason: str = Field(min_length=1, max_length=4000)
    file_ids: list[UUID] = Field(default_factory=list, max_length=5)

    @model_validator(mode='after')
    def valid(self):
        if self.request_type == 'work':
            if not self.proposed_work or self.proposed_start or self.proposed_finish:
                raise ValueError('작업 변경에는 변경할 작업 내용만 지정하세요.')
        elif not (self.proposed_start or self.proposed_finish) or self.proposed_work:
            raise ValueError('일정 변경에는 변경할 날짜를 지정하세요.')
        if self.request_type == 'extension' and self.proposed_finish is None:
            raise ValueError('추가 기간 요청 종료일이 필요합니다.')
        if (self.proposed_start or self.expected_start) > (self.proposed_finish or self.expected_finish):
            raise ValueError('종료일이 시작일보다 빠릅니다.')
        if len(set(self.file_ids)) != len(self.file_ids):
            raise ValueError('첨부파일이 중복되었습니다.')
        return self


class Message(Input):
    id: UUID
    body: str = Field(default='', max_length=4000)
    file_ids: list[UUID] = Field(default_factory=list, max_length=5)

    @model_validator(mode='after')
    def valid(self):
        if not self.body and not self.file_ids:
            raise ValueError('내용이나 첨부파일이 필요합니다.')
        if len(set(self.file_ids)) != len(self.file_ids):
            raise ValueError('첨부파일이 중복되었습니다.')
        return self


class Decision(Input):
    id: UUID
    outcome: Literal['accept', 'reject']
    body: str = Field(default='', max_length=4000)

    @model_validator(mode='after')
    def valid(self):
        if self.outcome == 'reject' and not self.body:
            raise ValueError('반려 사유가 필요합니다.')
        return self


class ReportRequest(Input):
    id: UUID
    week_start: date

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

    @model_validator(mode='after')
    def valid(self):
        if self.status == 'validated' and self.report_file_id is None:
            raise ValueError('검증한 보고서 파일이 필요합니다.')
        if self.status == 'failed' and (not self.error or self.report_file_id):
            raise ValueError('실패 사유를 입력하고 결과 파일은 생략하세요.')
        return self


class ReportClaim(Input):
    id: UUID
