"""Whole-view wide operations preserve public query and file-writing contracts."""

import csv
import stat
from concurrent.futures import CancelledError

import pytest

from csv_workbench import core
from csv_workbench.core import (
    DELETED_COLUMN,
    NEW_ROW,
    Cancellation,
    CsvFormat,
    Selection,
    export_csv,
    export_edited_csv,
    file_revision,
    prepare_editable_result,
    prepare_result,
    read_editable_page,
    read_page,
    save_csv,
)
from csv_workbench.edits import DiskBatch, DiskEdits


@pytest.fixture(autouse=True)
def small_wide_threshold(monkeypatch):
    monkeypatch.setattr(core, 'WIDE_CSV_COLUMNS', 3)


def write_source(path, headers, rows, fmt=None):
    fmt = fmt or CsvFormat()
    with path.open('w', encoding=fmt.encoding or 'utf-8', newline='') as output:
        writer = csv.writer(output, delimiter=fmt.delimiter, quotechar=fmt.quote,
                            quoting=csv.QUOTE_NOTNULL, lineterminator='\r\n')
        if fmt.header:
            writer.writerow(headers)
        writer.writerows(rows)


def test_unedited_wide_export_rejects_source_change_before_publication(tmp_path, monkeypatch):
    source, output = tmp_path / 'source.csv', tmp_path / 'existing.csv'
    write_source(source, ['a', 'b', 'c', 'd'], [('old', 'value', None, '')])
    output.write_bytes(b'existing export')
    write = core.write_csv_records

    def change_source(*args, **kwargs):
        count = write(*args, **kwargs)
        write_source(source, ['a', 'b', 'c', 'd'], [('new', 'changed', '', None)])
        return count

    monkeypatch.setattr(core, 'write_csv_records', change_source)
    with pytest.raises(ValueError, match='source CSV changed'):
        export_csv(source, output, Selection())
    assert output.read_bytes() == b'existing export'
    assert set(tmp_path.iterdir()) == {source, output}


def test_full_payload_sort_preserves_multikey_nulls_ties_and_source_ids(tmp_path):
    source = tmp_path / 'source.csv'
    headers = ['__csv_source_row', 'rank', 'label', 'payload']
    rows = [('r0', '02', 'beta', 'payload 0'),
            ('r1', '01', 'same', 'payload 1'),
            ('r2', None, 'omega', 'payload 2'),
            ('r3', '01', 'zulu', 'payload 3'),
            ('r4', '01', 'same', 'payload 4'),
            ('r5', '01', None, 'payload 5')]
    write_source(source, headers, rows)
    selection = Selection(sort_column='rank', extra_sort=(('label', True),))
    order = [3, 1, 4, 5, 0, 2]
    expected = [rows[index] for index in order]

    assert read_page(source, 0, selection, limit=2) == (headers, expected[:2], True)
    assert read_page(source, 2, selection, limit=2) == (headers, expected[2:4], True)
    assert read_page(source, 4, selection, limit=2) == (headers, expected[4:], False)
    result = prepare_result(source, selection, cancel=Cancellation())
    assert result.page(0, limit=10) == (headers, expected, False)
    identified, records, more = read_editable_page(
        source, 0, selection, {}, cancel=Cancellation(), limit=10,
    )
    assert identified[1:] == headers and identified[0] not in headers
    assert records == [(index, *rows[index]) for index in order]
    assert not more


