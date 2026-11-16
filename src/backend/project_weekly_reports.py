"""Frozen canonical report inputs, repeatable requests and leased Agent jobs."""
from datetime import date, datetime, time, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo
from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.security import HTTPBearer
from sqlalchemy import insert, select, update
from project_models import Decision, ReportClaim
from project_weekly_models import ReportRequest, ReportResult, VisionDelivery
from project_report_material import build_material, instant, latest_observations
from project_service import digest, fail, monday


def due_start(now, zone):
    local = instant(now).astimezone(ZoneInfo(zone))
    return monday(local.date()) if local.weekday() == 6 and local.hour >= 9 else monday(local.date()) - timedelta(days=7)


def create_router(s):
    router = APIRouter(prefix='/api/v1/weekly', tags=['주간 보고서·Agent 작업'])
    root = '/sites/{site_code}/schedules/{schedule_id}'

    def identity(request: Request, credentials=Depends(HTTPBearer(auto_error=False, scheme_name='ProjectBearer'))):
        return s.authenticate(request)

    def public(row):
        p = row['provenance']
        return {k: row[k] for k in ('id', 'schedule_id', 'period_start', 'period_finish', 'revision',
            'status', 'report_file_id', 'generated_at', 'approved_at')} | {
            'input_digest': p['input_digest'], 'mode': p.get('mode', 'draft'),
            'template_version': p.get('input', {}).get('template_version')}

    def find(c, code, report_id, actor, roles):
        ctx = s.scope(c, code, actor, roles=roles, lock=True)
        t = s.table('agent_reports')
        row = c.execute(select(t).where(t.c.id == report_id, t.c.site_id == ctx['site']['id'])).mappings().first()
        if not row:
            fail(404, '보고서 작업을 찾을 수 없습니다.')
        if row['provenance'].get('kind') != 'project_weekly_report':
            fail(409, '이 API에서 생성한 보고서 작업이 아닙니다.')
        return s.scope(c, code, actor, row['schedule_id'], roles=roles), dict(row)

    def create_job(c, ctx, payload, response, trigger):
        t, ev = s.table('agent_reports'), s.table('task_events')
        owner = str(ctx['user_id']) if ctx['user_id'] else digest(ctx['identity']['token_sha256'])
        fingerprint = digest({'schedule_id': str(ctx['schedule']['id']), 'site_id': str(ctx['site']['id']),
            **payload.model_dump(mode='json'), 'owner': owner, 'trigger': trigger})
        receipt = c.execute(select(ev).where(ev.c.id == payload.id)).mappings().first()
        if receipt:
            d = receipt['details']
            if d.get('kind') != 'report_request' or d.get('payload_digest') != fingerprint:
                fail(409, '같은 요청 ID의 내용이나 요청자가 다릅니다.')
            row = c.execute(select(t).where(t.c.id == UUID(d['report_id']), t.c.site_id == ctx['site']['id'])).mappings().one()
            response.status_code = 200
            return public(row) | {'reused': True}
        if c.execute(select(t.c.id).where(t.c.id == payload.id)).first():
            fail(409, '이미 사용 중인 보고서 ID입니다.')
        frozen = build_material(s, c, ctx, payload.week_start)
        if payload.mode == 'final' and not frozen['ready_for_final']:
            fail(409, {'message': '비전 최종 전달 또는 작업별 사진·수치가 부족합니다.', 'readiness': frozen['readiness']})
        frozen['generation_mode'] = payload.mode
        hashed = digest(frozen)
        previous = list(c.execute(select(t).where(t.c.site_id == ctx['site']['id'],
            t.c.schedule_id == ctx['schedule']['id'], t.c.period_start == payload.week_start)
            .order_by(t.c.revision.desc())).mappings())
        reusable = next((r for r in previous if r['status'] in {'pending', 'validated', 'approved'} and
            r['provenance'].get('kind') == 'project_weekly_report' and r['provenance'].get('input_digest') == hashed), None)
        if reusable:
            row = dict(reusable)
            if row['report_file_id']:
                s.verify_bytes(s.file(c, ctx, row['report_file_id']))
            response.status_code = 200
        else:
            row = dict(id=payload.id, site_id=ctx['site']['id'], schedule_id=ctx['schedule']['id'],
                period_start=payload.week_start, period_finish=payload.week_start + timedelta(days=4),
                revision=max((r['revision'] for r in previous), default=0) + 1, status='pending',
                generated_at=s.clock(), report_file_id=None, approved_by=None, approved_at=None,
                provenance={'kind': 'project_weekly_report', 'contract_version': frozen['schema_version'],
                    'input': frozen, 'input_digest': hashed, 'mode': payload.mode, 'requested_by': owner, 'trigger': trigger})
            c.execute(insert(t).values(**row))
        s.event(c, ctx, 'workflow_state', event_id=payload.id,
                details={'kind': 'report_request', 'report_id': str(row['id']), 'payload_digest': fingerprint})
        return public(row) | {'reused': reusable is not None}

    @router.get(root + '/report-input', summary='월~금 보고서 입력·일별 근거·누락 작업 조회')
    def inputs(site_code: str, schedule_id: UUID, week_start: date, actor=Depends(identity)):
        with s.engine.connect() as c:
            ctx = s.scope(c, site_code, actor, schedule_id, roles={'field', 'hq', 'viewer', 'agent'})
            data = build_material(s, c, ctx, week_start)
            return {**data, 'input_digest': digest(data)}

    @router.get(root + '/report-readiness', summary='비전 전달 완료 및 관리자 사진 필요 작업')
    def readiness(site_code: str, schedule_id: UUID, week_start: date, actor=Depends(identity)):
        with s.engine.connect() as c:
            ctx = s.scope(c, site_code, actor, schedule_id, roles={'field', 'hq', 'viewer', 'agent'})
            return build_material(s, c, ctx, week_start)['readiness']

    @router.post(root + '/vision-deliveries', status_code=201, summary='주차 비전 최종 전달 완료·누락 작업 계산')
    def delivery(site_code: str, schedule_id: UUID, payload: VisionDelivery, response: Response, actor=Depends(identity)):
        with s.engine.begin() as c:
            ctx = s.scope(c, site_code, actor, schedule_id, roles={'vision'}, lock=True)
            fingerprint = digest(payload.model_dump(mode='json'))
            ev = s.table('task_events')
            old = c.execute(select(ev).where(ev.c.id == payload.id)).mappings().first()
            if old:
                if (old['site_id'] != ctx['site']['id'] or old['schedule_id'] != schedule_id or
                    old['details'].get('kind') != 'vision_week_delivery' or old['details'].get('payload_digest') != fingerprint):
                    fail(409, '같은 전달 ID의 내용이 다릅니다.')
                response.status_code = 200
                return {'delivery_id': payload.id, 'revision': payload.revision, 'reused': True}
            close = datetime.combine(payload.week_start + timedelta(days=4), time(17), ZoneInfo(ctx['site']['timezone']))
            if instant(s.clock()) < close:
                fail(409, '금요일 작업 마감 이후 주차 최종 전달을 확정하세요.')
            observations = latest_observations(s, c, ctx, payload.week_start, payload.week_start + timedelta(days=4))
            if set(str(o['id']) for o in observations) != set(str(i) for i in payload.observation_ids):
                fail(409, '해당 주차의 최신 관측 ID 전체와 최종 전달 목록이 일치해야 합니다.')
            rows = c.execute(select(ev.c.details).where(ev.c.site_id == ctx['site']['id'],
                ev.c.schedule_id == schedule_id, ev.c.event_type == 'workflow_state')).scalars()
            revisions = [r['revision'] for r in rows if r.get('kind') == 'vision_week_delivery' and
                         r.get('week_start') == payload.week_start.isoformat()]
            if revisions and payload.revision <= max(revisions):
                fail(409, '새 비전 전달은 이전보다 높은 revision이 필요합니다.')
            s.event(c, ctx, 'workflow_state', event_id=payload.id,
                details={'kind': 'vision_week_delivery', 'week_start': payload.week_start.isoformat(),
                    'revision': payload.revision, 'observation_ids': sorted(str(i) for i in payload.observation_ids),
                    'payload_digest': fingerprint})
            return {'delivery_id': payload.id, 'revision': payload.revision, 'reused': False}

    @router.post(root + '/reports', status_code=201, summary='초안·최종 생성 요청; 동일 자료는 기존 작업 재사용')
    def create(site_code: str, schedule_id: UUID, payload: ReportRequest, response: Response, actor=Depends(identity)):
        with s.engine.begin() as c:
            ctx = s.scope(c, site_code, actor, schedule_id, roles={'hq'}, lock=True)
            return create_job(c, ctx, payload, response, 'manual')

    @router.get('/agent-projects', summary='Agent에게 허용된 매장·공정표·자동 생성 대상 주')
    def agent_projects(actor=Depends(identity)):
        if actor.get('kind') != 'agent':
            fail(403, '보고서 Agent 전용 조회입니다.')
        sites, schedules = s.table('sites'), s.table('schedules')
        with s.engine.connect() as c:
            result = []
            for site in c.execute(select(sites).where(sites.c.id.in_([UUID(i) for i in actor['site_ids']])).order_by(sites.c.code)).mappings():
                schedule = c.execute(select(schedules).where(schedules.c.site_id == site['id'])
                    .order_by(schedules.c.created_at.desc(), schedules.c.id.desc()).limit(1)).mappings().first()
                if schedule:
                    result.append({'site_code': site['code'], 'schedule_id': schedule['id'],
                        'timezone': site['timezone'], 'due_week_start': due_start(s.clock(), site['timezone'])})
            return {'items': result}

    @router.post(root + '/reports/auto', status_code=201, summary='현지 일요일 09시 이후 최종 보고서 자동 접수')
    def auto_create(site_code: str, schedule_id: UUID, payload: ReportRequest, response: Response, actor=Depends(identity)):
        with s.engine.begin() as c:
            ctx = s.scope(c, site_code, actor, schedule_id, roles={'agent'}, lock=True)
            if payload.mode != 'final' or payload.week_start != due_start(s.clock(), ctx['site']['timezone']):
                fail(409, '자동 생성 대상 주차의 최종 보고서만 접수할 수 있습니다.')
            return create_job(c, ctx, payload, response, 'scheduled')

    @router.get(root + '/reports', summary='생성 보고서와 최신 자료 변경 여부')
    def list_reports(site_code: str, schedule_id: UUID, limit: int = Query(100, ge=1, le=500),
                     offset: int = Query(0, ge=0), actor=Depends(identity)):
        with s.engine.connect() as c:
            ctx = s.scope(c, site_code, actor, schedule_id, roles={'field', 'hq', 'viewer'})
            t = s.table('agent_reports')
            rows = c.execute(select(t).where(t.c.site_id == ctx['site']['id'], t.c.schedule_id == schedule_id,
                t.c.provenance['kind'].as_string() == 'project_weekly_report')
                .order_by(t.c.period_start.desc(), t.c.revision.desc()).limit(limit).offset(offset)).mappings()
            results, cache = [], {}
            for row in rows:
                p = row['provenance']
                if p.get('kind') != 'project_weekly_report':
                    continue
                key = (row['period_start'], p.get('mode', 'draft'))
                if key not in cache:
                    value = build_material(s, c, ctx, key[0]); value['generation_mode'] = key[1]
                    cache[key] = digest(value)
                results.append(public(row) | {'stale': cache[key] != p['input_digest']})
            return {'items': results}

    @router.get('/sites/{site_code}/report-jobs', summary='Agent가 가져갈 미점유·점유 만료 생성 작업')
    def jobs(site_code: str, limit: int = Query(20, ge=1, le=100), actor=Depends(identity)):
        with s.engine.connect() as c:
            ctx = s.scope(c, site_code, actor, roles={'agent'}); t = s.table('agent_reports')
            rows = c.execute(select(t).where(t.c.site_id == ctx['site']['id'], t.c.status == 'pending',
                t.c.provenance['kind'].as_string() == 'project_weekly_report')
                .order_by(t.c.generated_at, t.c.id)).mappings()
            result = []
            for row in rows:
                p = row['provenance']
                if p.get('kind') != 'project_weekly_report':
                    continue
                lease = p.get('claim')
                if lease and instant(lease['expires_at']) > s.clock():
                    continue
                result.append(public(row))
                if len(result) == limit:
                    break
            return {'items': result}

    @router.get('/sites/{site_code}/report-jobs/{report_id}/input', summary='Agent 전용 불변 입력과 파일 해시')
    def job_input(site_code: str, report_id: UUID, actor=Depends(identity)):
        with s.engine.begin() as c:
            _, row = find(c, site_code, report_id, actor, {'agent'})
            return {**row['provenance']['input'], 'input_digest': row['provenance']['input_digest']}

    @router.post('/sites/{site_code}/report-jobs/{report_id}/claim', summary='Agent 생성 작업 15분 점유')
    def claim(site_code: str, report_id: UUID, payload: ReportClaim, actor=Depends(identity)):
        with s.engine.begin() as c:
            _, row = find(c, site_code, report_id, actor, {'agent'})
            if row['status'] != 'pending':
                fail(409, '실행 가능한 보고서 작업이 아닙니다.')
            p = dict(row['provenance']); lease, owner = p.get('claim'), digest(actor['token_sha256'])
            if lease and instant(lease['expires_at']) > s.clock():
                if lease['id'] != str(payload.id) or lease['owner'] != owner:
                    fail(409, '다른 실행이 이 작업을 처리하고 있습니다.')
                return {'claim_id': payload.id, 'expires_at': lease['expires_at']}
            expires = s.clock() + timedelta(minutes=15)
            p['claim'] = {'id': str(payload.id), 'owner': owner, 'expires_at': expires.isoformat()}
            t = s.table('agent_reports')
            c.execute(update(t).where(t.c.id == report_id).values(provenance=p))
            return {'claim_id': payload.id, 'expires_at': expires}

    @router.post('/sites/{site_code}/report-jobs/{report_id}/heartbeat', summary='현재 소유자의 유효한 점유 연장')
    def heartbeat(site_code: str, report_id: UUID, payload: ReportClaim, actor=Depends(identity)):
        with s.engine.begin() as c:
            _, row = find(c, site_code, report_id, actor, {'agent'})
            p = dict(row['provenance']); lease = p.get('claim', {})
            if (row['status'] != 'pending' or lease.get('id') != str(payload.id) or
                lease.get('owner') != digest(actor['token_sha256']) or
                instant(lease.get('expires_at', '1970-01-01T00:00:00+00:00')) <= s.clock()):
                fail(409, '유효한 점유가 필요합니다.')
            expires = s.clock() + timedelta(minutes=15)
            p['claim'] = {**lease, 'expires_at': expires.isoformat()}; t = s.table('agent_reports')
            c.execute(update(t).where(t.c.id == report_id).values(provenance=p))
            return {'claim_id': payload.id, 'expires_at': expires}

    @router.post('/sites/{site_code}/report-jobs/{report_id}/result', summary='입력·서식·파일 검증 결과 등록')
    def result(site_code: str, report_id: UUID, payload: ReportResult, actor=Depends(identity)):
        with s.engine.begin() as c:
            ctx, row = find(c, site_code, report_id, actor, {'agent'})
            p = dict(row['provenance']); lease = p.get('claim') or {}
            if p['input_digest'] != payload.input_digest or lease.get('owner') != digest(actor['token_sha256']):
                fail(409, '입력 또는 생성 작업 소유자가 일치하지 않습니다.')
            fingerprint = digest(payload.model_dump(mode='json'))
            if p.get('result_digest') == fingerprint:
                if row['report_file_id']:
                    s.verify_bytes(s.file(c, ctx, row['report_file_id']))
                return public(row)
            if row['status'] != 'pending' or lease.get('id') != str(payload.claim_id) or instant(lease['expires_at']) <= s.clock():
                fail(409, '유효한 생성 작업 점유가 필요합니다.')
            if payload.report_file_id:
                file = s.file(c, ctx, payload.report_file_id)
                if file['kind'] != 'report_pdf':
                    fail(422, '현재 생성기는 PDF 결과를 지원합니다.')
                s.verify_bytes(file)
                validation = payload.validation or {}; expected = p['input']
                for key in ('schema_version', 'template_version', 'generator_version'):
                    if validation.get(key) != expected.get(key):
                        fail(422, '검증한 입력 계약·생성기·서식 버전이 필요합니다.')
                if (validation.get('pdf_sha256') != file['sha256'] or
                    type(validation.get('page_count')) is not int or validation['page_count'] < 1 or
                    sorted(validation.get('verified_asset_ids', [])) != sorted(expected['asset_manifest']) or
                    validation.get('structure_checks_passed') is not True):
                    fail(422, '보고서 파일 해시·페이지·근거 검증이 일치하지 않습니다.')
            p.update(result_digest=fingerprint, error=payload.error, validation=payload.validation)
            row.update(status=payload.status, report_file_id=payload.report_file_id, provenance=p)
            t = s.table('agent_reports')
            c.execute(update(t).where(t.c.id == report_id).values(status=row['status'], report_file_id=row['report_file_id'], provenance=p))
            return public(row)

    @router.post('/sites/{site_code}/reports/{report_id}/decision', summary='검증된 최종 보고서의 본사 승인·반려')
    def approve(site_code: str, report_id: UUID, payload: Decision, actor=Depends(identity)):
        with s.engine.begin() as c:
            ctx, row = find(c, site_code, report_id, actor, {'hq'})
            p = dict(row['provenance']); fingerprint = digest(payload.model_dump(mode='json'))
            if p.get('decision_digest') == fingerprint and p.get('decided_by') == str(ctx['user_id']):
                return public(row)
            if row['status'] != 'validated':
                fail(409, '검증된 보고서만 검토할 수 있습니다.')
            if payload.outcome == 'accept':
                current = build_material(s, c, ctx, row['period_start']); current['generation_mode'] = p.get('mode', 'draft')
                if current['generation_mode'] != 'final' or not current['ready_for_approval'] or digest(current) != p['input_digest']:
                    fail(409, '검토가 끝난 최신 최종 보고서를 생성한 뒤 승인하세요.')
                s.verify_bytes(s.file(c, ctx, row['report_file_id']))
                row.update(status='approved', approved_by=ctx['user_id'], approved_at=s.clock())
            else:
                row.update(status='failed')
            p.update(decision_digest=fingerprint, decided_by=str(ctx['user_id']), decision_reason=payload.body)
            t = s.table('agent_reports')
            c.execute(update(t).where(t.c.id == report_id).values(status=row['status'], approved_by=row['approved_by'],
                approved_at=row['approved_at'], provenance=p))
            return public(row)

    return router
