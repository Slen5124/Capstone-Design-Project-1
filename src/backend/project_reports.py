"""Frozen weekly report inputs and a persisted Agent job/result contract."""
from datetime import datetime, time, timedelta, timezone
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.security import HTTPBearer
from sqlalchemy import insert, select, update

from project_models import Decision, ReportClaim, ReportRequest, ReportResult
from project_service import digest, fail


def create_router(s):
    router = APIRouter(prefix='/api/v1', tags=['주간 보고서·Agent 작업'])
    root = '/sites/{site_code}/schedules/{schedule_id}'

    def identity(request: Request, credentials=Depends(HTTPBearer(auto_error=False, scheme_name="ProjectBearer"))):
        return s.authenticate(request)

    def material(c, ctx, start):
        cutoff = datetime.combine(start + timedelta(days=7), time.min,
                                  ZoneInfo(ctx['site']['timezone'])).astimezone(timezone.utc)
        ctx = {**ctx, '_as_of': cutoff}
        current = s.weekly(c, ctx, start)
        current.pop('local_now', None)
        following = s.weekly(c, ctx, start + timedelta(days=7))
        current['next_week_tasks'] = following['tasks']
        previous_cutoff = datetime.combine(start, time.min, ZoneInfo(ctx['site']['timezone'])).astimezone(timezone.utc)
        previous = {}
        for item in current['tasks']:
            if not item['continued_from_previous_week']:
                continue
            task = dict(c.execute(select(s.table('schedule_tasks')).where(
                s.table('schedule_tasks').c.id == item['id'])).mappings().one())
            previous[str(item['id'])] = s.task_data(c, {**ctx, 'task': task, '_as_of': previous_cutoff,
                '_period_start': start - timedelta(days=7), '_period_finish': start - timedelta(days=1)})
        current['previous_week'] = previous
        current['comparison_rule'] = '이번 주 처음 시작한 작업에는 지난주 설명을 넣지 않습니다.'
        current['ready_for_approval'] = all(t['status'] in {'confirmed', 'accepted', 'ai_observation_accepted'} and not t['handoffs']
                                            for t in current['tasks']) and bool(current['tasks'])
        return jsonable_encoder(current)

    def find(c, site_code, report_id, actor, roles):
        ctx = s.scope(c, site_code, actor, roles=roles, lock=True)
        t = s.table('agent_reports')
        row = c.execute(select(t).where(t.c.id == report_id, t.c.site_id == ctx['site']['id'])).mappings().first()
        if not row:
            fail(404, '보고서 작업을 찾을 수 없습니다.')
        ctx = s.scope(c, site_code, actor, row['schedule_id'], roles=roles)
        return ctx, dict(row)

    def public(row):
        return {k: row[k] for k in ('id', 'schedule_id', 'period_start', 'period_finish', 'revision',
                                    'status', 'report_file_id', 'generated_at', 'approved_at')} | {
            'input_digest': row['provenance']['input_digest']}

    @router.get(root + '/report-input', summary='이번 주 비교·다음 주 작업과 보고서 준비 상태')
    def inputs(site_code: str, schedule_id: UUID, week_start: str, actor=Depends(identity)):
        from datetime import date
        try:
            day = date.fromisoformat(week_start)
        except ValueError:
            fail(422, '날짜는 YYYY-MM-DD 형식으로 입력하세요.')
        with s.engine.connect() as c:
            ctx = s.scope(c, site_code, actor, schedule_id, roles={'field', 'hq', 'viewer'})
            data = material(c, ctx, day)
            return {**data, 'input_digest': digest(data)}

    @router.post(root + '/reports', status_code=201, summary='주간 보고서 생성 작업 접수·입력 고정')
    def create(site_code: str, schedule_id: UUID, payload: ReportRequest, response: Response, actor=Depends(identity)):
        with s.engine.begin() as c:
            ctx = s.scope(c, site_code, actor, schedule_id, roles={'hq'}, lock=True)
            t = s.table('agent_reports')
            existing = c.execute(select(t).where(t.c.id == payload.id)).mappings().first()
            if existing:
                if (existing['site_id'] != ctx['site']['id'] or existing['schedule_id'] != schedule_id or
                        existing['period_start'] != payload.week_start or
                        existing['provenance'].get('requested_by') != str(ctx['user_id'])):
                    fail(409, '같은 보고서 ID의 요청이 다릅니다.')
                response.status_code = 200
                return public(existing)
            previous = c.execute(select(t.c.revision).where(t.c.schedule_id == schedule_id,
                t.c.period_start == payload.week_start).order_by(t.c.revision.desc())).scalar()
            frozen = material(c, ctx, payload.week_start)
            row = dict(id=payload.id, site_id=ctx['site']['id'], schedule_id=schedule_id,
                period_start=payload.week_start, period_finish=payload.week_start + timedelta(days=6),
                revision=(previous or 0) + 1, status='pending', generated_at=s.clock(),
                report_file_id=None, approved_by=None, approved_at=None,
                provenance={'kind': 'project_api_report', 'input': frozen, 'input_digest': digest(frozen),
                            'requested_by': str(ctx['user_id'])})
            c.execute(insert(t).values(**row))
            return public(row)

    @router.get(root + '/reports', summary='주차별 생성 보고서와 최신 자료 변경 여부')
    def list_reports(site_code: str, schedule_id: UUID, limit: int = Query(100, ge=1, le=500),
                     offset: int = Query(0, ge=0), actor=Depends(identity)):
        with s.engine.connect() as c:
            ctx = s.scope(c, site_code, actor, schedule_id, roles={'field', 'hq', 'viewer'})
            t = s.table('agent_reports')
            rows = c.execute(select(t).where(t.c.site_id == ctx['site']['id'], t.c.schedule_id == schedule_id)
                .order_by(t.c.period_start.desc(), t.c.revision.desc()).limit(limit).offset(offset)).mappings()
            return {'items': [public(r) | {'stale': digest(material(c, ctx, r['period_start'])) !=
                r['provenance']['input_digest']} for r in rows if r['provenance'].get('kind') == 'project_api_report']}

    @router.get('/sites/{site_code}/report-jobs', summary='Agent가 가져갈 생성 대기 작업')
    def jobs(site_code: str, limit: int = Query(20, ge=1, le=100), actor=Depends(identity)):
        with s.engine.connect() as c:
            ctx = s.scope(c, site_code, actor, roles={'agent'})
            t = s.table('agent_reports')
            rows = c.execute(select(t).where(t.c.site_id == ctx['site']['id'], t.c.status == 'pending')
                .order_by(t.c.generated_at).limit(limit)).mappings()
            return {'items': [public(r) for r in rows if r['provenance'].get('kind') == 'project_api_report']}

    @router.get('/sites/{site_code}/report-jobs/{report_id}/input', summary='Agent 전용 고정 입력과 근거 자료')
    def job_input(site_code: str, report_id: UUID, actor=Depends(identity)):
        with s.engine.begin() as c:
            ctx, row = find(c, site_code, report_id, actor, {'agent'})
            if row['provenance'].get('kind') != 'project_api_report':
                fail(409, '이 API에서 생성한 보고서 작업이 아닙니다.')
            return {**row['provenance']['input'], 'input_digest': row['provenance']['input_digest']}

    @router.post('/sites/{site_code}/report-jobs/{report_id}/claim', summary='Agent 작업 15분 점유·중복 실행 방지')
    def claim(site_code: str, report_id: UUID, payload: ReportClaim, actor=Depends(identity)):
        with s.engine.begin() as c:
            ctx, row = find(c, site_code, report_id, actor, {'agent'})
            if row['status'] != 'pending' or row['provenance'].get('kind') != 'project_api_report':
                fail(409, '실행 가능한 보고서 작업이 아닙니다.')
            provenance = dict(row['provenance'])
            lease = provenance.get('claim')
            owner = digest(actor['token_sha256'])
            if lease and datetime.fromisoformat(lease['expires_at']) > s.clock():
                if lease['id'] == str(payload.id) and lease['owner'] == owner:
                    return {'claim_id': payload.id, 'expires_at': lease['expires_at']}
                fail(409, '다른 실행이 이 작업을 처리하고 있습니다.')
            expires = s.clock() + timedelta(minutes=15)
            provenance['claim'] = {'id': str(payload.id), 'owner': owner, 'expires_at': expires.isoformat()}
            c.execute(update(s.table('agent_reports')).where(s.table('agent_reports').c.id == report_id)
                      .values(provenance=provenance))
            return {'claim_id': payload.id, 'expires_at': expires}

    @router.post('/sites/{site_code}/report-jobs/{report_id}/result', summary='Agent 생성 결과·실패 사유 등록')
    def result(site_code: str, report_id: UUID, payload: ReportResult, actor=Depends(identity)):
        with s.engine.begin() as c:
            ctx, row = find(c, site_code, report_id, actor, {'agent'})
            provenance = dict(row['provenance'])
            if provenance.get('kind') != 'project_api_report' or provenance['input_digest'] != payload.input_digest:
                fail(409, '보고서 생성 입력이 일치하지 않습니다.')
            fingerprint = digest(payload.model_dump(mode='json'))
            if provenance.get('result_digest') == fingerprint:
                return public(row)
            if row['status'] != 'pending':
                fail(409, '이미 결과가 등록되었습니다. 새 생성 작업을 요청하세요.')
            lease = provenance.get('claim') or {}
            if (lease.get('id') != str(payload.claim_id) or lease.get('owner') != digest(actor['token_sha256']) or
                    datetime.fromisoformat(lease.get('expires_at', '1970-01-01T00:00:00+00:00')) <= s.clock()):
                fail(409, '유효한 작업 점유가 필요합니다. 점유 만료 시 다시 요청하세요.')
            if payload.report_file_id:
                file = s.file(c, ctx, payload.report_file_id)
                if file['kind'] not in {'report_pdf', 'report_docx'}:
                    fail(422, 'Agent가 업로드한 보고서 파일이 필요합니다.')
            provenance.update(result_digest=fingerprint, error=payload.error)
            row.update(status=payload.status, report_file_id=payload.report_file_id, provenance=provenance)
            c.execute(update(s.table('agent_reports')).where(s.table('agent_reports').c.id == report_id)
                .values(status=row['status'], report_file_id=row['report_file_id'], provenance=provenance))
            return public(row)

    @router.post('/sites/{site_code}/reports/{report_id}/decision', summary='본사 최종 보고서 승인·반려')
    def approve(site_code: str, report_id: UUID, payload: Decision, actor=Depends(identity)):
        with s.engine.begin() as c:
            ctx, row = find(c, site_code, report_id, actor, {'hq'})
            provenance = dict(row['provenance'])
            if provenance.get('kind') != 'project_api_report':
                fail(409, '이 API에서 생성한 보고서 작업이 아닙니다.')
            fingerprint = digest(payload.model_dump(mode='json'))
            if provenance.get('decision_digest') == fingerprint and provenance.get('decided_by') == str(ctx['user_id']):
                return public(row)
            if row['status'] != 'validated':
                fail(409, 'Agent 결과 등록을 마친 보고서만 검토할 수 있습니다.')
            if payload.outcome == 'accept':
                current = material(c, ctx, row['period_start'])
                if not current['ready_for_approval'] or digest(current) != provenance['input_digest']:
                    fail(409, '미검토 작업 또는 변경된 자료가 있습니다. 검토 후 새 보고서를 생성하세요.')
                s.verify_bytes(s.file(c, ctx, row['report_file_id']))
                row.update(status='approved', approved_by=ctx['user_id'], approved_at=s.clock())
            else:
                row.update(status='failed')
            provenance.update(decision_digest=fingerprint, decided_by=str(ctx['user_id']), decision_reason=payload.body)
            c.execute(update(s.table('agent_reports')).where(s.table('agent_reports').c.id == report_id).values(
                status=row['status'], approved_by=row['approved_by'], approved_at=row['approved_at'], provenance=provenance))
            return public(row)

    return router
