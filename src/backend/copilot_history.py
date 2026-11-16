"""Private, durable chat turns using the existing question_archives/files tables.

Each request has one archive row. Immutable JSON revisions preserve the question,
generation state, answer, suggestions and provenance without changing business data.
"""
import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4, uuid5, NAMESPACE_URL

from sqlalchemy import insert, select, update
from project_service import fail


def utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def scope_id(ctx, week):
    parts = [ctx['user_id'], ctx['organization_id'], ctx['site']['id'],
             ctx.get('schedule', {}).get('id'), week]
    return str(uuid5(NAMESPACE_URL, 'aloha-copilot/v1/' + '/'.join(map(str, parts))))


def archive_rows(s):
    a, f = s.table('question_archives'), s.table('files')
    return select(a, f.c.relative_path, f.c.size_bytes, f.c.sha256, f.c.uploaded_by).join(
        f, f.c.id == a.c.archive_file_id).where(f.c.kind == 'copilot_turn', f.c.site_id == a.c.site_id)


def query(s, ctx, week):
    a, f = s.table('question_archives'), s.table('files')
    return archive_rows(s).where(a.c.site_id == ctx['site']['id'],
        a.c.description == 'copilot:v1:' + scope_id(ctx, week), f.c.uploaded_by == ctx['user_id'])


def read(s, row):
    return json.loads(s.verify_bytes(row).read_text(encoding='utf-8'))


def write(s, c, ctx, document, *, existing=False):
    fid = uuid4()
    relative = f"sites/{ctx['site']['code']}/copilot/{document['conversation_id']}/{fid}.json"
    path = s.root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(document, ensure_ascii=False, default=str).encode('utf-8')
    # An uncommitted file is never exposed by an API. Old revisions are retained.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    c.execute(insert(s.table('files')).values(id=fid, site_id=ctx['site']['id'], relative_path=relative,
        kind='copilot_turn', size_bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest(), status='ready',
        visibility='request', uploaded_by=ctx['user_id'], created_at=s.clock()))
    a = s.table('question_archives')
    if existing:
        c.execute(update(a).where(a.c.id == UUID(document['request_id'])).values(archive_file_id=fid))
    else:
        c.execute(insert(a).values(id=UUID(document['request_id']), site_id=ctx['site']['id'],
            title=document['question'][:200], asked_at=datetime.fromisoformat(document['asked_at']),
            archive_file_id=fid, description='copilot:v1:' + document['conversation_id'], created_at=s.clock()))
    return fid


