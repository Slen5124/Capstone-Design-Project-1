"""Company-to-company changes, conversation and atomic acceptance/application."""
from uuid import NAMESPACE_URL, UUID, uuid5

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.security import HTTPBearer
from sqlalchemy import insert, select, update

from project_models import ChangeRequest, Decision, Message
from project_service import digest, fail


def create_router(s):
    router = APIRouter(prefix='/api/v1', tags=['회사 간 일정·작업 조정'])
    root = '/sites/{site_code}/schedules/{schedule_id}'

    def identity(request: Request, credentials=Depends(HTTPBearer(auto_error=False, scheme_name="ProjectBearer"))):
        return s.authenticate(request)

    def dates(task):
        return task['effective_start'] or task['start'], task['effective_finish'] or task['finish']

    def baseline(c, ctx):
        work = [e for e in s.events(c, ctx) if e['event_type'] == 'work_apply']
        return digest({'dates': dates(ctx['task']), 'work_revisions': sorted(str(e['id']) for e in work)})

    def lookup(c, site_code, schedule_id, request_id, actor, lock=False):
        ctx = s.scope(c, site_code, actor, schedule_id, roles={'field', 'hq'}, lock=lock)
        t = s.table('change_requests')
        item = c.execute(select(t).where(t.c.id == request_id, t.c.site_id == ctx['site']['id'],
                                        t.c.schedule_id == schedule_id)).mappings().first()
        if item is None:
            fail(404, '요청을 찾을 수 없습니다.')
        if ctx['organization_id'] not in {item['from_organization_id'], item['to_organization_id']}:
            fail(403, '요청 참여 회사만 열람할 수 있습니다.')
        ctx = s.scope(c, site_code, actor, schedule_id, item['task_id'], roles={'field', 'hq'})
        return ctx, dict(item)

    def request_events(c, ctx, request_id):
        return [e for e in s.events(c, ctx) if e['request_id'] == request_id]

    def output(c, ctx, item):
        return {**item, 'task_name': ctx['task']['task_name'], 'source_task_id': ctx['task']['source_task_id'],
                'messages': [s.public_event(c, ctx, e) for e in request_events(c, ctx, item['id'])],
                'can_decide': item['status'] == 'pending' and ctx['organization_id'] == item['to_organization_id']}

    @router.get(root + '/change-requests', summary='양사 간 변경 요청과 답변 대기 목록')
    def list_requests(site_code: str, schedule_id: UUID, status: str | None = None,
                      limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0), actor=Depends(identity)):
        with s.engine.connect() as c:
            ctx = s.scope(c, site_code, actor, schedule_id, roles={'field', 'hq'})
            t, tasks = s.table('change_requests'), s.table('schedule_tasks')
            query = select(t, tasks.c.task_name, tasks.c.source_task_id).join(tasks, tasks.c.id == t.c.task_id).where(t.c.site_id == ctx['site']['id'], t.c.schedule_id == schedule_id,
                (t.c.from_organization_id == ctx['organization_id']) | (t.c.to_organization_id == ctx['organization_id']))
            if status:
                if status not in {'pending', 'accepted', 'rejected', 'applied'}:
                    fail(422, '지원하지 않는 요청 상태입니다.')
                query = query.where(t.c.status == status)
            return {'items': [dict(r) for r in c.execute(query.order_by(t.c.created_at.desc(), t.c.id)
                                  .limit(limit).offset(offset)).mappings()]}

    @router.post(root + '/tasks/{task_id}/change-requests', status_code=201, summary='일정·기간·작업 변경 요청과 첨부 등록')
    def create(site_code: str, schedule_id: UUID, task_id: UUID, payload: ChangeRequest,
               response: Response, actor=Depends(identity)):
        with s.engine.begin() as c:
            ctx = s.scope(c, site_code, actor, schedule_id, task_id, roles={'field', 'hq'}, lock=True)
            fingerprint = digest(payload.model_dump(mode='json'))
            replay = s.replay(c, ctx, payload.id, fingerprint)
            t = s.table('change_requests')
            if replay:
                response.status_code = 200
                return output(c, ctx, dict(c.execute(select(t).where(t.c.id == payload.id)).mappings().one()))
            if c.execute(select(t.c.id).where(t.c.id == payload.id)).first():
                fail(409, '이미 사용한 요청 ID입니다.')
            m, users = s.table('site_memberships'), s.table('users')
            recipient = c.execute(select(m.c.user_id).join(users, users.c.id == m.c.user_id).where(
                m.c.site_id == ctx['site']['id'], m.c.organization_id == payload.recipient_org_id,
                m.c.role.in_(['hq', 'field']), m.c.is_active.is_(True), users.c.is_active.is_(True))).first()
            if ctx['organization_id'] == payload.recipient_org_id or not recipient:
                fail(422, '이 매장에 참여하는 상대 회사를 선택하세요.')
            current_start, current_finish = dates(ctx['task'])
            if (current_start, current_finish) != (payload.expected_start, payload.expected_finish):
                fail(409, '일정이 변경되었습니다. 최신 작업을 다시 조회하세요.')
            if payload.request_type == 'extension' and payload.proposed_finish <= current_finish:
                fail(422, '현재 종료일보다 늦은 날짜가 필요합니다.')
            item = dict(id=payload.id, site_id=ctx['site']['id'], schedule_id=schedule_id, task_id=task_id,
                requester_id=ctx['user_id'], from_organization_id=ctx['organization_id'],
                to_organization_id=payload.recipient_org_id, request_type=payload.request_type,
                proposed_start=payload.proposed_start, proposed_finish=payload.proposed_finish,
                proposed_work=payload.proposed_work, reason=payload.reason, status='pending', created_at=s.clock())
            c.execute(insert(t).values(**item))
            event = s.event(c, ctx, 'message', event_id=payload.id, request_id=payload.id, body=payload.reason,
                details={'kind': 'request_opened', 'baseline_digest': baseline(c, ctx),
                         'payload_digest': fingerprint})
            s.attach(c, ctx, event, payload.file_ids)
            return output(c, ctx, item)

    @router.get(root + '/change-requests/{request_id}', summary='변경 요청 내용·첨부·주고받은 대화')
    def get(site_code: str, schedule_id: UUID, request_id: UUID, actor=Depends(identity)):
        with s.engine.connect() as c:
            ctx, item = lookup(c, site_code, schedule_id, request_id, actor)
            return output(c, ctx, item)

    @router.post(root + '/change-requests/{request_id}/messages', status_code=201, summary='상대 회사에 답변과 파일 첨부')
    def message(site_code: str, schedule_id: UUID, request_id: UUID, payload: Message,
                response: Response, actor=Depends(identity)):
        with s.engine.begin() as c:
            ctx, item = lookup(c, site_code, schedule_id, request_id, actor, lock=True)
            fingerprint = digest({'request_id': str(request_id), **payload.model_dump(mode='json')})
            existing = s.replay(c, ctx, payload.id, fingerprint)
            if existing:
                response.status_code = 200
                return s.public_event(c, ctx, existing)
            event = s.event(c, ctx, 'message', event_id=payload.id, request_id=request_id, body=payload.body,
                            details={'payload_digest': fingerprint})
            s.attach(c, ctx, event, payload.file_ids)
            return s.public_event(c, ctx, event)

    @router.post(root + '/change-requests/{request_id}/decision', summary='수신 회사 결정과 작업·일정 적용을 한 번에 처리')
    def decide(site_code: str, schedule_id: UUID, request_id: UUID, payload: Decision, actor=Depends(identity)):
        with s.engine.begin() as c:
            ctx, item = lookup(c, site_code, schedule_id, request_id, actor, lock=True)
            if ctx['organization_id'] != item['to_organization_id']:
                fail(403, '수신 회사만 요청을 결정할 수 있습니다.')
            fingerprint = digest({'request_id': str(request_id), **payload.model_dump(mode='json')})
            if s.replay(c, ctx, payload.id, fingerprint):
                return output(c, ctx, item)
            if item['status'] != 'pending':
                fail(409, '이미 결정된 요청입니다.')
            opened = next(e for e in request_events(c, ctx, request_id) if e['details'].get('kind') == 'request_opened')
            accepted = payload.outcome == 'accept'
            if accepted and opened['details']['baseline_digest'] != baseline(c, ctx):
                fail(409, '요청 후 작업·일정이 바뀌었습니다. 변경안을 다시 요청하세요.')
            s.event(c, ctx, 'request_accept' if accepted else 'request_reject', event_id=payload.id,
                request_id=request_id, body=payload.body, details={'payload_digest': fingerprint})
            t = s.table('change_requests')
            if accepted:
                # PostgreSQL's scope trigger also validates these transitions. Explicit updates support test DBs.
                c.execute(update(t).where(t.c.id == request_id).values(status='accepted'))
                start, finish = dates(ctx['task'])
                details = ({'before_start': start, 'before_finish': finish,
                            'after_start': item['proposed_start'] or start,
                            'after_finish': item['proposed_finish'] or finish} if item['request_type'] != 'work'
                           else {'effective_work': item['proposed_work']})
                s.event(c, ctx, 'work_apply' if item['request_type'] == 'work' else 'schedule_apply',
                    event_id=uuid5(NAMESPACE_URL, 'aloha-apply:' + str(payload.id)), request_id=request_id, details=details)
                if item['request_type'] != 'work':
                    tasks = s.table('schedule_tasks')
                    c.execute(update(tasks).where(tasks.c.id == item['task_id']).values(
                        effective_start=details['after_start'], effective_finish=details['after_finish']))
                item['status'] = 'applied'
            else:
                item['status'] = 'rejected'
            c.execute(update(t).where(t.c.id == request_id).values(status=item['status']))
            return output(c, ctx, item)

    return router