def test_filtered_edits_affect_unloaded_rows_and_unique_frequencies(tmp_path):
    source = tmp_path / 'source.csv'
    headers = ['id', 'group', 'note', 'payload']
    rows = [(f'{index:04}', 'keep' if index % 2 == 0 else 'other', 'old', f'p{index}')
            for index in range(405)]
    write_source(source, headers, rows)
    original = source.read_bytes()
    edits = DiskEdits().updated([
        ((404, 1), 'other'), ((403, 1), 'KEEP'), ((402, 2), 'changed'),
    ], cancel=Cancellation())
    selection = Selection(filters=(('group', 'keep'),), sort_column='id', descending=True,
                          visible_columns=('id', 'note'), column_order=('note', 'id'))
    order = [403, *range(402, -1, -2)]
    expected = [(index, 'changed' if index == 402 else 'old', f'{index:04}')
                for index in order]

    first = read_editable_page(source, 0, selection, edits, cancel=Cancellation())
    second = read_editable_page(source, 200, selection, edits, cancel=Cancellation())
    assert first[0][1:] == ['note', 'id']
    assert first[1:] == (expected[:200], True)
    assert second[1:] == (expected[200:], False)
    unique = prepare_editable_result(source, selection, edits, cancel=Cancellation(),
                                     distinct='note')
    assert unique.page(0)[1:] == ([('changed', 1), ('old', 202)], False)
    singletons = prepare_editable_result(source, selection, edits, cancel=Cancellation(),
                                        distinct='note', singletons=True)
    assert singletons.page(0)[1:] == ([('changed', 1)], False)
    assert source.read_bytes() == original


@pytest.mark.parametrize('match_any', [False, True])
def test_wide_filters_combine_unicode_contains_and_exact_conditions(tmp_path, match_any):
    source = tmp_path / 'source.csv'
    rows = [('0', 'keep', 'ПРИВЕТ', 'a'), ('1', 'other', 'привет', None),
            ('2', 'keep', 'нет', ''), ('3', 'other', 'нет', 'b'),
            ('4', None, None, 'c')]
    write_source(source, ['id', 'group', 'note', 'payload'], rows)
    edits = {(3, 2): 'ПРИВЕТ', (4, 1): 'keep'}
    selection = Selection(filters=(('note', 'привет'),), match_any=match_any,
                          exact_filters=(('group', 'equals', 'keep'),),
                          sort_column='id', descending=True, visible_columns=('id', 'payload'))
    order = [4, 3, 2, 1, 0] if match_any else [0]
    page = read_editable_page(source, 0, selection, edits, cancel=Cancellation())
    assert page[0][1:] == ['id', 'payload']
    assert page[1:] == ([(index, rows[index][0], rows[index][3]) for index in order], False)


def test_wide_unique_keeps_null_empty_and_case_sensitive_frequencies(tmp_path):
    source = tmp_path / 'source.csv'
    notes = ['ПРИВЕТ', 'привет', None, '', None, '']
    write_source(source, ['id', 'group', 'note', 'payload'],
                 [(str(index), 'keep', note, 'x') for index, note in enumerate(notes)]
                 + [('9', 'other', 'excluded', 'y')])
    selection = Selection(filters=(('group', 'KEEP'),), visible_columns=('id',))
    result = prepare_result(source, selection, distinct='note', cancel=Cancellation())
    assert result.page(0) == (['Value', 'Frequency'],
                              [('', 2), ('ПРИВЕТ', 1), ('привет', 1), (None, 2)], False)


def test_structural_disk_edits_follow_undo_redo_snapshots(tmp_path):
    source = tmp_path / 'source.csv'
    headers = ['id', 'label', 'group', 'payload']
    rows = [('1', 'first', 'keep', 'a'), ('2', 'second', 'keep', 'b'),
            ('3', 'hidden', 'other', 'c')]
    write_source(source, headers, rows)
    before = DiskEdits()
    after = before.updated([
        ((0, DELETED_COLUMN), None), ((1, 1), 'patched'),
        ((NEW_ROW, 0), '4'), ((NEW_ROW, 1), 'second'),
        ((NEW_ROW, 2), 'keep'), ((NEW_ROW, 3), 'b'),
    ], cancel=Cancellation())
    history = DiskBatch(before, after, 6)
    selection = Selection(filters=(('group', 'keep'),), sort_column='id', descending=True)

    def view(snapshot):
        return read_editable_page(source, 0, selection, snapshot, cancel=Cancellation())[1]

    expected = [(NEW_ROW, '4', 'second', 'keep', 'b'), (1, '2', 'patched', 'keep', 'b')]
    assert view(history.after) == expected
    assert view(history.before) == [(1, *rows[1]), (0, *rows[0])]
    assert view(history.after.copy()) == expected
    branch = history.after.copy()
    branch[(1, 1)] = 'another branch'
    assert view(history.after) == expected


