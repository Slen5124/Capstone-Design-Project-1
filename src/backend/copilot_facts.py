"""Render project facts from scoped records, never from generated prose."""
import re
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation


WEEK = re.compile(r'지난\s*주|전주|이번\s*주|현재\s*주차|다음\s*주|\d+\s*주(?:차)?|\bweek\s*\d+|\d{4}-\d{2}-\d{2}', re.I)
GREETING = re.compile(r'(?:안녕(?:하세요|하십니까|요)?|안뇽|반가워(?:요)?|반갑습니다|하이|헬로|hi|hello|hey)', re.I)
THANKS = re.compile(r'(?:고마워(?:요)?|감사(?:합니다|해요)|고맙습니다|땡큐|thanks|thank you)', re.I)
CAPABILITIES = re.compile(r'뭘\s*(?:물어볼|할)\s*수\s*있|무슨\s*질문|어떤\s*질문.{0,12}(?:가능|할\s*수)|'
                          r'what can (?:i ask|you do)', re.I)
def social_answer(question):
    plain = question.strip().strip('?!.,~…。😊👋 ').strip()
    if GREETING.fullmatch(plain):
        return '안녕하세요! 공정표, 작업 진행률, 사진 반려 이력이나 주간 보고서에 관해 물어보세요.'
    if THANKS.fullmatch(plain):
        return '천만에요! 궁금한 점이 더 있으면 말씀해 주세요.'
    if CAPABILITIES.search(plain) and not WEEK.search(plain):
        return ('공정표에 어떤 작업이 있는지, 제출 사진이 왜 반려됐는지, 작업 진행률과 보고서 준비 상태를 확인할 수 있어요. '
                '예를 들어 “이번 주에 시작하는 작업은?”이라고 물어보세요.')
    return None


STATUS_FILTERS = {
    'confirmed': {'confirmed'}, 'needs_review': {'review_pending', 'completion_requested', 'observation_review_pending'},
    'needs_evidence': {'planned', 'ai_observing', 'field_confirmation_required', 'rejected'},
    'rejected': {'rejected'},
}


def weekly_scope_rows(rows, start, finish, scope, status_filter='all'):
    if status_filter in STATUS_FILTERS:
        rows = [row for row in rows if row['status'] in STATUS_FILTERS[status_filter]]
    if scope == 'starting':
        return [row for row in rows if row['planned_start'] and start <= row['planned_start'] <= finish]
    if scope == 'continuing':
        return [row for row in rows if row['planned_start'] and row['planned_start'] < start]
    return rows


def percent(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value))
        return number if number.is_finite() and 0 <= number <= 100 else None
    except InvalidOperation:
        return None


def number(value):
    return format(value.normalize(), 'f') if value else '0'


def readings(data, source, en=False):
    items = {}
    if source != 'ai':
        value = percent(data.get('manager_progress_percent'))
        if value is not None:
            items[('manager',)] = (('Site manager report' if en else '현장 관리자 보고'), value, data.get('manager_capture_date'))
    if source != 'manager':
        for obs in data.get('ai_observations', []):
            value = percent(obs.get('installed_percent'))
            if obs['status'] == 'observed' and value is not None:
                key = ('ai', obs['location_id'], obs['view'], obs['target'])
                label = 'AI observation' if en else 'AI 관측'
                items[key] = (f"{label} {obs['target']} / {obs['view']}", value, obs['date'])
    if source == 'both':
        for obs in data.get('historical_observations', []):
            value = percent(obs['installed_percent'])
            if value is not None:
                label = 'reviewed historical' if en else '검토된 과거 관측 자료'
                items[('historical', obs['target'], obs['view'])] = (f"{label} {obs['target']}", value, obs['date'])
    return items


def week_label(value, origin, en=False):
    day = date.fromisoformat(value)
    if origin and day >= origin:
        week = (day - origin).days // 7 + 1
        return f'Week {week} (starting {value})' if en else f'{week}주차 ({value} 시작)'
    return f'Week starting {value}' if en else f'{value} 시작 주차'


def display_reading(item):
    return f'{number(item[1])}% ({item[2]})' if item[2] else f'{number(item[1])}%'


