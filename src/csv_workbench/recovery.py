"""Versioned, atomic recovery checkpoints; never writes to source CSV files."""

import json
import os
import shutil
from concurrent.futures import CancelledError, ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import Event
from uuid import uuid4

from PySide6.QtCore import QLockFile

from csv_workbench.core import NEW_ROW, CsvFormat, Selection, file_revision
from csv_workbench.session import parse_session_tab, session_tab

MISSING = object()


@dataclass
class RecoveryState:
    source: Path
    selection: Selection
    fmt: CsvFormat
    revision: tuple
    headers: list
    edits: dict
    history: list
    position: int
    clean: int
    next_row: int
    base: Path | None = None


def encode_value(value):
    return {"missing": True} if value is MISSING else value


def decode_value(value, *, missing=False):
    if missing and value == {"missing": True}:
        return MISSING
    if value is not None and not isinstance(value, str):
        raise ValueError("Invalid recovery cell value.")
    return value


def cell_key(value, width):
    if (
        not isinstance(value, list) or len(value) != 2
        or any(type(number) is not int for number in value)
        or not 0 <= value[0] < 2**63 or not -1 <= value[1] < width
    ):
        raise ValueError("Invalid recovery cell address.")
    return tuple(value)


def read_checkpoint(directory):
    data = json.loads((directory / "state.json").read_text(encoding="utf-8"))
    if not isinstance(data, dict) or type(data.get("version")) is not int or data["version"] != 1:
        raise ValueError("Unsupported recovery checkpoint.")
    source, selection, fmt = parse_session_tab(data)
    revision, headers = data.get("revision"), data.get("headers")
    if (
        not isinstance(revision, list) or len(revision) != 5
        or any(type(value) is not int or value < 0 for value in revision)
        or not isinstance(headers, list) or not headers
        or not all(isinstance(name, str) for name in headers)
        or len(set(headers)) != len(headers)
        or not isinstance(data.get("edits"), list)
        or not isinstance(data.get("history"), list)
        or type(data.get("base")) is not bool
    ):
        raise ValueError("Invalid recovery checkpoint.")
    edits = {}
    for entry in data["edits"]:
        if not isinstance(entry, list) or len(entry) != 2:
            raise ValueError("Invalid recovery edits.")
        key = cell_key(entry[0], len(headers))
        if key in edits:
            raise ValueError("Duplicate recovery cell address.")
        edits[key] = decode_value(entry[1])
    history = []
    for batch in data["history"]:
        if not isinstance(batch, list) or not batch:
            raise ValueError("Invalid recovery history.")
        changes = []
        for change in batch:
            if not isinstance(change, list) or len(change) != 3:
                raise ValueError("Invalid recovery history entry.")
            changes.append((
                cell_key(change[0], len(headers)),
                decode_value(change[1], missing=True),
                decode_value(change[2], missing=True),
            ))
        history.append(changes)
    position, clean, next_row = data.get("position"), data.get("clean"), data.get("next_row")
    if (
        any(type(value) is not int for value in (position, clean, next_row))
        or not 0 <= position <= len(history) or not -1 <= clean <= len(history)
        or not NEW_ROW <= next_row < 2**63
        or any(row >= next_row for row, _ in edits)
    ):
        raise ValueError("Invalid recovery history position.")
    base = directory / "base.csv" if data["base"] else None
    if base is not None and not base.is_file():
        raise ValueError("Recovery base file is missing.")
    if base is not None and list(file_revision(base)) != data.get("base_revision"):
        raise ValueError("The recovery base file changed.")
    if file_revision(source) != tuple(revision):
        raise ValueError("The source CSV changed; recovery was not applied.")
    return RecoveryState(
        source, selection, fmt, tuple(revision), headers, edits, history,
        position, clean, next_row, base,
    )


def checkpoint_data(source, selection, fmt, revision, headers, edits, history, position, clean, next_row):
    return {
        "version": 1, **session_tab(source, selection, fmt),
        "revision": list(revision), "headers": list(headers),
        "edits": [[list(key), value] for key, value in edits.items()],
        "history": [[[list(key), encode_value(before), encode_value(after)]
                     for key, before, after in batch] for batch in history],
        "position": position, "clean": clean, "next_row": next_row,
    }


class RecoveryJournal:
    def __init__(self, root, *, directory=None):
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.directory = directory or root / uuid4().hex
        self.directory.mkdir(exist_ok=True, mode=0o700)
        self.lock = QLockFile(str(self.directory / "owner.lock"))
        self.lock.setStaleLockTime(0)
        if not self.lock.tryLock(0):
            raise BlockingIOError("Recovery checkpoint is in use.")
        self.executor = ThreadPoolExecutor(max_workers=1)
        self.stopping = Event()
        self.closed = False

    def write(self, data, snapshot=None):
        # Keep the working snapshot alive until the worker has copied its base.
        return self.executor.submit(self._write, data, snapshot)

    def _write(self, data, snapshot):
        if self.stopping.is_set():
            return
        base = self.directory / "base.csv"
        try:
            if snapshot is not None and not base.exists():
                with (Path(snapshot.name) / "source.csv").open("rb") as source:
                    self.atomic_write(base, source=source)
            self.atomic_write(
                self.directory / "state.json",
                text=json.dumps({
                    **data, "base": snapshot is not None,
                    "base_revision": list(file_revision(base)) if snapshot is not None else None,
                }, ensure_ascii=False),
            )
        except CancelledError:
            return

    def atomic_write(self, destination, *, text=None, source=None):
        temporary = None
        try:
            with NamedTemporaryFile(dir=self.directory, delete=False) as output:
                temporary = Path(output.name)
                if source is None:
                    output.write(text.encode("utf-8"))
                else:
                    while chunk := source.read(1024 * 1024):
                        if self.stopping.is_set():
                            raise CancelledError()
                        output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
            if self.stopping.is_set():
                raise CancelledError()
            temporary.replace(destination)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def clear(self):
        return self.executor.submit(self._clear)

    def _clear(self):
        (self.directory / "state.json").unlink(missing_ok=True)
        (self.directory / "base.csv").unlink(missing_ok=True)

    def close(self, *, discard):
        if self.closed:
            return None
        self.closed = True
        if discard:
            self.stopping.set()

        def finish():
            try:
                if discard:
                    self._clear()
            finally:
                self.lock.unlock()
                if discard:
                    shutil.rmtree(self.directory, ignore_errors=True)

        future = self.executor.submit(finish)
        self.executor.shutdown(wait=False)
        return future
