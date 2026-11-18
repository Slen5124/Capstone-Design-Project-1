from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src/llm_agent_module/vision_delivery'))
from vision_contract import validate_delivery, VisionContractError

TASK = '11111111-1111-4111-8111-111111111111'
TASK2 = '11111111-1111-4111-8111-111111111112'
TASK3 = '11111111-1111-4111-8111-111111111113'
PHOTO = '22222222-2222-4222-8222-222222222222'
PHOTO2 = '22222222-2222-4222-8222-222222222223'
OVERLAY = '33333333-3333-4333-8333-333333333333'
BATCH = '44444444-4444-4444-8444-444444444444'


class VisionContractTests(unittest.TestCase):
    def setUp(self):
        self.check = {'site_code': 'sample-site', 'schedule_version': 'v001', 'week': 7,
                      'period_start': '2031-02-17', 'period_end': '2031-02-21',
                      'tasks': [{'task_id': TASK, 'source_task_id': '45', 'task_name': 'Hang Drywall'},
                                {'task_id': TASK2, 'source_task_id': '46', 'task_name': 'Paint Walls'}]}
        self.check['schedule_tasks'] = self.check['tasks'] + [{'task_id': TASK3, 'source_task_id': '47', 'task_name': 'Later task'}]
        self.payload = {
            'schema_version': 'vision_output/1.0', 'site_id': 'SAMPLE-SITE',
            'model': {'source': 'model', 'model_version': 'seg-v1'},
            'delivery': {'site_code': 'sample-site', 'schedule_version': 'v001', 'week': 7,
                         'revision': 1, 'batch_id': BATCH, 'status': 'completed',
                         'period_start': '2031-02-17', 'period_end': '2031-02-21'},
            'targets': [{'target_id': 'W01', 'surface_type': 'wall', 'name': 'Left wall',
                         'schedule_link': {'wall_board_installation': {'source_task_id': '45'}}}],
            'views': [{'view_id': 'V01', 'targets': [{'target_id': 'W01'}]}],
            'daily_progress': [{'date': '2031-02-21', 'site_id': 'SAMPLE-SITE', 'view_ids': ['V01'],
                                'target_id': 'W01', 'activity_id': 'wall_board_installation',
                                'source': 'model', 'revision': 1, 'provisional': False, 'status': 'ok',
                                'installed_ratio': .9717, 'observed_fraction': .42,
                                'photo_file_id': PHOTO, 'overlay_file_id': OVERLAY,
                                'box': [.1, .2, .8, .9]}]}

    def result(self, payload=None, check=None):
        return validate_delivery(payload or self.payload, check or self.check)

    def test_fraction_and_explicit_identity_preserve_meaning_without_mutation(self):
        before = deepcopy(self.payload)
        output = self.result()
        analysis = output['results'][0]
        self.assertEqual(analysis['task_id'], TASK)
        self.assertEqual(analysis['region'], 'wall')
        observation = analysis['observations'][0]
        self.assertEqual(observation['values'], {'W01': 97.17})
        self.assertEqual(observation['labels'], {'W01': 'Left wall'})
        self.assertEqual(observation['boxes'], {'W01': [.1, .2, .8, .9]})
        self.assertNotIn('observed_fraction', observation)
        self.assertEqual(output['skipped'], [])
        self.assertEqual(before, self.payload)

    def test_synthetic_label_data_cannot_be_promoted(self):
        raw = deepcopy(self.payload)
        raw['example_note'] = 'Synthetic label fixture'
        with self.assertRaisesRegex(VisionContractError, 'example|label'):
            validate_delivery(raw, self.check)
        raw.pop('example_note')
        raw['delivery'] = self.payload['delivery']
        raw['model'] = {'source': 'model', 'model_version': 'seg-v1'}
        raw['daily_progress'][0]['source'] = 'label'
        for target in raw['targets']:
            target['schedule_link'] = {'wall_board_installation': {'source_task_id': '45'}}
        with self.assertRaisesRegex(VisionContractError, 'source.*model'):
            validate_delivery(raw, self.check)

    def test_model_source_version_and_example_flags_are_required(self):
        for where, key, value in [('model', 'source', 'label'), ('model', 'source', 'test'),
                                  ('model', 'model_version', None), ('model', 'model_version', ''),
                                  ('root', 'source', 'manager'), ('root', 'is_test', True),
                                  ('root', 'is_dummy', True), ('root', 'example_note', '')]:
            payload = deepcopy(self.payload)
            (payload if where == 'root' else payload[where])[key] = value
            with self.subTest(where=where, key=key, value=value), self.assertRaises(VisionContractError):
                self.result(payload)

    def test_delivery_matches_exact_week_schedule_and_explicit_completion(self):
        cases = [('site_code', 'lasvegas'), ('site_code', 'SAMPLE-SITE'), ('schedule_version', 'v002'),
                 ('week', 8), ('week', True), ('revision', 0), ('revision', True),
                 ('revision', 1.5), ('batch_id', 'filename.json'), ('status', 'processing'),
                 ('period_start', '2031-02-18'), ('period_end', '2031-02-22')]
        for key, value in cases:
            payload = deepcopy(self.payload)
            payload['delivery'][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(VisionContractError):
                self.result(payload)
        for key in self.payload['delivery']:
            payload = deepcopy(self.payload)
            payload['delivery'].pop(key)
            with self.subTest(missing=key), self.assertRaises(VisionContractError):
                self.result(payload)

    def test_explicit_empty_completion_is_valid(self):
        self.payload.update(targets=[], views=[], daily_progress=[])
        output = self.result()
        self.assertEqual(output['results'], [])
        self.assertEqual(output['delivery']['status'], 'completed')
        self.payload.pop('daily_progress')
        with self.assertRaisesRegex(VisionContractError, 'daily_progress'):
            self.result()

    def test_held_uncertain_provisional_and_null_never_become_zero(self):
        for status, provisional, value, reason in [('held', False, None, 'held'),
                                                  ('low_confidence', False, .8, 'low_confidence'),
                                                  ('ok', True, .9, 'provisional'),
                                                  ('ok', False, None, 'missing_installed_ratio')]:
            payload = deepcopy(self.payload)
            row = payload['daily_progress'][0]
            row.update(status=status, provisional=provisional, installed_ratio=value)
            row.pop('photo_file_id')
            with self.subTest(status=status, provisional=provisional, value=value):
                result = self.result(payload)
                self.assertEqual(result['results'], [])
                self.assertEqual(result['skipped'][0]['reason'], reason)
        self.payload['daily_progress'][0]['installed_ratio'] = 0
        self.assertEqual(self.result()['results'][0]['observations'][0]['values']['W01'], 0.0)

    def test_fraction_is_not_percent_and_visibility_is_not_installation(self):
        for value in [97.17, -.1, 1.1, True, '0.5', float('nan'), float('inf'), 10**1000]:
            payload = deepcopy(self.payload)
            payload['daily_progress'][0]['installed_ratio'] = value
            with self.subTest(value=str(value)[:20]), self.assertRaisesRegex(VisionContractError, 'fraction'):
                self.result(payload)
        row = self.payload['daily_progress'][0]
        row['installed_ratio'] = None
        row['observed_fraction'] = 1
        self.assertEqual(self.result()['results'], [])
        row.pop('installed_ratio')
        with self.assertRaisesRegex(VisionContractError, 'installed_ratio'):
            self.result()

    def test_cumulative_history_filters_other_weeks_without_guessing_missing_media(self):
        old = deepcopy(self.payload['daily_progress'][0])
        old.update(date='2031-02-16', source='label')
        old.pop('photo_file_id')
        later = deepcopy(old)
        later['date'] = '2031-02-22'
        self.payload['daily_progress'].extend([old, later])
        result = self.result()
        self.assertEqual(len(result['results'][0]['observations']), 1)
        self.assertEqual([r['reason'] for r in result['skipped']], ['outside_reporting_period'] * 2)
        old['site_id'] = 'OTHER'
        with self.assertRaisesRegex(VisionContractError, 'another site'):
            self.result()

    def test_other_site_and_malformed_dates_fail_even_in_history(self):
        for site, day in [('OTHER', '2031-01-01'), ('SAMPLE-SITE', '2031-02-30'), ('SAMPLE-SITE', '20310221'), ('SAMPLE-SITE', '2031-02-21T00:00:00')]:
            payload = deepcopy(self.payload)
            payload['daily_progress'][0].update(site_id=site, date=day)
            with self.subTest(site=site, day=day), self.assertRaises(VisionContractError):
                self.result(payload)

    def test_missing_unknown_and_conflicting_mapping_fail(self):
        for mapping in [None, {}, {'wall_board_installation': {'source_task_id': '999'}},
                        {'wall_board_installation': {'task_name': 'Hang Drywall'}},
                        {'wall_board_installation': {'task_id': TASK, 'source_task_id': '46'}}]:
            payload = deepcopy(self.payload)
            payload['targets'][0]['schedule_link'] = mapping
            with self.subTest(mapping=mapping), self.assertRaises(VisionContractError):
                self.result(payload)

    def test_only_known_schedule_tasks_can_be_excluded_as_outside_week(self):
        self.payload['targets'][0]['schedule_link']['wall_board_installation'] = {'source_task_id': '47'}
        result = self.result()
        self.assertEqual(result['results'], [])
        self.assertEqual(result['skipped'][0]['reason'], 'task_outside_reporting_week')
        check = deepcopy(self.check)
        check.pop('schedule_tasks')
        with self.assertRaisesRegex(VisionContractError, 'unknown source_task_id'):
            self.result(check=check)

    def test_highest_daily_revision_wins_and_identical_duplicate_is_idempotent(self):
        row = self.payload['daily_progress'][0]
        corrected = deepcopy(row)
        corrected.update(revision=3, installed_ratio=.5)
        self.payload['daily_progress'] = [corrected, row, deepcopy(corrected)]
        result = self.result()
        self.assertEqual(result['results'][0]['observations'][0]['values'], {'W01': 50.0})
        self.assertEqual([item['reason'] for item in result['skipped']], ['superseded_revision', 'duplicate'])
        corrected['status'] = 'held'
        self.payload['daily_progress'] = [row, corrected]
        self.assertEqual(self.result()['results'], [])

    def test_conflicting_same_revision_never_picks_an_arbitrary_value(self):
        different = deepcopy(self.payload['daily_progress'][0])
        different['installed_ratio'] = .3
        self.payload['daily_progress'].append(different)
        with self.assertRaisesRegex(VisionContractError, 'conflicting rows'):
            self.result()

    def test_multiple_targets_merge_with_explicit_scene_and_composite_overlay(self):
        target = deepcopy(self.payload['targets'][0])
        target.update(target_id='W02', name='Right wall')
        self.payload['targets'].append(target)
        row = deepcopy(self.payload['daily_progress'][0])
        row.update(target_id='W02', installed_ratio=1)
        self.payload['daily_progress'].append(row)
        observation = self.result()['results'][0]['observations'][0]
        self.assertEqual(observation['values'], {'W01': 97.17, 'W02': 100.0})
        self.assertEqual(observation['labels']['W02'], 'Right wall')
        for key, value in [('photo_file_id', PHOTO2), ('overlay_file_id', None)]:
            payload = deepcopy(self.payload)
            payload['daily_progress'][1][key] = value
            with self.subTest(key=key), self.assertRaisesRegex(VisionContractError, 'same scene'):
                self.result(payload)

    def test_one_target_maps_different_activities_to_distinct_tasks(self):
        self.payload['targets'][0]['schedule_link']['wall_painting'] = {'task_id': TASK2}
        row = deepcopy(self.payload['daily_progress'][0])
        row.update(activity_id='wall_painting', installed_ratio=.2)
        self.payload['daily_progress'].append(row)
        output = self.result()['results']
        self.assertEqual([r['task_id'] for r in output], [TASK, TASK2])
        self.assertEqual(output[1]['observations'][0]['values']['W01'], 20)
        self.payload['targets'][0]['schedule_link']['wall_painting'] = {'task_id': TASK}
        with self.assertRaisesRegex(VisionContractError, 'multiple activities'):
            self.result()

    def test_wall_ceiling_and_floor_share_the_same_contract(self):
        for region in ['wall', 'ceiling', 'floor']:
            self.payload['targets'][0]['surface_type'] = region
            with self.subTest(region=region):
                self.assertEqual(self.result()['results'][0]['region'], region)

    def test_media_view_and_boxes_are_explicit_and_valid(self):
        for key, value in [('photo_file_id', None), ('photo_file_id', 'photos/scene.jpg'),
                           ('overlay_file_id', ''), ('view_ids', ['UNKNOWN']), ('view_ids', []),
                           ('view_ids', ['V01', 'V01']), ('box', [10, 10, 30, 30]),
                           ('box', [.8, .1, .2, .9]), ('box', [True, 0, 1, 1])]:
            payload = deepcopy(self.payload)
            payload['daily_progress'][0][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(VisionContractError):
                self.result(payload)

    def test_usable_observation_requires_roi_box_for_report_photo_annotation(self):
        for missing in [True, False]:
            payload = deepcopy(self.payload)
            row = payload['daily_progress'][0]
            if missing:
                row.pop('box')
            else:
                row['box'] = None
            with self.subTest(missing=missing), self.assertRaisesRegex(VisionContractError, 'box.*normalized'):
                self.result(payload)

    def test_low_visibility_is_held_even_if_model_status_says_ok(self):
        for visibility in [0, .2999, .3, None]:
            payload = deepcopy(self.payload)
            row = payload['daily_progress'][0]
            row['observed_fraction'] = visibility
            if visibility is not None and visibility < .3:
                row.pop('photo_file_id')
                row.pop('box')
            with self.subTest(visibility=visibility):
                output = self.result(payload)
                if visibility is not None and visibility < .3:
                    self.assertEqual(output['results'], [])
                    self.assertEqual(output['skipped'][0]['reason'], 'insufficient_observation')
                else:
                    self.assertEqual(output['results'][0]['observations'][0]['values'], {'W01': 97.17})
        self.payload['daily_progress'][0].pop('observed_fraction')
        self.assertEqual(len(self.result()['results']), 1)

    def test_usable_row_source_status_and_provisional_are_not_inferred(self):
        for key, value in [('source', None), ('source', 'label'), ('source', 'manager'),
                           ('source', 'test'), ('status', 'unknown'), ('status', {}), ('provisional', None),
                           ('provisional', 0), ('revision', True), ('revision', 0)]:
            payload = deepcopy(self.payload)
            payload['daily_progress'][0][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(VisionContractError):
                self.result(payload)

    def test_malformed_region_returns_contract_error(self):
        self.payload['targets'][0]['surface_type'] = {'region': 'wall'}
        with self.assertRaisesRegex(VisionContractError, 'surface_type'):
            self.result()


if __name__ == '__main__':
    unittest.main()
