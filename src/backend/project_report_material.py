"""Canonical, deterministic inputs for the approved construction report renderer.

This module makes business decisions in BE code, not in an LLM. Observation
date, receipt time, and the historical schedule cutoff are separate concepts.
No schema changes and no legacy workflow views are needed.
"""
import json
import re
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from sqlalchemy import select

from project_service import digest, fail, monday

CONTRACT_VERSION = 'alo-report-input/2'
TEMPLATE_VERSION = 'aloha-weekly-renderer-v1'
GENERATOR_VERSION = 'alo-formatted-worker/20'
PROFILE_FIELDS = {'display_site_name', 'report_site_name', 'brand_name', 'store_title',
                  'site_address', 'cover_caption', 'primary_location_code',
                  'camera_available_start', 'camera_available_finish'}


def day(value):
    return value if isinstance(value, date) and not isinstance(value, datetime) else date.fromisoformat(str(value))


def instant(value):
    value = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    # PostgreSQL TIMESTAMPTZ is aware; SQLite's test adapter returns naive UTC.
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def latest_observations(s, c, ctx, start, finish):
    obs, daily = s.table('task_observations'), s.table('daily_records')
    rows = c.execute(select(obs, daily.c.observation_date, daily.c.photo_file_id,
        daily.c.captured_at, daily.c.location_id).join(daily, daily.c.id == obs.c.daily_record_id).where(
        obs.c.site_id == ctx['site']['id'], obs.c.schedule_id == ctx['schedule']['id'],
        daily.c.site_id == ctx['site']['id'], daily.c.observation_date >= start,
        daily.c.observation_date <= finish, obs.c.created_at <= s.clock())
        .order_by(daily.c.observation_date, obs.c.revision, obs.c.created_at, obs.c.id)).mappings()
    current = {}
    for raw in rows:
        row = dict(raw)
        key = (row['task_id'], row['daily_record_id'], row['target_key'], row['view_key'])
        current[key] = row
    return sorted(current.values(), key=lambda r: (str(r['task_id']), r['observation_date'],
                  str(r['daily_record_id']), r['target_key'], r['view_key']))


def delivery_state(s, c, ctx, start, observations, requires_vision):
    ev = s.table('task_events')
    rows = c.execute(select(ev).where(ev.c.site_id == ctx['site']['id'],
        ev.c.schedule_id == ctx['schedule']['id'], ev.c.event_type == 'workflow_state',
        ev.c.occurred_at <= s.clock())).mappings()
    matches = [dict(r) for r in rows if r['details'].get('kind') == 'vision_week_delivery'
               and r['details'].get('week_start') == start.isoformat()]
    received = max(matches, key=lambda r: r['details']['revision'], default=None)
    ids = sorted(str(o['id']) for o in observations)
    if received:
        complete = ids == sorted(received['details']['observation_ids'])
        return {'status': 'completed' if complete else 'awaiting_updated_delivery',
                'complete': complete, 'delivery_id': str(received['id']),
                'revision': received['details']['revision']}
    return {'status': 'waiting' if requires_vision else 'not_required',
            'complete': not requires_vision, 'delivery_id': None, 'revision': None}


