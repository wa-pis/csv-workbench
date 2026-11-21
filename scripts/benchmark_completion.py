"""Completion latency, process RSS and sampled scratch usage (macOS/Linux)."""

import argparse
import json
import os
import platform
import resource
import subprocess
import sys
from concurrent.futures import CancelledError, ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from time import perf_counter, sleep

from csv_workbench.completion import complete_values
from csv_workbench.core import Cancellation, Selection
from csv_workbench.edits import DiskEdits


def worker(source, rows, scenario):
    import duckdb
    import polars

    edits = DiskEdits()
    edits[(rows - 1, 1)] = 'id-edited'
    edits[(rows - 1, 2)] = 'group-edited'
    selection = Selection(filters=(('note', 'hit'),), sort_column='id', descending=True)
    column, prefix = ('category', '') if scenario == 'low' else ('id', '')
    if scenario == 'selective':
        prefix = 'id-edited'
    token = Cancellation()
    start = perf_counter()
    cancel_seconds = None

    def search():
        return complete_values(source, selection, edits.copy(), column, prefix, cancel=token)

    if scenario == 'cancel':
        entered = Event()

        def work():
            entered.set()
            return search()

        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(work)
            assert entered.wait(5)
            sleep(0.2)
            cancellation_start = perf_counter()
            token.cancel()
            try:
                future.result(timeout=10)
            except CancelledError:
                pass
            else:
                raise AssertionError('operation finished before cancellation')
            cancel_seconds = perf_counter() - cancellation_start
        values, truncated = [], False
    else:
        values, truncated = search()
        if scenario == 'low':
            assert values == ['group-edited', *[f'group{i}' for i in range(10)]] and not truncated
        elif scenario == 'high':
            assert values == [f'{i:010}' for i in range(100)] and truncated
        else:
            assert values == ['id-edited'] and not truncated
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    print(json.dumps({
        'scenario': scenario, 'seconds': perf_counter() - start,
        'rss_high_water_bytes': rss * (1 if sys.platform == 'darwin' else 1024),
        'cancel_seconds': cancel_seconds, 'suggestions': len(values), 'truncated': truncated,
        'python': platform.python_version(), 'duckdb': duckdb.__version__, 'polars': polars.__version__,
    }), flush=True)


def run(rows):
    with TemporaryDirectory(prefix=f'csv-completion-bench-{rows}-') as scratch:
        root = Path(scratch)
        source = root / 'input.csv'
        with source.open('w') as output:
            output.write('note,id,category,payload\n')
            for start in range(0, rows, 10000):
                output.writelines(
                    f'hit,{i:010},group{i % 10},' + 'x' * 64 + '\n'
                    for i in range(start, min(start + 10000, rows))
                )
        reports = []
        for scenario in ('low', 'high', 'selective', 'cancel'):
            case_root = root / scenario
            case_root.mkdir()
            process = subprocess.Popen(
                [sys.executable, __file__, '--worker', str(source), str(rows), scenario],
                env={**os.environ, 'TMPDIR': str(case_root)},
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
            peak = 0
            while process.poll() is None:
                used = 0
                for file in case_root.rglob('*'):
                    try:
                        if file.is_file():
                            used += file.stat().st_size
                    except FileNotFoundError:
                        pass
                peak = max(peak, used)
                sleep(0.02)
            stdout, stderr = process.communicate()
            if process.returncode:
                raise RuntimeError(f'{scenario} failed: {stderr}\n{stdout}')
            report = json.loads(stdout)
            report.update(rows=rows, source_bytes=source.stat().st_size,
                          temporary_disk_peak_bytes=peak, sample_interval_seconds=0.02)
            reports.append(report)
            print(json.dumps(report), flush=True)
        return reports


if __name__ == '__main__':
    if sys.argv[1] == '--worker':
        worker(Path(sys.argv[2]), int(sys.argv[3]), sys.argv[4])
    else:
        parser = argparse.ArgumentParser(description=__doc__)
        parser.add_argument('rows', type=int, nargs='+')
        parser.add_argument('--output', type=Path, required=True)
        args = parser.parse_args()
        results = []
        for count in args.rows:
            results.extend(run(count))
            args.output.write_text(json.dumps({'platform': platform.platform(), 'measurements': results},
                                             indent=2) + '\n')
