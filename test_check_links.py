"""Contract and failure controls, using invented identifiers and no customer data."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from check_links import inspect, validate_map

OLD = '11111111-1111-4111-8111-111111111111'
NEW = '22222222-2222-4222-8222-222222222222'
OTHER = '33333333-3333-4333-8333-333333333333'
MAP = [{'old_id': OLD, 'new_id': NEW, 'resource_type': 'database'}]


def workflow(value=OLD, **node_options):
    return {'nodes': [{'type': 'n8n-nodes-base.notion', 'typeVersion': 2.2,
                      'parameters': {'resource': 'database', 'operation': 'get',
                          'databaseId': {'__rl': True, 'mode': 'id', 'value': value}},
                      'credentials': {'notionApi': {'id': OLD}}, **node_options}], 'active': True}


class Checks(unittest.TestCase):
    def test_literal_and_no_mutation(self):
        data = workflow(); before = copy.deepcopy(data)
        report = inspect(data, MAP)
        self.assertEqual(data, before)
        self.assertEqual(len(report['findings']), 1)  # Credentials excluded.
        hit = report['findings'][0]
        self.assertEqual(hit['pointer'], '/nodes/0/parameters/databaseId/value')
        self.assertEqual(hit['status'], 'literal_database_rebind_candidate')
        self.assertEqual(hit['proposed_locator']['value'], NEW)
        self.assertFalse(report['runtime_recovery_verified'])

    def test_id_spelling(self):
        for value in (OLD.upper(), OLD.replace('-', ''), 'https://www.notion.so/CRM-' + OLD.replace('-', '')):
            with self.subTest(value=value):
                self.assertEqual(inspect(workflow(value), MAP)['findings'][0]['status'],
                                 'literal_database_rebind_candidate')

    def test_omitted_database_default_operation(self):
        data = workflow()
        del data['nodes'][0]['parameters']['operation']
        self.assertEqual(inspect(data, MAP)['findings'][0]['status'],
                         'literal_database_rebind_candidate')
        data['nodes'][0]['parameters']['resource'] = 'databasePage'
        self.assertEqual(inspect(data, MAP)['findings'][0]['status'], 'reference_requires_review')

    def test_cache_does_not_mean_live_failure(self):
        data = workflow(NEW)
        data['nodes'][0]['parameters']['databaseId']['cachedResultUrl'] = 'https://www.notion.so/' + OLD
        report = inspect(data, MAP)
        self.assertEqual([f['status'] for f in report['findings']], ['cached_metadata_only'])

    def test_expression_never_rebound(self):
        data = workflow('={{ "' + OLD + '" }}')
        hit = inspect(data, MAP)['findings'][0]
        self.assertEqual(hit['status'], 'dynamic_expression_requires_review')
        self.assertNotIn('proposed_locator', hit)
        report = inspect(workflow('={{ $json.databaseId }}'), MAP)
        self.assertTrue(report['limitations'])
        self.assertEqual(report['findings'], [])

    def test_other_nodes_and_disabled(self):
        for override, expected in (({'type': 'n8n-nodes-base.httpRequest'}, 'reference_requires_review'),
                                   ({'typeVersion': 3}, 'reference_requires_review'),
                                   ({'disabled': True}, 'disabled_node_reference')):
            with self.subTest(override=override):
                self.assertEqual(inspect(workflow(**override), MAP)['findings'][0]['status'], expected)

    def test_inactive_parameter_is_not_a_rebind(self):
        data = workflow()
        data['nodes'][0]['parameters']['resource'] = 'user'
        self.assertEqual(inspect(data, MAP)['findings'][0]['status'], 'reference_requires_review')

    def test_map_identity_and_conflicts(self):
        duplicate = MAP + [dict(MAP[0])]
        self.assertEqual(validate_map(duplicate), validate_map(MAP))
        for bad in (MAP + [{'old_id': OLD, 'new_id': OTHER, 'resource_type': 'database'}],
                    MAP + [{'old_id': OTHER, 'new_id': NEW, 'resource_type': 'database'}],
                    [{'old_id': 'CRM', 'new_id': NEW, 'resource_type': 'database'}]):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                validate_map(bad)
        unchanged = [{'old_id': OLD, 'new_id': OLD, 'resource_type': 'database'}]
        self.assertEqual(inspect(workflow(), unchanged)['findings'], [])

    def test_type_mismatch(self):
        wrong = [{'old_id': OLD, 'new_id': NEW, 'resource_type': 'page'}]
        self.assertEqual(inspect(workflow(), wrong)['findings'][0]['status'], 'resource_type_mismatch')

    def test_unknown_targets_and_tokens(self):
        self.assertTrue(inspect(workflow(OTHER), MAP)['limitations'])
        for value in ('prefix' + OLD, OLD + 'suffix', 'a' + OLD.replace('-', '') + 'b'):
            self.assertEqual(inspect(workflow(value), MAP)['findings'], [])

    def test_url_ambiguity(self):
        for value in ('https://example.com/' + OLD,
                      'https://www.notion.so/' + OLD + '?v=' + OTHER,
                      'https://www.notion.so/' + OLD + '#section',
                      'https://www.notion.so:bad/' + OLD):
            with self.subTest(value=value):
                self.assertNotIn('proposed_locator', inspect(workflow(value), MAP)['findings'][0])

    def test_pointer_escaping_and_no_text_disclosure(self):
        data = workflow(NEW)
        data['nodes'][0]['parameters']['a/b~c'] = 'private prose containing ' + OLD
        report = inspect(data, MAP)
        self.assertEqual(report['findings'][0]['pointer'], '/nodes/0/parameters/a~1b~0c')
        self.assertNotIn('private prose', json.dumps(report))

    def test_isolated_target_control(self):
        # A minimal model of an ID-addressed target, not a live Notion API test.
        restored_database = {NEW: {'result': 'expected business row'}}
        data = workflow()
        self.assertNotIn(data['nodes'][0]['parameters']['databaseId']['value'], restored_database)
        hit = inspect(data, MAP)['findings'][0]
        reviewed = copy.deepcopy(data)
        reviewed['nodes'][0]['parameters']['databaseId'] = hit['proposed_locator']
        self.assertEqual(restored_database[reviewed['nodes'][0]['parameters']['databaseId']['value']],
                         {'result': 'expected business row'})
        self.assertEqual(inspect(reviewed, MAP)['findings'], [])

    def test_cli_preserves_inputs_and_prior_report(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); wf = root / 'workflow.json'; mapping = root / 'map.json'; out = root / 'report.json'
            wf.write_text(json.dumps(workflow())); mapping.write_text(json.dumps(MAP))
            before = [wf.read_bytes(), mapping.read_bytes()]
            cmd = [sys.executable, str(Path(__file__).with_name('check_links.py')), str(wf), str(mapping), str(out)]
            subprocess.run(cmd, capture_output=True, check=True)
            original = out.read_bytes()
            retry = subprocess.run(cmd, capture_output=True)
            self.assertNotEqual(retry.returncode, 0)
            self.assertEqual(out.read_bytes(), original)
            self.assertEqual([wf.read_bytes(), mapping.read_bytes()], before)
            self.assertEqual(list(root.glob('.restore-link-check-*')), [])


if __name__ == '__main__':
    unittest.main()
