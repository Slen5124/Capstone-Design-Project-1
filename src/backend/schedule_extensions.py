"""Canonical extension requests with immutable, week-scoped schedule projection.

The caller authenticates actor_id and holds a site row lock in a transaction.
Original schedule_tasks dates are never updated. Context rows add the baseline
and reporting-week information absent from the canonical request schema.
"""
from __future__ import annotations
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import Column, Date, Integer, MetaData, String, Table, insert, inspect, select, update

metadata = MetaData()
contexts = Table('workflow_extension_context', metadata,
    Column('request_id', String, primary_key=True),
    Column('site_code', String, nullable=False),
    Column('schedule_id', String, nullable=False),
    Column('task_id', String, nullable=False),
    Column('requested_week', Integer, nullable=False),
    Column('applies_from_week', Integer, nullable=False),
    Column('baseline_start', Date, nullable=False),
    Column('baseline_end', Date, nullable=False),
    Column('baseline_digest', String, nullable=False),
    Column('created_at', String, nullable=False))


def fail(message, status=422):
    raise HTTPException(status, message)


def _uuid(value, label='ID', status=422):
    try:
        return UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        fail(label + ' must be a UUID', status)


def _date(value, label='Date'):
    if isinstance(value, datetime):
        fail(label + ' must be a date without a time')
    if isinstance(value, date):
        return value
    try:
        result = date.fromisoformat(value)
        if result.isoformat() != value:
            raise ValueError()
        return result
    except (ValueError, TypeError):
        fail(label + ' must use YYYY-MM-DD')


