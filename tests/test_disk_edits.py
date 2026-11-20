from concurrent.futures import CancelledError, ThreadPoolExecutor
from threading import Event

import pytest

from csv_workbench.core import (
    DELETED_COLUMN,
    NEW_ROW,
    Cancellation,
    CsvFormat,
    Selection,
    export_edited_csv,
    file_revision,
    find_csv_cell,
    prepare_editable_result,
    read_editable_page,
)
from csv_workbench.edits import MISSING, DiskBatch, DiskEdits
from csv_workbench.recovery import RecoveryJournal, checkpoint_data, read_checkpoint
from csv_workbench.replace import preview_replacement


def test_snapshots_null_missing_and_redo_branch():
    edits = DiskEdits()
    edits[(0, 1)] = None
    edits[(1, 1)] = ''
    before = edits.copy()
    edits[(0, 1)] = 'new'
    del edits[(1, 1)]
    after = edits.copy()
    assert dict(before.items()) == {(0, 1): None, (1, 1): ''}
    assert dict(after.items()) == {(0, 1): 'new'}
    assert len(after) == 1
    branch = before.copy()
    branch[(2, 1)] = 'branch'
    assert branch.get((0, 1), MISSING) is None
    assert (2, 1) not in after and (1, 1) not in after
    assert len(branch) == 3
    branch.clear()
    assert not branch and len(before) == 2


@pytest.mark.parametrize('failure', ['cancel', 'io'])
def test_failed_generation_rolls_back_without_publishing(failure):
    edits = DiskEdits()
    edits[(0, 0)] = 'original'
    before = edits.copy()
    token = Cancellation()

    def changes():
        for row in range(5000):
            if row == 3000:
                if failure == 'io':
                    raise OSError('disk full')
                token.cancel()
            yield (row, 0), 'replacement'

    with pytest.raises(CancelledError if failure == 'cancel' else OSError):
        edits.updated(changes(), cancel=token)
    assert edits == before and edits[(0, 0)] == 'original'
    assert len(edits.store.db.execute('SELECT id FROM versions').fetchall()) == 1


def test_bulk_writer_does_not_block_reads_or_snapshot_copies():
    edits = DiskEdits()
    edits[(0, 0)] = 'original'
    snapshot = edits.copy()
    ready, release = Event(), Event()

    def changes():
        yield (0, 0), 'changed'
        ready.set()
        assert release.wait(5)
        yield (1, 0), 'added'

    with ThreadPoolExecutor() as executor:
        future = executor.submit(edits.updated, changes(), cancel=Cancellation())
        try:
            assert ready.wait(5)
            assert snapshot[(0, 0)] == 'original'
            assert edits.copy() == snapshot
        finally:
            release.set()
        after = future.result(timeout=5)
    assert after[(0, 0)] == 'changed' and len(after) == 2


def test_disk_patches_reach_query_search_unique_and_export(tmp_path, monkeypatch):
    source = tmp_path / 'input.csv'
    source.write_text('id,note,group\n1,hit,yes\n2,hit,no\n3,hit,yes\n')
    edits = DiskEdits().updated([
        ((0, 1), 'Привет'), ((1, 2), 'yes'), ((2, DELETED_COLUMN), None),
        ((NEW_ROW, 0), '4'), ((NEW_ROW, 1), 'Привет'), ((NEW_ROW, 2), 'yes'),
    ], cancel=Cancellation())
    first = edits.copy()
    edits[(1, 1)] = 'привет'
    selection = Selection((('group', 'yes'),), 'id', True, ('id', 'note'))
    monkeypatch.setattr(DiskEdits, 'items', lambda _: (_ for _ in ()).throw(AssertionError('materialized patches')))
    token = Cancellation()
    page = read_editable_page(source, 0, selection, edits, cancel=token)
    assert page[1] == [(NEW_ROW, '4', 'Привет'), (1, '2', 'привет'), (0, '1', 'Привет')]
    assert (1, 1) not in first
    unique = prepare_editable_result(source, Selection(), edits, cancel=token, distinct='note')
    assert unique.page(0, cancel=token)[1] == [('Привет', 2), ('привет', 1)]
    output = tmp_path / 'export.csv'
    assert export_edited_csv(source, output, edits, file_revision(source), selection, cancel=token) == 3
    match = find_csv_cell(source, selection, edits, ('id', 'note'), 'ПРИВЕТ', (-1, -1), True, cancel=token)
    assert match is not None


