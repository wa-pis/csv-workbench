import csv
import sqlite3
from concurrent.futures import CancelledError
from contextlib import closing
from pathlib import Path

import pytest

from csv_workbench import wide
from csv_workbench.core import (
    Cancellation,
    CsvFormat,
    Selection,
    prepare_editable_result,
    read_editable_page,
)
from csv_workbench.edits import MISSING, NEW_ROW, DiskEdits
from csv_workbench.wide import effective_rows, prepare_wide_result


def csv_source(tmp_path, columns, rows, *, fmt=None):
    fmt = fmt or CsvFormat()
    source = tmp_path / "source.csv"
    with source.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter=fmt.delimiter, quotechar=fmt.quote,
                            quoting=csv.QUOTE_NOTNULL)
        if fmt.header:
            writer.writerow(columns)
        writer.writerows(rows)
    return source


def prepare(source, columns, selection=None, edits=None, **kwargs):
    return prepare_wide_result(source, columns, selection or Selection(), edits or {},
                               cancel=Cancellation(), fmt=CsvFormat(), **kwargs)


@pytest.mark.parametrize("disk", [False, True])
def test_effective_rows_keep_stable_ids_null_edits_deletions_and_added_rows(tmp_path, disk):
    columns = ["id", "text"]
    source = csv_source(tmp_path, columns, [["a", "original"], ["b", ""], ["c", None]])
    changes = [((0, 1), None), ((1, -1), "present"), ((7, 1), "ignore old id"),
               ((NEW_ROW + 2, 0), "z"), ((NEW_ROW, 1), "new"),
               ((NEW_ROW + 1, -1), None), ((NEW_ROW + 1, 0), "deleted new row")]
    edits = DiskEdits().updated(changes, cancel=Cancellation()) if disk else dict(changes)
    assert list(effective_rows(source, columns, edits, cancel=Cancellation(), fmt=CsvFormat())) == [
        (0, ["a", None]), (2, ("c", None)),
        (NEW_ROW, [None, "new"]), (NEW_ROW + 2, ["z", None]),
    ]


def test_effective_disk_edits_respect_latest_missing_patch(tmp_path):
    source = csv_source(tmp_path, ["id", "text"], [["a", "original"]])
    edits = DiskEdits().updated([((0, 1), "changed")], cancel=Cancellation())
    edits = edits.updated([((0, 1), MISSING)], cancel=Cancellation())
    assert list(effective_rows(source, ["id", "text"], edits,
                               cancel=Cancellation(), fmt=CsvFormat())) == [(0, ("a", "original"))]


@pytest.mark.parametrize("match_any", [False, True])
@pytest.mark.parametrize("operator,value", [("equals", None), ("not_equals", "")])
def test_filters_match_duckdb_unicode_and_null_semantics(tmp_path, match_any, operator, value):
    columns = ["id", "tag", "note"]
    source = csv_source(tmp_path, columns, [
        ["0", "ΟΣ İt", None], ["1", "Ος it", ""],
        ["2", "οσ IT", "other"], ["3", "plain", None], ["4", None, "other"],
    ])
    selection = Selection(filters=(("tag", "οσ it"),), visible_columns=("id", "note"),
                          exact_filters=(("note", operator, value),), match_any=match_any,
                          column_order=("note",))
    edits = {(4, 1): "ΟΣ İt"}
    expected = read_editable_page(source, 0, selection, edits, cancel=Cancellation())
    result = prepare(source, columns, selection, edits, identified=True)
    with result.scratch:
        assert result.page(0) == expected
        assert result.headers == ["note", "id"]


def test_multikey_sort_is_stable_nulls_last_and_keeps_original_row_ids(tmp_path):
    columns = ["__CSV_SOURCE_ROW", "first", "second", "hidden"]
    source = csv_source(tmp_path, columns, [
        ["0", "b", "2", "x"], ["1", "a", "2", "x"],
        ["2", "a", "2", "x"], ["3", "a", None, "x"],
        ["4", None, "9", "x"], ["5", "a", "1", "x"],
    ])
    selection = Selection(sort_column="first", extra_sort=(("second", True),),
                          visible_columns=("__CSV_SOURCE_ROW", "second"))
    edits = {(0, 1): "a", (NEW_ROW, 0): "new", (NEW_ROW, 1): "a", (NEW_ROW, 2): "2"}
    expected = prepare_editable_result(source, selection, edits, cancel=Cancellation())
    result = prepare(source, columns, selection, edits, identified=True)
    with result.scratch, expected.scratch:
        assert result.page(0) == expected.page(0)
        assert result.rowidname == "__csv_source_row_"
        assert [rid for _, rid, _ in result.iter_rows(cancel=Cancellation())] == [
            0, 1, 2, NEW_ROW, 5, 3, 4,
        ]
        assert [path.name for path in result.path.parent.iterdir()] == ["result.sqlite"]