def task_label(data, en=False):
    return f"{data['task_name']} ({'Task ID' if en else '작업 ID'} {data['source_task_id']})"


def progress_answer(details, origin, *, source='both', include=(), compare=False, en=False):
    rows = sorted(details, key=lambda d: d['week_start'])
    title = task_label(rows[0], en)
    parts = []
    comparisons = []
    if compare:
        intro = f'Here’s how the recorded progress for {title} compares.' if en else f'{title}의 두 주차 진행률을 비교해 봤어요.'
        first, second = rows
        earlier, later = readings(first, source, en), readings(second, source, en)
        a, b = (week_label(row['week_start'], origin, en) for row in rows)
        for key in dict.fromkeys([*earlier, *later]):
            before, after = earlier.get(key), later.get(key)
            label = (before or after)[0]
            if en and key[0] == 'manager':
                label = 'site manager’s progress'
            if before and after:
                delta = after[1] - before[1]
                comparisons.append({'source': key[0], 'series': list(key[1:]), 'week_start_from': first['week_start'],
                                    'week_start_to': second['week_start'], 'delta_pp': float(delta)})
                change = f"{number(abs(delta))}%p {'늘었어요' if delta > 0 else '줄었어요'}." if delta else '변화가 없었어요 (0%p).'
                if en:
                    change = f"a {'rise' if delta > 0 else 'drop'} of {number(abs(delta))} percentage points" if delta else 'no change (0 percentage points)'
                    parts.append(f'• The {label} reading was {display_reading(before)} in {a} and {display_reading(after)} in {b}. That’s {change}.')
                else:
                    parts.append(f'• {label} 수치는 {a}에 {display_reading(before)}, {b}에 {display_reading(after)}였어요. {change}')
            else:
                missing, known, item = (b, a, before) if before else (a, b, after)
                if en:
                    parts.append(f"• The {label} reading was {display_reading(item)} in {known}. There’s no reading for {missing}, so I can’t calculate the change.")
                else:
                    parts.append(f'• {label} 수치는 {known}에 {display_reading(item)}였어요. {missing}에는 수치가 없어 차이를 계산할 수 없어요.')
        if not parts:
            intro = (f'I couldn’t find recorded progress for {title} in either {a} or {b}.' if en else
                     f'{title}은 {a}와 {b} 모두 비교할 진행률 수치가 없어요.')
    else:
        intro = ''
        for row in rows:
            week = week_label(row['week_start'], origin, en)
            values = readings(row, source, en)
            if not values:
                label_en = {'ai': 'an AI installation reading', 'manager': 'a progress report from the site manager'}.get(source, 'a progress reading')
                label_ko = {'ai': '비전 설치율', 'manager': '현장 관리자 진행률'}.get(source, '진행률')
                parts.append(f'I couldn’t find {label_en} for {title} in {week}.' if en else
                             f'{week}에 {title}의 {label_ko} 수치를 확인할 수 없어요.')
                if source == 'ai' and row.get('historical_observations'):
                    values = {key: item for key, item in readings(row, 'both', en).items() if key[0] == 'historical'}
            for key, item in values.items():
                value = display_reading(item)
                if key[0] == 'manager':
                    parts.append(f'The site manager reported that {title} was at {value} in {week}.' if en else
                                 f'{week}에 현장 관리자가 보고한 {title}의 진행률은 {value}예요.')
                elif key[0] == 'ai':
                    _, _, view, target = key
                    area = f'{target} / {view}'
                    parts.append(f'In {week}, the AI measured {value} installed for {title} in {area}.' if en else
                                 f'{week}에 비전이 관측한 {title}의 설치율은 {area}에서 {value}였어요.')
                else:
                    target = key[1]
                    parts.append(f'The historical labels used in the report show {target} at {value} installed for {title} in {week}.' if en else
                                 f'{week} 보고서에 쓰인 사람이 검토한 과거 라벨에서는 {title}의 {target} 설치율이 {value}였어요.')
    if 'manager_report' in include:
        for row in rows:
            week = week_label(row['week_start'], origin, en)
            report = row.get('manager_report')
            if report:
                parts.append((f'Here’s the site manager’s note for {week}.' if en else f'{week} 현장 관리자 보고 원문이에요.') + '\n\n' + report)
            else:
                parts.append(f'There’s no site manager note for {week}.' if en else f'{week}에는 등록된 현장 관리자 보고 문장이 없어요.')
    if 'schedule' in include:
        parts.append(schedule_line(rows[-1], en))
    if 'status' in include:
        parts.append(status_line(rows[-1], en))
    return '\n\n'.join(filter(None, [intro, *parts])), comparisons


