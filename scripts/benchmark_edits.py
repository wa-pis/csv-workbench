"""Measure disk-backed replacement, history and recovery in a fresh process (macOS/Linux)."""

import json
import os
import resource
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter, sleep

from csv_workbench.core import Cancellation, CsvFormat, Selection, file_revision, read_editable_page
from csv_workbench.edits import DiskEdits
from csv_workbench.recovery import RecoveryJournal, checkpoint_data, read_checkpoint
from csv_workbench.replace import preview_replacement


def worker(source, root, rows):
    measurements = []

    def measure(name, action):
        start = perf_counter()
        result = action()
        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        measurements.append({'stage': name, 'seconds': perf_counter() - start,
                                 'rss_high_water_bytes': rss * (1 if sys.platform == 'darwin' else 1024)})
        print(json.dumps(measurements[-1]), flush=True)
        return result

    token = Cancellation()
    edits = DiskEdits()
    selection = Selection(visible_columns=('note',))
    plan = measure('preview', lambda: preview_replacement(source, selection, edits, 'hit', 'new', cancel=token))
    assert plan.cells == rows and plan.occurrences == rows * 2
    batch = measure('apply', lambda: plan.prepare(edits, cancel=token))
    edits = batch.after.copy()
    assert len(edits) == rows and edits[(rows - 1, 1)] == 'new new'
    page = measure('read-last', lambda: read_editable_page(source, rows - 1, selection, edits, cancel=token))
    assert page[1][0][-1] == 'new new'
    edits = measure('undo', batch.before.copy)
    assert not edits
    edits = measure('redo', batch.after.copy)
    assert edits[(0, 1)] == 'new new'
    journal = RecoveryJournal(root / 'recovery')
    data = checkpoint_data(source, selection, CsvFormat(), file_revision(source),
                           ['id', 'note', 'category', 'payload'], edits, [batch], 1, 0, 1 << 62)
    measure('checkpoint', lambda: journal.write(data).result())
    state = measure('recover', lambda: read_checkpoint(journal.directory))
    assert len(state.edits) == rows and state.history[0].before == {}
    assert state.edits[(rows - 1, 1)] == 'new new'
    journal.close(discard=True).result()
    print(json.dumps({'complete': True}), flush=True)


def run(rows):
    with TemporaryDirectory(prefix=f'csv-edit-bench-{rows}-') as scratch:
        root = Path(scratch)
        source = root / 'input.csv'
        with source.open('w') as output:
            output.write('id,note,category,payload\n')
            for start in range(0, rows, 10000):
                output.writelines(f'{i:010},hit HIT,group{i % 10},' + 'x' * 64 + '\n'
                                  for i in range(start, min(start + 10000, rows)))
        process = subprocess.Popen([sys.executable, __file__, '--worker', str(source), str(root), str(rows)],
                                   env={**os.environ, 'TMPDIR': scratch}, stdout=subprocess.PIPE, text=True)
        peak = 0
        while process.poll() is None:
            used = 0
            for path in root.rglob('*'):
                try:
                    if path.is_file() and path != source:
                        used += path.stat().st_size
                except FileNotFoundError:
                    pass
            peak = max(peak, used)
            sleep(0.05)
        stdout, _ = process.communicate()
        print(stdout, end='')
        if process.returncode:
            raise SystemExit(f'benchmark failed: {process.returncode}')
        report = {'rows': rows, 'source_bytes': source.stat().st_size, 'temporary_disk_peak_bytes': peak,
                      'stages': [json.loads(line) for line in stdout.splitlines() if 'stage' in line]}
        print(json.dumps(report), flush=True)


if __name__ == '__main__':
    if sys.argv[1] == '--worker':
        worker(Path(sys.argv[2]), Path(sys.argv[3]), int(sys.argv[4]))
    else:
        for count in map(int, sys.argv[1:]):
            run(count)
