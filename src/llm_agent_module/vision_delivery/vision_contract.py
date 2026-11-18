"""Validate a complete weekly delivery of the existing vision_output/1.0 shape.

This is a pure adapter, not an authentication or file-registration boundary. The
backend must authenticate the vision uploader, verify every returned media UUID,
and atomically enforce delivery revision ordering before accepting the results.
``installed_ratio`` measures installation in the observable ROI, not project or
schedule progress. Historical values and observed_fraction are never substituted.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
import math
import re
from uuid import UUID


class VisionContractError(ValueError):
    """The delivery is invalid; do not mark its reporting week complete."""


def _fail(path, message):
    raise VisionContractError(f'{path}: {message}')


def _object(value, path):
    if not isinstance(value, dict):
        _fail(path, 'must be an object')
    return value


def _text(value, path):
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        _fail(path, 'must be a nonempty string without surrounding whitespace')
    return value


def _positive_int(value, path, maximum=None):
    if type(value) is not int or value < 1 or (maximum and value > maximum):
        _fail(path, f'must be an integer from 1 to {maximum}' if maximum else 'must be an integer >= 1')
    return value


def _day(value, path):
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        _fail(path, 'must use YYYY-MM-DD')
    try:
        return date.fromisoformat(value)
    except ValueError:
        _fail(path, 'must be a valid calendar date')


def _uuid(value, path):
    if not isinstance(value, str):
        _fail(path, 'must be a registered backend UUID string')
    try:
        return str(UUID(value))
    except (ValueError, AttributeError):
        _fail(path, 'must be a registered backend UUID string')


def _fraction(value, path):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1 or not math.isfinite(value):
        _fail(path, 'must be a finite fraction from 0 to 1, not a percentage')
    return float(value)


def _production(value, path, require_source=False):
    if any(value.get(key) for key in ('is_test', 'is_dummy', 'is_example')) or 'example_note' in value:
        _fail(path, 'example, test, or label data cannot be accepted as a model delivery')
    if (require_source or 'source' in value) and value.get('source') != 'model':
        _fail(path + '.source', 'must be model; label, human, manager, and test results are not vision deliveries')


def _task_registry(checklist):
    """Distinguish valid off-week tasks from unknown/mistyped task mappings."""
    weekly = checklist.get('tasks')
    if not isinstance(weekly, list):
        _fail('checklist.tasks', 'must be a list')
    all_tasks = checklist.get('schedule_tasks', weekly)
    if not isinstance(all_tasks, list):
        _fail('checklist.schedule_tasks', 'must be a list for the current schedule version')
    by_id, by_source = {}, {}
    for index, task in enumerate(all_tasks):
        path = f'checklist.schedule_tasks[{index}]'
        _object(task, path)
        tid = _uuid(task.get('task_id'), path + '.task_id')
        sid = _text(task.get('source_task_id'), path + '.source_task_id')
        if tid in by_id or sid in by_source:
            _fail(path, 'duplicate canonical or source task ID')
        by_id[tid] = sid
        by_source[sid] = tid
    week_ids = set()
    for index, task in enumerate(weekly):
        path = f'checklist.tasks[{index}]'
        _object(task, path)
        tid = _uuid(task.get('task_id'), path + '.task_id')
        sid = _text(task.get('source_task_id'), path + '.source_task_id')
        if tid in week_ids or by_id.get(tid) != sid:
            _fail(path, 'weekly tasks must be unique members of the current schedule registry')
        week_ids.add(tid)
    return by_id, by_source, week_ids


def _mapping(value, path, registry):
    _object(value, path)
    if not value or set(value) - {'task_id', 'source_task_id'}:
        _fail(path, 'use only task_id and/or source_task_id; names and inferred mappings are not supported')
    by_id, by_source, _ = registry
    tid = _uuid(value['task_id'], path + '.task_id') if 'task_id' in value else None
    sid = _text(value['source_task_id'], path + '.source_task_id') if 'source_task_id' in value else None
    if tid is not None and tid not in by_id:
        _fail(path, 'unknown task_id for this schedule version; include its canonical entry in checklist.schedule_tasks')
    if sid is not None and sid not in by_source:
        _fail(path, 'unknown source_task_id for this schedule version; include its canonical entry in checklist.schedule_tasks')
    if tid is not None and sid is not None and by_source[sid] != tid:
        _fail(path, 'task_id and source_task_id disagree')
    return tid if tid is not None else by_source[sid]


def _box(value, path):
    if not isinstance(value, list) or len(value) != 4:
        _fail(path, 'must be [x0, y0, x1, y1] in normalized photo coordinates')
    box = [_fraction(x, path) for x in value]
    if not (box[0] < box[2] and box[1] < box[3]):
        _fail(path, 'requires x0 < x1 and y0 < y1')
    return box


def validate_delivery(payload, checklist):
    """Return ``{delivery, results, skipped}`` without mutating either argument.

    ``delivery.status=completed`` explicitly asserts completion of the entire
    supplied Monday-Friday week, even if no usable observations were found.
    Each target maps activities independently via
    ``schedule_link={activity_id: {source_task_id: "45"}}`` (or a task_id UUID).
    ``checklist.schedule_tasks`` may provide all tasks in the exact schedule
    version; only IDs also in ``checklist.tasks`` are emitted for this week.
    Highest daily revision wins; conflicting duplicates at one revision fail.
    Usable observations require an explicit normalized ROI box. Visibility below
    the source schema's 0.3 threshold is held even if the row claims status=ok.
    Targets sharing a task/region/date need one common scene photo and composite
    overlay, matching the existing backend observation contract.
    """
    _object(payload, 'payload')
    _object(checklist, 'checklist')
    _production(payload, 'payload')
    if payload.get('schema_version') != 'vision_output/1.0':
        _fail('schema_version', 'must be vision_output/1.0')
    model = _object(payload.get('model'), 'model')
    _production(model, 'model', require_source=True)
    _text(model.get('model_version'), 'model.model_version')

    raw_delivery = _object(payload.get('delivery'), 'delivery')
    keys = {'site_code', 'schedule_version', 'week', 'revision', 'batch_id', 'status', 'period_start', 'period_end'}
    if set(raw_delivery) != keys:
        _fail('delivery', 'requires exactly ' + ', '.join(sorted(keys)))
    code = _text(raw_delivery['site_code'], 'delivery.site_code')
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', code):
        _fail('delivery.site_code', 'invalid site code')
    expected_code = _text(checklist.get('site_code'), 'checklist.site_code')
    if code != expected_code:
        _fail('delivery.site_code', 'must match the exact backend site_code')
    version = _text(raw_delivery['schedule_version'], 'delivery.schedule_version')
    if not re.fullmatch(r'v[0-9]{3,}', version) or version != checklist.get('schedule_version'):
        _fail('delivery.schedule_version', 'must match the current backend schedule version')
    week = _positive_int(raw_delivery['week'], 'delivery.week', 1000)
    if type(checklist.get('week')) is not int or week != checklist['week']:
        _fail('delivery.week', 'must match the requested checklist week')
    revision = _positive_int(raw_delivery['revision'], 'delivery.revision', 2_147_483_647)
    bid = _uuid(raw_delivery['batch_id'], 'delivery.batch_id')
    if raw_delivery['status'] != 'completed':
        _fail('delivery.status', 'must be completed, explicitly declaring the entire reporting week complete')
    start = _day(raw_delivery['period_start'], 'delivery.period_start')
    end = _day(raw_delivery['period_end'], 'delivery.period_end')
    if start.weekday() != 0 or end != start + timedelta(days=4):
        _fail('delivery', 'reporting period must be one Monday-Friday week')
    if start.isoformat() != checklist.get('period_start') or end.isoformat() != checklist.get('period_end'):
        _fail('delivery', 'reporting period differs from the selected checklist')
    delivery = dict(raw_delivery, batch_id=bid, week=week, revision=revision)
    raw_site = _text(payload.get('site_id'), 'site_id')
    if raw_site.casefold() != code.casefold():
        _fail('site_id', 'belongs to another site')
    registry = _task_registry(checklist)

    targets = payload.get('targets')
    if not isinstance(targets, list):
        _fail('targets', 'must be a list (empty is allowed for a no-analysis completion)')
    target_map = {}
    for index, target in enumerate(targets):
        path = f'targets[{index}]'
        _object(target, path)
        _production(target, path)
        target_id = _text(target.get('target_id'), path + '.target_id')
        if target_id in target_map:
            _fail(path, 'duplicate target_id')
        name = _text(target.get('name'), path + '.name')
        region = target.get('surface_type')
        if not isinstance(region, str) or region not in {'wall', 'ceiling', 'floor'}:
            _fail(path + '.surface_type', 'must be wall, ceiling, or floor')
        links = target.get('schedule_link')
        mapped = {}
        if links is not None:
            _object(links, path + '.schedule_link')
            for activity, config in links.items():
                _text(activity, path + '.schedule_link activity_id')
                mapped[activity] = _mapping(config, path + f'.schedule_link.{activity}', registry)
        target_map[target_id] = {'name': name, 'region': region, 'links': mapped}

    views = payload.get('views')
    if not isinstance(views, list):
        _fail('views', 'must be a list')
    view_ids = set()
    for index, view in enumerate(views):
        _object(view, f'views[{index}]')
        vid = _text(view.get('view_id'), f'views[{index}].view_id')
        if vid in view_ids:
            _fail(f'views[{index}]', 'duplicate view_id')
        view_ids.add(vid)

    rows = payload.get('daily_progress')
    if not isinstance(rows, list):
        _fail('daily_progress', 'must be an explicit list; [] declares no observed results')
    skipped, candidates = [], defaultdict(list)

    def skip(index, row, reason):
        skipped.append({'index': index, 'target_id': row.get('target_id'), 'activity_id': row.get('activity_id'), 'date': row.get('date'), 'reason': reason})

    for index, row in enumerate(rows):
        path = f'daily_progress[{index}]'
        _object(row, path)
        day = _day(row.get('date'), path + '.date')
        site_id = _text(row.get('site_id'), path + '.site_id')
        if site_id.casefold() != code.casefold():
            _fail(path + '.site_id', 'belongs to another site')
        if not start <= day <= end:
            skip(index, row, 'outside_reporting_period')
            continue
        _production(row, path, require_source=True)
        target_id = _text(row.get('target_id'), path + '.target_id')
        activity = _text(row.get('activity_id'), path + '.activity_id')
        if target_id not in target_map:
            _fail(path + '.target_id', 'is not declared in targets')
        if activity not in target_map[target_id]['links']:
            _fail(path, f'missing explicit targets[].schedule_link mapping for {target_id}/{activity}')
        _positive_int(row.get('revision'), path + '.revision')
        candidates[(target_id, activity, day.isoformat())].append((index, row))

    grouped = {}
    for key in sorted(candidates):
        versions = candidates[key]
        by_revision = {}
        for index, row in versions:
            prior = by_revision.get(row['revision'])
            if prior is not None and prior[1] != row:
                _fail(f'daily_progress[{index}]', 'conflicting rows have the same target/activity/date/revision')
            if prior is None:
                by_revision[row['revision']] = (index, row)
            else:
                skip(index, row, 'duplicate')
        latest = max(by_revision)
        for rev, (index, row) in by_revision.items():
            if rev != latest:
                skip(index, row, 'superseded_revision')
        index, row = by_revision[latest]
        path = f'daily_progress[{index}]'
        target_id, activity, day = key
        target = target_map[target_id]
        tid = target['links'][activity]
        if tid not in registry[2]:
            skip(index, row, 'task_outside_reporting_week')
            continue
        if not isinstance(row.get('status'), str) or row['status'] not in {'ok', 'held', 'low_confidence'}:
            _fail(path + '.status', 'must be ok, held, or low_confidence')
        if type(row.get('provisional')) is not bool:
            _fail(path + '.provisional', 'must explicitly be true or false')
        if 'installed_ratio' not in row:
            _fail(path + '.installed_ratio', 'must be supplied explicitly; use null when no reliable value is available')
        ratio = row['installed_ratio']
        if ratio is not None:
            ratio = _fraction(ratio, path + '.installed_ratio')
        visibility = row.get('observed_fraction')
        if visibility is not None:
            visibility = _fraction(visibility, path + '.observed_fraction')
        if row['status'] != 'ok':
            skip(index, row, row['status'])
            continue
        if row['provisional']:
            skip(index, row, 'provisional')
            continue
        if ratio is None:
            skip(index, row, 'missing_installed_ratio')
            continue
        if visibility is not None and visibility < .3:
            skip(index, row, 'insufficient_observation')
            continue
        refs = row.get('view_ids')
        if not isinstance(refs, list) or not refs or any(not isinstance(v, str) or v not in view_ids for v in refs) or len(set(refs)) != len(refs):
            _fail(path + '.view_ids', 'must reference unique declared view IDs for this observation')
        photo = _uuid(row.get('photo_file_id'), path + '.photo_file_id')
        overlay = _uuid(row['overlay_file_id'], path + '.overlay_file_id') if row.get('overlay_file_id') is not None else None
        box = _box(row.get('box'), path + '.box')
        group_key = (tid, target['region'], day)
        if group_key not in grouped:
            grouped[group_key] = {'date': day, 'values': {}, 'labels': {}, 'boxes': {}, 'photo_file_id': photo, 'overlay_file_id': overlay}
        observation = grouped[group_key]
        if observation['photo_file_id'] != photo or observation['overlay_file_id'] != overlay:
            _fail(path, 'targets in the same task/region/date require the same scene photo_file_id and composite overlay_file_id')
        if target_id in observation['values']:
            _fail(path, 'multiple activities map one target/date to the same task; choose one explicit activity mapping')
        observation['values'][target_id] = round(ratio * 100, 10)
        observation['labels'][target_id] = target['name']
        observation['boxes'][target_id] = box

    analyses = defaultdict(list)
    for (tid, region, _), observation in sorted(grouped.items()):
        analyses[(tid, region)].append(observation)
    results = [{'task_id': tid, 'region': region, 'observations': observations}
               for (tid, region), observations in sorted(analyses.items())]
    return {'delivery': delivery, 'results': results, 'skipped': sorted(skipped, key=lambda item: item['index'])}