def schedule_line(data, en):
    start, finish = data['planned_start'], data['planned_finish']
    if en:
        return f"On the current schedule, the start is {start or 'unavailable'} and the finish is {finish or 'unavailable'}."
    return f"현재 공정표의 시작일은 {start or '미등록'}, 종료일은 {finish or '미등록'}으로 표시돼요."


def status_line(data, en):
    return (f"It’s currently marked ‘{data['status']}’ in the task records." if en else
            f"현재 작업 기록에는 ‘{data['status']}’로 표시돼요.")


def rejection_entry(task, attempt, day, reason, en, *, excerpt=False):
    if en:
        submission = f'photo submission #{attempt}' if attempt is not None else 'photo submission'
        intro = f'The {submission} for {task} was rejected on {day}.'
        attribution = 'Here’s an excerpt from the reviewer’s note.' if excerpt else 'Here’s what the reviewer wrote.'
        missing = 'The record doesn’t include a reason.'
    else:
        submission = f'{attempt}차 제출 사진' if attempt is not None else '제출 사진'
        intro = f'{task}의 {submission}은 {day}에 반려됐어요.'
        attribution = '검토자가 남긴 사유의 일부예요.' if excerpt else '검토자가 남긴 반려 사유는 아래와 같아요.'
        missing = '기록에 남아 있는 반려 사유는 없어요.'
    return f'{intro} {attribution}\n{reason}' if reason else f'{intro} {missing}'


def task_answer(data, origin, *, rejection=False, en=False):
    task = task_label(data, en)
    if rejection:
        week = week_label(data['week_start'], origin, en)
        item = data.get('rejected_submission')
        if not item:
            return (f'I couldn’t find a matching rejection for {task} among the photos submitted for {week}.' if en else
                    f'{week} {task} 사진에서 해당하는 반려 기록을 찾지 못했어요.')
        return rejection_entry(task, item.get('submission_attempt'), item['reviewed_at'][:10], item['reason'], en)
    start, finish = data['planned_start'], data['planned_finish']
    original_start, original_finish = data.get('original_start'), data.get('original_finish')
    if en:
        intro = (f"The current schedule has {task} starting on {start} and finishing on {finish}."
                 if start and finish else f"For {task}, the current start date is {start or 'unavailable'} and the finish date is {finish or 'unavailable'}.")
        original = f"Original schedule: {original_start or 'Unavailable'} ~ {original_finish or 'Unavailable'}."
    else:
        intro = (f"현재 공정표에서 {task} 작업은 {start}에 시작해 {finish}에 끝나는 일정이에요."
                 if start and finish else f"{task}의 현재 공정표에 시작일은 {start or '미등록'}, 종료일은 {finish or '미등록'}으로 표시돼요.")
        original = f"원본 공정표에는 {original_start or '미등록'} ~ {original_finish or '미등록'}로 기록돼 있어요."
    return ' '.join([intro, status_line(data, en)] + ([original] if (start, finish) != (original_start, original_finish) else []))


def task_location_answer(data, page, sources_available, en=False):
    task = task_label(data, en)
    if en:
        screen = 'the site manager screen' if page == 'field' else 'the Task Review tab for this site'
        intro = f'You can see the photos and review history for {task} by selecting it in {screen}.'
        link = 'You can open the record directly using the source button below.'
    else:
        screen = '현장 관리자 화면' if page == 'field' else '매장의 ‘작업별 검토’ 탭'
        intro = f'{screen}에서 {task} 작업을 선택하면 제출 사진과 검토 이력을 볼 수 있어요.'
        link = '이 답변 아래 ‘원본 보기’에서도 해당 기록을 바로 열 수 있어요.'
    return ' '.join([intro, status_line(data, en)] + ([link] if sources_available else []))