def _instant(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _iso(value):
    return value.isoformat() if value is not None else None


def _text(value, label, required=False):
    if not isinstance(value, str):
        fail(label + ' must be text')
    value = value.strip()
    if required and not value:
        fail(label + ' is required')
    if len(value) > 4000:
        fail(label + ' exceeds 4000 characters')
    return value


def _week(value):
    if type(value) is not int or not 1 <= value <= 1000:
        fail('Week must be an integer from 1 to 1000')
    return value


def _resolve(c, supplied):
    """Reflect canonical storage or the DB20 compatibility views."""
    result = dict(supplied)
    inspector = inspect(c)
    available = set(inspector.get_table_names()) | set(inspector.get_view_names())
    aliases = {'change_requests': 'legacy_change_requests',
               'site_memberships': 'legacy_site_memberships'}
    for name in ('change_requests', 'change_request_decisions', 'site_memberships',
                 'site_organizations', 'organizations', 'schedule_tasks', 'schedules'):
        physical = aliases.get(name, name)
        if physical not in available:
            physical = name
        if physical in available and (name not in result or physical != name):
            result[name] = Table(physical, MetaData(), autoload_with=c, resolve_fks=False)
    result['_context_available'] = contexts.name in available
    return result


def _required(tables, *, write=False):
    required = {'change_requests', 'change_request_decisions'}
    if write:
        required |= {'site_memberships', 'site_organizations', 'schedule_tasks'}
    missing = required - tables.keys()
    if missing and write:
        fail('Extension workflow tables are unavailable: ' + ', '.join(sorted(missing)), 503)
    return not missing


def extension_tables(c, tables, required=False):
    resolved = _resolve(c, tables)
    if not _required(resolved, write=required):
        return None
    return resolved


def actor_access(c, tables, site, actor_id):
    tables = _resolve(c, tables)
    _required(tables, write=True)
    membership = tables['site_memberships']
    row = c.execute(select(membership).where(membership.c.site_id == _uuid(site['id']),
        membership.c.user_id == _uuid(actor_id, 'Authenticated actor', 403),
        membership.c.active.is_(True))).mappings().first()
    if row is None or row['role'] not in {'field', 'hq', 'viewer'}:
        fail('An active membership in this site is required', 403)
    associations = tables['site_organizations']
    recipients = [dict(item) for item in c.execute(select(associations).where(
        associations.c.site_id == _uuid(site['id']),
        associations.c.organization_id != row['organization_id'])).mappings()]
    names = {}
    organizations = tables.get('organizations')
    if organizations is not None and recipients:
        names = {str(item['id']): item['name'] for item in c.execute(select(organizations).where(
            organizations.c.id.in_([r['organization_id'] for r in recipients]))).mappings()}
    return {'role': row['role'], 'can_request': row['role'] == 'field',
            'can_review': row['role'] == 'hq',
            'recipient_organizations': [{'id': str(item['organization_id']),
                'name': names.get(str(item['organization_id']), str(item['organization_id']))}
                for item in sorted(recipients, key=lambda r: str(r['organization_id']))]}


def _local_week(value, site, project):
    local_day = _instant(value).astimezone(ZoneInfo(project.get('timezone') or site['timezone'])).date()
    return max(1, (local_day - _date(project['project_start'])).days // 7 + 1)


def _rows(c, tables, site, project):
    if not _required(tables):
        return []
    requests, decisions = tables['change_requests'], tables['change_request_decisions']
    rows = [dict(row) for row in c.execute(select(requests).where(
        requests.c.site_id == _uuid(site['id']),
        requests.c.schedule_id == _uuid(project['schedule_id']),
        requests.c.kind.in_(['extension', 'schedule']))).mappings()]
    if not rows:
        return []
    ids = [row['id'] for row in rows]
    choices = {str(row['request_id']): dict(row) for row in c.execute(select(decisions).where(
        decisions.c.request_id.in_(ids), decisions.c.site_id == _uuid(site['id']))).mappings()}
    ctx = {}
    if tables['_context_available']:
        ctx = {row['request_id']: dict(row) for row in c.execute(select(contexts).where(
            contexts.c.request_id.in_([str(value) for value in ids]))).mappings()}
    for row in rows:
        context = ctx.get(str(row['id']))
        if context and (context['site_code'] != site['code'] or
                        context['schedule_id'] != str(project['schedule_id']) or
                        context['task_id'] != str(row['task_id'])):
            fail('Extension context does not match its canonical request', 409)
        decision = choices.get(str(row['id']))
        requested_week = context['requested_week'] if context else _local_week(row['created_at'], site, project)
        applies_week = context['applies_from_week'] if context else (
            _local_week(decision['applied_at'], site, project)
            if decision and decision.get('applied_at') else requested_week)
        row.update(_context=context, _decision=decision,
                   _requested_week=requested_week, _applies_week=applies_week)
    return rows


def _project_tasks(tasks, events, week, known_through_week=None):
    known_week = week if known_through_week is None else min(week, _week(known_through_week))
    applied = [row for row in events if row['_requested_week'] <= known_week
               and row['_applies_week'] <= week and row['_decision']
               and row['_decision']['outcome'] == 'accepted'
               and row['_decision'].get('applied_at') is not None]
    applied.sort(key=lambda row: (_instant(row['_decision']['applied_at']), str(row['id'])))
    result = []
    for raw in tasks:
        task = dict(raw)
        task['original_start'] = task['start']
        task['original_finish'] = task['finish']
        task['schedule_change_ids'] = []
        for event in applied:
            if str(event['task_id']) != str(task['id']):
                continue
            task['start'] = event.get('proposed_start') or task['start']
            task['finish'] = event.get('proposed_finish') or task['finish']
            if task['start'] is None or task['finish'] is None or task['start'] > task['finish']:
                fail('Applied schedule change contains invalid task dates', 409)
            task['schedule_change_ids'].append(str(event['id']))
        result.append(task)
    return result


def effective_tasks(c, tables, site, project, week, tasks, *, known_through_week=None):
    """Resolve dates for week without leaking later requests into an earlier report.

    A report's next-week preview passes its own week as known_through_week;
    this still includes previously requested changes becoming effective then.
    """
    tables = _resolve(c, tables)
    return _project_tasks(tasks, _rows(c, tables, site, project), _week(week), known_through_week)


def _all_tasks(c, tables, project):
    tasks = tables.get('schedule_tasks')
    if tasks is None:
        return []
    return [dict(row) for row in c.execute(select(tasks).where(
        tasks.c.schedule_id == _uuid(project['schedule_id']))).mappings()]


def list_extensions(c, tables, site, project, week):
    week = _week(week)
    tables = _resolve(c, tables)
    events = _rows(c, tables, site, project)
    projected = {str(task['id']): task for task in _project_tasks(_all_tasks(c, tables, project), events, week)}
    result = []
    for row in sorted(events, key=lambda item: (_instant(item['created_at']), str(item['id']))):
        if row['kind'] != 'extension' or row['_requested_week'] > week:
            continue
        decision, context = row['_decision'], row['_context']
        pending = decision is None or (decision['outcome'] == 'accepted' and not decision.get('applied_at'))
        applied_this_week = bool(decision and decision.get('applied_at') and row['_applies_week'] == week)
        if row['_requested_week'] != week and not pending and not applied_this_week:
            continue
        task = projected.get(str(row['task_id']))
        if task is None:
            continue
        result.append({
            'request_id': str(row['id']), 'task_id': str(row['task_id']),
            'task_name': task['task_name'], 'requested_week': row['_requested_week'],
            'original_start': _iso(context['baseline_start'] if context else task['original_start']),
            'original_finish': _iso(context['baseline_end'] if context else task['original_finish']),
            'current_start': _iso(task['start']), 'current_finish': _iso(task['finish']),
            'proposed_start': _iso(row.get('proposed_start')),
            'proposed_finish': _iso(row.get('proposed_finish')),
            'reason': row.get('reason') or '',
            'status': decision['outcome'] if decision else 'pending',
            'decision_reason': decision.get('reason') or '' if decision else '',
            'applied_at': _iso(decision.get('applied_at')) if decision else None,
            'applies_from_week': row['_applies_week'],
        })
    return result


def _member(c, tables, site, actor_id, role, organization_id=None):
    membership = tables['site_memberships']
    query = select(membership).where(membership.c.site_id == _uuid(site['id']),
        membership.c.user_id == _uuid(actor_id, 'Authenticated actor', 403),
        membership.c.active.is_(True), membership.c.role == role)
    if organization_id is not None:
        query = query.where(membership.c.organization_id == _uuid(organization_id))
    row = c.execute(query).mappings().first()
    if row is None:
        fail('An active ' + role + ' membership in the required site and organization is required', 403)
    return dict(row)


def _task(c, tables, site, project, task_id):
    table = tables['schedule_tasks']
    row = c.execute(select(table).where(table.c.id == _uuid(task_id))).mappings().first()
    if row is None:
        fail('Task not found', 404)
    if str(row['schedule_id']) != str(project['schedule_id']):
        schedules = tables.get('schedules')
        other = c.execute(select(schedules).where(schedules.c.id == row['schedule_id'])).mappings().first() if schedules is not None else None
        fail('Task belongs to another site' if other and str(other['site_id']) != str(site['id'])
             else 'Task belongs to another schedule version', 403 if other and str(other['site_id']) != str(site['id']) else 409)
    return dict(row)


def _fingerprint(task, events):
    # All applied revisions guard against a later-week change being overwritten
    # by an old pending request, even when its week-specific dates are unchanged.
    applied = sorted(str(row['id']) for row in events
        if str(row['task_id']) == str(task['id']) and row['_decision']
        and row['_decision']['outcome'] == 'accepted' and row['_decision'].get('applied_at'))
    raw = {'task_id': str(task['id']), 'start': _iso(task['start']),
           'finish': _iso(task['finish']), 'applied_request_ids': applied}
    return hashlib.sha256(json.dumps(raw, sort_keys=True).encode()).hexdigest()


def _request(c, tables, site, project, request_id):
    requests = tables['change_requests']
    row = c.execute(select(requests).where(requests.c.id == _uuid(request_id))).mappings().first()
    if row is None:
        fail('Extension request not found', 404)
    if str(row['site_id']) != str(site['id']):
        fail('Extension belongs to another site', 403)
    if str(row['schedule_id']) != str(project['schedule_id']):
        fail('Extension belongs to another schedule version', 409)
    if row['kind'] != 'extension':
        fail('This endpoint accepts extension requests only')
    return dict(row)


def _context(c, tables, request_id):
    if not tables['_context_available']:
        return None
    row = c.execute(select(contexts).where(contexts.c.request_id == str(request_id))).mappings().first()
    return dict(row) if row else None


def _unchanged(c, tables, site, project, request):
    context = _context(c, tables, request['id'])
    if not context:
        fail('This request has no immutable baseline; create a new reviewed request before applying it', 409)
    events = _rows(c, tables, site, project)
    task = _project_tasks([_task(c, tables, site, project, request['task_id'])], events, context['applies_from_week'])[0]
    if _fingerprint(task, events) != context['baseline_digest']:
        fail('Task schedule changed since this request was submitted; refresh and submit a new request', 409)
    # Extension semantics never shorten another already-effective future plan.
    all_time = _project_tasks([_task(c, tables, site, project, request['task_id'])], events, 1000)[0]
    if request['proposed_finish'] <= all_time['finish']:
        fail('Proposed finish must extend the current approved finish', 409)
    return context


def _response(c, tables, site, project, week, request_id, idempotent=False):
    candidates = list_extensions(c, tables, site, project, week)
    match = next((row for row in candidates if row['request_id'] == str(request_id)), None)
    if match is None:
        context = _context(c, tables, request_id)
        if context:
            match = next((row for row in list_extensions(c, tables, site, project, context['requested_week'])
                          if row['request_id'] == str(request_id)), None)
    return {**(match or {'request_id': str(request_id)}), 'idempotent': idempotent}


def create_extension(c, tables, site, project, week, actor_id, payload):
    week = _week(week)
    tables = _resolve(c, tables)
    _required(tables, write=True)
    if not tables['_context_available']:
        fail('Extension context migration is required', 503)
    member = _member(c, tables, site, actor_id, 'field')
    required = {'request_id', 'task_id', 'recipient_org_id', 'proposed_finish',
                'reason', 'expected_start', 'expected_finish'}
    if not isinstance(payload, dict) or set(payload) != required:
        fail('Extension request requires exactly: ' + ', '.join(sorted(required)))
    rid, tid = _uuid(payload['request_id']), _uuid(payload['task_id'])
    recipient = _uuid(payload['recipient_org_id'])
    if recipient == _uuid(member['organization_id']):
        fail('Recipient organization must differ from the sender organization')
    organizations = tables['site_organizations']
    if c.execute(select(organizations).where(organizations.c.site_id == _uuid(site['id']),
            organizations.c.organization_id == recipient)).first() is None:
        fail('Recipient organization is not assigned to this site', 403)
    proposed = _date(payload['proposed_finish'], 'proposed_finish')
    expected_start = _date(payload['expected_start'], 'expected_start')
    expected_finish = _date(payload['expected_finish'], 'expected_finish')
    reason = _text(payload['reason'], 'reason', required=True)
    task = _task(c, tables, site, project, tid)
    requests = tables['change_requests']
    existing = c.execute(select(requests).where(requests.c.id == rid)).mappings().first()
    if existing:
        _request(c, tables, site, project, rid)
        context = _context(c, tables, rid)
        same = context and context['requested_week'] == week and context['baseline_start'] == expected_start and context['baseline_end'] == expected_finish
        same = same and str(existing['task_id']) == str(tid) and str(existing['sender_id']) == str(actor_id)
        same = same and existing['recipient_org_id'] == recipient and existing['proposed_finish'] == proposed and (existing['reason'] or '') == reason
        if not same:
            fail('Request ID is immutable; use a new request ID', 409)
        return _response(c, tables, site, project, week, rid, True)
    events = _rows(c, tables, site, project)
    current = _project_tasks([task], events, week)[0]
    if (current['start'], current['finish']) != (expected_start, expected_finish):
        fail('Expected task dates are stale; refresh before requesting an extension', 409)
    future = _project_tasks([task], events, 1000)[0]
    if proposed <= max(expected_finish, future['finish']):
        fail('proposed_finish must be later than the current approved finish')
    stamp = datetime.now(timezone.utc)
    c.execute(insert(requests).values(id=rid, site_id=_uuid(site['id']),
        schedule_id=_uuid(project['schedule_id']), task_id=tid,
        sender_id=_uuid(actor_id), sender_org_id=member['organization_id'],
        recipient_org_id=recipient, kind='extension', summary='Schedule extension: ' + task['task_name'],
        reason=reason, proposed_start=expected_start, proposed_finish=proposed, created_at=stamp))
    c.execute(insert(contexts).values(request_id=str(rid), site_code=site['code'],
        schedule_id=str(project['schedule_id']), task_id=str(tid), requested_week=week,
        applies_from_week=week, baseline_start=expected_start, baseline_end=expected_finish,
        baseline_digest=_fingerprint(current, events), created_at=stamp.isoformat()))
    return _response(c, tables, site, project, week, rid)


def decide_extension(c, tables, site, project, week, request_id, actor_id, payload):
    week = _week(week)
    tables = _resolve(c, tables)
    _required(tables, write=True)
    request = _request(c, tables, site, project, request_id)
    _member(c, tables, site, actor_id, 'hq', request['recipient_org_id'])
    if not isinstance(payload, dict) or set(payload) - {'outcome', 'reason'} or payload.get('outcome') not in {'accepted', 'rejected'}:
        fail('Decision must supply accepted or rejected outcome and optional reason')
    outcome = payload['outcome']
    reason = _text(payload.get('reason', ''), 'reason', required=outcome == 'rejected')
    table = tables['change_request_decisions']
    existing = c.execute(select(table).where(table.c.request_id == request['id'])).mappings().first()
    if existing:
        if existing['outcome'] != outcome or (existing['reason'] or '') != reason or str(existing['decided_by']) != str(actor_id):
            fail('Decision is immutable; an existing decision cannot be replaced', 409)
        return _response(c, tables, site, project, week, request['id'], True)
    if outcome == 'accepted':
        _unchanged(c, tables, site, project, request)
    c.execute(insert(table).values(request_id=request['id'], site_id=_uuid(site['id']),
        decided_by=_uuid(actor_id), recipient_org_id=request['recipient_org_id'],
        outcome=outcome, reason=reason, decided_at=datetime.now(timezone.utc),
        applied_at=None, applied_by=None))
    return _response(c, tables, site, project, week, request['id'])


def apply_extension(c, tables, site, project, week, request_id, actor_id):
    week = _week(week)
    tables = _resolve(c, tables)
    _required(tables, write=True)
    request = _request(c, tables, site, project, request_id)
    _member(c, tables, site, actor_id, 'hq', request['recipient_org_id'])
    table = tables['change_request_decisions']
    decision = c.execute(select(table).where(table.c.request_id == request['id'])).mappings().first()
    if not decision or decision['outcome'] != 'accepted':
        fail('Only a headquarters-accepted extension can be applied', 409)
    if decision['applied_at'] is not None:
        return _response(c, tables, site, project, week, request['id'], True)
    _unchanged(c, tables, site, project, request)
    stamp = max(datetime.now(timezone.utc), _instant(decision['decided_at']))
    c.execute(update(table).where(table.c.request_id == request['id']).values(applied_at=stamp, applied_by=_uuid(actor_id)))
    return _response(c, tables, site, project, week, request['id'])
