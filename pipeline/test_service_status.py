import json
from pathlib import Path
from subprocess import CompletedProcess
import tempfile
import unittest
from unittest.mock import patch

from service_status import journal_completion, persistent_service_state


UNIT = 'test-search.service'


def record(time, invocation='new', **fields):
    return {'USER_UNIT': UNIT, '_COMM': 'systemd', '_BOOT_ID': 'boot',
            'USER_INVOCATION_ID': invocation, '__MONOTONIC_TIMESTAMP': str(time),
            '__REALTIME_TIMESTAMP': str(10000 + time), **fields}


class CompletionTests(unittest.TestCase):
    def setUp(self):
        self.start = record(100, JOB_TYPE='start', JOB_RESULT='done')
        self.end = record(200, MESSAGE=f'{UNIT}: Succeeded.')

    def test_success_and_wall_time(self):
        result = journal_completion(UNIT, [self.start, self.end])
        self.assertTrue(result['completion_verified'])
        self.assertEqual(result['completion_source'], 'journal')
        self.assertEqual(int(result['InactiveEnterTimestampMonotonic']) -
                         int(result['ActiveEnterTimestampMonotonic']), 100)

    def test_prior_failed_invocation_does_not_hide_new_success(self):
        old = [record(10, 'old', JOB_TYPE='start', JOB_RESULT='done'),
               record(20, 'old', UNIT_RESULT='exit-code')]
        self.assertTrue(journal_completion(UNIT, old + [self.start, self.end])['completion_verified'])

    def test_new_start_does_not_reuse_previous_success(self):
        with self.assertRaises(RuntimeError):
            journal_completion(UNIT, [self.start, self.end,
                               record(300, 'next', JOB_TYPE='start', JOB_RESULT='done')])

    def test_missing_or_mismatched_evidence_rejected(self):
        cases = [[], [self.start], [self.end],
                 [self.start, {**self.end, 'USER_INVOCATION_ID': 'other'}],
                 [self.start, {**self.end, '_BOOT_ID': 'other'}]]
        for rows in cases:
            with self.subTest(rows=rows), self.assertRaises(RuntimeError):
                journal_completion(UNIT, rows)

    def test_failure_signal_or_manual_stop_rejected(self):
        for fields in ({'UNIT_RESULT': 'exit-code'}, {'EXIT_STATUS': '1'},
                       {'EXIT_CODE': 'killed'}, {'JOB_TYPE': 'stop'},
                       {'MESSAGE': 'Stopping search...'}):
            with self.subTest(fields=fields), self.assertRaises(RuntimeError):
                journal_completion(UNIT, [self.start, record(150, **fields), self.end])

    def test_unloaded_success_writes_durable_receipt(self):
        output = ('LoadState=not-found\nActiveState=inactive\nSubState=dead\n'
                  'Result=success\nExecMainCode=0\nExecMainStatus=0\n'
                  'ActiveEnterTimestampMonotonic=0\nInactiveEnterTimestampMonotonic=0\n')
        replies = [CompletedProcess([], 0, output, ''), CompletedProcess(
            [], 0, '\n'.join(json.dumps(row) for row in (self.start, self.end)), '')]
        with tempfile.TemporaryDirectory() as directory, \
                patch('service_status.subprocess.run', side_effect=replies):
            state = persistent_service_state(UNIT, directory)
            self.assertTrue(state['completion_verified'])
            self.assertEqual(state['LoadState'], 'not-found')
            self.assertTrue((Path(directory) / f'{UNIT}.json').is_file())

    def test_active_service_does_not_read_old_journal(self):
        reply = CompletedProcess([], 0, 'LoadState=loaded\nActiveState=active\n', '')
        with patch('service_status.subprocess.run', return_value=reply) as run:
            result = persistent_service_state(UNIT, '/unused')
            self.assertFalse(result['completion_verified'])
            self.assertEqual(run.call_count, 1)


if __name__ == '__main__':
    unittest.main()
