"""Measure simultaneous sort and unique requests in two tabs (macOS/Linux)."""

import json
import os
import resource
import subprocess
import sys
from itertools import pairwise
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter, sleep

from PySide6.QtCore import QSettings, QTimer
from PySide6.QtWidgets import QApplication, QMessageBox

from csv_workbench import ui
from csv_workbench.core import CsvFormat, Selection


def run(source, other, scratch):
    app = QApplication(["benchmark", "-platform", "offscreen"])
    ui.QSettings = lambda *_: QSettings(str(scratch / "settings.ini"), QSettings.Format.IniFormat)

    def fail(*args):
        raise RuntimeError(args[-1])

    QMessageBox.critical = fail
    window = ui.WorkspaceWindow()
    documents = []
    for path in (source, other):
        window.open_document(path, Selection(), CsvFormat())
        doc = window.tabs.currentWidget()
        doc.view_job[1].result()
        doc.collect_reads()
        doc.count_job[1].result()
        doc.collect_reads()
        documents.append(doc)

    ticks = []
    timer = QTimer()
    timer.setInterval(10)
    timer.timeout.connect(lambda: ticks.append(perf_counter()))
    start = perf_counter()
    ticks.append(start)
    timer.start()
    selection = Selection((("label", "match"),), "id", True, ("id", "payload"))
    first, second = documents
    first.load_view(source, selection)
    second.load_view(other, Selection((("label", "match"),)), distinct="id")
    jobs = [doc.view_job[1] for doc in documents]
    window.tabs.setCurrentWidget(first)
    while not all(job.done() for job in jobs):
        app.processEvents()
        sleep(0.002)
    for job in jobs:
        job.result()
    app.processEvents()
    for doc in documents:
        doc.collect_reads()
    elapsed = perf_counter() - start
    ticks.append(perf_counter())
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    print(
        json.dumps(
            {
                "seconds": elapsed,
                "rss_mib": rss * (1 if sys.platform == "darwin" else 1024) / 2**20,
                "max_ui_tick_ms": 1000 * max(b - a for a, b in pairwise(ticks)),
            }
        ),
        flush=True,
    )
    window.close()


def measure(source):
    with TemporaryDirectory() as scratch:
        root = Path(scratch)
        alias = source.with_name(f".{source.stem}-tab-{os.getpid()}.csv")
        alias.hardlink_to(source)
        try:
            with subprocess.Popen(
                [sys.executable, __file__, "--worker", str(source), str(alias), str(root)],
                env={**os.environ, "TMPDIR": scratch},
                stdout=subprocess.PIPE,
                text=True,
            ) as process:
                peak = 0
                while process.poll() is None:
                    used = 0
                    for path in root.rglob("*"):
                        try:
                            if path.is_file():
                                used += path.stat().st_size
                        except FileNotFoundError:
                            continue
                    peak = max(peak, used)
                    sleep(0.02)
                stdout, _ = process.communicate()
                if process.returncode:
                    raise SystemExit(f"Benchmark failed with exit code {process.returncode}")
        finally:
            alias.unlink()
        print(source.name, json.dumps({**json.loads(stdout), "temp_mib": peak / 2**20}), flush=True)


if __name__ == "__main__":
    if sys.argv[1:2] == ["--worker"]:
        run(*(Path(argument) for argument in sys.argv[2:]))
    else:
        for name in sys.argv[1:]:
            measure(Path(name).resolve(strict=True))
