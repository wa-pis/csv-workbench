"""Wide CSV preview keeps page and CSV-format contracts under a fixed memory budget."""

import csv
import io
from concurrent.futures import CancelledError
from functools import partial

import pytest

from csv_workbench import core
from csv_workbench.core import Cancellation, CsvFormat, read_page

WIDTH = 4000


@pytest.fixture(autouse=True)
def limited_duckdb_memory(monkeypatch):
    connect = core.duckdb.connect

    def limited_connect(*args, **kwargs):
        kwargs['config'] = {**kwargs.get('config', {}), 'memory_limit': '512MiB'}
        return connect(*args, **kwargs)

    monkeypatch.setattr(core.duckdb, 'connect', limited_connect)


def write_wide(path, headers, rows, fmt=None):
    fmt = fmt or CsvFormat()
    with path.open('w', encoding=fmt.encoding or 'utf-8', newline='') as handle:
        writer = csv.writer(handle, delimiter=fmt.delimiter, quotechar=fmt.quote,
                            quoting=csv.QUOTE_NOTNULL, lineterminator='\r\n')
        if fmt.header:
            writer.writerow(headers)
        writer.writerows(rows)


def test_wide_preview_next_page_tail_and_past_end(tmp_path):
    path = tmp_path / "wide[1]'s.csv"
    headers = [f'n{i}' for i in range(WIDTH)]
    rows = [(str(row), *(['x'] * (WIDTH - 1))) for row in range(405)]
    write_wide(path, headers, rows)
    original = path.read_bytes()

    assert read_page(path, 0) == (headers, rows[:200], True)
    assert read_page(path, 200) == (headers, rows[200:400], True)
    assert read_page(path, 400) == (headers, rows[400:], False)
    assert read_page(path, None) == (headers, rows[200:], False, 200)
    assert read_page(path, 1000) == (headers, [], False)
    assert path.read_bytes() == original


@pytest.mark.parametrize(('fmt', 'text'), [
    (CsvFormat(), '漢字, 😀 "чай"\ncontinued' + 'x' * 200_000),
    (CsvFormat(';', encoding='cp1251', quote="'"), "ёж; 'чай'\nстрока"),
    (CsvFormat('\t', False), '漢字 чай'),
], ids=['utf8-long-field', 'cp1251-custom-quote', 'headerless-tsv'])
def test_wide_preview_preserves_format_null_empty_and_multiline(tmp_path, monkeypatch, fmt, text):
    path = tmp_path / 'format.csv'
    headers = ['dup', 'dup', *[f'n{i}' for i in range(2, WIDTH)]]
    row = (None, '', text, '00012', *(['x'] * (WIDTH - 7)), None, '', None)
    rows = [row, ('NULL', 'tail', *row[2:]), row]
    write_wide(path, headers, rows, fmt)
    # Cross several input blocks, including the header and a quoted multiline value.
    monkeypatch.setattr(core, 'csv_blocks', partial(core.csv_blocks, block_bytes=16 * 1024))
    expected_headers = (['dup', 'dup_1', *headers[2:]] if fmt.header else
                        [f'column{i:04}' for i in range(WIDTH)])

    assert read_page(path, 0, fmt=fmt, limit=2) == (expected_headers, rows[:2], True)
    assert read_page(path, 2, fmt=fmt, limit=2) == (expected_headers, rows[2:], False)
    assert read_page(path, None, fmt=fmt) == (expected_headers, rows, False, 0)


def test_wide_header_only_keeps_canonical_columns(tmp_path):
    path = tmp_path / 'header.csv'
    headers = [f'n{i}' for i in range(WIDTH)]
    write_wide(path, headers, [])
    assert read_page(path, 0) == (headers, [], False)
    assert read_page(path, None) == (headers, [], False, 0)


def test_cancel_after_wide_parse_discards_result_and_cleans_temporaries(tmp_path, monkeypatch):
    path = tmp_path / 'legacy.csv'
    fmt = CsvFormat(';', encoding='cp1251', quote="'")
    write_wide(path, [f'n{i}' for i in range(WIDTH)], [('ёж',) * WIDTH], fmt)
    original = path.read_bytes()
    temporary_directory = core.TemporaryDirectory
    monkeypatch.setattr(core, 'TemporaryDirectory',
                        lambda **kwargs: temporary_directory(dir=tmp_path, **kwargs))
    cancel = Cancellation()
    parse = core.format_records
    parsed = []

    def cancel_after_parse(*args, **kwargs):
        records = parse(*args, **kwargs)
        parsed.append(len(records))
        cancel.cancel()
        return records

    monkeypatch.setattr(core, 'format_records', cancel_after_parse)
    with pytest.raises(CancelledError):
        read_page(path, 0, fmt=fmt, cancel=cancel)

    assert len(parsed) == 1 and parsed[0] > 0
    assert set(tmp_path.iterdir()) == {path}
    assert path.read_bytes() == original


@pytest.mark.parametrize('width', [2048, 2049])
def test_preview_width_boundary_preserves_cells_and_column_names(tmp_path, width):
    path = tmp_path / f'boundary-{width}.csv'
    headers = [f'n{i}' for i in range(width)]
    row = ('0001', None, '', 'ёж 漢字 😀', 'NULL', '"quoted"', *(['x'] * (width - 6)))
    rows = [row, ('0002', *row[1:])]
    write_wide(path, headers, rows)

    assert read_page(path, 0) == (headers, rows, False)
    assert read_page(path, 1) == (headers, rows[1:], False)


