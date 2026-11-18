import json
import subprocess
import sys
from tempfile import TemporaryDirectory

import pytest

from csv_workbench.core import NEW_ROW, CsvFormat, Selection, file_revision
from csv_workbench.recovery import (
    MISSING,
    RecoveryJournal,
    checkpoint_data,
    read_checkpoint,
)


def data_for(source):
    return checkpoint_data(
        source, Selection(pinned_columns=("note",)), CsvFormat(), file_revision(source),
        ["id", "note"], {(0, 1): "Привет\nworld", (NEW_ROW, 0): "2", (1, -1): None},
        [[((0, 1), MISSING, "Привет\nworld")]], 1, 0, NEW_ROW + 1,
    )


def test_checkpoint_round_trip_and_sequential_updates(tmp_path):
    source = tmp_path / "input.csv"
    original = "id,note\n1,old\n"
    source.write_text(original)
    journal = RecoveryJournal(tmp_path / "recovery")
    data = data_for(source)
    journal.write(data).result(timeout=5)
    state = read_checkpoint(journal.directory)
    assert state.edits == {(0, 1): "Привет\nworld", (NEW_ROW, 0): "2", (1, -1): None}
    assert state.history[0][0] == ((0, 1), MISSING, "Привет\nworld")
    assert state.selection.pinned_columns == ("note",)
    assert state.next_row == NEW_ROW + 1
    assert state.source == source.resolve()
    updated = {**data, "edits": [[[0, 1], "latest"]]}
    journal.write(data)
    journal.write(updated).result(timeout=5)
    assert read_checkpoint(journal.directory).edits == {(0, 1): "latest"}
    assert source.read_text() == original
    journal.close(discard=True).result(timeout=5)
    assert not journal.directory.exists()


def test_active_lock_is_not_reopened(tmp_path):
    journal = RecoveryJournal(tmp_path)
    try:
        with pytest.raises(BlockingIOError):
            RecoveryJournal(tmp_path, directory=journal.directory)
    finally:
        journal.close(discard=True).result(timeout=5)


def test_dead_process_checkpoint_can_be_reopened(tmp_path):
    source = tmp_path / "input.csv"
    source.write_text("id,note\n1,old\n")
    code = """
import os, sys
from pathlib import Path
from csv_workbench.core import CsvFormat, Selection, NEW_ROW, file_revision
from csv_workbench.recovery import RecoveryJournal, checkpoint_data
source = Path(sys.argv[1])
journal = RecoveryJournal(Path(sys.argv[2]))
data = checkpoint_data(source, Selection(), CsvFormat(), file_revision(source),
                       ['id', 'note'], {(0, 1): 'new'},
                       [[((0, 1), 'old', 'new')]], 1, 0, NEW_ROW)
journal.write(data).result()
os._exit(0)
"""
    root = tmp_path / "recovery"
    subprocess.run([sys.executable, "-c", code, str(source), str(root)], check=True, timeout=15)
    directory = next(root.iterdir())
    journal = RecoveryJournal(root, directory=directory)
    try:
        assert read_checkpoint(directory).edits[(0, 1)] == "new"
    finally:
        journal.close(discard=True).result(timeout=5)


def test_failed_atomic_write_keeps_previous_checkpoint(tmp_path, monkeypatch):
    source = tmp_path / "input.csv"
    source.write_text("id,note\n1,old\n")
    journal = RecoveryJournal(tmp_path / "recovery")
    data = data_for(source)
    journal.write(data).result(timeout=5)
    old = (journal.directory / "state.json").read_bytes()

    def fail(*_):
        raise OSError("disk full")

    monkeypatch.setattr("csv_workbench.recovery.os.fsync", fail)
    with pytest.raises(OSError, match="disk full"):
        journal.write({**data, "edits": []}).result(timeout=5)
    assert (journal.directory / "state.json").read_bytes() == old
    assert sorted(p.name for p in journal.directory.iterdir()) == ["owner.lock", "state.json"]
    journal.close(discard=True).result(timeout=5)


@pytest.mark.parametrize("invalid", [
    {"version": 99}, {"edits": [[[True, 0], "bad"]]}, {"history": "bad"},
    {"position": 100}, {"next_row": 1}, {"headers": ["id", "id"]},
    {"edits": [[[0, 999], "bad"]]}, {"base": True},
    {"edits": [[[0, 1], {"missing": True}]]},
])
def test_invalid_checkpoint_is_preserved(tmp_path, invalid):
    source = tmp_path / "input.csv"
    source.write_text("id,note\n1,old\n")
    journal = RecoveryJournal(tmp_path / "recovery")
    journal.write(data_for(source)).result(timeout=5)
    path = journal.directory / "state.json"
    data = json.loads(path.read_text())
    path.write_text(json.dumps({**data, **invalid}))
    with pytest.raises(ValueError):
        read_checkpoint(journal.directory)
    assert path.exists()
    journal.close(discard=True).result(timeout=5)


@pytest.mark.parametrize("missing", [False, True])
def test_changed_or_missing_source_is_not_applied(tmp_path, missing):
    source = tmp_path / "input.csv"
    source.write_text("id,note\n1,old\n")
    journal = RecoveryJournal(tmp_path / "recovery")
    journal.write(data_for(source)).result(timeout=5)
    if missing:
        source.unlink()
    else:
        source.write_text("id,note\n1,external\n")
    with pytest.raises((ValueError, OSError)):
        read_checkpoint(journal.directory)
    assert (journal.directory / "state.json").exists()
    journal.close(discard=True).result(timeout=5)


def test_snapshot_is_saved_independently_and_clear_removes_it(tmp_path):
    source = tmp_path / "input.csv"
    source.write_text("id,note\n1,old\n")
    journal = RecoveryJournal(tmp_path / "recovery")
    with TemporaryDirectory() as base:
        from pathlib import Path

        (Path(base) / "source.csv").write_text("original base")
        snapshot = type("Snapshot", (), {"name": base})()
        journal.write(data_for(source), snapshot).result(timeout=5)
    assert read_checkpoint(journal.directory).base.read_text() == "original base"
    journal.clear().result(timeout=5)
    assert not (journal.directory / "state.json").exists()
    assert not (journal.directory / "base.csv").exists()
    journal.close(discard=True).result(timeout=5)


def test_modified_recovery_base_is_rejected(tmp_path):
    source = tmp_path / "input.csv"
    source.write_text("id,note\n1,old\n")
    journal = RecoveryJournal(tmp_path / "recovery")
    with TemporaryDirectory() as base:
        from pathlib import Path

        (Path(base) / "source.csv").write_bytes(source.read_bytes())
        snapshot = type("Snapshot", (), {"name": base})()
        journal.write(data_for(source), snapshot).result(timeout=5)
    (journal.directory / "base.csv").write_text("id,note\n1,corrupt\n")
    with pytest.raises(ValueError, match="base file changed"):
        read_checkpoint(journal.directory)
    journal.close(discard=True).result(timeout=5)
