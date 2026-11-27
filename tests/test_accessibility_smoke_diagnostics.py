"""Native probe diagnostics can be checked without opening a Cocoa window."""

import io
import json
import sys

import pytest

from csv_workbench import accessibility_smoke, main


class FlushedOutput(io.StringIO):
    flushed = ''

    def flush(self):
        self.flushed = self.getvalue()


def test_phase_flushes_one_complete_json_record(monkeypatch):
    output = FlushedOutput()
    monkeypatch.setattr(sys, 'stdout', output)
    accessibility_smoke.phase('selection-end', model_flags_calls=12, label='ёж')

    assert output.flushed == output.getvalue()
    lines = output.flushed.splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record['accessibility_phase'] == 'selection-end'
    assert record['model_flags_calls'] == 12
    assert record['label'] == 'ёж'
    assert record['elapsed_seconds'] >= 0


@pytest.mark.parametrize('failure', [False, True])
def test_entrypoint_flushes_before_application_creation_and_preserves_probe_outcome(
        monkeypatch, failure):
    output = FlushedOutput()
    monkeypatch.setattr(sys, 'stdout', output)
    monkeypatch.setattr(sys, 'argv', ['csv-workbench', '--accessibility-smoke-test'])
    calls = []

    def application(arguments):
        records = [json.loads(line) for line in output.flushed.splitlines()]
        assert [record['accessibility_phase'] for record in records] == [
            'application-create-start']
        assert arguments == sys.argv
        calls.append('application')
        return object()

    def probe():
        records = [json.loads(line) for line in output.flushed.splitlines()]
        assert [record['accessibility_phase'] for record in records] == [
            'application-create-start', 'application-create-end']
        calls.append('probe')
        if failure:
            raise RuntimeError('native probe failed')
        return 7

    monkeypatch.setattr(main, 'QApplication', application)
    monkeypatch.setattr(accessibility_smoke, 'check_tables', probe)
    if failure:
        with pytest.raises(RuntimeError, match='native probe failed'):
            main.main()
    else:
        assert main.main() == 7
    assert calls == ['application', 'probe']
