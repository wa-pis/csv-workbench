"""Measure CSV operations in isolated processes (macOS/Linux)."""

import json
import os
import resource
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter, sleep

from csv_workbench.core import Selection, count_rows, export_csv, prepare_result, read_page


def run(operation, source, output):
    selection = Selection("label", "match", "id", True, ("id", "payload"))

    def cached(distinct=None):
        result = prepare_result(
            source, Selection() if distinct == "label" else selection, distinct=distinct
        )
        result.page(0)
        result.page(200)

    actions = {
        "open": lambda: read_page(source, 0),
        "count": lambda: count_rows(source),
        "select": lambda: read_page(source, 0, selection),
        "cached-sort": cached,
        "distinct": lambda: cached("id"),
        "distinct-low": lambda: cached("label"),
        "export": lambda: export_csv(source, output, selection),
    }
    start = perf_counter()
    actions[operation]()
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    print(json.dumps([perf_counter() - start, rss * (1 if sys.platform == "darwin" else 1024)]))


def measure(source, operation):
    with TemporaryDirectory() as scratch:
        root = Path(scratch)
        output = root / "output.csv"
        process = subprocess.Popen(
            [sys.executable, __file__, operation, str(source), str(output)],
            env={**os.environ, "TMPDIR": scratch},
            stdout=subprocess.PIPE,
            text=True,
        )
        peak = 0
        while process.poll() is None:
            used = 0
            for path in root.rglob("*"):
                if path == output:
                    continue
                try:
                    if path.is_file():
                        used += path.stat().st_size
                except FileNotFoundError:
                    continue
            peak = max(peak, used)
            sleep(0.02)
        stdout, _ = process.communicate()
        if process.returncode:
            raise SystemExit(f"{operation} failed: {process.returncode}")
        elapsed, rss = json.loads(stdout)
        print(
            f"{source.name} {operation}: {elapsed:.3f} s; RSS {rss / 2**20:.1f} MiB; "
            f"temporary disk {peak / 2**20:.1f} MiB",
            flush=True,
        )


if __name__ == "__main__":
    if len(sys.argv) == 4:
        run(sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3]))
    else:
        for filename in sys.argv[1:]:
            source = Path(filename).resolve(strict=True)
            print(f"{source.name}: {source.stat().st_size:,} bytes", flush=True)
            for operation in ("open", "cached-sort", "distinct", "distinct-low"):
                measure(source, operation)