def weekly_rejections_answer(data, origin, en=False, count_only=False):
    week = week_label(data['week_start'], origin, en)
    review_week = data.get('basis') == 'review'
    count = data['task_count']
    if en:
        quantity = f"{count or 'no'} {'task' if count == 1 else 'tasks'}"
        intro = (f'I found {quantity} with an HQ rejection dated within {week}.' if review_week else
                 f'I found {quantity} with a rejection on a photo submitted for {week}.')
        remaining = 'You can ask about a specific task to see its rejection record.'
    else:
        basis = f'{week}에 본사가 반려한 작업' if review_week else f'{week} 제출 사진에 반려 이력이 있는 작업'
        intro = f'{basis}은 {count}개예요.' if count else f'{basis}은 찾지 못했어요.'
        remaining = '다른 작업의 반려 기록은 작업명을 알려주시면 확인할 수 있어요.'
    if count_only or not count:
        return intro
    text = [intro]
    for item in data['items']:
        text.append('• ' + rejection_entry(task_label(item, en), item['submission_attempt'], item['reviewed_on'],
                                            item['reason'], en, excerpt=item.get('reason_excerpt', False)))
    if len(data['items']) < count:
        text.append(remaining)
    return '\n\n'.join(text)


def weekly_answer(data, origin, en=False, count_only=False):
    title = week_label(data['reporting_week_start'], origin, en)
    scope = data.get('scope', 'all')
    status = data.get('status_filter', 'all')
    rows = data['tasks'][:12]
    count = data.get('scope_count', data['counts']['total'])
    if en:
        metric = {'confirmed': 'tasks confirmed complete', 'needs_review': 'tasks awaiting HQ review',
                  'needs_evidence': 'tasks that need evidence or field verification', 'rejected': 'currently rejected tasks'}.get(status)
        scope_label = {'starting': 'new starts', 'continuing': 'carry-over tasks', 'all': 'scheduled tasks'}[scope]
        label = metric or scope_label
        if count == 1:
            label = label.replace('tasks that need', 'task that needs').replace('tasks', 'task').replace('starts', 'start')
        if metric and scope != 'all':
            scope_phrase = 'the tasks starting that week' if scope == 'starting' else 'the tasks carrying over from earlier weeks'
            intro = f"For {title}, I found {count or 'no'} {label} among {scope_phrase}."
        else:
            verb = 'is' if count == 1 else 'are'
            intro = f"There {verb} {count or 'no'} {label} in {title}."
        if scope == status == 'all' and count:
            intro += f" Of those, {data['starting_count']} start that week and {data['continuing_count']} carry over from earlier weeks."
        remaining = 'You can see the remaining tasks under View source.'
        basis = 'The dates above come from the schedule currently in effect.'
    else:
        scope_label = {'starting': '새로 시작하는 작업', 'continuing': '이전 주부터 이어지는 작업', 'all': '잡혀 있는 작업'}[scope]
        status_label = {'confirmed': '완료가 확인된 작업', 'needs_review': '본사 검토를 기다리는 작업',
                        'needs_evidence': '자료나 현장 확인이 필요한 작업', 'rejected': '현재 반려 상태인 작업'}.get(status)
        label = status_label or scope_label
        subject = f'{scope_label} 중 {label}' if status_label and scope != 'all' else label
        result = f'{count}개예요.' if count else '없어요.'
        intro = f'{title}에 {subject}은 {result}'
        if scope == status == 'all' and count:
            intro += f" 그중 {data['starting_count']}개는 그 주에 시작하고, {data['continuing_count']}개는 이전 주부터 이어져요."
        remaining = '나머지 작업은 아래 ‘원본 보기’에서 확인할 수 있어요.'
        basis = '위 날짜는 현재 적용 중인 공정표 기준이에요.'
    if count_only or not count:
        return intro
    items = [f"• {task['task_name']} (ID {task['source_task_id']}): {task['planned_start']} ~ {task['planned_finish']} · {task['status']}"
             for task in rows]
    return '\n\n'.join((intro, '\n'.join(items), remaining if count > len(items) else basis))


