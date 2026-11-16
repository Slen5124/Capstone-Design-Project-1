"""Private, rebuildable full-text index for archived Qwen conversations."""
import logging
import re
import sqlite3
import unicodedata
from collections import defaultdict
from contextlib import closing, contextmanager
from datetime import date
from uuid import UUID, NAMESPACE_URL, uuid5
from zoneinfo import ZoneInfo

from fastapi import HTTPException

from copilot_history import archive_rows, read, scope_id, utc


RECALL_REQUEST = re.compile(
    r'전에|이전|예전|과거|기억|아까|그때|지난번|저번|다시|뭐였|했던|말했|알려줬|물어봤|답변|대화|'
    r'\b(earlier|previous(?:ly)?|before|remember|recall|again|last time|what did we)\b', re.I)
TOPICS = (
    ('조명', '전등', '등기구', 'lighting', 'lights'),
    ('사진', '이미지', '촬영', 'photo', 'photos', 'image'),
    ('재제출', '재촬영', '다시 달', '다시 올', '다시 찍', '새 사진', 'resubmit', 'resubmission', 'retake'),
    ('반려', '보완 요청', '보정 요청', 'correction requested', 'rejected', 'rejection'),
    ('기간 연장', '일정 연장', 'extension'),
    ('진행률', '작업률', '공정률', 'progress'),
    ('벽체', '벽면', '벽 설치', '벽보드', 'drywall'),
    ('스프링클러', 'sprinkler'),
    ('피팅룸', '탈의실', 'fitting room'),
    ('색온도', '색 온도', 'color temperature'),
)
STOP = frozenset('전에 이전 예전 과거 기억 아까 지난번 저번 다시 뭐였 했던 말했 알려줬 물어봤 답변 대화 '
    '있는 없는 언제 어떤 이유 알려 보여 설명 확인 부탁 찾아 작업 자료 생각 관련 결과 화면 질문 현재 먼저 필요 요청 '
    '거야 그때 당시 달라고 어떻게 무엇 말해 건가 이유가 했는지 되나요 나왔 대한 알려줘 정리 '
    'earlier previous previously before remember recall again last time what did does about tell was were '
    'the and our this that why how please work task tasks answer conversation chat'.split())
WORD = re.compile(r'[가-힣]+|[a-z]+[0-9]*|\d+', re.I)
SUFFIX = re.compile(r'(?:에서|으로|부터|까지|에게|처럼|하고|라고|에는|이라|이며|인데|이라면|[은는이가을를에도와과의])$')
HANGUL = re.compile(r'^[가-힣]+$')
LOGGER = logging.getLogger(__name__)


def recall_requested(question):
    return bool(RECALL_REQUEST.search(question))


def index_terms(value):
    normalized = unicodedata.normalize('NFKC', value).casefold()
    compact = re.sub(r'\s+', '', normalized)
    for index, aliases in enumerate(TOPICS):
        if any((alias.replace(' ', '') in compact if HANGUL.search(alias.replace(' ', ''))
                else re.search(r'\b' + re.escape(alias) + r'\b', normalized)) for alias in aliases):
            yield f'topic{index}'
    for match in WORD.finditer(normalized):
        word = match.group()
        if HANGUL.fullmatch(word):
            stem = SUFFIX.sub('', word)
            word = stem if len(stem) >= 2 else word
        if len(word) >= 2 and word not in STOP:
            yield word
            if HANGUL.fullmatch(word) and len(word) > 2:
                yield from ('ko' + word[i:i+2] for i in range(len(word) - 1))


def partition(ctx):
    return uuid5(NAMESPACE_URL, 'aloha-copilot/search/v1/' + '/'.join(map(str, (
        ctx['user_id'], ctx['organization_id'], ctx['site']['id'], ctx.get('schedule', {}).get('id'),
    )))).hex


@contextmanager
def database(s):
    private = s.root / '.copilot-search'
    private.mkdir(mode=0o700, exist_ok=True)
    with closing(sqlite3.connect(private / 'turns-v1.sqlite3', timeout=0.4)) as db, db:
        yield db


def put(db, ctx, document, file_id):
    turn_id = document['request_id']
    db.execute('INSERT INTO turn_versions(turn_id, file_id) VALUES(?,?) '
               'ON CONFLICT(turn_id) DO UPDATE SET file_id=excluded.file_id', (turn_id, str(file_id)))
    rowid = db.execute('SELECT rowid FROM turn_versions WHERE turn_id = ?', (turn_id,)).fetchone()[0]
    db.execute('INSERT OR REPLACE INTO turn_text(rowid, scope, question, answer, week) VALUES(?,?,?,?,?)', (
        rowid, partition(ctx), ' '.join(index_terms(document['question'])),
        ' '.join(index_terms(document['answer'])), document['week_start'] or '',
    ))