def begin(s, site_code, actor, schedule_id, week, payload):
    with s.engine.begin() as c:
        ctx = s.scope(c, site_code, actor, schedule_id, roles={'field', 'hq', 'viewer'}, lock=True)
        a = s.table('question_archives')
        rows = c.execute(query(s, ctx, week).order_by(a.c.asked_at.desc()).limit(8)).mappings().all()
        latest = read(s, rows[0]) if rows else None
        own = c.execute(query(s, ctx, week).where(a.c.id == payload.request_id)).mappings().first()
        now = utc(s.clock())
        document = read(s, own) if own else None
        if document:
            if document['question'] != payload.message:
                fail(409, '같은 질문 요청 번호로 다른 내용을 보낼 수 없습니다.')
            if document['status'] == 'complete':
                return ctx, document, [], [], [], True
            if latest and latest['request_id'] != document['request_id']:
                fail(409, '이후 대화가 있습니다. 현재 대화에서 다시 질문해 주세요.')
            if document['status'] == 'pending' and datetime.fromisoformat(document['lease_until']) > now:
                fail(409, '이 질문의 답변을 작성 중입니다. 잠시 후 대화를 다시 열어 주세요.')
        else:
            if c.execute(select(a.c.id).where(a.c.id == payload.request_id)).first():
                fail(409, '질문 요청 번호를 새로 생성해 주세요.')
            if latest and latest['status'] in {'pending', 'retryable'} and datetime.fromisoformat(latest['lease_until']) > now:
                fail(409, '이 대화의 이전 답변을 작성 중입니다. 잠시 후 다시 질문해 주세요.')
            asked = max(now, datetime.fromisoformat(latest['asked_at']) + timedelta(microseconds=1)) if latest else now
            document = {'version': 1, 'conversation_id': scope_id(ctx, week), 'request_id': str(payload.request_id),
                'site_code': site_code, 'schedule_id': str(schedule_id) if schedule_id else None,
                'week_start': str(week) if week else None, 'question': payload.message,
                'asked_at': asked.isoformat(), 'page': payload.page, 'sources': [], 'suggestions': []}
        document.update(status='pending', lease_until=(now + timedelta(minutes=10)).isoformat(),
                        attempt_id=str(uuid4()), error=None)
        write(s, c, ctx, document, existing=bool(own))
        # History is exclusively server-owned; client-supplied assistant claims are ignored.
        prior = [read(s, row) for row in reversed(rows) if str(row['id']) != document['request_id']]
        prior = [item for item in prior if item['status'] == 'complete'][-5:]
        history, recent_ids, contexts, size = [], [], [], 0
        for item in reversed(prior):
            pair = [{'role': 'user', 'content': item['question']},
                    {'role': 'assistant', 'content': item['answer'][:3000]}]
            if schedule_id is not None and item.get('query_plan'):
                pair[1]['query_plan'] = item['query_plan']
            if schedule_id is not None and item.get('clarification'):
                pair[1]['clarification'] = item['clarification']
            length = sum(len(turn['content']) for turn in pair)
            if size + length > 12000:
                break
            history[0:0] = pair
            recent_ids.append(item['request_id'])
            contexts.append([record for record in item.get('source_records', [])
                             if record.get('site_code') == site_code and
                             record.get('schedule_id') == str(schedule_id) and record.get('week_start')])
            size += length
        # Preserve the nearest turn's task references, including ambiguity in a list answer.
        return ctx, document, history, recent_ids, next((group for group in contexts if group), []), False


def finish(s, actor, document, *, result=None, retryable=False):
    schedule = UUID(document['schedule_id']) if document['schedule_id'] else None
    with s.engine.begin() as c:
        ctx = s.scope(c, document['site_code'], actor, schedule, roles={'field', 'hq', 'viewer'}, lock=True)
        a = s.table('question_archives')
        row = c.execute(query(s, ctx, document['week_start']).where(a.c.id == UUID(document['request_id']))).mappings().one()
        current = read(s, row)
        if current.get('attempt_id') != document['attempt_id'] or current['status'] == 'complete':
            fail(409, '이 질문의 처리가 갱신되었습니다. 대화를 다시 열어 주세요.')
        if result is not None:
            answered = max(utc(s.clock()), datetime.fromisoformat(current['asked_at']))
            current.update(result, status='complete', answered_at=answered.isoformat())
        else:
            current.update(status='retryable' if retryable else 'failed',
                           error='답변 준비 중입니다.' if retryable else '답변을 받지 못했습니다. 다시 질문해 주세요.')
        file_id = write(s, c, ctx, current, existing=True)
        return current, file_id


def public(document):
    keys = ('request_id', 'conversation_id', 'question', 'answer', 'asked_at', 'answered_at', 'status',
            'site_code', 'week_start', 'sources', 'source_records', 'comparisons', 'suggestions', 'suggestion_source', 'error')
    return {key: document[key] for key in keys if key in document} | {'history_saved': True, 'read_only': True}


def history_page(s, ctx, week, c, before=None, limit=30):
    a = s.table('question_archives')
    stmt = query(s, ctx, week)
    if before:
        cursor = c.execute(stmt.where(a.c.id == before)).mappings().first()
        if not cursor:
            fail(404, '이 대화의 이전 내역을 찾을 수 없습니다.')
        stmt = stmt.where(a.c.asked_at < cursor['asked_at'])
    rows = c.execute(stmt.order_by(a.c.asked_at.desc()).limit(limit + 1)).mappings().all()
    has_more = len(rows) > limit
    rows = rows[:limit]
    return {'conversation_id': scope_id(ctx, week), 'site_code': ctx['site']['code'],
            'week_start': str(week) if week else None, 'turns': [public(read(s, row)) for row in reversed(rows)],
            'next_before': str(rows[-1]['id']) if has_more else None}
