"""Measure the generated 600 x 4000 fixture: SOURCE.csv REPORT.json (macOS/Linux)."""

import csv
import hashlib
import json
import os
import platform
import resource
import subprocess
import sys
from concurrent.futures import CancelledError, ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from time import perf_counter, sleep

import duckdb

from csv_workbench import core


def worker(operation, source, output):
    token = core.Cancellation()
    selection = core.Selection(sort_column="n0", extra_sort=(("n3999", False),))
    start = perf_counter()
    details = {}
    if operation in {"sort", "edited-sort", "unique"}:
        edits = {(0, 0): "z-edited"} if operation == "edited-sort" else {}
        result = core.prepare_editable_result(
            source, selection, edits, cancel=token,
            distinct="n0" if operation == "unique" else None,
        )
        with result.scratch:
            page = result.page(0, cancel=token)
            if operation == "unique":
                assert page == (["Value", "Frequency"], [("1234567890" * 10, 600)], False)
            else:
                assert len(page[0]) == 4001 and len(page[1]) == 200
                assert page[1][0][0] == (1 if edits else 0)
                del page
                page = result.page(400, cancel=token)
                assert page[1][-1][0] == (0 if edits else 599)
                assert len(page[1]) == 200 and not page[2]
    elif operation == "save":
        edits = {(0, 0): "edited", (599, 3999): None, (core.NEW_ROW, 0): "added"}
        destination, revision = core.save_csv(
            source, output, edits, core.file_revision(source), cancel=token,
        )
        assert destination == output.resolve() and revision == core.file_revision(output)
        details["published_output_bytes"] = output.stat().st_size
    elif operation == "cancel-sort":
        started = Event()
        original = core.iter_csv_rows

        def tracked(*args, **kwargs):
            with closing(original(*args, **kwargs)) as rows:
                for row in rows:
                    started.set()
                    yield row

        core.iter_csv_rows = tracked
        try:
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(core.prepare_editable_result, source, selection, {}, cancel=token)
                assert started.wait(30), "CSV processing did not start"
                sleep(0.05)
                cancelled = perf_counter()
                token.cancel()
                try:
                    future.result(timeout=30)
                except CancelledError:
                    details["cancellation_seconds"] = perf_counter() - cancelled
                else:
                    raise AssertionError("Operation completed before cancellation")
        finally:
            core.iter_csv_rows = original
        assert not list(output.parent.glob("csv-wide-*")), "Cancelled payload was retained"
    else:
        raise ValueError(operation)

    elapsed = perf_counter() - start
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    details.update(seconds=elapsed, peak_rss_bytes=rss * (1 if sys.platform == "darwin" else 1024))
    if operation == "save":
        # Verify after capturing operation time and RAM; keep only one CSV row at a time.
        with output.open(encoding="utf-8", newline="") as handle:
            rows = csv.reader(handle)
            assert len(next(rows)) == 4000
            count = 0
            for row in rows:
                if count == 0:
                    assert row[0] == "edited"
                count += 1
            assert count == 601 and row[0] == "added"
            details["verified_saved_rows"] = count
    print(json.dumps(details))


def measure(operation, source):
    with TemporaryDirectory(prefix="csv-wide-benchmark-") as temporary:
        root = Path(temporary)
        output = root / "saved.csv"
        process = subprocess.Popen(
            [sys.executable, __file__, "--worker", operation, str(source), str(output)],
            env={**os.environ, "TMPDIR": temporary}, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True,
        )
        peak = 0
        while process.poll() is None:
            size = 0
            for path in root.rglob("*"):
                try:
                    if path != output and path.is_file():
                        size += path.stat().st_size
                except FileNotFoundError:
                    pass
            peak = max(peak, size)
            sleep(0.01)
        stdout, stderr = process.communicate()
        if process.returncode:
            raise RuntimeError(f"{operation}: {stderr}")
        return {"operation": operation, **json.loads(stdout), "peak_temporary_file_bytes": peak}


if __name__ == "__main__":
    if sys.argv[1] == "--worker":
        worker(sys.argv[2], Path(sys.argv[3]), Path(sys.argv[4]))
    else:
        source = Path(sys.argv[1]).resolve(strict=True)
        with source.open("rb") as handle:
            digest = hashlib.file_digest(handle, "sha256").hexdigest()
        assert source.stat().st_size == 242422890
        results = []
        for operation in ("sort", "edited-sort", "unique", "save", "cancel-sort"):
            result = measure(operation, source)
            results.append(result)
            print(json.dumps(result), flush=True)
        with source.open("rb") as handle:
            assert hashlib.file_digest(handle, "sha256").hexdigest() == digest
        Path(sys.argv[2]).write_text(json.dumps({
            "platform": platform.platform(), "python": platform.python_version(),
            "duckdb": duckdb.__version__, "duckdb_memory_limit": "512MiB",
            "source_bytes": source.stat().st_size, "source_sha256": digest,
            "source_unchanged": True, "rows": 600, "columns": 4000,
            "temporary_disk_poll_seconds": 0.01, "results": results,
        }, indent=2) + "\n")