def project_period_answer(data, en=False):
    site, start, finish = data['site_name'], data['start'], data['finish']
    if start and finish:
        return (f'The current construction schedule for {site} runs from {start} through {finish}.' if en else
                f'현재 등록된 {site} 공정표 기준으로 공사 기간은 {start}부터 {finish}까지예요.')
    if start or finish:
        if en:
            return f'The current schedule for {site} shows a start of {start or "unknown"} and a finish of {finish or "unknown"}.'
        return f'{site} 공정표에서 시작일은 {start or "미등록"}, 종료일은 {finish or "미등록"}으로 확인돼요.'
    return (f'I couldn’t find construction dates in the current schedule for {site}.' if en else
            f'{site} 공정표에 등록된 공사 기간을 찾지 못했어요.')


def report_answer(data, origin, en=False):
    readiness = data['readiness']
    week = week_label(data['week_start'], origin, en)
    ready = data['ready_for_final']
    if en:
        intro = f"{'You can' if ready else 'You can’t yet'} generate the final report for {week}."
    else:
        intro = f'{week} 최종 보고서는 지금 만들 수 있어요.' if ready else f'{week} 최종 보고서를 만들려면 자료가 더 필요해요.'
    lines = [intro]
    if not readiness['vision']['complete']:
        lines.append('The final AI analysis hasn’t come in yet.' if en else '비전팀의 최종 분석 결과가 아직 들어오지 않았어요.')
    for task in (task for task in readiness['tasks'] if not task['has_data']):
        if en:
            action = 'needs a photo from the site manager' if task['needs_manager_photo'] else 'is missing supporting evidence'
            lines.append(f"• {task['task_name']} {action}.")
        else:
            action = '현장 관리자 사진이 필요해요' if task['needs_manager_photo'] else '근거 자료가 아직 없어요'
            lines.append(f"• {task['task_name']} 작업은 {action}.")
    return '\n\n'.join(lines)


def change_answer(data, origin, en=False):
    if not data['available']:
        return ('You can see a request if your organization is involved in it.' if en else
                '변경 요청은 참여 회사 담당자가 확인할 수 있어요.')
    rows = data['items']
    weeks = ' / '.join(week_label(day, origin, en) for day in data.get('weeks', []))
    scope = (f' submitted in {weeks}' if en else f'{weeks}에 접수된 ') if weeks else ''
    if not rows:
        return (f'I couldn’t find any matching change requests{scope}.' if en else
                f'{scope}변경 요청 중 조건에 맞는 기록을 찾지 못했어요.')
    if en:
        labels = {'pending': 'awaiting approval', 'accepted': 'approved', 'rejected': 'rejected', 'applied': 'applied to the schedule'}
        intro = 'Here’s what I found in the change request records.'
        scope = f'I looked at requests submitted in {weeks}.' if weeks else ''
    else:
        labels = {'pending': '승인 대기', 'accepted': '승인', 'rejected': '반려', 'applied': '일정 적용 완료'}
        intro = '변경 요청 기록을 확인했어요.'
        scope = f'{weeks}에 접수된 요청 기준이에요.' if weeks else ''
    parts = [' '.join(filter(None, (intro, scope)))]
    for row in rows[:10]:
        name, day = task_label(row, en), row['submitted_on']
        status = labels.get(row['status'], row['status'])
        start, finish = row['proposed_start'], row['proposed_finish']
        if en:
            line = f'The request for {name} was submitted on {day} and is marked “{status}”. The requested start is {start or "unspecified"}, and the requested finish is {finish or "unspecified"}.'
            attribution = 'Here’s the reason given in the request.'
        else:
            line = f'{name} 변경 요청은 {day}에 접수됐고, 현재 ‘{status}’로 표시돼요. 요청 시작일은 {start or "미지정"}, 요청 종료일은 {finish or "미지정"}으로 기록돼 있어요.'
            attribution = '요청서에 적힌 사유예요.'
        parts.append('• ' + line + (f' {attribution}\n{row["reason"]}' if row['reason'] else ''))
    return '\n\n'.join(parts)