def test_disk_recovery_roundtrip_and_history_stays_metadata(tmp_path, monkeypatch):
    source = tmp_path / 'input.csv'
    source.write_text('id,note\n1,hit\n2,hit\n')
    before = DiskEdits()
    plan = preview_replacement(source, Selection(), before, 'hit', 'new')
    batch = plan.prepare(before, cancel=Cancellation())
    after = batch.after.copy()
    monkeypatch.setattr(DiskBatch, '__iter__', lambda _: (_ for _ in ()).throw(AssertionError('loaded history')))
    monkeypatch.setattr(DiskEdits, 'items', lambda _: (_ for _ in ()).throw(AssertionError('loaded patches')))
    journal = RecoveryJournal(tmp_path / 'recovery')
    try:
        data = checkpoint_data(source, Selection(), CsvFormat(), file_revision(source),
                               ['id', 'note'], after, [batch], 1, 0, NEW_ROW)
        journal.write(data).result(timeout=5)
        state = read_checkpoint(journal.directory)
        assert state.edits[(0, 1)] == 'new'
        assert not state.history[0].before and len(state.history[0].after) == 2
        assert state.position == 1 and state.clean == 0
        assert (journal.directory / 'state.json').stat().st_size < 4096
        journal.write(data).result(timeout=5)
        assert len(list(journal.directory.glob('edits-*.sqlite'))) == 1
    finally:
        journal.close(discard=True).result(timeout=5)


def test_disk_failure_in_single_and_bulk_writes_preserves_snapshots():
    edits = DiskEdits()
    edits[(0, 0)] = 'original'
    before = edits.copy()
    edits.store.db.execute("""
        CREATE TRIGGER reject_write BEFORE INSERT ON patches
        BEGIN SELECT RAISE(ABORT,'simulated disk failure'); END
    """)
    with pytest.raises(OSError, match='simulated disk failure'):
        edits[(0, 0)] = 'broken'
    assert edits == before and edits[(0, 0)] == 'original'
    with pytest.raises(OSError, match='simulated disk failure'):
        edits.updated([((0, 0), 'broken')], cancel=Cancellation())
    assert edits == before


def test_large_generation_and_history_copies_do_not_retain_cell_lists(monkeypatch):
    import tracemalloc

    before = DiskEdits()
    tracemalloc.start()
    try:
        after = before.updated((((i, 0), 'changed') for i in range(100000)), cancel=Cancellation())
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert len(after) == 100000
    assert peak < 12 * 2**20
    monkeypatch.setattr(DiskEdits, '__iter__', lambda _: (_ for _ in ()).throw(AssertionError('loaded values')))
    history = DiskBatch(before, after, 100000)
    assert len(history) == 100000 and history.after.copy() == after
    assert not history.before


def test_legacy_recovery_migrates_history_to_disk(tmp_path):
    from csv_workbench.recovery import disk_recovery

    source = tmp_path / 'input.csv'
    source.write_text('note\nold\n')
    journal = RecoveryJournal(tmp_path / 'recovery')
    try:
        data = checkpoint_data(source, Selection(), CsvFormat(), file_revision(source), ['note'],
                               {(0, 0): 'new'}, [[((0, 0), 'old', 'new')]], 1, 0, NEW_ROW)
        journal.write(data).result(timeout=5)
        state = read_checkpoint(journal.directory)
        disk_recovery(state, Cancellation())
        assert isinstance(state.edits, DiskEdits) and isinstance(state.history[0], DiskBatch)
        assert state.history[0].before[(0, 0)] == 'old'
        assert state.history[0].after[(0, 0)] == 'new'
    finally:
        journal.close(discard=True).result(timeout=5)


def test_failed_disk_checkpoint_retains_previous_complete_manifest(tmp_path, monkeypatch):
    source = tmp_path / 'input.csv'
    source.write_text('note\nhit\n')
    before = DiskEdits()
    batch = preview_replacement(source, Selection(), before, 'hit', 'new').prepare(before, cancel=Cancellation())
    journal = RecoveryJournal(tmp_path / 'recovery')
    try:
        data = checkpoint_data(source, Selection(), CsvFormat(), file_revision(source), ['note'],
                               batch.after, [batch], 1, 0, NEW_ROW)
        journal.write(data).result(timeout=5)
        original = (journal.directory / 'state.json').read_bytes()
        monkeypatch.setattr(journal, 'atomic_write', lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError('disk full')))
        with pytest.raises(OSError, match='disk full'):
            journal.write(data).result(timeout=5)
        assert (journal.directory / 'state.json').read_bytes() == original
        assert len(list(journal.directory.glob('edits-*.sqlite'))) == 1
        assert read_checkpoint(journal.directory).edits[(0, 0)] == 'new'
    finally:
        journal.close(discard=True).result(timeout=5)


def test_cleanup_closes_sqlite_before_removing_directory_with_reference_cycle(monkeypatch):
    import gc
    import sqlite3
    from pathlib import Path
    from tempfile import TemporaryDirectory

    events = []
    connect = sqlite3.connect

    class TrackedConnection(sqlite3.Connection):
        def close(self):
            super().close()
            events.append('closed')

    monkeypatch.setattr(sqlite3, 'connect', lambda *args, **kwargs: connect(*args, **kwargs, factory=TrackedConnection))
    edits = DiskEdits()
    edits[(0, 0)] = 'value'
    store = edits.store
    store.cycle = store
    root = Path(store.scratch.name)
    remove = TemporaryDirectory.cleanup

    def check_closed(scratch):
        if Path(scratch.name) == root:
            assert events == ['closed']
            events.append('removed')
        return remove(scratch)

    monkeypatch.setattr(TemporaryDirectory, 'cleanup', check_closed)
    del edits, store
    gc.collect()
    assert events == ['closed', 'removed'] and not root.exists()