def rebuild(s):
    """Reconcile existing and missed writes once at startup, without changing archives."""
    try:
        with database(s) as db, s.engine.connect() as c:
            db.execute('CREATE TABLE IF NOT EXISTS turn_versions(turn_id TEXT NOT NULL UNIQUE, file_id TEXT NOT NULL)')
            db.execute('CREATE VIRTUAL TABLE IF NOT EXISTS turn_text USING fts5('
                       'scope, question, answer, week UNINDEXED)')
            indexed = dict(db.execute('SELECT turn_id, file_id FROM turn_versions'))
            membership = s.table('site_memberships')
            organizations = defaultdict(set)
            for site, user, organization in c.execute(membership.select().with_only_columns(
                    membership.c.site_id, membership.c.user_id, membership.c.organization_id)):
                organizations[site, user].add(organization)
            retained = set()
            for row in c.execute(archive_rows(s)).mappings():
                turn_id = str(row['id'])
                if indexed.get(turn_id) == str(row['archive_file_id']):
                    retained.add(turn_id)
                    continue
                try:
                    document = read(s, row)
                    if document['status'] != 'complete' or document['request_id'] != turn_id:
                        continue
                    schedule = document['schedule_id']
                    week = date.fromisoformat(document['week_start']) if document['week_start'] else None
                    for organization in organizations[row['site_id'], row['uploaded_by']]:
                        ctx = {'site': {'id': row['site_id']}, 'user_id': row['uploaded_by'],
                               'organization_id': organization,
                               'schedule': {'id': UUID(schedule) if schedule else None}}
                        if row['description'] == 'copilot:v1:' + scope_id(ctx, week):
                            put(db, ctx, document, row['archive_file_id'])
                            retained.add(turn_id)
                            break
                except (HTTPException, OSError, ValueError, KeyError, TypeError):
                    LOGGER.warning('Could not index a saved copilot turn')
            stale = [(key,) for key in indexed.keys() - retained]
            db.executemany('DELETE FROM turn_text WHERE rowid = '
                           '(SELECT rowid FROM turn_versions WHERE turn_id = ?)', stale)
            db.executemany('DELETE FROM turn_versions WHERE turn_id = ?', stale)
    except (OSError, sqlite3.Error):
        LOGGER.warning('Copilot full-text index is unavailable')


def record(s, ctx, document, file_id):
    try:
        with database(s) as db:
            put(db, ctx, document, file_id)
    except (OSError, sqlite3.Error):
        LOGGER.warning('Copilot turn will be indexed on the next startup')


def excerpt(answer, terms):
    if len(answer) <= 900:
        return answer
    # Use the original text around a match even when it occurs late in a long answer.
    window = max(range(0, len(answer), 350), key=lambda start: len(
        terms.intersection(index_terms(answer[start:start + 700]))))
    start = max(0, window - 100)
    return ('…' if start else '') + answer[start:start + 898] + ('…' if start + 898 < len(answer) else '')


def search(s, ctx, question, recent_ids):
    terms = list(dict.fromkeys(index_terms(question)))[:40]
    if not terms:
        return []
    matching = ' OR '.join(f'"{term}"' for term in terms)
    expression = f'scope:"{partition(ctx)}" AND {{question answer}}:({matching})'
    # Limit the number of candidate files opened even for very common words.
    exclusion = ' AND turn_id NOT IN (' + ','.join('?' for _ in recent_ids) + ')' if recent_ids else ''
    try:
        with database(s) as db:
            candidates = db.execute(
                f'SELECT turn_id, file_id, week FROM turn_text JOIN turn_versions ON turn_versions.rowid = turn_text.rowid '
                f'WHERE turn_text MATCH ?{exclusion} '
                f'ORDER BY bm25(turn_text, 0, 2, 1), turn_text.rowid DESC LIMIT 12',
                (expression, *recent_ids),
            ).fetchall()
    except (OSError, sqlite3.Error):
        LOGGER.warning('Could not search saved copilot conversations')
        return []
    if not candidates:
        return []
    archives = s.table('question_archives')
    files = s.table('files')
    candidate_ids = [UUID(item[0]) for item in candidates]
    term_set = set(terms)
    with s.engine.connect() as c:
        rows = {str(row['id']): row for row in c.execute(archive_rows(s).where(
            archives.c.site_id == ctx['site']['id'], archives.c.id.in_(candidate_ids),
            files.c.uploaded_by == ctx['user_id'],
        )).mappings()}
        hits = []
        for turn_id, file_id, week in candidates:
            row = rows.get(turn_id)
            expected = 'copilot:v1:' + scope_id(ctx, date.fromisoformat(week) if week else None)
            if not row or str(row['archive_file_id']) != file_id or row['description'] != expected:
                continue
            try:
                document = read(s, row)
                if document['status'] == 'complete':
                    hits.append({
                        'request_id': turn_id, 'week_start': week or None,
                        'date': str(utc(row['asked_at']).astimezone(ZoneInfo(ctx['site']['timezone'])).date()),
                        'question': document['question'][:350], 'answer': excerpt(document['answer'], term_set),
                    })
            except (HTTPException, OSError, ValueError, KeyError, TypeError):
                LOGGER.warning('Could not retrieve an indexed copilot turn')
            if len(hits) == 3:
                break
        return hits