def test_save_all_rows_and_columns_is_separate_from_formatted_view_export(tmp_path):
    source = tmp_path / 'source.csv'
    headers = ['id', 'group', 'note', 'payload']
    rows = [('1', 'keep', None, "ёж; 'чай'"), ('2', 'other', '', 'строка\nещё'),
            ('3', 'keep', 'Привет', None), ('4', 'keep', '', '')]
    fmt = CsvFormat(';', encoding='cp1251', quote="'")
    write_source(source, headers, rows, fmt)
    original = source.read_bytes()
    revision = file_revision(source)
    edits = DiskEdits().updated([((1, 2), 'скрытая правка'), ((0, 3), 'правка')],
                                cancel=Cancellation())
    selection = Selection(filters=(('group', 'keep'),), sort_column='id', descending=True,
                          visible_columns=('id', 'note'), column_order=('note', 'id'))
    exported = tmp_path / 'selection.tsv'
    output_format = CsvFormat('|', False, 'utf-16', '^')
    assert export_edited_csv(source, exported, edits, revision, selection, fmt=fmt,
                             output_format=output_format) == 3
    assert read_page(exported, 0, fmt=output_format)[1] == [('', '4'), ('Привет', '3'),
                                                          (None, '1')]

    saved = tmp_path / 'saved.csv'
    saved.write_bytes(b'previous document')
    saved.chmod(0o640)
    supported_mode = stat.S_IMODE(saved.stat().st_mode)
    assert save_csv(source, saved, edits, revision, fmt=fmt) == (
        saved.resolve(), file_revision(saved),
    )
    expected = [('1', 'keep', None, 'правка'), ('2', 'other', 'скрытая правка', 'строка\nещё'),
                *rows[2:]]
    assert read_page(saved, 0, fmt=fmt) == (headers, expected, False)
    assert stat.S_IMODE(saved.stat().st_mode) == supported_mode
    assert source.read_bytes() == original


@pytest.mark.parametrize('operation', ['save', 'export'])
@pytest.mark.parametrize('failure', ['cancel', 'revision'])
def test_wide_write_failure_preserves_existing_destination(tmp_path, monkeypatch, operation, failure):
    source = tmp_path / 'source.csv'
    write_source(source, ['id', 'note', 'group', 'payload'],
                 [('1', 'before', 'keep', 'x'), ('2', 'hidden', 'other', 'y')])
    original = source.read_bytes()
    revision = file_revision(source)
    destination = tmp_path / 'destination.csv'
    destination.write_bytes(b'previous document')
    token = Cancellation()
    write_records = core.write_csv_records

    def interrupted(*args, **kwargs):
        count = write_records(*args, **kwargs)
        if failure == 'cancel':
            token.cancel()
        else:
            source.write_bytes(original + b'3,external,other,z\r\n')
        return count

    monkeypatch.setattr(core, 'write_csv_records', interrupted)
    with pytest.raises(CancelledError if failure == 'cancel' else ValueError,
                       match=None if failure == 'cancel' else 'source CSV changed'):
        if operation == 'save':
            save_csv(source, destination, {(0, 1): 'edited'}, revision, cancel=token)
        else:
            export_edited_csv(source, destination, {(0, 1): 'edited'}, revision,
                              Selection(filters=(('group', 'keep'),)), cancel=token)
    assert destination.read_bytes() == b'previous document'
    assert set(tmp_path.iterdir()) == {source, destination}
    if failure == 'cancel':
        assert source.read_bytes() == original


@pytest.mark.parametrize('edited', [False, True])
def test_wide_export_encoding_error_is_atomic(tmp_path, edited):
    source = tmp_path / 'source.csv'
    write_source(source, ['id', 'note', 'group', 'payload'],
                 [('1', 'ok', 'keep', 'x'), ('2', '😀', 'keep', 'y')])
    destination = tmp_path / 'destination.csv'
    destination.write_bytes(b'previous export')
    selection = Selection(sort_column='id', descending=True, visible_columns=('note',))
    with pytest.raises(UnicodeEncodeError):
        if edited:
            export_edited_csv(source, destination, {(0, 1): 'edited'}, file_revision(source),
                              selection, output_format=CsvFormat(encoding='cp1251'))
        else:
            export_csv(source, destination, selection,
                       output_format=CsvFormat(encoding='cp1251'))
    assert destination.read_bytes() == b'previous export'
    assert set(tmp_path.iterdir()) == {source, destination}