def build_material(s, c, ctx, start):
    if start.weekday() != 0:
        fail(422, '주간 시작일은 월요일이어야 합니다.')
    finish, tz = start + timedelta(days=4), ZoneInfo(ctx['site']['timezone'])
    week_boundary = datetime.combine(start + timedelta(days=7), time.min, tz).astimezone(timezone.utc)
    schedule_cutoff = min(instant(s.clock()) + timedelta(microseconds=1), week_boundary)
    tables = s.table('schedule_tasks')
    task_rows = [dict(r) for r in c.execute(select(tables).where(
        tables.c.schedule_id == ctx['schedule']['id']).order_by(tables.c.display_order, tables.c.id)).mappings()]
    ev = s.table('task_events')
    events = [dict(r) for r in c.execute(select(ev).where(ev.c.site_id == ctx['site']['id'],
        ev.c.schedule_id == ctx['schedule']['id'], ev.c.occurred_at <= s.clock())
        .order_by(ev.c.occurred_at, ev.c.id)).mappings()]
    events.sort(key=lambda r: (instant(r['occurred_at']), r['details'].get('sequence', 0), str(r['id'])))
    by_task = defaultdict(list)
    for event in events:
        by_task[event['task_id']].append(event)
    manifest, issues = {}, []

    def asset(file_id, *, optional=False):
        if not file_id:
            return None
        key = str(file_id)
        if key in manifest:
            return key
        try:
            files = s.table('files')
            row = c.execute(select(files).where(files.c.id == UUID(key), files.c.site_id == ctx['site']['id'],
                files.c.status == 'ready', files.c.kind == 'photo', files.c.visibility.in_(['site', 'internal']))).mappings().first()
            if not row or not row['relative_path'].startswith('sites/' + ctx['site']['code'] + '/'):
                fail(409, '보고서 사진의 소유 범위를 확인하세요.')
            s.verify_bytes(row)
            manifest[key] = {'id': key, 'sha256': row['sha256'], 'size_bytes': row['size_bytes'], 'kind': 'photo'}
            return key
        except (HTTPException, OSError, ValueError):
            if not optional:
                raise
            issue = {'file_id': key, 'reason': 'photo_unavailable'}
            if issue not in issues:
                issues.append(issue)
            return None

    profile = {}
    files = s.table('files')
    profiles = c.execute(select(files).where(files.c.site_id == ctx['site']['id'],
        files.c.relative_path.like(f"sites/{ctx['site']['code']}/report-assets/profile-%.json"),
        files.c.kind == 'weekly_report_metadata', files.c.status == 'ready')
        .order_by(files.c.created_at.desc(), files.c.id.desc())).mappings().first()
    if profiles:
        raw = json.loads(s.verify_bytes(profiles).read_text(encoding='utf-8'))
        if not isinstance(raw, dict):
            fail(409, '보고서 프로필 형식을 확인하세요.')
        profile = {k: v for k, v in raw.items() if k in PROFILE_FIELDS and isinstance(v, str)}
        profile['report_profile_file_id'] = str(profiles['id'])
        profile['report_profile_sha256'] = profiles['sha256']
        if raw.get('cover_photo_file_id'):
            profile['cover_photo_file_id'] = asset(raw['cover_photo_file_id'])

    first = min((r['start'] for r in task_rows if r['start']), default=start)
    project_monday = monday(first)
    week = (start - project_monday).days // 7 + 1
    if week < 1:
        fail(422, '공사 시작 이전 주차입니다.')

    def plan(row, cutoff=schedule_cutoff):
        # Effective columns describe now; replay dated application events for a historical report.
        a, b = row['start'], row['finish']
        for event in by_task[row['id']]:
            if event['event_type'] == 'schedule_apply' and instant(event['occurred_at']) < cutoff:
                details = event['details']
                if details.get('after_start') and details.get('after_finish'):
                    a, b = day(details['after_start']), day(details['after_finish'])
                else:
                    cr = s.table('change_requests')
                    req = c.execute(select(cr).where(cr.c.id == event['request_id'], cr.c.site_id == ctx['site']['id'])).mappings().first()
                    if req:
                        a, b = req['proposed_start'] or a, req['proposed_finish'] or b
        return a, b

    def complete_before(row, boundary):
        return any(e['event_type'] == 'completion_confirm' and instant(e['occurred_at']) < boundary
                   for e in by_task[row['id']])

    def planned(row, target_start, target_finish):
        a, b = plan(row)
        if not a or not b or a > target_finish:
            return None
        # Legacy imported schedules have no route or completion tracking. Do not
        # label every expired historical task as carried over without evidence.
        if b < target_start and row['processing_route'] not in {'ai', 'field'}:
            active = any(target_start <= instant(e['occurred_at']).astimezone(tz).date() <= target_finish
                         for e in by_task[row['id']])
            if not active:
                return None
        cutoff = datetime.combine(target_start, time.min, tz).astimezone(timezone.utc)
        confirmed = complete_before(row, min(cutoff, schedule_cutoff))
        if confirmed:
            return None
        return {'task_id': str(row['id']), 'source_task_id': row['source_task_id'], 'task_name': row['task_name'],
                'planned_start': a.isoformat(), 'planned_finish': b.isoformat(),
                'original_start': row['start'].isoformat() if row['start'] else None,
                'original_finish': row['finish'].isoformat() if row['finish'] else None,
                'continued_from_previous_week': a < target_start, 'carried_over': b < target_start,
                'delayed': b < target_finish and not complete_before(row, schedule_cutoff),
                'processing_route': row['processing_route'], 'surface_type': row['surface_type']}

    all_observations = latest_observations(s, c, ctx, start, finish)
    prior_observations = latest_observations(s, c, ctx, start - timedelta(days=7), finish - timedelta(days=7))
    obs_metadata = {str(e['observation_id']): e['details'] for e in events
                    if e['details'].get('kind') == 'vision_region' and e['observation_id']}
    links = s.table('schedule_task_locations')
    allowed_locations = {(r.task_id, r.location_id) for r in c.execute(select(links.c.task_id, links.c.location_id).where(
        links.c.site_id == ctx['site']['id'], links.c.schedule_id == ctx['schedule']['id']))}

    def analysis(row, observations):
        groups = defaultdict(dict)
        used_ids, unobserved = [], []
        rejected = {e['observation_id'] for e in by_task[row['id']] if e['event_type'] == 'review_reject'}
        accepted = {e['observation_id'] for e in by_task[row['id']] if e['event_type'] == 'review_accept'}
        for obs in observations:
            if obs['task_id'] != row['id']:
                continue
            if (row['id'], obs['location_id']) not in allowed_locations:
                issues.append({'observation_id': str(obs['id']), 'reason': 'unlinked_camera'})
                continue
            if instant(obs['captured_at']).astimezone(tz).date() != obs['observation_date']:
                issues.append({'observation_id': str(obs['id']), 'reason': 'capture_date_mismatch'})
                continue
            if obs['status'] != 'observed' or obs['installed_ratio'] is None or obs['id'] in rejected:
                unobserved.append({'observation_id': str(obs['id']), 'date': obs['observation_date'].isoformat(),
                                  'status': 'rejected' if obs['id'] in rejected else obs['status'],
                                  'hold_reason': obs['hold_reason'], 'target_key': obs['target_key'], 'view_key': obs['view_key']})
                continue
            metadata = obs_metadata.get(str(obs['id']), {})
            region = metadata.get('surface_type') or row['surface_type']
            if region not in {'wall', 'floor', 'ceiling'}:
                issues.append({'observation_id': str(obs['id']), 'reason': 'missing_surface_type'})
                continue
            photo = asset(obs['photo_file_id'], optional=True)
            if not photo:
                continue
            # A series belongs to one camera and view; never average unrelated views.
            key = (region, str(obs['location_id']), obs['view_key'])
            stamp = obs['observation_date'].isoformat()
            data = groups[key].setdefault(stamp, {'date': stamp, 'photo_file_id': photo,
                'values': {}, 'labels': {}, 'boxes': {}, 'observation_ids': [], 'overlay_file_ids': []})
            target = obs['target_key']
            data['values'][target] = round(float(obs['installed_ratio']) * 100, 8)
            data['labels'][target] = target + ' / ' + obs['view_key']
            data['observation_ids'].append(str(obs['id']))
            if metadata.get('roi_box'):
                data['boxes'][target] = metadata['roi_box']
            overlay = asset(obs['overlay_file_id'], optional=True)
            if overlay and overlay not in data['overlay_file_ids']:
                data['overlay_file_ids'].append(overlay)
                data.setdefault('overlay_file_id', overlay)
            used_ids.append(str(obs['id']))
        analyses = [{'region': key[0], 'location_id': key[1], 'view_key': key[2],
                     'observations': [rows[d] for d in sorted(rows)]} for key, rows in sorted(groups.items())]
        return analyses, used_ids, unobserved, all(UUID(i) in accepted for i in used_ids)

    def submission(row, target_start, target_finish):
        candidates = []
        for event in by_task[row['id']]:
            if event['event_type'] not in {'submission', 'resubmission'}:
                continue
            details = event['details']
            try:
                captured = instant(details['captured_at']).astimezone(tz).date()
            except (KeyError, ValueError, TypeError):
                continue
            explicit = details.get('reporting_week_start')
            if explicit:
                if explicit != target_start.isoformat() or not target_start <= captured <= target_start + timedelta(days=6):
                    continue
            elif not target_start <= captured <= target_finish:
                continue
            candidates.append(event)
        latest = candidates[-1] if candidates else None
        if not latest:
            return [], None, False
        reviews = [e for e in by_task[row['id']] if e['target_event_id'] == latest['id'] and
                   e['event_type'] in {'review_accept', 'review_reject', 'completion_confirm'}]
        reviewed = bool(reviews and reviews[-1]['event_type'] != 'review_reject')
        if reviews and reviews[-1]['event_type'] == 'review_reject':
            return [], latest, False
        ef = s.table('task_event_files')
        ids = c.execute(select(ef.c.file_id).where(ef.c.site_id == ctx['site']['id'],
            ef.c.event_id == latest['id']).order_by(ef.c.sort_order, ef.c.file_id)).scalars()
        photos = []
        for fid in ids:
            d = latest['details']
            value = d.get('photo_progress', {}).get(str(fid), d.get('progress_percent'))
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 100:
                continue
            safe = asset(fid, optional=True)
            if safe:
                capture = instant(d['captured_at']).astimezone(tz)
                photos.append({'file_id': safe, 'date': capture.date().isoformat(),
                    'captured_at': d['captured_at'], 'source': 'manager', 'progress_percent': value,
                    'description': latest['body'] or '', 'report_eligible': True,
                    'supplemental': capture.date() > target_finish, 'submission_id': str(latest['id'])})
        return photos, latest, reviewed

    def submission_history(row, target_start, target_finish):
        submissions=[]
        review_cutoff=target_finish+timedelta(days=2)
        for event in by_task[row['id']]:
            if event['event_type'] not in {'submission','resubmission'}:
                continue
            details=event.get('details') or {}
            try:
                captured=instant(details['captured_at']).astimezone(tz).date()
            except (KeyError,TypeError,ValueError):
                continue
            explicit=details.get('reporting_week_start')
            if (explicit==target_start.isoformat() and target_start<=captured<=target_start+timedelta(days=6)) or (
                    not explicit and target_start<=captured<=target_finish):
                submissions.append(event)
        # Historical photos can be imported after their reporting week.  Keep
        # their latest evidence event and present the captured date. Reviews
        # recorded months later belong to their own current workflow, not an
        # old weekly report.
        late_submissions=[event for event in submissions
                          if instant(event['occurred_at']).astimezone(tz).date()>review_cutoff]
        late_ids={event['id'] for event in late_submissions}
        if late_submissions:
            submissions=[event for event in submissions if event['id'] not in late_ids]+[late_submissions[-1]]
        submitted_ids={event['id'] for event in submissions}
        relevant=[]
        for event in by_task[row['id']]:
            if event['id'] in submitted_ids:
                relevant.append(event)
            elif (event['target_event_id'] in submitted_ids and event['event_type'] in
                  {'review_accept','review_reject','completion_confirm'} and
                  instant(event['occurred_at']).astimezone(tz).date()<=review_cutoff):
                relevant.append(event)
        history = []
        for event in relevant:
            recorded = instant(event['occurred_at']).astimezone(tz)
            item = {'type': event['event_type'], 'date': recorded.date().isoformat(),
                    'recorded_at': recorded.isoformat(), 'body': event.get('body') or '',
                    'event_id': str(event['id'])}
            if event['id'] in submitted_ids:
                captured_date=instant(event['details']['captured_at']).astimezone(tz).date().isoformat()
                item['captured_date'] = captured_date
                if event['id'] in late_ids:
                    item['date']=captured_date
                    item['recorded_at']=captured_date
            history.append(item)
        # A resubmission can be the first record included in this reporting
        # period when its earlier submission belongs to a prior period.  In a
        # weekly report it is still the period's first delivered evidence.
        if history and history[0]['type'] == 'resubmission':
            history[0] = {**history[0], 'type': 'submission'}
        return history

    tasks, previous_week = [], {}
    for row in task_rows:
        task = planned(row, start, finish)
        if task is None:
            continue
        analyses, used, missing, observed_reviewed = analysis(row, all_observations)
        photos, submitted, photo_reviewed = submission(row, start, finish)
        source = 'vision' if analyses else 'manager' if photos else 'missing'
        latest_targets = {}
        for observation in all_observations:
            if observation['task_id'] == row['id']:
                latest_targets[(str(observation['location_id']), observation['target_key'], observation['view_key'])] = observation
        missing_observation_ids = {u['observation_id'] for u in missing}
        needs_supplement = any(str(o['id']) in missing_observation_ids for o in latest_targets.values()) and not photos
        task.update(source=source, analyses=analyses, photos=photos, unobserved=missing,
                    observation_ids=used, submission_id=str(submitted['id']) if submitted else None,
                    submission_history=submission_history(row,start,finish),
                    requires_manager_evidence=needs_supplement,
                    review_complete=(observed_reviewed if analyses else photo_reviewed) and (photo_reviewed or not photos),
                    description='')
        corrections=[e for e in by_task[row['id']] if e['event_type']=='review_reject' and e.get('body')
                     and start<=instant(e['occurred_at']).astimezone(tz).date()<=finish]
        task['review_note']=corrections[-1]['body'] if corrections else ''
        task['completion_confirmed']=complete_before(row,schedule_cutoff)
        task['schedule_status'] = ('Head office completion confirmed' if task['completion_confirmed'] else
            'Delayed / carried over' if task['carried_over'] else 'Delayed / incomplete' if task['delayed'] else
            'Evidence reviewed; completion not confirmed' if task['review_complete'] else 'Awaiting Head Office Confirmation')
        if task['continued_from_previous_week']:
            old_analyses, old_ids, _, _ = analysis(row, prior_observations)
            old_photos, old_sub, _ = submission(row, start - timedelta(days=7), finish - timedelta(days=7))
            previous_week[task['task_id']] = {'analyses': old_analyses, 'photos': old_photos,
                                            'observation_ids': old_ids,
                                            'submission_id': str(old_sub['id']) if old_sub else None}
            comparisons = []
            for old in old_analyses:
                if old['observations']:
                    last = old['observations'][-1]
                    comparisons.append(last['date'] + ' / ' + old['view_key'] + ': ' +
                        ', '.join(f'{name} {value:g}%' for name, value in last['values'].items()))
            if old_photos:
                comparisons.extend(p['date'] + f" / manager {p['progress_percent']:g}%" for p in old_photos)
            if comparisons:
                task['description'] = 'Previous reporting week: ' + '; '.join(comparisons)
            if old_photos:
                # Progress is per photo; use a previous submission baseline only for a single-photo pair.
                if len(old_photos) == len(photos) == 1:
                    photos[0].update(baseline_progress_percent=old_photos[0]['progress_percent'],
                                     baseline_date=old_photos[0]['date'], baseline_source='previous_submission')
        tasks.append(task)

    delivery = delivery_state(s, c, ctx, start, all_observations,
                              any(t['processing_route'] == 'ai' for t in tasks))
    missing_ids = [t['task_id'] for t in tasks if t['source'] == 'missing' or t['requires_manager_evidence']]
    readiness_tasks = [{'task_id': t['task_id'], 'task_name': t['task_name'], 'source': t['source'],
                       'has_data': t['task_id'] not in missing_ids, 'has_vision_data': bool(t['analyses']),
                       'needs_manager_photo': delivery['complete'] and t['task_id'] in missing_ids} for t in tasks]
    daily = s.table('daily_records')
    loc = s.table('locations')
    daily_query = select(daily.c.id, daily.c.observation_date, daily.c.captured_at,
        daily.c.photo_file_id, loc.c.code.label('location_code')).join(loc, loc.c.id == daily.c.location_id).where(
        daily.c.site_id == ctx['site']['id'], loc.c.site_id == ctx['site']['id'],
        daily.c.observation_date >= start, daily.c.observation_date <= finish,
        (daily.c.schedule_id == ctx['schedule']['id']) | daily.c.schedule_id.is_(None))
    if profile.get('primary_location_code'):
        daily_query = daily_query.where(loc.c.code == profile['primary_location_code'])
    daily_photos = []
    for record in c.execute(daily_query.order_by(daily.c.observation_date, loc.c.code, daily.c.id)).mappings():
        if instant(record['captured_at']).astimezone(tz).date() != record['observation_date']:
            issues.append({'daily_record_id': str(record['id']), 'reason': 'capture_date_mismatch'})
            continue
        photo = asset(record['photo_file_id'], optional=True)
        if photo:
            daily_photos.append({'file_id': photo, 'daily_record_id': str(record['id']),
                'date': record['observation_date'].isoformat(), 'captured_at': instant(record['captured_at']).isoformat(),
                'location_code': record['location_code']})

    cr = s.table('change_requests')
    changes = []
    task_map = {r['id']: r for r in task_rows}
    for req in c.execute(select(cr).where(cr.c.site_id == ctx['site']['id'],
        cr.c.schedule_id == ctx['schedule']['id'], cr.c.created_at < schedule_cutoff)
        .order_by(cr.c.created_at, cr.c.id)).mappings():
        row = task_map.get(req['task_id'])
        if not row or req['request_type'] not in {'extension', 'schedule', 'work'}:
            continue
        history = [e for e in events if e['request_id'] == req['id'] and instant(e['occurred_at']) < schedule_cutoff]
        decisions = [e for e in history if e['event_type'] in {'request_accept', 'request_reject'}]
        applies = [e for e in history if e['event_type'] in {'schedule_apply', 'work_apply'}]
        decided = decisions[-1] if decisions else None
        applied = applies[-1] if applies else None
        created_day = instant(req['created_at']).astimezone(tz).date()
        activity_this_week = any(start <= instant(e['occurred_at']).astimezone(tz).date() < start + timedelta(days=7) for e in history)
        if created_day < start and decided and not activity_this_week:
            continue
        state = 'accepted' if decided and decided['event_type'] == 'request_accept' else 'rejected' if decided else 'pending'
        a, b = plan(row)
        work_history=[e for e in by_task[row['id']] if e['event_type']=='work_apply' and instant(e['occurred_at'])<schedule_cutoff]
        changes.append({'request_id': str(req['id']), 'task_id': str(req['task_id']), 'task_name': row['task_name'],
            'request_type': req['request_type'], 'proposed_work': req['proposed_work'],
            'current_work': work_history[-1]['details'].get('effective_work') if work_history else None,
            'reason': req['reason'], 'status': state, 'application_status': 'applied' if applied else 'not_applied',
            'applied_at': instant(applied['occurred_at']).isoformat() if applied else None,
            'decided_at': instant(decided['occurred_at']).isoformat() if decided else None,
            'decision_reason': decided['body'] if decided else None,
            'requested_week': (monday(created_day) - project_monday).days // 7 + 1,
            'applies_from_week': (monday(instant(applied['occurred_at']).astimezone(tz).date()) - project_monday).days // 7 + 1 if applied else None,
            'original_start': row['start'], 'original_finish': row['finish'], 'current_start': a, 'current_finish': b,
            'proposed_start': req['proposed_start'] or a, 'proposed_finish': req['proposed_finish'] or b})

    prior_start=start-timedelta(days=7)
    previous_week_schedule_changes=[]
    for event in events:
        if event['event_type']!='schedule_apply':continue
        applied_day=instant(event['occurred_at']).astimezone(tz).date()
        if not prior_start<=applied_day<start:continue
        row=task_map.get(event['task_id'])
        if row:
            details=event.get('details') or {}
            previous_week_schedule_changes.append({
                'task_id':str(row['id']),'task_name':row['task_name'],
                'original_finish':str(details.get('before_finish') or row['finish']),
                'current_finish':str(details.get('after_finish') or plan(row)[1]),
                'applied_at':instant(event['occurred_at']).isoformat(),
            })
    following = [v for row in task_rows if (v := planned(row, start + timedelta(days=7), finish + timedelta(days=7))) is not None]
    ready = bool(tasks) and delivery['complete'] and not missing_ids
    return jsonable_encoder({
        'schema_version': CONTRACT_VERSION, 'template_version': TEMPLATE_VERSION, 'generator_version': GENERATOR_VERSION,
        'site_code': ctx['site']['code'], 'site_name': ctx['site']['name'],
        'site_address': ctx['site'].get('address') or '', 'schedule_id': str(ctx['schedule']['id']),
        'schedule_version': ctx['schedule']['version'], 'timezone': ctx['site']['timezone'],
        'week': week, 'period_start': start, 'period_end': finish, 'period_finish': finish,
        'project_start': first, 'project_finish': max((plan(r)[1] for r in task_rows if plan(r)[1]), default=finish),
        **profile, 'tasks': tasks, 'next_week_tasks': following, 'previous_week': previous_week,
        'daily_site_photos': daily_photos, 'extension_requests': changes,
        'previous_week_schedule_changes': previous_week_schedule_changes,
        'missing_task_ids': missing_ids, 'ready_for_final': ready,
        'ready_for_approval': ready and all(t['review_complete'] for t in tasks),
        'readiness': {'vision': delivery, 'ready_for_final': ready, 'missing_task_ids': missing_ids, 'tasks': readiness_tasks},
        'asset_manifest': manifest, 'asset_issues': issues,
        'comparison_rule': 'Only continuing tasks have previous-week evidence; no cross-view averaging.',
        'is_demo': ctx['site']['code'].startswith('test-'),
    })
