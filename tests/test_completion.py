from concurrent.futures import CancelledError, ThreadPoolExecutor
from threading import Event

import pytest

from csv_workbench.completion import complete_values, query_values
from csv_workbench.core import (
    DELETED_COLUMN,
    NEW_ROW,
    Cancellation,
    CsvFormat,
    Selection,
    prepare_editable_result,
)
from csv_workbench.edits import DiskEdits


@pytest.mark.parametrize('cached', [False, True])
def test_completion_full_selection_disk_edits_and_structural_rows(tmp_path, cached):
    source = tmp_path / 'input.csv'
    source.write_text('id,note,group\n' + ''.join(
        f'{i},value{i:04},yes\n' for i in range(601)
    ) + '601,excluded,no\n')
    edits = DiskEdits().updated([
        ((0, 1), 'value-edited'), ((1, DELETED_COLUMN), None),
        ((NEW_ROW, 0), '602'), ((NEW_ROW, 1), 'value-added'), ((NEW_ROW, 2), 'yes'),
    ], cancel=Cancellation())
    selection = Selection(filters=(('group', 'yes'),), sort_column='id', descending=True,
                          visible_columns=('note',), pinned_columns=('note',))
    result = prepare_editable_result(source, selection, edits, cancel=Cancellation()) if cached else None

    def complete(prefix):
        return complete_values(source, selection, edits, 'note', prefix,
                               result=result, cancel=Cancellation())

    assert complete('value00') == ([f'value{i:04}' for i in range(2, 100)], False)
    assert complete('value06') == (['value0600'], False)  # Outside the GUI buffer.
    assert complete('value-') == (['value-added', 'value-edited'], False)
    assert complete('excluded') == ([], False)
    assert complete('value')[1]


@pytest.mark.parametrize('cached', [False, True])
def test_literal_unicode_null_empty_quoted_column_and_format(tmp_path, cached):
    source = tmp_path / 'input.tsv'
    source.write_text('odd"name;tag\nΟΣ;yes\nΟς;yes\nİt;yes\nit;yes\n%_x;yes\n%_x;yes\n;yes\n', encoding='utf-16')
    fmt = CsvFormat(delimiter=';', encoding='utf-16')
    edits = {(6, 0): ''}
    result = prepare_editable_result(source, Selection(), edits, cancel=Cancellation(), fmt=fmt) if cached else None
    for prefix, expected in [('', ['', '%_x', 'it', 'İt', 'ΟΣ', 'Ος']),
                             ('Ο', ['ΟΣ', 'Ος']), ('İ', ['İt']), ('%_', ['%_x']),
                             ('_', []), ('null', [])]:
        assert complete_values(source, Selection(), edits, 'odd"name', prefix,
                               fmt=fmt, result=result, cancel=Cancellation()) == (expected, False)


def test_low_and_high_cardinality_deterministic_top100():
    with Cancellation().connection() as db:
        sql = "SELECT 'v' || lpad(CAST(999-i AS VARCHAR), 4, '0') AS value FROM range(1000) t(i)"
        values, truncated = query_values(db, sql, [], 'value', '', Cancellation())
        assert values == [f'v{i:04}' for i in range(100)] and truncated
    with Cancellation().connection() as db:
        sql = "SELECT CAST(i % 10 AS VARCHAR) AS value FROM range(100000) t(i)"
        assert query_values(db, sql, [], 'value', '', Cancellation()) == (list('0123456789'), False)


def test_pre_cancelled_completion(tmp_path):
    token = Cancellation()
    token.cancel()
    with pytest.raises(CancelledError):
        complete_values(tmp_path / 'not-read.csv', Selection(), {}, 'x', '', cancel=token)


def test_running_completion_interrupts_query():
    token, started = Cancellation(), Event()

    def search():
        with token.connection() as db:
            started.set()
            return query_values(db, 'SELECT CAST(i AS VARCHAR) AS value FROM range(1000000000) t(i)',
                                [], 'value', '', token)

    with ThreadPoolExecutor() as executor:
        future = executor.submit(search)
        assert started.wait(5)
        token.cancel()
        with pytest.raises(CancelledError):
            future.result(timeout=5)


def test_cancel_polars_phase_removes_scratch(monkeypatch):
    import polars as pl

    from csv_workbench import completion

    token, directories = Cancellation(), []
    original_directory = completion.TemporaryDirectory
    original_sink = pl.LazyFrame.sink_batches

    def directory(**kwargs):
        temporary = original_directory(**kwargs)
        from pathlib import Path
        directories.append(Path(temporary.name))
        return temporary

    def sink(frame, callback, **kwargs):
        def first_batch(batch):
            callback(batch)
            token.cancel()
            return True
        return original_sink(frame, first_batch, **kwargs)

    monkeypatch.setattr(completion, 'TemporaryDirectory', directory)
    monkeypatch.setattr(pl.LazyFrame, 'sink_batches', sink)
    with pytest.raises(CancelledError), token.connection() as db:
        query_values(db, 'SELECT CAST(i AS VARCHAR) AS value FROM range(100000) t(i)',
                     [], 'value', '', token)
    assert directories and all(not path.exists() for path in directories)