def test_wide_consumers_share_current_values_and_view_positions(tmp_path):
    from csv_workbench.completion import complete_values
    from csv_workbench.core import find_csv_cell
    from csv_workbench.replace import preview_replacement
    from csv_workbench.validation import validate_csv_values

    source = tmp_path / 'source.csv'
    write_source(source, ['id', 'note', 'group', 'number'],
                 [('000', 'old', 'keep', '1'), ('001', 'hit', 'other', 'bad'),
                  ('002', 'OLD', 'keep', None), ('003', 'old', 'keep', '3')])
    edits = DiskEdits().updated([
        ((0, 1), 'edited'), ((3, 3), 'bad'), ((NEW_ROW, 0), '004'),
        ((NEW_ROW, 1), 'old'), ((NEW_ROW, 2), 'keep'), ((NEW_ROW, 3), '4'),
    ], cancel=Cancellation())
    selection = Selection(filters=(('group', 'keep'),), sort_column='id', descending=True,
                          visible_columns=('note',), column_types=(('number', 'integer', False),))
    assert find_csv_cell(source, selection, edits, ('note',), 'EDIT', (-1, -1), True,
                         cancel=Cancellation()) == (3, 0)
    assert complete_values(source, selection, edits, 'note', '', cancel=Cancellation()) == (
        ['OLD', 'edited', 'old'], False,
    )
    validation = validate_csv_values(source, selection, edits, cancel=Cancellation())
    assert validation.rows == 4 and validation.errors == (('number', 2),)
    plan = preview_replacement(source, selection, edits, 'old', 'new')
    assert plan.cells == plan.occurrences == 3
    changed = plan.prepare(edits, cancel=Cancellation())
    assert read_editable_page(source, 0, selection, changed.after, cancel=Cancellation())[1] == [
        (NEW_ROW, 'new'), (3, 'new'), (2, 'new'), (0, 'edited'),
    ]
    assert read_editable_page(source, 0, selection, changed.before, cancel=Cancellation())[1] == [
        (NEW_ROW, 'old'), (3, 'old'), (2, 'OLD'), (0, 'edited'),
    ]


def test_real_4000_columns_sort_edit_and_save_keep_production_memory_budget(tmp_path, monkeypatch):
    monkeypatch.setattr(core, 'WIDE_CSV_COLUMNS', 2048)
    connect = core.duckdb.connect

    def checked_connect(*args, **kwargs):
        assert kwargs['config']['memory_limit'] == '512MiB'
        return connect(*args, **kwargs)

    monkeypatch.setattr(core.duckdb, 'connect', checked_connect)
    source = tmp_path / 'wide.csv'
    headers = [f'n{index}' for index in range(4000)]
    rows = [(key, *(['payload'] * 3998), tail) for key, tail in
            [('003', None), ('001', ''), ('002', 'ёж 😀')]]
    write_source(source, headers, rows)
    original = source.read_bytes()
    selection = Selection(sort_column='n0')
    assert read_page(source, 0, selection) == (headers, [rows[1], rows[2], rows[0]], False)
    edits = DiskEdits().updated([((1, 3999), 'исправлено')], cancel=Cancellation())
    edited = (*rows[1][:-1], 'исправлено')
    page = read_editable_page(source, 0, selection, edits, cancel=Cancellation())
    assert page[0][1:] == headers
    assert page[1:] == ([(1, *edited), (2, *rows[2]), (0, *rows[0])], False)
    destination = tmp_path / 'saved.csv'
    save_csv(source, destination, edits, file_revision(source))
    assert read_page(destination, 0) == (headers, [rows[0], edited, rows[2]], False)
    assert source.read_bytes() == original
