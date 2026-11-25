"""Wide consumer paths preserve results and release streaming resources."""

import csv
from concurrent.futures import CancelledError
from contextlib import closing
from dataclasses import replace
from pathlib import Path

import pytest

from csv_workbench import completion, core, validation, wide
from csv_workbench import replace as replacement
from csv_workbench.core import DELETED_COLUMN, NEW_ROW, Cancellation, CsvFormat, Selection
from csv_workbench.edits import DiskEdits


@pytest.fixture(autouse=True)
def small_wide_threshold(monkeypatch):
    monkeypatch.setattr(core, 'WIDE_CSV_COLUMNS', 3)

    def full_width_query(*_args, **_kwargs):
        raise AssertionError('Wide consumers must not query the full CSV payload with DuckDB')

    for module in (core, completion, replacement):
        monkeypatch.setattr(module, 'editable_query', full_width_query)


def write_source(path, rows, fmt=None):
    fmt = fmt or CsvFormat()
    with path.open('w', encoding=fmt.encoding or 'utf-8', newline='') as handle:
        writer = csv.writer(handle, delimiter=fmt.delimiter, quotechar=fmt.quote,
                            quoting=csv.QUOTE_NOTNULL)
        writer.writerow(['id', 'note', 'tag', 'unused'])
        writer.writerows(rows)


def track_scratch(monkeypatch, module, tmp_path):
    directory = module.TemporaryDirectory
    paths = []

    def create(**kwargs):
        scratch = directory(dir=tmp_path, **kwargs)
        paths.append(Path(scratch.name))
        return scratch

    monkeypatch.setattr(module, 'TemporaryDirectory', create)
    return paths


def test_cached_wide_completion_is_exact_case_and_bounded_without_source(tmp_path, monkeypatch):
    source = tmp_path / 'values.csv'
    rows = [(str(index), f'item{index:04}', 'yes', 'x') for index in range(109, -1, -1)]
    rows.extend([('110', None, 'yes', 'x'), ('111', '', 'yes', 'x'),
                 ('112', 'ItemCase', 'yes', 'x'), ('113', 'İt', 'yes', 'x')])
    write_source(source, rows)
    selection = Selection(sort_column='id', descending=True, visible_columns=('note',))
    result = core.prepare_editable_result(source, selection, {}, cancel=Cancellation())
    source.unlink()
    token = Cancellation()

    def no_duckdb():
        raise AssertionError('Cached completion should read the existing wide result')

    monkeypatch.setattr(token, 'connection', no_duckdb)
    with result.scratch:
        def complete(prefix):
            return completion.complete_values(source, selection, {}, 'note', prefix,
                                              cancel=token, result=result)

        assert complete('item') == ([f'item{index:04}' for index in range(100)], True)
        assert complete('Item') == (['ItemCase'], False)
        assert complete('İ') == (['İt'], False)
        assert complete('ITEM') == ([], False)
        assert complete('')[0][0] == ''


def test_uncached_wide_completion_uses_format_filters_and_disk_edits(tmp_path, monkeypatch):
    source = tmp_path / 'legacy.csv'
    fmt = CsvFormat(';', encoding='cp1251', quote="'")
    write_source(source, [('1', 'Привет', 'yes', 'x'), ('2', 'привет', 'no', 'x'),
                          ('3', 'ПРИВЕТ', 'yes', 'x'), ('4', None, 'yes', 'x'),
                          ('5', '', 'yes', 'x')], fmt)
    original = source.read_bytes()
    edits = DiskEdits().updated([
        ((0, 1), 'привет-edited'), ((1, 2), 'yes'),
        ((NEW_ROW, 1), 'привет-added'), ((NEW_ROW, 2), 'yes'),
    ], cancel=Cancellation())
    selection = Selection(filters=(('tag', 'yes'),), sort_column='id', descending=True,
                          visible_columns=('note',))
    paths = track_scratch(monkeypatch, wide, tmp_path)

    def complete(prefix):
        return completion.complete_values(source, selection, edits, 'note', prefix,
                                          cancel=Cancellation(), fmt=fmt)

    assert complete('привет') == (['привет', 'привет-added', 'привет-edited'], False)
    assert complete('П') == (['ПРИВЕТ'], False)
    assert complete('') == (['', 'ПРИВЕТ', 'привет', 'привет-added', 'привет-edited'], False)
    assert paths and all(not path.exists() for path in paths)
    assert source.read_bytes() == original