def report_status_answer(data, origin, en=False):
    week = week_label(data['week_start'], origin, en)
    rows = data['reports']
    if en:
        label = {'draft': 'draft', 'final': 'final report'}.get(data.get('mode'), 'report')
        labels = {'pending': 'is queued', 'validated': 'has been generated and is awaiting approval',
                  'approved': 'was approved by HQ', 'failed': 'couldn’t be generated or approved'}
        if not rows:
            return f'I couldn’t find a {label} record for {week}.'
        return '\n\n'.join(f"For {week}, {'final report' if row['mode'] == 'final' else 'draft'} revision {row['revision']} "
                            + labels.get(row['status'], 'has no available status')
                            + (f" on {row['approved_on']}" if row['approved_on'] and row['status'] == 'approved' else '') + '.' for row in rows)
    if not rows:
        label = {'draft': '초안', 'final': '최종본'}.get(data.get('mode'), '보고서')
        return f'{week} {label} 생성 기록을 찾지 못했어요.'
    labels = {'pending': '생성을 기다리고 있어요.', 'validated': '생성됐고 본사 승인을 기다리고 있어요.',
              'approved': '본사에서 승인했어요.', 'failed': '생성 또는 승인 과정에서 실패한 것으로 표시돼요.'}
    return '\n\n'.join(f"{week} {'최종본' if row['mode'] == 'final' else '초안'} {row['revision']}차는 "
                        + (f"{row['approved_on']}에 " if row['approved_on'] and row['status'] == 'approved' else '')
                        + labels.get(row['status'], '상태를 확인할 수 없어요.') for row in rows)


def archive_answer(data, metric, en):
    labels = {
        'photos': ('photo_count', '보관된 원본 사진', 'original photos'),
        'json': ('json_count', '보관된 비전 JSON 파일', 'vision JSON files'),
        'first_date': ('first_date', '가장 이른 파일 날짜', 'earliest file date'),
        'last_date': ('last_date', '가장 늦은 파일 날짜', 'latest file date'),
    }
    lines = []
    for name in labels if metric == 'summary' else [metric]:
        field, ko, english = labels[name]
        value = data.get(field)
        if type(value) is int:
            unit = '장' if name == 'photos' else '개'
            lines.append(f'The archive lists {value:,} {english}.' if en else f'{ko}은 {value:,}{unit}예요.')
        elif value:
            lines.append(f'The {english} in the archive is {value}.' if en else f'{ko}는 {value}이에요.')
        else:
            lines.append(f'I couldn’t verify the {english} in the archive.' if en else f'{ko} 정보를 보관 목록에서 확인할 수 없어요.')
    if metric in ('summary', 'first_date', 'last_date') and data.get('date_basis') == 'filename':
        lines.append('The dates come from the archived filenames.' if en else '날짜는 보관된 파일 이름을 기준으로 확인했어요.')
    return '\n'.join(lines)


def report_photos_answer(data, origin, en):
    week = week_label(data['week_start'], origin, en)
    if data['status'] == 'missing_report':
        return f'I couldn’t find a saved report PDF for {week}.' if en else f'{week}에 해당하는 저장된 보고서 PDF를 찾지 못했어요.'
    if data['status'] != 'checked':
        return (f'I couldn’t verify which task pages have photos in the saved PDF for {week}.' if en else
                f'{week}에 저장된 PDF에서 작업별 사진 유무를 모두 확인할 수 없었어요.')
    count, revision = data['task_count'], data['revision']
    label = {'draft': ('초안', 'draft'), 'final': ('최종본', 'final report')}[data['mode']]
    if not data['missing']:
        return (f'I checked the saved {label[1]} (revision {revision}) for {week}. All {count} tasks have a photo on their detail pages.' if en else
                f'{week}에 저장된 {label[0]} {revision}차를 확인했어요. 작업 {count}개 모두 상세 페이지에 사진이 들어 있어요.')
    missing = len(data['missing'])
    title = (f"I checked the saved {label[1]} (revision {revision}) for {week}. {missing} {'task has' if missing == 1 else 'tasks have'} a detail page without a photo." if en else
             f'{week}에 저장된 {label[0]} {revision}차를 확인해 보니, 상세 페이지에 사진이 빠진 작업이 {missing}개 있어요.')
    return title + '\n\n' + '\n'.join(f"• {task['name']} (ID {task['id']}) · {'Pages' if en else '페이지'} "
                                     + ', '.join(map(str, task['pages'])) for task in data['missing'])
