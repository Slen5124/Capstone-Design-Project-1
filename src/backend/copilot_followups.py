"""Generate follow-ups with the answer; validate before displaying them as text."""
import json
import re
from project_service import fail

FOLLOWUP_INSTRUCTION = '''
Use recent server-provided conversation to resolve follow-ups such as "그중" or "그 작업".
Past assistant statements are context, not verified evidence; check current tools for factual claims.
When giving your final answer (not a tool call), output ONLY a JSON object:
{"answer":"concise answer in the user's language","suggestions":["follow-up question 1?","follow-up question 2?"]}.
Both suggestions must naturally continue THIS question and YOUR answer, be distinct, and be answerable
with the same site's permitted data/tools or usage guide. Prefer one detail question and one next-step
question. Write each as a short user question (at most 90 characters), not an action command.
Do not assume missing data exists, invent task names, dates, figures or approvals, or suggest another
site. If data is missing, suggest how to provide it or how to use the relevant screen. Do not repeat
the answered question. Do not output HTML, URLs, credentials, or markdown fences around the JSON.
'''


def fallback_suggestions(question, answer, source_only=False):
    topic = question + ' ' + answer
    if source_only:
        return ['방금 설명한 자료로 확인할 수 있는 범위는 어디까지인가요?', '작업별 진행률을 확인하려면 어떤 자료가 더 필요한가요?']
    if answer.startswith(('안녕하세요!', '천만에요!', '공정표에 어떤 작업이 있는지')):
        return ['이번 주에 시작하는 작업은?', '이번 주 사진 반려 이력이 있는 작업은?']
    if '반려' in topic and '확인할 작업명' not in answer:
        return ['해당 작업의 자료와 검토 상태는 어디에서 확인하나요?', '해당 작업의 현재 진행률은 어떻게 되나요?']
    if any(x in topic for x in ('연장', '변경 요청', '승인')):
        return ['해당 요청의 승인과 일정 적용은 어떻게 구분하나요?', '이 요청의 처리 결과는 어느 화면에서 확인하나요?']
    if any(x in topic for x in ('누락', '없는', '없습', '미제출', '부족', '미등록', '자료 대기')):
        return ['방금 설명한 부족 자료는 어떻게 제출하나요?', '이 자료가 없으면 보고서 생성에 어떤 영향이 있나요?']
    if any(x in topic for x in ('진행률', '설치율', '관측', '분석')):
        return ['방금 설명한 수치는 어떤 자료를 근거로 하나요?', '이 진행 상태와 작업 완료 확인은 어떻게 다른가요?']
    if '보고서' in topic:
        return ['이 보고서에 필요한 자료는 무엇인가요?', '이 보고서의 초안과 최종본은 어떻게 다른가요?']
    return ['방금 설명한 작업의 예정 기간을 자세히 알려줘', '해당 작업의 자료와 검토 상태는 어디에서 확인하나요?']


def final_content(message, question, *, source_only=False, verified_text=None, language='ko'):
    raw = str(message.get('content') or '').strip()
    if not raw and verified_text is None:
        fail(502, 'Qwen이 답변 문장을 만들지 못했습니다. 다시 질문해 주세요.')
    cleaned = re.sub(r'^```(?:json)?\s*|\s*```$', '', raw).strip()
    candidates = []
    if cleaned.startswith('{'):
        try:
            result = json.loads(cleaned)
            answer = result['answer'].strip()
            candidates = result.get('suggestions', [])
            if not isinstance(answer, str) or not answer:
                raise ValueError()
        except (ValueError, KeyError, TypeError, AttributeError):
            if verified_text is None:
                fail(502, '답변 형식을 확인하지 못했습니다. 다시 질문해 주세요.')
            answer, candidates = verified_text, []
    else:
        answer = raw
    if verified_text is not None:
        answer = verified_text
    if len(answer) > 10000:
        fail(502, '답변이 너무 깁니다. 질문 범위를 줄여 다시 시도해 주세요.')
    valid = []
    if isinstance(candidates, list):
        for item in candidates[:6]:
            if not isinstance(item, str):
                continue
            item = item.strip()
            if language == 'en' and re.search(r'[가-힣]', item):
                continue
            if not 5 <= len(item) <= 100 or re.search(r'[<>\n\r]|https?://|api[_ -]?key|비밀번호|토큰', item, re.I):
                continue
            if re.search(r'(승인|삭제|저장|변경|전송|제출|생성)\s*(해줘|해 줘|하세요|하라)', item):
                continue
            if not (item.endswith('?') or item.endswith(('알려줘', '설명해줘', '알려 주세요'))):
                continue
            if item == question.strip() or item in valid:
                continue
            if any(n not in question + ' ' + answer for n in re.findall(r'\d+(?:[.%-]\d+)*', item)):
                continue
            valid.append(item)
    generated = len(valid) >= 2
    defaults = (['What evidence is on record for this work?', 'How can I check the reporting week in the schedule?']
                if language == 'en' else fallback_suggestions(question, answer, source_only))
    for item in defaults:
        if item not in valid and item != question.strip():
            valid.append(item)
    return {'answer': answer, 'suggestions': valid[:2], 'suggestion_source': 'qwen' if generated else 'fallback'}