def test_cached_pages_tail_and_ordered_iterators_survive_source_removal(tmp_path):
    columns = ["id", "text"]
    source = csv_source(tmp_path, columns, [[f"{i:04}", f"v{i}"] for i in range(405)])
    result = prepare(source, columns, Selection(visible_columns=("id",)))
    source.unlink()
    with result.scratch:
        assert result.page(400) == (["id"], [(f"{i:04}",) for i in range(400, 405)], False)
        tail = result.page(None)
        assert tail == (["id"], [(f"{i:04}",) for i in range(200, 405)], False, 200)
        assert list(result.iter_rows(cancel=Cancellation(), start=403)) == [
            (403, 403, ("0403",)), (404, 404, ("0404",)),
        ]
        with closing(result.iter_rows(cancel=Cancellation(), start=1, reverse=True)) as rows:
            assert list(rows) == [(1, 1, ("0001",)), (0, 0, ("0000",))]


def test_values_project_one_column_without_parsing_payload_in_python(tmp_path, monkeypatch):
    columns = ['odd"name', "other"]
    source = csv_source(tmp_path, columns, [[None, "x"], ["", "y"], ["ΟΣ", "z"]])
    result = prepare(source, columns)
    with result.scratch:
        def unexpected(*args, **kwargs):
            raise AssertionError("values must not parse the full JSON row in Python")
        monkeypatch.setattr(wide.json, "loads", unexpected)
        assert list(result.values('odd"name', cancel=Cancellation())) == [None, "", "ΟΣ"]
        with pytest.raises(ValueError, match="Unknown CSV column"):
            list(result.values("missing", cancel=Cancellation()))


@pytest.mark.parametrize("singletons", [False, True])
def test_distinct_counts_effective_filtered_hidden_values(tmp_path, singletons):
    columns = ["id", "group", "value"]
    source = csv_source(tmp_path, columns, [
        ["0", "yes", "x"], ["1", "yes", "x"], ["2", "yes", None],
        ["3", "yes", ""], ["4", "no", "x"], ["5", "yes", "x"],
    ])
    edits = {(5, -1): None, (NEW_ROW, 1): "yes", (NEW_ROW, 2): "new"}
    selection = Selection(filters=(("group", "yes"),), visible_columns=("id",))
    result = prepare(source, columns, selection, edits, distinct="value", singletons=singletons)
    with result.scratch:
        rows = [("", 1), ("new", 1)] + ([] if singletons else [("x", 2)]) + [(None, 1)]
        assert result.page(0) == (["Value", "Frequency"], rows, False)
        assert [path.name for path in result.path.parent.iterdir()] == ["result.parquet"]


def test_only_narrow_sort_keys_are_staged_in_bounded_batches(tmp_path, monkeypatch):
    import polars as pl

    columns = ["id", "key", "unused", "payload"]
    source = csv_source(tmp_path, columns, [[str(i), str(9 - i), "ignored", "x" * 100] for i in range(10)])
    original = pl.DataFrame.write_parquet
    batches = []

    def record(frame, file, *args, **kwargs):
        batches.append((frame.columns, frame.height))
        return original(frame, file, *args, **kwargs)

    monkeypatch.setattr(pl.DataFrame, "write_parquet", record)
    monkeypatch.setattr(wide, "BATCH_ROWS", 3)
    monkeypatch.setattr(wide, "BATCH_BYTES", 20)
    result = prepare(source, columns, Selection(sort_column="key"))
    with result.scratch:
        assert [row[0] for row in result.page(0)[1]] == list(map(str, range(9, -1, -1)))
        assert all(names == ["rid", "k0"] and 0 < rows <= 2 for names, rows in batches)
        assert sum(rows for _, rows in batches) == 10


def test_early_iterator_close_closes_sqlite_reader(tmp_path, monkeypatch):
    source = csv_source(tmp_path, ["id"], [["0"], ["1"]])
    result = prepare(source, ["id"])
    connections = []
    original = sqlite3.connect

    def connect(*args, **kwargs):
        db = original(*args, **kwargs)
        connections.append(db)
        return db

    monkeypatch.setattr(wide.sqlite3, "connect", connect)
    with result.scratch:
        for iterator in (result.iter_rows(cancel=Cancellation()), result.values("id", cancel=Cancellation())):
            next(iterator)
            iterator.close()
            with pytest.raises(sqlite3.ProgrammingError, match="closed"):
                connections[-1].execute("SELECT 1")


