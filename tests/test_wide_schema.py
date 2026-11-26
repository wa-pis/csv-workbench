"""Bound schema sniffing and wide counts without changing CSV semantics."""

import codecs
import csv
import os
from concurrent.futures import CancelledError
from contextlib import closing

import pytest

from csv_workbench import core
from csv_workbench.core import Cancellation, CsvFormat, Selection


def write_csv(path, headers, rows, fmt, newline='\r\n', *, blank_lines=0):
    with path.open('w', encoding=fmt.encoding or 'utf-8', newline='') as handle:
        handle.write(newline * blank_lines)
        writer = csv.writer(handle, delimiter=fmt.delimiter, quotechar=fmt.quote,
                            quoting=csv.QUOTE_NOTNULL, lineterminator=newline)
        if fmt.header:
            writer.writerow(headers)
        writer.writerows(rows)


@pytest.mark.parametrize('fmt', [
    CsvFormat(encoding='utf-8-sig'),
    CsvFormat(';', encoding='cp1251', quote="'"),
    CsvFormat('\t', False, 'utf-16'),
], ids=['bom-duplicates', 'legacy-custom-quote', 'headerless-utf16'])
@pytest.mark.parametrize('newline', ['\r', '\n', '\r\n'], ids=['cr', 'lf', 'crlf'])
def test_bounded_metadata_keeps_canonical_names_format_and_wide_counts(
    tmp_path, monkeypatch, fmt, newline,
):
    path = tmp_path / ("schema[]'s.csv" if os.name == 'nt' else "schema[?]*'s.csv")
    headers = ['dup', 'DUP', '', 'value']
    rows = [('0001', None, '', f"ёж{fmt.delimiter}{fmt.quote}чай\rстрока\nещё"),
            ('0002', '', None, 'plain'), ('0003', 'tail', '', None)]
    write_csv(path, headers, rows, fmt, newline, blank_lines=100)
    original = path.read_bytes()
    token = Cancellation()
    with core.utf8_source(path, token, fmt) as decoded, token.connection() as connection:
        canonical = [column[0] for column in connection.execute(
            'SELECT * FROM ' + fmt.source + ' LIMIT 0', [core.csv_pattern(decoded)],
        ).description]
    monkeypatch.setattr(core, 'CSV_SCHEMA_BYTES', 64)
    monkeypatch.setattr(core, 'WIDE_CSV_COLUMNS', 3)

    assert core.read_columns(path, fmt=fmt) == canonical
    assert core.read_page(path, 0, fmt=fmt, limit=2) == (canonical, rows[:2], True)
    assert core.count_rows(path, fmt=fmt) == 3
    assert core.read_page(path, None, fmt=fmt) == (canonical, rows, False, 0)
    assert path.read_bytes() == original


def test_schema_sample_is_complete_bounded_and_shared_by_query_builders(tmp_path, monkeypatch):
    path = tmp_path / 'source.csv'
    fmt = CsvFormat()
    rows = [(f'{index:03}', 'a,b\ncontinued', '', None) for index in range(100)]
    write_csv(path, ['id', 'note', 'empty', 'null'], rows, fmt)
    monkeypatch.setattr(core, 'CSV_SCHEMA_BYTES', 256)
    samples = []
    csv_pattern = core.csv_pattern

    def checked_pattern(source):
        if source.name == 'sample.csv':
            data = source.read_bytes()
            assert len(data) < 300
            assert data.endswith(b'\r\n')
            samples.append(source)
        return csv_pattern(source)

    monkeypatch.setattr(core, 'csv_pattern', checked_pattern)
    token = Cancellation()
    selection = Selection(visible_columns=('note',))
    with token.connection() as connection:
        assert core.csv_columns(connection, path, fmt, cancel=token) == [
            'id', 'note', 'empty', 'null',
        ]
        sql, params = core.selection_query(connection, path, selection)
        assert connection.execute(sql, params).fetchone() == ('a,b\ncontinued',)
        sql, params = core.editable_query(connection, path, selection, {}, cancel=token)
        assert connection.execute(sql, params).fetchone() == (0, 'a,b\ncontinued')
    assert len(samples) == 3 and not any(sample.exists() for sample in samples)


@pytest.mark.parametrize('action', ['metadata', 'count'])
def test_cancelled_schema_or_count_cleans_resources_and_preserves_source(
    tmp_path, monkeypatch, action,
):
    path = tmp_path / 'source.csv'
    write_csv(path, ['id', 'note', 'empty', 'null'],
              [(str(index), 'ёж' * 100, '', None) for index in range(10)],
              CsvFormat(encoding='cp1251'))
    original = path.read_bytes()
    monkeypatch.setattr(core, 'CSV_SCHEMA_BYTES', 128)
    monkeypatch.setattr(core, 'WIDE_CSV_COLUMNS', 3)
    temporary_directory = core.TemporaryDirectory
    monkeypatch.setattr(core, 'TemporaryDirectory',
                        lambda **kwargs: temporary_directory(dir=tmp_path, **kwargs))
    token = Cancellation()
    if action == 'metadata':
        csv_blocks = core.csv_blocks

        def interrupted(*args, **kwargs):
            with closing(csv_blocks(*args, **kwargs)) as blocks:
                block = next(blocks)
                token.cancel()
                yield block

        monkeypatch.setattr(core, 'csv_blocks', interrupted)
        operation = core.read_columns
    else:
        iter_csv_rows = core.iter_csv_rows

        def interrupted(*args):
            with closing(iter_csv_rows(*args)) as records:
                yield next(records)
                token.cancel()
                yield next(records)

        monkeypatch.setattr(core, 'iter_csv_rows', interrupted)
        operation = core.count_rows
    with pytest.raises(CancelledError):
        operation(path, fmt=CsvFormat(encoding='cp1251'), cancel=token)
    assert path.read_bytes() == original
    assert set(tmp_path.iterdir()) == {path}


def test_bounded_metadata_preserves_explicit_wide_preamble_rejection(tmp_path, monkeypatch):
    path = tmp_path / 'preamble.csv'
    write_csv(path, ['id', 'note', 'empty', 'null'],
              [(str(index), 'value', '', None) for index in range(100)], CsvFormat())
    path.write_bytes(codecs.BOM_UTF8 + b'Generated report\r\n' + path.read_bytes())
    monkeypatch.setattr(core, 'CSV_SCHEMA_BYTES', 128)
    monkeypatch.setattr(core, 'WIDE_CSV_COLUMNS', 3)
    for operation in (core.read_page, core.count_rows):
        args = (path, 0) if operation is core.read_page else (path,)
        with pytest.raises(ValueError, match='does not support a nonempty preamble'):
            operation(*args)


def test_comparison_uses_shared_bounded_metadata(tmp_path, monkeypatch):
    from csv_workbench.compare import compare_csv

    left, right = tmp_path / 'left.csv', tmp_path / 'right.csv'
    headers = ['id', 'note', 'empty', 'null']
    rows = [(str(index), 'value', '', None) for index in range(50)]
    write_csv(left, headers, rows, CsvFormat())
    write_csv(right, headers, rows, CsvFormat())
    monkeypatch.setattr(core, 'CSV_SCHEMA_BYTES', 128)
    result, counts, page = compare_csv(left, right, 'id')
    with result.scratch:
        assert counts == {} and page[1] == []
