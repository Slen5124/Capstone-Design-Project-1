import os
import json
from io import BytesIO
from datetime import timedelta
from pathlib import Path
import unittest
from unittest.mock import patch
from uuid import uuid4

from aloha_fixture import Fixture


def plan(operation, **fields):
    return {'content': json.dumps({'operation': operation, **fields}, ensure_ascii=False)}


class CopilotPlanTests(unittest.TestCase):
    def test_single_allowed_include_value_is_kept_and_validated(self):
        from copilot_intent import parse_plan

        content = json.dumps({'queries': [{'operation': 'progress', 'include': 'manager_report'}]})
        self.assertEqual(['manager_report'], parse_plan({'content': content}).queries[0].include)
        invalid = json.dumps({'queries': [{'operation': 'progress', 'include': 'unsupported'}]})
        self.assertIsNone(parse_plan({'content': invalid}))


class CopilotTests(unittest.TestCase):
    def setUp(self):
        feature = patch.dict("os.environ", {"ALO_ENABLE_COPILOT": "1", "ALO_ENABLE_WEEKLY_REPORTS": "1"})
        feature.start()
        self.addCleanup(feature.stop)
        self.f = Fixture()
        goal_check = patch('project_copilot.check_question_goal', return_value=None)
        goal_check.start()
        self.addCleanup(goal_check.stop)
        continuation = patch('project_copilot.resolve_clarification_reply', return_value=None)
        continuation.start()
        self.addCleanup(continuation.stop)
        self.env = {
            'ALO_COPILOT_PROVIDER': 'cloud',
            'ALO_QWEN_API_KEY': 'unit-test-key',
            'ALO_QWEN_BASE_URL': 'https://workspace.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1',
            'ALO_QWEN_MODEL': 'qwen-unit-test',
        }

    def tearDown(self):
        self.f.close()

    def test_copilot_is_scoped_and_read_only(self):
        path = self.f.base + '/copilot/ask'
        with patch.dict(os.environ, self.env, clear=False), patch(
                'project_copilot.call_qwen', return_value=plan('schedule')) as qwen:
            response = self.f.request('POST', path, {
                'message': '이번 주 작업은?', 'week_start': str(self.f.start), 'page': 'schedule',
            })
        self.assertEqual(200, response.status_code, response.text)
        body = response.json()
        self.assertTrue(body['read_only'])
        self.assertEqual(self.f.code, body['site_code'])
        self.assertEqual(str(self.f.start), body['week_start'])
        self.assertEqual(1, qwen.call_count)
        self.assertEqual(20, len(self.f.tables))

    def test_copilot_resolves_only_supported_scoped_plans(self):
        path = self.f.base + '/copilot/ask'
        with patch.dict(os.environ, self.env, clear=False), patch(
                'project_copilot.call_qwen', side_effect=[
                    plan('task_status', task='1'), plan('task_status', task='outside-schedule'),
                    plan('schedule', site='another-site'), plan('schedule', site='another-site'),
                ]) as qwen:
            body = self.f.request('POST', path, {'message': '벽체 작업의 현황은?'})
            self.assertIn('Drywall installation', body.json()['answer'])
            self.assertIn('주간 공정표·작업 상태', body.json()['sources'])
            invalid_id = self.f.request('POST', path, {'message': '그 작업 현황은?'})
            self.assertIn('현재 매장의 공정표', invalid_id.json()['answer'])
            self.assertEqual([], invalid_id.json()['source_records'])
            bad_plan = self.f.request('POST', path, {'message': '다른 매장 일정도 봐줘'})
            self.assertIn('조회 조건을 확인하지 못했습니다', bad_plan.json()['answer'])
            self.assertEqual([], bad_plan.json()['sources'])
        self.assertTrue(all(call.args[2] == [] for call in qwen.call_args_list))

    def test_project_period_replans_misrouted_date_and_uses_entire_effective_schedule(self):
        tasks = self.f.tables['schedule_tasks']
        last = self.f.start + timedelta(weeks=8)
        with self.f.engine.begin() as c:
            c.execute(tasks.update().where(tasks.c.id == self.f.tasks['wall']).values(effective_finish=last))
        path = self.f.base + '/copilot/ask'
        with patch.dict(os.environ, self.env), patch('project_copilot.call_qwen', side_effect=[
            plan('local_date'), plan('project_period'), plan('local_date'), plan('local_date'), plan('local_date'),
        ]) as qwen:
            period = self.f.request('POST', path, {'message': '현장 공사 기간이 어떻게 되지>', 'week_start': str(self.f.start)}).json()
            self.assertIn(f'{self.f.start}부터 {last}까지', period['answer'])
            self.assertEqual(['전체 공정표 적용 일정'], period['sources'])
            self.assertIn('does not ask for the current calendar date', qwen.call_args_list[1].args[1][0]['content'])
            retry_failed = self.f.request('POST', path, {'message': '전체 공사가 언제 끝나?' }).json()
            self.assertNotIn('오늘은', retry_failed['answer'])
            today = self.f.request('POST', path, {'message': "What is today's date at the site?"}).json()
            self.assertIn('Today at the site is', today['answer'])

    def test_planned_milestone_cannot_answer_actual_project_completion(self):
        path = self.f.base + '/copilot/ask'
        actual = 'What date was construction actually completed at this site?'
        with patch.dict(os.environ, self.env), patch('project_copilot.call_qwen', side_effect=[
            plan('task_status', task='1'), {'content': json.dumps({'queries': [
                {'operation': 'project_period'}, {'operation': 'project_period'},
            ]})}, plan('project_actual_dates'),
        ]):
            en = self.f.request('POST', path, {'message': actual}).json()
            self.assertIn('couldn’t verify the project’s actual', en['answer'])
            self.assertNotIn(str(self.f.start), en['answer'])
            self.assertEqual([], en['source_records'])
            scheduled = self.f.request('POST', path, {'message': '현장 현장 준공 예정일은 언제야?'}).json()
            self.assertEqual(1, scheduled['answer'].count(str(self.f.start + timedelta(days=4))))
            ko = self.f.request('POST', path, {'message': '현장 공사는 실제로 언제 준공됐어?'}).json()
            self.assertIn('실제 착공일이나 준공일은 확인할 수 없어요', ko['answer'])

    def test_report_instructions_are_distinct_from_saved_report_status(self):
        path = self.f.base + '/copilot/ask'
        with patch.dict(os.environ, self.env), patch('project_copilot.call_qwen', side_effect=[
            plan('guide', guide_topic='reports'), plan('guide', guide_topic='reports'), plan('report_status'),
        ]) as qwen:
            for question in ('12주차 보고서 받아보고 싶은데 어떻게 하면 돼?', 'How do I download the Week 12 report?'):
                reply = self.f.request('POST', path, {'message': question}).json()
                self.assertIn('초안 만들기 · PDF', reply['answer'])
                self.assertIn('생성된 PDF 열기', reply['answer'])
            status = self.f.request('POST', path, {'message': '이미 생성된 보고서가 있어?'}).json()
            self.assertIn('생성 기록을 찾지 못했어요', status['answer'])
            self.assertEqual(3, qwen.call_count)

    def test_first_plan_can_clarify_goal_without_reading_records(self):
        path = self.f.base + '/copilot/ask'
        with patch.dict(os.environ, self.env), patch(
            'project_copilot.call_qwen', side_effect=[
                plan('clarify', needs='week', guide_topic='reports'),
                plan('clarify', needs='goal', guide_topic='changes'),
                plan('clarify', needs='goal'), plan('schedule'),
            ]) as qwen, patch('project_copilot.report_status') as lookup, patch('project_copilot.change_requests') as changes:
            for question, answer in (
                ('12주차 보고서 좀.', '이미 저장된 PDF를 찾으세요?'),
                ('Can you help me with schedule extensions?', 'check existing requests?'),
                ('Drywall installation 좀 봐줘', '일정, 진행률, 검토 기록 중 무엇을 확인할까요?'),
            ):
                ambiguous = self.f.request('POST', path, {'message': question}).json()
                self.assertEqual([], ambiguous['source_records'])
                self.assertIn(answer, ambiguous['answer'])
            lookup.assert_not_called()
            changes.assert_not_called()
            schedule = self.f.request('POST', path, {'message': '이번 주 작업은 뭐야?'}).json()
            self.assertEqual('weekly_schedule', schedule['source_records'][0]['type'])
            self.assertEqual(4, qwen.call_count)

    def test_goal_check_can_stop_an_unsupported_assumption_before_lookup(self):
        from copilot_intent import GoalCheck

        path = self.f.base + '/copilot/ask'
        with patch.dict(os.environ, self.env), patch('project_copilot.call_qwen', return_value=plan('change_requests')), patch(
                'project_copilot.check_question_goal', return_value=GoalCheck(decision='clarify', guide_topic='changes')), patch(
                'project_copilot.change_requests') as lookup:
            result = self.f.request('POST', path, {'message': '일정 조정 건을 봐줘.'}).json()
            self.assertIn('기존 변경 요청을 확인할까요?', result['answer'])
            lookup.assert_not_called()

    def test_goal_check_cannot_ask_again_for_an_explicit_task(self):
        from copilot_intent import GoalCheck

        path = self.f.base + '/copilot/ask'
        with patch.dict(os.environ, self.env), patch(
                'project_copilot.call_qwen', return_value=plan('task_status', task='1')), patch(
                'project_copilot.check_question_goal', return_value=GoalCheck(decision='clarify', needs='task')):
            result = self.f.request('POST', path, {
                'message': 'Drywall installation 작업의 종료일이 언제예요?',
            }).json()
            self.assertIn('Drywall installation', result['answer'])
            self.assertEqual('task_schedule', result['source_records'][0]['type'])

    def test_copilot_rejects_non_monday_scope(self):
        with patch.dict(os.environ, self.env, clear=False):
            response = self.f.request('POST', self.f.base + '/copilot/ask', {
                'message': '이번 주 작업은?', 'week_start': '2025-02-18',
            })
        self.assertEqual(422, response.status_code)

    def test_english_multi_request_retains_each_scope_and_reading(self):
        content = json.dumps({'language': 'en', 'queries': [
            {'operation': 'schedule', 'scope': 'starting', 'list_mode': 'count'},
            {'operation': 'progress', 'task': '4', 'task_mention': 'In-Wall Inspection'},
        ]})
        self.f.submit(progress=64)
        with patch.dict(os.environ, self.env), patch('project_copilot.call_qwen', return_value={'content': content}):
            response = self.f.request('POST', self.f.base + '/copilot/ask', {
                'message': 'How many tasks start this week, and how far along is In-Wall Inspection?',
                'week_start': str(self.f.start),
            })
        self.assertEqual(200, response.status_code, response.text)
        result = response.json()
        self.assertIn('There are 4 new starts', result['answer'])
        self.assertRegex(result['answer'], r'The site manager reported .* at 64%')
        self.assertEqual({'weekly_schedule', 'task_schedule', 'manager_submission'},
                         {ref['type'] for ref in result['source_records']})
        self.assertNotRegex(result['answer'] + ' '.join(result['suggestions']), '[가-힣]')

    def test_photo_coverage_reads_the_saved_pdf_instead_of_readiness(self):
        from reportlab.pdfgen.canvas import Canvas

        buf = BytesIO()
        pdf = Canvas(buf)
        for index in range(1, 3):
            row = pdf.beginText(40, 750)
            for text in ('Task detail', 'TASK ID', str(index), 'SITE PHOTO'):
                row.textLine(text)
            pdf.drawText(row)
            if index == 1:
                from PIL import Image
                from reportlab.lib.utils import ImageReader

                pdf.drawImage(ImageReader(Image.new('RGB', (20, 20), 'white')), 40, 600)
            pdf.showPage()
        pdf.save()
        file_id = self.f.file(buf.getvalue(), 'report_pdf')
        self.f.add('agent_reports', id=uuid4(), site_id=self.f.site, schedule_id=self.f.schedule,
                   period_start=self.f.start, period_finish=self.f.start + timedelta(days=4), revision=1,
                   report_file_id=file_id, status='validated', generated_at=self.f.now,
                   provenance={'kind': 'project_weekly_report', 'mode': 'draft', 'input': {'tasks': [
                       {'source_task_id': '1', 'task_name': 'Drywall installation'},
                       {'source_task_id': '2', 'task_name': 'Floor installation'},
                   ]}})
        with patch.dict(os.environ, self.env), patch('project_copilot.call_qwen', return_value=plan('report_task_photos')):
            response = self.f.request('POST', self.f.base + '/copilot/ask', {
                'message': 'Which saved detail pages have no photo?', 'week_start': str(self.f.start),
            })
        self.assertEqual(200, response.status_code, response.text)
        result = response.json()
        self.assertIn('1 task has a detail page without a photo', result['answer'])
        self.assertIn('Floor installation (ID 2)', result['answer'])
        self.assertNotIn('Drywall installation', result['answer'])
        self.assertEqual(str(file_id), result['source_records'][0]['record_id'])
        second_week = self.f.start + timedelta(days=7)
        with patch.dict(os.environ, self.env), patch('project_copilot.call_qwen', return_value=plan(
                'report_task_photos', weeks=['selected'], period_mention='1주차')):
            explicit = self.f.request('POST', self.f.base + '/copilot/ask', {
                'message': '1주차 보고서에 사진이 없는 작업이 있어?', 'week_start': str(second_week),
            }).json()
        self.assertEqual(str(second_week), explicit['week_start'])
        self.assertEqual(str(self.f.start), explicit['source_records'][0]['week_start'])
        self.f.add('agent_reports', id=uuid4(), site_id=self.f.site, schedule_id=self.f.schedule,
                   period_start=second_week, period_finish=second_week + timedelta(days=4), revision=2,
                   report_file_id=file_id, status='validated', generated_at=self.f.now,
                   provenance={'kind': 'project_weekly_report', 'mode': 'draft', 'input': {'tasks': [
                       {'source_task_id': '1', 'task_name': 'Drywall installation'},
                       {'source_task_id': '2', 'task_name': 'Floor installation'},
                   ]}})
        with patch('project_copilot.call_qwen', return_value=plan('schedule')) as qwen:
            for question, week, expected in (
                ('2주차는 ?', second_week, 'Floor installation (ID 2)'),
                ('And week 3?', second_week + timedelta(days=7), 'find a saved report PDF'),
            ):
                followup = self.f.request('POST', self.f.base + '/copilot/ask', {
                    'message': question, 'week_start': str(self.f.start),
                }).json()
                self.assertIn(expected, followup['answer'])
                self.assertIn(str(week), followup['answer'])
                self.assertNotIn('Weekly schedule and task status', followup['sources'])
            qwen.assert_not_called()

    def test_rejection_button_resolves_original_submission_after_resubmission(self):
        task_id = str(self.f.tasks['manager'])
        task_path = self.f.base + '/tasks/' + task_id
        original_photo = self.f.photo()
        original = self.f.submit(photos=[original_photo])
        review_id = str(uuid4())
        reason = '조명이 켜진 모습을 확인할 수 없어 다시 찍어 주세요.'
        review = self.f.request('POST', task_path + '/reviews', {
            'id': review_id, 'target_event_id': original['id'], 'outcome': 'reject', 'body': reason,
        })
        self.assertEqual(201, review.status_code, review.text)
        replacement = self.f.submit(progress=100, photos=[self.f.photo()])
        completed = self.f.request('POST', task_path + '/reviews', {
            'id': str(uuid4()), 'target_event_id': replacement['id'], 'outcome': 'complete',
        })
        self.assertEqual(201, completed.status_code, completed.text)

        with patch.dict(os.environ, self.env), patch(
                'project_copilot.call_qwen', return_value=plan('rejection_reason', task='4')) as qwen:
            answer = self.f.request('POST', self.f.base + '/copilot/ask', {
                'message': '내부 검사 사진이 왜 반려됐어?', 'week_start': str(self.f.start),
            })
            self.assertEqual(200, answer.status_code, answer.text)
            self.assertEqual(1, qwen.call_count)
            self.assertTrue(answer.json()['answer'].endswith('\n' + reason))
            self.assertIn('에 반려됐어요.', answer.json()['answer'])
            self.assertNotIn(reason, str(qwen.call_args.args[1]))
        refs = answer.json()['source_records']
        self.assertEqual([review_id], [item['review_id'] for item in refs])
        self.assertEqual(task_id, refs[0]['task_id'])
        self.assertEqual(1, refs[0]['submission_attempt'])
        history = self.f.request('GET', self.f.base + '/copilot/history?week_start=' + str(self.f.start))
        self.assertEqual(refs, history.json()['turns'][-1]['source_records'])

        path = task_path + '/rejection-records/' + review_id
        for actor in ('hq', 'field'):
            record = self.f.request('GET', path, actor=actor)
            self.assertEqual(200, record.status_code, record.text)
            self.assertEqual(reason, record.json()['reason'])
            self.assertEqual([str(original_photo)], record.json()['submission']['photo_ids'])
        wrong_task = self.f.base + '/tasks/' + str(self.f.tasks['wall']) + '/rejection-records/' + review_id
        self.assertEqual(404, self.f.request('GET', wrong_task).status_code)
        self.assertEqual(403, self.f.request('GET', path, actor='other').status_code)

        with patch.dict(os.environ, self.env), patch(
                'project_copilot.call_qwen', return_value=plan('open_record', task='previous')) as qwen:
            followup = self.f.request('POST', self.f.base + '/copilot/ask', {
                'message': '해당 작업의 자료와 검토 상태는 어디에서 확인하나요?',
                'week_start': str(self.f.start), 'page': 'review',
            })
            self.assertEqual(200, followup.status_code, followup.text)
            body = followup.json()
            self.assertIn('In-Wall Inspection (작업 ID 4)', body['answer'])
            self.assertIn('작업별 검토', body['answer'])
            self.assertIn('작업 완료 확인', body['answer'])
            self.assertEqual(review_id, body['source_records'][0]['review_id'])
            self.assertIn('previous_verified_tasks', qwen.call_args.args[1][0]['content'])

    def test_weekly_rejection_list_uses_submission_week_and_keeps_historical_rejections(self):
        first = self.f.submit(progress=50)
        second_task = self.f.submit(key='floor', progress=50)
        self.f.now += timedelta(days=10)
        refs = []
        for task, submission in ((self.f.tasks['manager'], first), (self.f.tasks['floor'], second_task)):
            review_id = str(uuid4())
            response = self.f.request('POST', self.f.base + f'/tasks/{task}/reviews', {
                'id': review_id, 'target_event_id': submission['id'], 'outcome': 'reject',
                'body': '작업 구간이 사진에 보이지 않습니다.',
            })
            self.assertEqual(201, response.status_code, response.text)
            refs.append(review_id)
        self.f.submit(progress=90)
        path = self.f.base + '/copilot/ask'
        with patch.dict(os.environ, self.env), patch('project_copilot.call_qwen', side_effect=[
            plan('weekly_rejections'), plan('weekly_rejections', weeks=[1]),
            plan('open_record', task='previous'),
            plan('rejection_reason', task='4', task_mention='벽체 내부 검사'),
            plan('weekly_rejections', weeks=['next']),
        ]) as qwen:
            found = self.f.request('POST', path, {
                'message': 'ㅇ이번주차에 사진 반려된 작업 있어?', 'week_start': str(self.f.start),
            })
            self.assertEqual(200, found.status_code, found.text)
            self.assertIn('작업은 2개예요.', found.json()['answer'])
            self.assertIn('In-Wall Inspection', found.json()['answer'])
            self.assertEqual(set(refs), {item['review_id'] for item in found.json()['source_records']})
            screenshot = self.f.request('POST', path, {
                'message': '1주차에 사진 반려된 작업은?', 'week_start': str(self.f.start),
            })
            self.assertEqual(found.json()['answer'], screenshot.json()['answer'])
            self.assertEqual(set(refs), {item['review_id'] for item in screenshot.json()['source_records']})
            ambiguous = self.f.request('POST', path, {
                'message': '해당 작업의 자료와 검토 상태는 어디에서 확인하나요?', 'week_start': str(self.f.start),
            })
            self.assertIn('앞에서 여러 작업을 확인했습니다', ambiguous.json()['answer'])
            named = self.f.request('POST', path, {
                'message': '벽체 내부 검사는 왜 새 사진이 필요해?', 'week_start': str(self.f.start),
            })
            self.assertIn('In-Wall Inspection', named.json()['answer'])
            self.assertEqual(refs[0], named.json()['source_records'][0]['review_id'])
            self.assertEqual(2, len(qwen.call_args.args[1]))
            empty = self.f.request('POST', path, {
                'message': '다음 주에 사진이 반려된 작업 있어?', 'week_start': str(self.f.start),
            })
            self.assertIn('반려 이력이 있는 작업은 찾지 못했어요.', empty.json()['answer'])
            self.assertEqual([], empty.json()['source_records'])
            self.assertEqual(5, qwen.call_count)

    def test_weekly_schedule_distinguishes_starting_continuing_and_overlap(self):
        tasks = self.f.tables['schedule_tasks']
        second = self.f.start + timedelta(days=7)
        with self.f.engine.begin() as c:
            c.execute(tasks.update().where(tasks.c.id == self.f.tasks['wall']).values(
                finish=second + timedelta(days=2)))
            c.execute(tasks.update().where(tasks.c.id == self.f.tasks['floor']).values(
                start=second, finish=second + timedelta(days=4)))
            c.execute(tasks.update().where(tasks.c.id == self.f.tasks['ceiling']).values(
                effective_start=second, effective_finish=second + timedelta(days=4)))
        path = self.f.base + '/copilot/ask'
        with patch.dict(os.environ, self.env), patch('project_copilot.call_qwen', side_effect=[
            plan('schedule', weeks=[2], scope='starting'), plan('schedule', weeks=[2], scope='continuing'),
            plan('schedule', weeks=[2], scope='both'), plan('weekly_rejections', weeks=['referenced']),
        ]) as qwen:
            starting = self.f.request('POST', path, {
                'message': '2주차에 시작되는 작업은?', 'week_start': str(self.f.start),
            }).json()
            self.assertIn('새로 시작하는 작업은 2개예요.', starting['answer'])
            self.assertTrue(starting['answer'].endswith('위 날짜는 현재 적용 중인 공정표 기준이에요.'))
            self.assertIn('Floor installation', starting['answer'])
            self.assertIn('Ceiling installation', starting['answer'])
            self.assertNotIn('Drywall installation', starting['answer'])
            self.assertEqual(str(second), starting['source_records'][0]['week_start'])
            self.assertEqual('starting', starting['source_records'][0]['scope'])
            continuing = self.f.request('POST', path, {
                'message': '2주차에 지난주부터 이어지는 작업은?', 'week_start': str(self.f.start),
            }).json()
            self.assertIn('Drywall installation', continuing['answer'])
            self.assertNotIn('Floor installation', continuing['answer'])
            self.assertEqual('continuing', continuing['source_records'][0]['scope'])
            grouped = self.f.request('POST', path, {
                'message': '2주차에 시작하는 작업과 이어지는 작업을 각각 알려줘', 'week_start': str(self.f.start),
            }).json()
            self.assertEqual({'starting', 'continuing'}, {r['scope'] for r in grouped['source_records']})
            following = self.f.request('POST', path, {
                'message': '그 주에 현장 증빙을 되돌려 보낸 게 뭐야?', 'week_start': str(self.f.start),
            }).json()
            self.assertIn('2주차', following['answer'])
            self.assertEqual(4, qwen.call_count)

    def test_greeting_does_not_query_project_facts_or_reuse_old_answers(self):
        path = self.f.base + '/copilot/ask'
        context = {'week_start': str(self.f.start), 'message': 'ID 4 일정은?'}
        with patch.dict(os.environ, self.env), patch('project_copilot.call_qwen', side_effect=[
            plan('task_status', task='4'), plan('greeting'),
        ]) as qwen, patch('project_copilot.weekly_snapshot') as project:
            self.assertEqual(200, self.f.request('POST', path, context).status_code)
            reply = self.f.request('POST', path, {**context, 'message': '좋은 아침이야!'}).json()
            self.assertTrue(reply['answer'].startswith('안녕하세요!'))
            self.assertEqual([], reply['sources'])
            self.assertEqual([], reply['source_records'])
            self.assertTrue(any('작업' in item for item in reply['suggestions']))
            self.assertEqual(2, qwen.call_count)
            project.assert_not_called()

    def test_explicit_date_and_longer_task_label_override_unsupported_model_choices(self):
        tasks = self.f.tables['schedule_tasks']
        second = self.f.start + timedelta(days=7)
        with self.f.engine.begin() as c:
            c.execute(tasks.update().where(tasks.c.id == self.f.tasks['wall']).values(task_name='Hang Drywall'))
            c.execute(tasks.update().where(tasks.c.id == self.f.tasks['ceiling']).values(
                task_name='Hang Drywall Fitting Room Ceiling', start=second, finish=second))
        path = self.f.base + '/copilot/ask'
        with patch.dict(os.environ, self.env), patch('project_copilot.call_qwen', side_effect=[
            plan('schedule', weeks=[1]), plan('task_status', task='1', weeks=[2]),
        ]):
            result = self.f.request('POST', path, {
                'message': '2025-02-26가 속한 주의 전체 일정', 'week_start': str(self.f.start),
            }).json()
            self.assertEqual(str(second), result['source_records'][0]['week_start'])
            result = self.f.request('POST', path, {
                'message': 'Hang Drywall Fitting Room Ceiling 일정은?', 'week_start': str(self.f.start),
            }).json()
            self.assertIn('Hang Drywall Fitting Room Ceiling (작업 ID 3)', result['answer'])

    def test_copilot_task_tool_reads_actual_values_for_each_requested_week(self):
        first = self.f.start
        second = first + timedelta(days=7)
        first_observation = self.f.observe(value=0.60)
        first_submission = self.f.submit(progress=40)
        self.f.now += timedelta(days=7)
        daily_id = uuid4()
        self.f.add('daily_records', id=daily_id, site_id=self.f.site, schedule_id=self.f.schedule,
                   location_id=self.f.location, observation_date=second, captured_at=self.f.now - timedelta(days=6),
                   photo_file_id=self.f.photo_ids[0], status='pending', created_at=self.f.now, updated_at=self.f.now)
        self.f.observe(value=0.80, daily_record_id=str(daily_id))
        self.f.submit(progress=65, captured_at='2025-02-28T10:00:00-08:00')

        def get_values(task_id, first_value, second_value, kind, record_id, task_key):
            with patch.dict(os.environ, self.env), patch('project_copilot.call_qwen',
                    return_value=plan('progress', task=task_id, weeks=[1, 'selected'], period_mention='두 주차')) as qwen:
                result = self.f.request('POST', self.f.base + '/copilot/ask', {
                    'message': '같은 작업의 두 주차 진행률은?', 'week_start': str(second),
                })
                self.assertEqual(200, result.status_code, result.text)
                self.assertEqual(1, qwen.call_count)
            self.assertIn(f'{first_value:g}%', result.json()['answer'])
            self.assertIn(f'{second_value:g}%', result.json()['answer'])
            records = [r for r in result.json()['source_records'] if r['type'] == kind]
            self.assertEqual([str(first), str(second)], [r['week_start'] for r in records])
            self.assertEqual(str(record_id), records[0]['record_id'])
            root = self.f.base + '/tasks/' + str(self.f.tasks[task_key]) + '/copilot-source/' + kind + '/'
            read = self.f.request('GET', root + str(record_id))
            self.assertEqual(200, read.status_code, read.text)
            self.assertEqual(403, self.f.request('GET', root + str(record_id), actor='other').status_code)
            return read.json()

        observation = get_values('1', 60.0, 80.0, 'ai_observation', first_observation, 'wall')
        self.assertEqual(60.0, observation['observation']['installed_percent'])
        submission = get_values('4', 40, 65, 'manager_submission', first_submission['id'], 'manager')
        self.assertEqual(40, submission['submission']['progress_percent'])

    def test_change_lookup_filters_status_and_only_uses_an_explicit_receipt_period(self):
        changes = self.f.tables['change_requests']
        request_id = uuid4()
        with self.f.engine.begin() as c:
            c.execute(changes.insert().values(id=request_id, site_id=self.f.site, schedule_id=self.f.schedule,
                task_id=self.f.tasks['manager'], from_organization_id=self.f.hq_org,
                to_organization_id=self.f.hq_org, request_type='extension', status='applied',
                created_at=self.f.now + timedelta(days=28)))
        path = self.f.base + '/copilot/ask'
        with patch.dict(os.environ, self.env), patch('project_copilot.call_qwen', side_effect=[
            plan('change_requests', weeks=[1], change_status='applied'),
            plan('change_requests', weeks=[1], change_period_mention='1주차'),
        ]):
            all_weeks = self.f.request('POST', path, {'message': '일정 적용된 요청 있어?'}).json()
            self.assertIn('일정 적용 완료', all_weeks['answer'])
            self.assertEqual(str(request_id), all_weeks['source_records'][0]['record_id'])
            first_week = self.f.request('POST', path, {'message': '1주차에 접수된 변경 요청 있어?'}).json()
            self.assertIn('에 접수된 변경 요청', first_week['answer'])
            self.assertIn('조건에 맞는 기록을 찾지 못했어요.', first_week['answer'])
            self.assertEqual([], first_week['source_records'])

    def test_unverified_model_numbers_dates_and_statuses_are_not_returned(self):
        self.f.submit(progress=35)
        invented = {'content': json.dumps({
            'answer': '2029-12-31에 99%로 완료 확인됐습니다.',
            'suggestions': ['99%가 맞는지 더 알려줘', '2029-12-31에 승인됐나요?'],
        }, ensure_ascii=False)}
        path = self.f.base + '/copilot/ask'
        with patch.dict(os.environ, self.env), patch('project_copilot.call_qwen', side_effect=[
            plan('progress', task='4', suggestions=['99%가 맞는지 더 알려줘', '2029-12-31에 승인됐나요?']),
            plan('task_status', task='4'), invented, invented,
        ]):
            progress = self.f.request('POST', path, {
                'message': 'In-Wall Inspection 진행률이 얼마야?', 'week_start': str(self.f.start),
            }).json()
            self.assertIn('35%', progress['answer'])
            self.assertNotIn('99%', str(progress))
            self.assertNotIn('2029-12-31', str(progress))
            status = self.f.request('POST', path, {
                'message': 'In-Wall Inspection 일정과 작업 상태는?', 'week_start': str(self.f.start),
            }).json()['answer']
            self.assertIn('2025-02-21', status)
            self.assertIn('증빙 검토 대기', status)
            self.assertNotIn('완료 확인', status)
            unspecified = self.f.request('POST', path, {'message': '요약해줘', 'week_start': str(self.f.start)}).json()
            self.assertIn('조회 조건을 확인하지 못했습니다', unspecified['answer'])

    def test_server_comparison_calculates_pp_and_distinguishes_missing_and_zero(self):
        first = self.f.start
        second = first + timedelta(days=7)
        self.f.observe(value=0.0)
        self.f.submit(progress=40)
        self.f.now += timedelta(days=7)
        daily_id = uuid4()
        self.f.add('daily_records', id=daily_id, site_id=self.f.site, schedule_id=self.f.schedule,
                   location_id=self.f.location, observation_date=second, captured_at=self.f.now - timedelta(days=6),
                   photo_file_id=self.f.photo_ids[0], status='pending', created_at=self.f.now, updated_at=self.f.now)
        self.f.observe(value=0.80, daily_record_id=str(daily_id))
        self.f.observe(value=None, status='unobserved', key='floor', daily_record_id=str(daily_id))
        self.f.submit(progress=65, captured_at='2025-02-28T10:00:00-08:00')
        keys = ('ai', 'manager')
        questions = ('1번 작업 비전 설치율을 2주차와 1주차 비교해줘', 'Compare the site manager progress for task ID 4 in Weeks 1 and 2.')
        path = self.f.base + '/copilot/ask'
        with patch.dict(os.environ, self.env), patch('project_copilot.call_qwen', side_effect=[
            plan('compare_progress', task=1, weeks=['1', '3'], progress_source='ai', progress_source_mention='비전 설치율'),
            plan('compare_progress', task=4, task_mention='task ID 4', weeks=[1, 3],
                                  progress_source='manager', progress_source_mention='site manager progress'),
            plan('compare_progress', task=1, weeks=[2, 3], progress_source='ai', progress_source_mention='AI 설치율'),
            plan('progress', task=2, weeks=[2], progress_source='ai', progress_source_mention='AI 설치율'),
        ]) as qwen:
            for source, question, expected in zip(keys, questions, (80, 25)):
                response = self.f.request('POST', path, {'message': question, 'week_start': str(second)})
                self.assertEqual(200, response.status_code, response.text)
                result = response.json()
                self.assertEqual(expected, result['comparisons'][0]['delta_pp'])
                self.assertEqual(source, result['comparisons'][0]['source'])
                change = f'{expected}%p 늘었어요.' if source == 'ai' else f'a rise of {expected} percentage points.'
                self.assertIn(change, result['answer'])
                self.assertIn('0% (2025-02-17)' if source == 'ai' else '40%', result['answer'])
            missing = self.f.request('POST', path, {
                'message': '1번 작업 AI 설치율 2주차와 3주차 차이는?', 'week_start': str(second),
            }).json()
            self.assertIn('3주차 (2025-03-03 시작)에는 수치가 없어 차이를 계산할 수 없어요.', missing['answer'])
            self.assertEqual([], missing['comparisons'])
            held = self.f.request('POST', path, {
                'message': '2번 작업 2주차 AI 설치율은?', 'week_start': str(second),
            }).json()
            self.assertIn('비전 설치율 수치를 확인할 수 없어요.', held['answer'])
            self.assertNotIn('0%', held['answer'])
            self.assertEqual(4, qwen.call_count)

    def test_registered_schedule_replaces_archive_only_chat(self):
        index = Path(self.f.root) / 'archive-fixture.json'
        index.write_text(json.dumps({self.f.code: {'photo_count': 9918, 'task_progress_available': False}}), encoding='utf-8')
        path='/api/v1/sites/'+self.f.code
        with patch.dict('os.environ', {'ALO_COPILOT_ARCHIVE_INDEX': str(index)}), patch('project_copilot.qwen_config',return_value={'provider':'gpu'}),patch('project_copilot.call_qwen',return_value={'content':'진행률은 80%입니다.'}) as qwen:
            r=self.f.request('GET',path+'/source-material');self.assertEqual(200,r.status_code,r.text);self.assertEqual(9918,r.json()['photo_count'])
            self.assertTrue(r.json()['schedule_registered']);self.assertFalse(r.json()['source_only'])
            r=self.f.request('POST',path+'/copilot/ask',{'message':'진행률은?'});self.assertEqual(409,r.status_code,r.text)
            r=self.f.request('POST','/api/v1/sites/no-such-site/copilot/ask',{'message':'사진 수'});self.assertIn(r.status_code,(403,404));self.assertEqual(0,qwen.call_count)
