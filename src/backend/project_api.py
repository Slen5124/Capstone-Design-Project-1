"""Screen-facing HTTP routes. All mutations run in a scoped transaction."""
from datetime import date
from uuid import NAMESPACE_URL, UUID, uuid5

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.security import HTTPBearer
from sqlalchemy import insert, select, update

from project_models import Classification, Observation, Review, Submission
from project_service import digest, fail, monday


def create_router(s):
    router = APIRouter(prefix='/api/v1', tags=['프로젝트·현장·검토'])
    root = '/sites/{site_code}/schedules/{schedule_id}'
    task_root = root + '/tasks/{task_id}'

    def identity(request: Request, credentials=Depends(HTTPBearer(auto_error=False, scheme_name="ProjectBearer"))):
        return s.authenticate(request)

    @router.get('/me', summary='현재 사용자와 매장별 권한')
    def me(actor=Depends(identity)):
        if actor.get('kind', 'user') != 'user':
            return {'kind': actor['kind'], 'site_ids': actor['site_ids']}
        u, m = s.table('users'), s.table('site_memberships')
        with s.engine.connect() as c:
            user = c.execute(select(u.c.id, u.c.display_name).where(
                u.c.id == UUID(actor['user_id']), u.c.is_active.is_(True))).mappings().first()
            if not user:
                fail(401, '활성 사용자가 아닙니다.')
            rows = c.execute(select(m.c.site_id, m.c.role).where(
                m.c.user_id == user['id'], m.c.organization_id == UUID(actor['organization_id']),
                m.c.is_active.is_(True))).mappings().all()
            return {**dict(user), 'memberships': [dict(r) for r in rows]}

    @router.get('/sites', summary='세계지도에 표시할 권한 있는 매장')
    def sites(actor=Depends(identity)):
        if actor.get('kind', 'user') != 'user':
            fail(403, '사용자용 조회입니다.')
        sites, m, u = (s.table(n) for n in ('sites', 'site_memberships', 'users'))
        with s.engine.connect() as c:
            rows = c.execute(select(sites, m.c.role).join(m, m.c.site_id == sites.c.id)
                .join(u, u.c.id == m.c.user_id).where(
                    m.c.user_id == UUID(actor['user_id']), m.c.organization_id == UUID(actor['organization_id']),
                    m.c.is_active.is_(True), u.c.is_active.is_(True)).order_by(sites.c.code)).mappings()
            return [dict(r) for r in rows]

    @router.get('/sites/{site_code}/schedules', summary='매장의 공정표 버전')
    def schedules(site_code: str, actor=Depends(identity)):
        with s.engine.connect() as c:
            ctx = s.scope(c, site_code, actor, roles={'field', 'hq', 'viewer'})
            t = s.table('schedules')
            return [dict(r) for r in c.execute(select(t).where(t.c.site_id == ctx['site']['id'])
                                               .order_by(t.c.created_at.desc(), t.c.id)).mappings()]

    @router.get('/sites/{site_code}/overview', summary='선택 매장 요약·현지 시간·전체 및 주간 현황')
    def overview(site_code: str, actor=Depends(identity)):
        from zoneinfo import ZoneInfo
        from sqlalchemy import func
        with s.engine.connect() as c:
            ctx = s.scope(c, site_code, actor, roles={'field', 'hq', 'viewer'})
            schedules = s.table('schedules')
            schedule = c.execute(select(schedules).where(schedules.c.site_id == ctx['site']['id'])
                .order_by(schedules.c.created_at.desc(), schedules.c.id).limit(1)).mappings().first()
            local = s.clock().astimezone(ZoneInfo(ctx['site']['timezone']))
            if not schedule:
                return {'site': ctx['site'], 'local_now': local, 'schedule': None, 'status': '자료 연결 전'}
            ctx['schedule'] = dict(schedule)
            tasks = s.table('schedule_tasks')
            count = c.execute(select(func.count()).select_from(tasks).where(tasks.c.schedule_id == schedule['id'])).scalar_one()
            week = s.weekly(c, ctx, monday(local.date()))
            requests = s.table('change_requests')
            pending = c.execute(select(func.count()).select_from(requests).where(
                requests.c.site_id == ctx['site']['id'], requests.c.status == 'pending',
                requests.c.to_organization_id == ctx['organization_id'])).scalar_one()
            return {'site': ctx['site'], 'local_now': local, 'schedule': dict(schedule),
                    'total_tasks': count, 'weekly': week, 'incoming_change_requests': pending}

    @router.get('/sites/{site_code}/participants', summary='변경 요청을 주고받을 상대 회사')
    def participants(site_code: str, actor=Depends(identity)):
        with s.engine.connect() as c:
            ctx = s.scope(c, site_code, actor, roles={'field', 'hq'})
            m, users, organizations = (s.table(n) for n in ('site_memberships', 'users', 'organizations'))
            rows = c.execute(select(organizations.c.id, organizations.c.name).distinct()
                .join(m, m.c.organization_id == organizations.c.id).join(users, users.c.id == m.c.user_id)
                .where(m.c.site_id == ctx['site']['id'], m.c.organization_id != ctx['organization_id'],
                       m.c.role.in_(['hq', 'field']), m.c.is_active.is_(True), users.c.is_active.is_(True))
                .order_by(organizations.c.name)).mappings()
            return [dict(r) for r in rows]

    @router.get(root + '/field-home', summary='현장 관리자 이번 주 작업과 우선 확인 항목')
    def field_home(site_code: str, schedule_id: UUID, week_start: date | None = None, actor=Depends(identity)):
        from zoneinfo import ZoneInfo
        with s.engine.connect() as c:
            ctx = s.scope(c, site_code, actor, schedule_id, roles={'field', 'hq', 'viewer'})
            start = week_start or monday(s.clock().astimezone(ZoneInfo(ctx['site']['timezone'])).date())
            return s.weekly(c, ctx, start)

    @router.get(root + '/vision-input', summary='Vision 연결용 AI 대상 작업과 일일 증거 사진')
    def vision_input(site_code: str, schedule_id: UUID, week_start: date, actor=Depends(identity)):
        from datetime import timedelta
        with s.engine.connect() as c:
            ctx = s.scope(c, site_code, actor, schedule_id, roles={'vision'})
            weekly = s.weekly(c, ctx, week_start)
            daily = s.table('daily_records')
            items = []
            for task in weekly['tasks']:
                if task['processing_route'] != 'ai':
                    continue
                # Location links prevent a camera elsewhere in the same store supplying this task.
                links = s.table('schedule_task_locations')
                photos = c.execute(select(daily.c.id, daily.c.location_id, daily.c.observation_date,
                    daily.c.photo_file_id).join(links, links.c.location_id == daily.c.location_id).where(
                        links.c.task_id == task['id'], links.c.site_id == ctx['site']['id'],
                        daily.c.site_id == ctx['site']['id'], daily.c.observation_date >= week_start,
                        daily.c.observation_date <= week_start + timedelta(days=6))
                    .order_by(daily.c.observation_date, daily.c.id)).mappings()
                items.append({'task_id': task['id'], 'surface_type': task['surface_type'],
                              'task_name': task['task_name'], 'photos': [dict(p) for p in photos]})
            return {'tasks': items, 'formula': 'installed / (installed + uninstalled)',
                    'unobservable_rule': '가림·인식 불가는 분모에서 제외하고 installed_ratio=null로 보냅니다.',
                    'result_path': f'/api/v1/sites/{site_code}/schedules/{schedule_id}/tasks/{{task_id}}/observations'}

    @router.get(root + '/tasks', summary='간트 작업과 검토 상태 조회')
    def tasks(site_code: str, schedule_id: UUID, week_start: date | None = None,
              limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0), actor=Depends(identity)):
        with s.engine.connect() as c:
            ctx = s.scope(c, site_code, actor, schedule_id, roles={'field', 'hq', 'viewer'})
            if week_start:
                rows = s.weekly(c, ctx, week_start)['tasks']
                return {'items': rows[offset:offset + limit], 'total': len(rows)}
            t = s.table('schedule_tasks')
            rows = c.execute(select(t).where(t.c.schedule_id == schedule_id)
                .order_by(t.c.display_order, t.c.id).offset(offset).limit(limit)).mappings()
            return {'items': [s.task_data(c, {**ctx, 'task': dict(r)}) for r in rows],
                    'offset': offset, 'limit': limit}

    @router.get(task_root, summary='선택한 작업의 현장 보고와 AI 관측')
    def task(site_code: str, schedule_id: UUID, task_id: UUID, week_start: date | None = None, actor=Depends(identity)):
        with s.engine.connect() as c:
            ctx = s.scope(c, site_code, actor, schedule_id, task_id, roles={'field', 'hq', 'viewer'})
            if week_start:
                from datetime import timedelta
                if week_start.weekday() != 0:
                    fail(422, '주간 시작일은 월요일이어야 합니다.')
                ctx.update(_period_start=week_start, _period_finish=week_start + timedelta(days=6))
            return s.task_data(c, ctx)

    @router.get(task_root + '/history', summary='작업 제출·본사 검토·자동 이관 이력')
    def history(site_code: str, schedule_id: UUID, task_id: UUID,
                limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0), actor=Depends(identity)):
        with s.engine.connect() as c:
            ctx = s.scope(c, site_code, actor, schedule_id, task_id, roles={'field', 'hq', 'viewer'})
            rows = s.events(c, ctx)
            # Company request conversations have a separate participant-only endpoint.
            rows = [e for e in rows if e['request_id'] is None]
            return {'items': [s.public_event(c, ctx, r) for r in rows[offset:offset + limit]], 'total': len(rows)}

    @router.patch(task_root + '/classification', summary='본사 작업 분류와 확인 경로 설정')
    def classify(site_code: str, schedule_id: UUID, task_id: UUID, payload: Classification, actor=Depends(identity)):
        with s.engine.begin() as c:
            ctx = s.scope(c, site_code, actor, schedule_id, task_id, roles={'hq'}, lock=True)
            allowed = {'wall', 'floor', 'ceiling', 'fixture', 'mep', 'admin', 'other'}
            if (not payload.category_codes or set(payload.category_codes) - allowed or
                    len(set(payload.category_codes)) != len(payload.category_codes)):
                fail(422, '지원하는 작업 분류 코드를 지정하세요.')
            if payload.processing_route == 'ai' and not set(payload.category_codes) & {'wall', 'floor', 'ceiling'}:
                fail(422, 'AI 관측은 벽·바닥·천장 작업에 사용합니다.')
            surfaces = set(payload.category_codes) & {'wall', 'floor', 'ceiling'}
            primary = payload.surface_type or ctx['task']['surface_type']
            if payload.surface_type and payload.surface_type not in surfaces:
                fail(422, '대표 표면은 선택한 작업 분류에 포함되어야 합니다.')
            if primary not in surfaces:
                primary = next(iter(surfaces)) if len(surfaces) == 1 else None
            if payload.processing_route == 'ai' and primary is None:
                fail(422, '여러 표면 분류 중 대표 AI 관측 표면을 지정하세요.')
            values = {**payload.model_dump(), 'surface_type': primary}
            c.execute(update(s.table('schedule_tasks')).where(s.table('schedule_tasks').c.id == task_id)
                      .values(**values))
            s.event(c, ctx, 'classification_change', details=values)
            return {'task_id': task_id, **values}

    @router.post(task_root + '/submissions', status_code=201, summary='사진과 진행률 보고·재제출')
    def submit(site_code: str, schedule_id: UUID, task_id: UUID, payload: Submission,
               response: Response, actor=Depends(identity)):
        with s.engine.begin() as c:
            ctx = s.scope(c, site_code, actor, schedule_id, task_id, roles={'field'}, lock=True)
            fingerprint = digest(payload.model_dump(mode='json'))
            replay = s.replay(c, ctx, payload.id, fingerprint)
            if replay:
                response.status_code = 200
                return s.public_event(c, ctx, replay)
            if payload.captured_at > s.clock():
                fail(422, '미래 촬영 시각을 제출할 수 없습니다.')
            if payload.observation_id:
                obs = s.table('task_observations')
                target = c.execute(select(obs).where(obs.c.id == payload.observation_id,
                    obs.c.site_id == ctx['site']['id'], obs.c.task_id == task_id)).mappings().first()
                if not target or target['status'] not in {'unobserved', 'failed'}:
                    fail(422, '이 작업의 현장 확인 요청을 선택하세요.')
            previous = s.latest_submission(c, ctx)
            event = s.event(c, ctx, 'resubmission' if previous else 'submission', event_id=payload.id,
                body=payload.body, observation_id=payload.observation_id,
                details={'progress_percent': payload.progress_percent,
                         'captured_at': payload.captured_at.isoformat(),
                         'attempt': previous['details'].get('attempt', 0) + 1 if previous else 1,
                         'payload_digest': fingerprint})
            s.attach(c, ctx, event, payload.photo_file_ids, kind='photo')
            return s.public_event(c, ctx, event)

    @router.post(task_root + '/reviews', status_code=201, summary='본사 수락·반려·완료 확인')
    def review(site_code: str, schedule_id: UUID, task_id: UUID, payload: Review,
               response: Response, actor=Depends(identity)):
        with s.engine.begin() as c:
            ctx = s.scope(c, site_code, actor, schedule_id, task_id, roles={'hq'}, lock=True)
            fingerprint = digest(payload.model_dump(mode='json'))
            replay = s.replay(c, ctx, payload.id, fingerprint)
            if replay:
                response.status_code = 200
                return s.public_event(c, ctx, replay)
            events = s.events(c, ctx)
            if payload.target_event_id:
                latest = s.latest_submission(c, ctx)
                if not latest or latest['id'] != payload.target_event_id:
                    fail(409, '최신 제출을 다시 확인해 주세요.')
                if payload.outcome == 'complete' and latest['details']['progress_percent'] != 100:
                    fail(422, '100%로 보고된 증빙에만 완료를 확인할 수 있습니다.')
                if payload.outcome == 'complete':
                    attached = s.table('task_event_files')
                    files = c.execute(select(attached.c.file_id).where(attached.c.event_id == latest['id'])).scalars().all()
                    if not files:
                        fail(409, '완료를 증명할 사진이 없습니다.')
                    for file_id in files:
                        s.verify_bytes(s.file(c, ctx, file_id, kind='photo'))
                decided = any(e['target_event_id'] == latest['id'] and e['event_type'] in
                              {'review_accept', 'review_reject', 'completion_confirm'} for e in events)
            else:
                if payload.outcome == 'complete':
                    fail(422, 'AI 관측 수락은 작업 완료 확인과 구분됩니다.')
                observations = s.observations(c, ctx)
                if not any(o['id'] == payload.observation_id for o in observations):
                    fail(409, '현재 관측 수정본을 다시 확인하세요.')
                decided = any(e['observation_id'] == payload.observation_id and e['event_type'] in
                              {'review_accept', 'review_reject'} for e in events)
            if decided:
                fail(409, '이미 검토된 대상입니다. 새 증빙을 제출해 주세요.')
            event = s.event(c, ctx, {'accept': 'review_accept', 'reject': 'review_reject',
                'complete': 'completion_confirm'}[payload.outcome], event_id=payload.id,
                body=payload.body, target_event_id=payload.target_event_id,
                observation_id=payload.observation_id, details={'payload_digest': fingerprint})
            return s.public_event(c, ctx, event)

    @router.post(task_root + '/observations', status_code=201, summary='Vision 관측 등록과 현장 확인 자동 이관')
    def observe(site_code: str, schedule_id: UUID, task_id: UUID, payload: Observation,
                response: Response, actor=Depends(identity)):
        with s.engine.begin() as c:
            ctx = s.scope(c, site_code, actor, schedule_id, task_id, roles={'vision'}, lock=True)
            if ctx['task']['processing_route'] != 'ai':
                fail(422, 'AI 관측 대상으로 분류된 작업이 아닙니다.')
            t = s.table('task_observations')
            body = payload.model_dump()
            existing = c.execute(select(t).where(t.c.id == payload.id)).mappings().first()
            if existing:
                comparable = {k: existing[k] for k in body}
                if (existing['site_id'] != ctx['site']['id'] or existing['task_id'] != task_id or
                        digest(comparable) != digest(body)):
                    fail(409, '같은 관측 ID의 내용이 다릅니다.')
                response.status_code = 200
                return dict(existing)
            daily = s.table('daily_records')
            record = c.execute(select(daily).where(daily.c.id == payload.daily_record_id,
                               daily.c.site_id == ctx['site']['id'])).mappings().first()
            if not record:
                fail(404, '이 매장의 일일 사진 기록이 필요합니다.')
            links = s.table('schedule_task_locations')
            linked = c.execute(select(links.c.task_id).where(links.c.site_id == ctx['site']['id'],
                links.c.schedule_id == schedule_id, links.c.task_id == task_id,
                links.c.location_id == record['location_id'])).first()
            if not linked:
                fail(422, '작업과 일일 사진의 촬영 위치 연결이 필요합니다.')
            s.file(c, ctx, record['photo_file_id'], kind='photo')
            from zoneinfo import ZoneInfo
            if record['observation_date'] > s.clock().astimezone(ZoneInfo(ctx['site']['timezone'])).date():
                fail(422, '미래 관측 기록을 등록할 수 없습니다.')
            if payload.overlay_file_id:
                s.file(c, ctx, payload.overlay_file_id, kind='photo')
            duplicate = c.execute(select(t.c.id).where(t.c.task_id == task_id,
                t.c.daily_record_id == payload.daily_record_id, t.c.target_key == payload.target_key,
                t.c.view_key == payload.view_key, t.c.revision >= payload.revision)).first()
            if duplicate:
                fail(409, '이미 존재하는 관측 수정 번호입니다.')
            row = {**body, 'site_id': ctx['site']['id'], 'schedule_id': schedule_id,
                   'task_id': task_id, 'created_at': s.clock()}
            c.execute(insert(t).values(**row))
            if payload.status in {'unobserved', 'failed'}:
                s.event(c, ctx, 'workflow_state',
                    event_id=uuid5(NAMESPACE_URL, 'aloha-field-handoff:' + str(payload.id)),
                    observation_id=payload.id, body=payload.hold_reason,
                    details={'kind': 'field_handoff', 'target_key': payload.target_key,
                             'view_key': payload.view_key, 'status': 'requested'})
            return row

    return router