def test_wide_csv_filter_sort_and_hidden_columns_apply_to_unloaded_rows(tmp_path):
    path = tmp_path / 'selection.csv'
    headers = ['id', 'label', *[f'n{i}' for i in range(2, WIDTH)]]
    rows = [(f'{row:05}', 'ПРИВЕТ' if row % 2 == 0 else 'other',
             *(['x'] * (WIDTH - 2))) for row in range(405)]
    write_wide(path, headers, rows)
    selection = core.Selection(filters=(('label', 'привет'),), sort_column='id',
                               descending=True, visible_columns=('id',))
    expected = [(f'{row:05}',) for row in range(404, -1, -2)]

    assert read_page(path, 0, selection) == (['id'], expected[:200], True)
    assert read_page(path, 200, selection) == (['id'], expected[200:], False)


def test_wide_preview_skips_blank_lines_before_header_and_between_records(tmp_path):
    path = tmp_path / 'blank-lines.csv'
    headers = [f'n{i}' for i in range(WIDTH)]
    rows = [('001', None, '', 'ёж 😀', *(['x'] * (WIDTH - 4))),
            ('002', '', None, '漢字', *(['x'] * (WIDTH - 4)))]
    write_wide(path, headers, rows)
    path.write_bytes(b'\r\n\r\n' + path.read_bytes().replace(b'\r\n', b'\r\n\r\n'))

    assert read_page(path, 0, limit=1) == (headers, rows[:1], True)
    assert read_page(path, 1) == (headers, rows[1:], False)
    assert read_page(path, None) == (headers, rows, False, 0)


@pytest.mark.parametrize(('delimiter', 'quote'), [('|', '^'), (']', '['), ('\\', '-'), (';', '\\')])
def test_format_records_metacharacters_preserve_null_and_quoted_values(delimiter, quote):
    row = [None, '', f'left{delimiter}right', f'one{quote}two', 'line\nnext', None, '', None]
    rows = [row, list(reversed(row))]
    stream = io.StringIO(newline='')
    writer = csv.writer(stream, delimiter=delimiter, quotechar=quote,
                        quoting=csv.QUOTE_NOTNULL, lineterminator='\r\n')
    writer.writerows(rows)

    assert core.format_records((stream.getvalue(), False), delimiter, quote=quote,
                               nullable=True, limit=None) == rows


@pytest.mark.parametrize('newline', ['\r', '\r\n', '\n'], ids=['cr', 'crlf', 'lf'])
@pytest.mark.parametrize(('delimiter', 'quote'), [(',', '"'), (';', "'")],
                         ids=['default-quote', 'custom-quote'])
def test_csv_blocks_keep_complete_records_at_every_small_boundary(newline, delimiter, quote):
    rows = [['id', 'text', 'null', 'empty'],
            ['1', f'left{delimiter}right{quote}quoted\rnext\nlast\r\nline', None, ''],
            ['2', 'tail', '', None]]
    records = []
    for row in rows:
        stream = io.StringIO(newline='')
        csv.writer(stream, delimiter=delimiter, quotechar=quote, quoting=csv.QUOTE_NOTNULL,
                   lineterminator=newline).writerow(row)
        records.append(stream.getvalue().encode())
    records[-1] = records[-1][:-len(newline)]
    data = b''.join(records)
    boundaries = {sum(map(len, records[:index])) for index in range(1, len(records) + 1)}

    for block_bytes in range(1, 41):
        blocks = list(core.csv_blocks(io.BytesIO(data), Cancellation(), block_bytes=block_bytes,
                                      delimiter=delimiter.encode(), quote=quote.encode()))
        assert b''.join(blocks) == data, block_bytes
        assert all(blocks), block_bytes
        position = 0
        actual = []
        for block in blocks:
            position += len(block)
            assert position in boundaries, block_bytes
            actual.extend(core.format_records((block.decode(), False), delimiter, quote=quote,
                                               nullable=True, limit=None))
        assert actual == rows, block_bytes


def test_wide_preview_cr_only_pages_preserve_quoted_newlines(tmp_path, monkeypatch):
    path = tmp_path / 'cr-only.csv'
    headers = [f'n{i}' for i in range(WIDTH)]
    rows = [('001', 'first\rsecond\nthird', None, '', *(['x'] * (WIDTH - 4))),
            ('002', 'tail', '', None, *(['x'] * (WIDTH - 4)))]
    with path.open('w', newline='') as handle:
        writer = csv.writer(handle, quoting=csv.QUOTE_NOTNULL, lineterminator='\r')
        writer.writerow(headers)
        writer.writerows(rows)
    monkeypatch.setattr(core, 'csv_blocks', partial(core.csv_blocks, block_bytes=16 * 1024))

    assert read_page(path, 0, limit=1) == (headers, rows[:1], True)
    assert read_page(path, 1, limit=1) == (headers, rows[1:], False)
    assert read_page(path, None) == (headers, rows, False, 0)


def test_wide_preview_rejects_nonempty_preamble(tmp_path):
    path = tmp_path / 'preamble.csv'
    write_wide(path, [f'n{i}' for i in range(WIDTH)], [('x',) * WIDTH])
    path.write_bytes(b'Generated report\r\n' + path.read_bytes())

    with pytest.raises(ValueError, match='Wide CSV preview does not support a nonempty preamble'):
        read_page(path, 0)