def test_effective_iterator_close_closes_source_and_patch_readers(tmp_path, monkeypatch):
    from csv_workbench import core

    source = csv_source(tmp_path, ["id"], [["0"], ["1"]])
    edits = DiskEdits().updated([((0, 0), "a"), ((1, 0), "b")], cancel=Cancellation())
    original_rows, original_patches = core.iter_csv_rows, DiskEdits.iter_rows
    closed = set()

    def rows(*args):
        try:
            yield from original_rows(*args)
        finally:
            closed.add("source")

    def patches(self, **kwargs):
        try:
            yield from original_patches(self, **kwargs)
        finally:
            closed.add("patches")

    monkeypatch.setattr(core, "iter_csv_rows", rows)
    monkeypatch.setattr(DiskEdits, "iter_rows", patches)
    iterator = effective_rows(source, ["id"], edits, cancel=Cancellation(), fmt=CsvFormat())
    assert next(iterator) == (0, ["a"])
    iterator.close()
    assert closed == {"source", "patches"}


def test_cancelled_prepare_removes_partial_payload_and_preserves_source(tmp_path, monkeypatch):
    from csv_workbench import core

    source = csv_source(tmp_path, ["id"], [["0"], ["1"]])
    original_bytes = source.read_bytes()
    token = Cancellation()
    original_rows, original_directory = core.iter_csv_rows, wide.TemporaryDirectory
    directories = []

    def cancelled_rows(*args):
        with closing(original_rows(*args)) as rows:
            yield next(rows)
            token.cancel()
            yield next(rows)

    def directory(**kwargs):
        scratch = original_directory(dir=tmp_path, **kwargs)
        directories.append(Path(scratch.name))
        return scratch

    monkeypatch.setattr(core, "iter_csv_rows", cancelled_rows)
    monkeypatch.setattr(wide, "TemporaryDirectory", directory)
    with pytest.raises(CancelledError):
        prepare_wide_result(source, ["id"], Selection(), {}, cancel=token, fmt=CsvFormat())
    assert directories and not any(path.exists() for path in directories)
    assert source.read_bytes() == original_bytes


def test_cancel_while_consuming_sorted_ids_removes_result(tmp_path, monkeypatch):
    import polars as pl

    source = csv_source(tmp_path, ["id"], [[str(i)] for i in range(10)])
    token = Cancellation()
    original_sink, original_directory = pl.LazyFrame.sink_batches, wide.TemporaryDirectory
    directories = []

    def directory(**kwargs):
        scratch = original_directory(dir=tmp_path, **kwargs)
        directories.append(Path(scratch.name))
        return scratch

    def sink(frame, function, **kwargs):
        def cancel_after_batch(batch):
            function(batch)
            token.cancel()
            return True
        return original_sink(frame, cancel_after_batch, **kwargs)

    monkeypatch.setattr(wide, "TemporaryDirectory", directory)
    monkeypatch.setattr(pl.LazyFrame, "sink_batches", sink)
    with pytest.raises(CancelledError):
        prepare_wide_result(source, ["id"], Selection(sort_column="id"), {},
                            cancel=token, fmt=CsvFormat())
    assert directories and not any(path.exists() for path in directories)
    assert source.exists()


def test_pre_cancelled_reads_and_empty_results(tmp_path):
    source = csv_source(tmp_path, ["id", "text"], [["0", None]])
    result = prepare(source, ["id", "text"], Selection(filters=(("id", "missing"),), sort_column="id"))
    token = Cancellation()
    token.cancel()
    with result.scratch:
        assert result.page(0) == (["id", "text"], [], False)
        assert result.page(None) == (["id", "text"], [], False, 0)
        for read in (lambda: result.page(0, cancel=token),
                     lambda: list(result.iter_rows(cancel=token)),
                     lambda: list(result.values("id", cancel=token))):
            with pytest.raises(CancelledError):
                read()


@pytest.mark.parametrize("selection", [
    Selection(visible_columns=()), Selection(sort_column="missing"),
    Selection(filters=(("missing", "x"),)),
    Selection(exact_filters=(("id", "unsupported", None),)),
])
def test_invalid_selection_rejected_before_building_result(tmp_path, selection):
    source = csv_source(tmp_path, ["id"], [["0"]])
    with pytest.raises(ValueError):
        prepare(source, ["id"], selection)
    assert list(tmp_path.iterdir()) == [source]


def test_custom_format_and_null_empty_are_preserved(tmp_path):
    fmt = CsvFormat(delimiter=";", quote="'", header=False)
    columns = ["column0", "column1"]
    source = csv_source(tmp_path, columns, [["a;b", None], ["multi\nline", ""], ["it's", "x"]], fmt=fmt)
    selection = Selection(sort_column="column0", visible_columns=("column1", "column0"),
                          column_order=("column1",))
    result = prepare_wide_result(source, columns, selection, {}, cancel=Cancellation(), fmt=fmt)
    with result.scratch:
        assert result.page(0) == (["column1", "column0"],
                                  [(None, "a;b"), ("x", "it's"), ("", "multi\nline")], False)
