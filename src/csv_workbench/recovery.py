"""Versioned, atomic recovery checkpoints; never writes to source CSV files."""

import json
import os
import shutil
import sqlite3
from concurrent.futures import CancelledError, ThreadPoolExecutor
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import Event
from uuid import uuid4

from PySide6.QtCore import QLockFile

from csv_workbench.core import NEW_ROW, CsvFormat, Selection, file_revision
from csv_workbench.edits import MISSING, DiskBatch, DiskEdits, EditStore
from csv_workbench.session import parse_session_tab, session_tab


@dataclass
class RecoveryState:
    source: Path
    selection: Selection
    fmt: CsvFormat
    revision: tuple
    headers: list
    edits: dict | DiskEdits
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
    if isinstance(data, dict) and data.get("version") == 2:
        return read_disk_checkpoint(directory, data)
    return parse_checkpoint(directory, data)


def parse_checkpoint(directory, data):
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
    if isinstance(edits, DiskEdits):
        return {
            "version": 2, **session_tab(source, selection, fmt),
            "revision": list(revision), "headers": list(headers),
            "edits": edits.descriptor(),
            "history": [{"before": batch.before.descriptor(), "after": batch.after.descriptor(),
                         "count": len(batch)} for batch in history],
            "position": position, "clean": clean, "next_row": next_row,
            "_store": edits.store,
        }
    return {
        "version": 1, **session_tab(source, selection, fmt),
        "revision": list(revision), "headers": list(headers),
        "edits": [[list(key), value] for key, value in edits.items()],
        "history": [[[list(key), encode_value(before), encode_value(after)]
                     for key, before, after in batch] for batch in history],
        "position": position, "clean": clean, "next_row": next_row,
    }


def read_disk_checkpoint(directory, data):
    state = parse_checkpoint(directory, {
        **data, "version": 1, "edits": [], "history": [], "position": 0, "clean": 0,
    })
    filename = data.get("store")
    if not isinstance(filename, str) or Path(filename).name != filename or not filename.endswith('.sqlite'):
        raise ValueError("Invalid recovery store path.")
    database = directory / filename
    if not database.is_file() or list(file_revision(database)) != data.get('store_revision'):
        raise ValueError("Recovery edit database changed or is missing.")
    store = EditStore()
    try:
        with closing(sqlite3.connect(database)) as source:
            source.backup(store.db)
        def snapshot(value):
            if not isinstance(value, dict):
                raise ValueError("Invalid edit snapshot.")  # noqa: TRY004
            versions, count = value.get('versions'), value.get('count')
            if (not isinstance(versions, list) or any(type(v) is not int or v < 1 for v in versions)
                    or versions != sorted(set(versions)) or type(count) is not int or count < 0):
                raise ValueError("Invalid edit snapshot.")
            for version in versions:
                if store.db.execute('SELECT 1 FROM versions WHERE id=?', (version,)).fetchone() is None:
                    raise ValueError("Missing edit generation.")
            expected = store.db.execute('SELECT count FROM versions WHERE id=?', (versions[-1],)).fetchone()[0] if versions else 0
            if count != expected:
                raise ValueError("Invalid edit count.")
            return DiskEdits(store, versions, count)
        history = data.get('history')
        if not isinstance(history, list):
            raise ValueError("Invalid recovery history.")  # noqa: TRY004
        batches = []
        for entry in history:
            if not isinstance(entry, dict) or type(entry.get('count')) is not int or entry['count'] < 1:
                raise ValueError("Invalid recovery batch.")
            batches.append(DiskBatch(snapshot(entry.get('before')), snapshot(entry.get('after')), entry['count']))
        position, clean = data.get('position'), data.get('clean')
        if (type(position) is not int or type(clean) is not int
                or not 0 <= position <= len(batches) or not -1 <= clean <= len(batches)):
            raise ValueError("Invalid recovery history position.")
        state.edits, state.history = snapshot(data.get('edits')), batches
        state.position, state.clean = position, clean
        return state
    except sqlite3.Error as exc:
        raise ValueError("Invalid recovery edit database.") from exc


def disk_recovery(state, cancel):
    """Migrate a legacy checkpoint once, in the existing recovery worker."""
    if isinstance(state.edits, DiskEdits):
        return
    current = DiskEdits().updated(state.edits.items(), cancel=cancel)
    root = current.copy()
    for batch in reversed(state.history[:state.position]):
        root = root.updated(((key, before) for key, before, _ in batch), cancel=cancel)
    batches = []
    for changes in state.history:
        after = root.updated(((key, value) for key, _, value in changes), cancel=cancel)
        batches.append(DiskBatch(root, after, len(changes)))
        root = after
    state.edits, state.history = current, batches


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
            data = dict(data)
            store = data.pop('_store', None)
            database = None
            if store is not None:
                database = self.directory / f"edits-{uuid4().hex}.sqlite"
                def check():
                    if self.stopping.is_set():
                        raise CancelledError()
                try:
                    store.backup(database, check)
                    with database.open('r+b') as handle:
                        os.fsync(handle.fileno())
                    data.update(store=database.name, store_revision=list(file_revision(database)))
                except BaseException:
                    database.unlink(missing_ok=True)
                    raise
            try:
                self.atomic_write(
                    self.directory / "state.json",
                    text=json.dumps({
                        **data, "base": snapshot is not None,
                        "base_revision": list(file_revision(base)) if snapshot is not None else None,
                    }, ensure_ascii=False),
                )
            except BaseException:
                if database is not None:
                    database.unlink(missing_ok=True)
                raise
            for old in self.directory.glob('edits-*.sqlite'):
                if old.name != data.get('store'):
                    old.unlink(missing_ok=True)
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
        for path in self.directory.glob('edits-*.sqlite'):
            path.unlink(missing_ok=True)

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
