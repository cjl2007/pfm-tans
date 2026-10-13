"""Verify completed transient services even after systemd unloads their state."""

import json
from pathlib import Path
import subprocess


def journal_completion(unit, entries):
    records = [row for row in entries if row.get('USER_UNIT') == unit
               and row.get('_COMM') == 'systemd']
    starts = [row for row in records if row.get('JOB_TYPE') == 'start'
              and row.get('JOB_RESULT') == 'done']
    if not starts:
        raise RuntimeError(f'No journal start record for {unit}')
    start = max(starts, key=lambda row: int(row['__MONOTONIC_TIMESTAMP']))
    invocation = start.get('USER_INVOCATION_ID')
    if not invocation or not start.get('_BOOT_ID'):
        raise RuntimeError(f'Missing journal invocation identity for {unit}')
    later = [row for row in records if int(row['__MONOTONIC_TIMESTAMP']) >=
             int(start['__MONOTONIC_TIMESTAMP'])]
    for row in later:
        if (row.get('UNIT_RESULT', 'success') != 'success' or
                row.get('EXIT_CODE', 'exited') != 'exited' or
                row.get('EXIT_STATUS', '0') != '0' or
                row.get('JOB_TYPE') == 'stop' or
                row.get('MESSAGE', '').startswith(('Stopping ', 'Stopped '))):
            raise RuntimeError(f'Journal records failure/interruption for {unit}')
    success = [row for row in later
               if row.get('MESSAGE') == f'{unit}: Succeeded.'
               and row.get('USER_INVOCATION_ID') == invocation
               and row.get('_BOOT_ID') == start['_BOOT_ID']]
    if not success:
        raise RuntimeError(f'No matching successful journal completion for {unit}')
    end = max(success, key=lambda row: int(row['__MONOTONIC_TIMESTAMP']))
    if int(end['__MONOTONIC_TIMESTAMP']) <= int(start['__MONOTONIC_TIMESTAMP']):
        raise RuntimeError(f'Invalid journal timing for {unit}')
    return {'unit': unit, 'completion_verified': True, 'completion_source': 'journal',
            'invocation_id': invocation, 'boot_id': start['_BOOT_ID'],
            'ActiveEnterTimestampMonotonic': start['__MONOTONIC_TIMESTAMP'],
            'InactiveEnterTimestampMonotonic': end['__MONOTONIC_TIMESTAMP'],
            'started_realtime_us': start['__REALTIME_TIMESTAMP'],
            'finished_realtime_us': end['__REALTIME_TIMESTAMP'],
            'journal_evidence': [start, end]}


def persistent_service_state(unit, receipt_dir):
    result = subprocess.run([
        'systemctl', '--user', 'show', unit, '-p', 'ActiveState', '-p', 'SubState',
        '-p', 'Result', '-p', 'ExecMainCode', '-p', 'ExecMainStatus', '-p', 'LoadState',
        '-p', 'ActiveEnterTimestampMonotonic', '-p', 'InactiveEnterTimestampMonotonic'],
        text=True, capture_output=True, check=True)
    state = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
    state['completion_verified'] = False
    if state.get('ActiveState') != 'inactive':
        return state
    if (state.get('Result') == 'success' and state.get('ExecMainCode') == '1'
            and state.get('ExecMainStatus') == '0'
            and int(state.get('ActiveEnterTimestampMonotonic', '0')) > 0
            and int(state.get('InactiveEnterTimestampMonotonic', '0')) > 0):
        state.update(completion_verified=True, completion_source='systemctl')
    elif (state.get('LoadState') == 'not-found' or
          state.get('ActiveEnterTimestampMonotonic') == '0'):
        journal = subprocess.run([
            'journalctl', '--user', '-u', unit, '-b', '0', '--no-pager', '-o', 'json'],
            text=True, capture_output=True, check=True)
        entries = [json.loads(line) for line in journal.stdout.splitlines() if line.strip()]
        state.update(journal_completion(unit, entries))
    if state['completion_verified']:
        path = Path(receipt_dir) / f'{unit}.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(state, indent=2) + '\n')
        temporary.replace(path)
    return state