def test_wide_validation_counts_hidden_rules_and_no_rules(tmp_path):
    source = tmp_path / 'rules.csv'
    write_source(source, [('1', 'bad', 'yes', 'x'), ('2', '', 'yes', 'x'),
                          ('3', None, 'yes', 'x'), ('4', 'bad', 'no', 'x')])
    edits = DiskEdits().updated([
        ((0, 1), '12'), ((2, DELETED_COLUMN), None),
        ((NEW_ROW, 1), '5'), ((NEW_ROW, 2), 'yes'),
    ], cancel=Cancellation())
    selection = Selection(filters=(('tag', 'yes'),), visible_columns=('id',),
                          column_types=(('note', 'number', False),))

    result = validation.validate_csv_values(source, selection, edits, cancel=Cancellation())
    assert result.rows == 3 and result.errors == (('note', 1),)
    result = validation.validate_csv_values(source, replace(selection, column_types=()), edits,
                                            cancel=Cancellation())
    assert result.rows == 3 and result.errors == ()


def test_wide_validation_cancellation_closes_source_iterator(tmp_path, monkeypatch):
    source = tmp_path / 'rules.csv'
    write_source(source, [('1', 'bad', 'yes', 'x'), ('2', 'bad', 'yes', 'x')])
    token, closed = Cancellation(), []
    parse, check = core.iter_csv_rows, validation.cell_error

    def rows(*args, **kwargs):
        try:
            yield from parse(*args, **kwargs)
        finally:
            closed.append(True)

    def interrupt(*args, **kwargs):
        result = check(*args, **kwargs)
        token.cancel()
        return result

    monkeypatch.setattr(core, 'iter_csv_rows', rows)
    monkeypatch.setattr(validation, 'cell_error', interrupt)
    with pytest.raises(CancelledError):
        validation.validate_csv_values(
            source, Selection(column_types=(('note', 'number', False),)), {}, cancel=token,
        )
    assert closed == [True]


def test_wide_first_replacement_keeps_view_position_rid_and_noop_count(tmp_path):
    source = tmp_path / 'replace.csv'
    write_source(source, [('1', 'hit hit', 'yes', 'x'), ('2', '', 'yes', 'x'),
                          ('3', 'HIT hit', 'yes', 'x'), ('4', 'quiet', 'yes', 'x')])
    selection = Selection(sort_column='id', descending=True, visible_columns=('note', 'id'),
                          column_order=('note', 'id'))
    plan = replacement.preview_replacement(source, selection, {}, 'hit', 'new', first=True)
    with plan.scratch:
        assert plan.occurrences == plan.cells == 1
        assert plan.preview == [(2, 'note', 'HIT hit', 'new hit')]
        assert list(plan.iter_changes(cancel=Cancellation())) == [((2, 1), 'HIT hit', 'new hit')]
    plan = replacement.preview_replacement(source, selection, {}, 'hit', 'HIT', first=True)
    with plan.scratch:
        assert plan.occurrences == 1 and plan.cells == 0 and plan.preview == []
        assert list(plan.iter_changes(cancel=Cancellation())) == []


def test_cancelled_uncached_wide_completion_closes_values_and_removes_result(tmp_path, monkeypatch):
    source = tmp_path / 'complete.csv'
    write_source(source, [('1', 'hit', 'yes', 'x'), ('2', 'other', 'yes', 'x')])
    paths = track_scratch(monkeypatch, wide, tmp_path)
    token, closed = Cancellation(), []
    values = wide.WideResult.values

    def interrupt(result, column, *, cancel):
        try:
            with closing(values(result, column, cancel=cancel)) as stream:
                for value in stream:
                    cancel.cancel()
                    yield value
        finally:
            closed.append(True)

    monkeypatch.setattr(wide.WideResult, 'values', interrupt)
    with pytest.raises(CancelledError):
        completion.complete_values(source, Selection(), {}, 'note', '', cancel=token)
    assert closed == [True]
    assert paths and all(not path.exists() for path in paths)


def test_cancelled_wide_replacement_removes_plan_and_result(tmp_path, monkeypatch):
    source = tmp_path / 'replace.csv'
    write_source(source, [('1', 'hit', 'yes', 'x'), ('2', 'hit', 'yes', 'x')])
    original = source.read_bytes()
    paths = track_scratch(monkeypatch, replacement, tmp_path)
    wide_paths = track_scratch(monkeypatch, wide, tmp_path)
    token, replace_value = Cancellation(), replacement.replace_literal

    def interrupt(*args, **kwargs):
        result = replace_value(*args, **kwargs)
        token.cancel()
        return result

    monkeypatch.setattr(replacement, 'replace_literal', interrupt)
    with pytest.raises(CancelledError):
        replacement.preview_replacement(source, Selection(), {}, 'hit', 'new', cancel=token)
    assert paths and wide_paths and all(not path.exists() for path in paths + wide_paths)
    assert source.read_bytes() == original
