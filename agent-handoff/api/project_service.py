"""Canonical table access shared by the versioned project API."""
import hashlib
import json
import secrets
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from sqlalchemy import insert, select


def fail(code, message):
    raise HTTPException(code, message, headers={'WWW-Authenticate': 'Bearer'} if code == 401 else None)


def digest(value):
    raw = json.dumps(jsonable_encoder(value), sort_keys=True, separators=(',', ':'), ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()


def monday(day):
    return day - timedelta(days=day.weekday())


class ProjectService:
    def __init__(self, engine, tables, identities, storage_root, clock=None):
        self.engine, self.tables, self.identities = engine, tables, identities
        self.root = Path(storage_root).resolve()
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def table(self, name):
        tables = self.tables() if callable(self.tables) else self.tables
        return tables[name]

    def authenticate(self, request):
        config = self.identities() if callable(self.identities) else self.identities
        if not config:
            fail(503, 'API 사용자 인증 설정이 필요합니다.')
        supplied = request.headers.get('authorization', '')
        if not supplied.startswith('Bearer ') or not 1 <= len(supplied[7:]) <= 512:
            fail(401, '인증이 필요합니다.')
        hashed = hashlib.sha256(supplied[7:].encode()).hexdigest()
        for identity in config:
            if secrets.compare_digest(hashed, identity['token_sha256']):
                return identity
        fail(401, '인증 정보가 올바르지 않습니다.')

    def member(self, c, site_id, identity):
        m, u = self.table('site_memberships'), self.table('users')
        row = c.execute(select(m).join(u, u.c.id == m.c.user_id).where(
            m.c.site_id == site_id, m.c.user_id == UUID(identity['user_id']),
            m.c.organization_id == UUID(identity['organization_id']),
            m.c.is_active.is_(True), u.c.is_active.is_(True))).mappings().first()
        if row is None:
            fail(403, '이 매장의 활성 사용자 권한이 필요합니다.')
        return dict(row)

    def scope(self, c, code, identity, schedule_id=None, task_id=None, *, roles=None, lock=False):
        sites = self.table('sites')
        query = select(sites).where(sites.c.code == code)
        site = c.execute(query.with_for_update() if lock else query).mappings().first()
        if site is None:
            fail(404, '매장을 찾을 수 없습니다.')
        ctx = {'site': dict(site), 'identity': identity, 'user_id': None, 'organization_id': None}
        kind = identity.get('kind', 'user')
        if kind in {'vision', 'agent'}:
            if str(site['id']) not in identity['site_ids']:
                fail(403, '서비스에 허용되지 않은 매장입니다.')
            ctx['role'] = kind
        else:
            membership = self.member(c, site['id'], identity)
            ctx.update(role=membership['role'], user_id=membership['user_id'],
                       organization_id=membership['organization_id'])
        if roles and ctx['role'] not in roles:
            fail(403, '이 기능을 수행할 권한이 없습니다.')
        if schedule_id is not None:
            t = self.table('schedules')
            row = c.execute(select(t).where(t.c.site_id == site['id'], t.c.id == schedule_id)).mappings().first()
            if row is None:
                fail(404, '매장에 연결된 공정표가 없습니다.')
            ctx['schedule'] = dict(row)
        if task_id is not None:
            t = self.table('schedule_tasks')
            row = c.execute(select(t).where(t.c.schedule_id == schedule_id, t.c.id == task_id)).mappings().first()
            if row is None:
                fail(404, '공정표에 연결된 작업이 없습니다.')
            ctx['task'] = dict(row)
        return ctx

    def file(self, c, ctx, file_id, *, kind=None, attaching=False):
        f = self.table('files')
        row = c.execute(select(f).where(f.c.id == file_id, f.c.site_id == ctx['site']['id'],
                                       f.c.status == 'ready')).mappings().first()
        if row is None or (kind and row['kind'] != kind):
            fail(404, '이 매장에서 사용할 수 있는 파일이 없습니다.')
        row = dict(row)
        if not row['relative_path'].startswith('sites/' + ctx['site']['code'] + '/'):
            fail(409, '파일 소유 매장과 저장 경로가 일치하지 않습니다.')
        if ctx['role'] in {'vision', 'agent'}:
            allowed = {'photo'} if ctx['role'] == 'vision' else {'photo', 'report_pdf', 'report_docx'}
            if row['visibility'] not in {'site', 'internal'} or row['kind'] not in allowed:
                fail(403, '서비스에 허용된 사진·보고서 파일이 아닙니다.')
            return row
        own = row['uploaded_by'] == ctx['user_id']
        if kind == 'photo' and attaching and row['visibility'] != 'site':
            fail(422, '현장 증빙에는 매장 공개 범위로 업로드한 사진을 선택하세요.')
        if row['visibility'] == 'internal' and not (own or ctx['role'] == 'hq'):
            fail(403, '내부 파일에 접근할 수 없습니다.')
        if row['visibility'] == 'request' and not own:
            if ctx['role'] not in {'field', 'hq'}:
                fail(403, '요청 참여 담당자만 첨부파일에 접근할 수 있습니다.')
            ef, ev, cr = (self.table(n) for n in ('task_event_files', 'task_events', 'change_requests'))
            linked = c.execute(select(cr.c.id).select_from(ef.join(ev, ev.c.id == ef.c.event_id)
                .join(cr, cr.c.id == ev.c.request_id)).where(ef.c.file_id == file_id,
                    cr.c.site_id == ctx['site']['id'],
                    (cr.c.from_organization_id == ctx['organization_id']) |
                    (cr.c.to_organization_id == ctx['organization_id']))).first()
            if attaching or linked is None:
                fail(403, '요청 참여 회사만 이 파일에 접근할 수 있습니다.')
        return row

    def public_file(self, row, code):
        return {k: row[k] for k in ('id', 'kind', 'size_bytes', 'status', 'created_at', 'visibility')} | {
            'filename': Path(row['relative_path']).name.split('_', 1)[-1],
            'download_url': f'/api/v1/sites/{code}/files/{row["id"]}'}

    def verify_bytes(self, row):
        path = (self.root / row['relative_path']).resolve()
        if not path.is_relative_to(self.root) or not path.is_file() or path.stat().st_size != row['size_bytes']:
            fail(409, '증빙 파일을 찾을 수 없거나 파일 크기가 변경되었습니다.')
        with path.open('rb') as stream:
            if hashlib.file_digest(stream, 'sha256').hexdigest() != row['sha256']:
                fail(409, '증빙 파일의 무결성 검증에 실패했습니다.')
        return path

    def events(self, c, ctx):
        t = self.table('task_events')
        query = select(t).where(t.c.site_id == ctx['site']['id'], t.c.task_id == ctx['task']['id'])
        if ctx.get('_as_of'):
            query = query.where(t.c.occurred_at < ctx['_as_of'])
        rows = [dict(r) for r in c.execute(query.order_by(t.c.occurred_at, t.c.id)).mappings()
            if r['event_type'] != 'workflow_state' or r['details'].get('kind') == 'field_handoff']
        rows.sort(key=lambda e: (e['occurred_at'], e['details'].get('sequence', 0), str(e['id'])))
        return rows

    def latest_submission(self, c, ctx):
        rows = [e for e in self.events(c, ctx) if e['event_type'] in {'submission', 'resubmission'}]
        return max(rows, key=lambda e: e['details'].get('attempt', 0), default=None)

    def replay(self, c, ctx, event_id, payload_digest):
        ev = self.table('task_events')
        row = c.execute(select(ev).where(ev.c.id == event_id)).mappings().first()
        if row is None:
            return None
        if (row['site_id'] != ctx['site']['id'] or row['task_id'] != ctx['task']['id'] or
                row['actor_id'] != ctx['user_id'] or row['details'].get('payload_digest') != payload_digest):
            fail(409, '같은 ID의 내용이나 제출자가 다릅니다.')
        return dict(row)

    def event(self, c, ctx, event_type, *, event_id=None, body='', details=None,
              request_id=None, target_event_id=None, observation_id=None):
        sequence = max((e['details'].get('sequence', 0) for e in self.events(c, ctx)), default=0) + 1
        row = dict(id=event_id or uuid4(), site_id=ctx['site']['id'],
                   schedule_id=ctx.get('schedule', {}).get('id'), task_id=ctx.get('task', {}).get('id'),
                   actor_id=ctx['user_id'], event_type=event_type, occurred_at=self.clock(),
                   request_id=request_id, target_event_id=target_event_id,
                   observation_id=observation_id, body=body,
                   details=jsonable_encoder({**(details or {}), 'sequence': sequence}))
        c.execute(insert(self.table('task_events')).values(**row))
        return row

    def attach(self, c, ctx, event, file_ids, *, kind=None):
        for order, file_id in enumerate(file_ids):
            self.verify_bytes(self.file(c, ctx, file_id, kind=kind, attaching=True))
            c.execute(insert(self.table('task_event_files')).values(
                site_id=ctx['site']['id'], event_id=event['id'], file_id=file_id, sort_order=order))

    def public_event(self, c, ctx, row):
        ef, files = self.table('task_event_files'), self.table('files')
        attachments = c.execute(select(files).join(ef, ef.c.file_id == files.c.id)
            .where(ef.c.event_id == row['id']).order_by(ef.c.sort_order)).mappings()
        result = {k: row[k] for k in ('id', 'event_type', 'occurred_at', 'body', 'actor_id',
                                      'request_id', 'target_event_id', 'observation_id')}
        result['details'] = {k: v for k, v in row['details'].items() if k != 'payload_digest'}
        result['files'] = [self.public_file(f, ctx['site']['code']) for f in attachments
                           if self.file(c, ctx, f['id'])]
        return result

    def observations(self, c, ctx):
        t, daily = self.table('task_observations'), self.table('daily_records')
        query = select(t, daily.c.observation_date).where(t.c.task_id == ctx['task']['id'], t.c.site_id == ctx['site']['id'])
        if ctx.get('_as_of'):
            query = query.where(t.c.created_at < ctx['_as_of'])
        query = query.join(daily, daily.c.id == t.c.daily_record_id)
        rows = c.execute(query.order_by(daily.c.observation_date, t.c.revision, t.c.created_at, t.c.id)).mappings()
        # The newest day/revision replaces a target/view; separate walls remain separate.
        current = {}
        for row in rows:
            current[(row['target_key'], row['view_key'])] = dict(row)
        return list(current.values())

    def task_data(self, c, ctx):
        task = ctx['task']
        events = self.events(c, ctx)
        sub = self.latest_submission(c, ctx)
        reviews = [e for e in events if e['target_event_id'] and sub and e['target_event_id'] == sub['id']
                   and e['event_type'] in {'review_accept', 'review_reject', 'completion_confirm'}]
        review = reviews[-1] if reviews else None
        observations = self.observations(c, ctx)
        observed_ids = {o['id'] for o in observations if o['status'] == 'observed' and
                        (not ctx.get('_period_start') or ctx['_period_start'] <= o['observation_date'] <= ctx['_period_finish'])}
        accepted_ids = {e['observation_id'] for e in events if e['event_type'] == 'review_accept'}
        observation_accepted = bool(observed_ids) and observed_ids <= accepted_ids and len(observed_ids) == len(observations)
        current_ids = {r['id'] for r in observations if r['status'] in {'unobserved', 'failed'}}
        handoffs = [e for e in events if e['details'].get('kind') == 'field_handoff'
                    and e['observation_id'] in current_ids and not any(
                        s['event_type'] in {'submission', 'resubmission'} and s['observation_id'] == e['observation_id']
                        for s in events)]
        status = ('confirmed' if review and review['event_type'] == 'completion_confirm' else
                  'rejected' if review and review['event_type'] == 'review_reject' else
                  'field_confirmation_required' if handoffs else
                  'accepted' if review else
                  'completion_requested' if sub and sub['details']['progress_percent'] == 100 else
                  'review_pending' if sub else
                  'ai_observation_accepted' if observation_accepted else
                  'observation_review_pending' if observed_ids else
                  'ai_observing' if task['processing_route'] == 'ai' else 'planned')
        result = {k: task.get(k) for k in ('id', 'source_task_id', 'task_name', 'start', 'finish',
                    'effective_start', 'effective_finish', 'surface_type', 'processing_route', 'category_codes')}
        applied = [e for e in events if e['event_type'] == 'schedule_apply']
        work = [e for e in events if e['event_type'] == 'work_apply']
        planned_start = task['effective_start'] or task['start']
        planned_finish = task['effective_finish'] or task['finish']
        if ctx.get('_as_of'):
            planned_start, planned_finish = task['start'], task['finish']
            for event in applied:
                if 'after_start' in event['details']:
                    planned_start = event['details']['after_start']
                    planned_finish = event['details']['after_finish']
                elif event['request_id']:
                    requests = self.table('change_requests')
                    request = c.execute(select(requests).where(requests.c.id == event['request_id'])).mappings().first()
                    if request:
                        planned_start = request['proposed_start'] or planned_start
                        planned_finish = request['proposed_finish'] or planned_finish
        result.update(status=status, planned_start=planned_start, planned_finish=planned_finish,
                      effective_work=work[-1]['details'].get('effective_work') if work else None,
                      latest_submission=None if not sub else self.public_event(c, ctx, sub),
                      latest_review=None if not review else self.public_event(c, ctx, review),
                      handoffs=[self.public_event(c, ctx, h) for h in handoffs], observations=observations)
        return result

    def weekly(self, c, ctx, week_start):
        if week_start.weekday() != 0:
            fail(422, '주간 시작일은 월요일이어야 합니다.')
        finish = week_start + timedelta(days=6)
        t = self.table('schedule_tasks')
        rows = c.execute(select(t).where(t.c.schedule_id == ctx['schedule']['id'])
                         .order_by(t.c.display_order, t.c.id)).mappings()
        tasks = []
        for row in rows:
            start, end = row['effective_start'] or row['start'], row['effective_finish'] or row['finish']
            task_ctx = {**ctx, 'task': dict(row), '_period_start': week_start, '_period_finish': finish}
            item = None
            if ctx.get('_as_of'):
                item = self.task_data(c, task_ctx)
                start, end = item['planned_start'], item['planned_finish']
                start = date.fromisoformat(start) if isinstance(start, str) else start
                end = date.fromisoformat(end) if isinstance(end, str) else end
            if start and end and start <= finish and end >= week_start:
                item = item or self.task_data(c, task_ctx)
                if item['status'] == 'confirmed':
                    confirmed_at = datetime.fromisoformat(str(item['latest_review']['occurred_at']))
                    if confirmed_at.tzinfo is None:
                        confirmed_at = confirmed_at.replace(tzinfo=timezone.utc)
                    if confirmed_at.astimezone(ZoneInfo(ctx['site']['timezone'])).date() < week_start:
                        continue
                item['continued_from_previous_week'] = start < week_start
                tasks.append(item)
        tasks.sort(key=lambda x: (0 if x['handoffs'] else 1 if x['status'] == 'rejected' else 2))
        return dict(site_code=ctx['site']['code'], schedule_id=ctx['schedule']['id'],
                    period_start=week_start, period_finish=finish, timezone=ctx['site']['timezone'],
                    local_now=self.clock().astimezone(ZoneInfo(ctx['site']['timezone'])), tasks=tasks,
                    counts={'total': len(tasks), 'needs_field_action': sum(
                        x['status'] in {'planned', 'rejected', 'field_confirmation_required'} for x in tasks),
                        'review_pending': sum(x['status'] in {'review_pending', 'completion_requested', 'observation_review_pending'} for x in tasks),
                        'confirmed': sum(x['status'] == 'confirmed' for x in tasks)})
