import errno
from concurrent.futures import CancelledError

import pytest

from csv_workbench.core import Cancellation, Selection, prepare_result


def test_cached_sort_reads_later_pages_without_csv(tmp_path):
    source = tmp_path / "input.csv"
    source.write_text(
        "id,label\n" + "".join(f"{i:04},keep\n" for i in range(405)) + "9999,exclude\n"
    )
    result = prepare_result(source, Selection((("label", "keep"),), "id", True, ("id",)))
    with result.scratch:
        source.unlink()
        assert result.page(0)[1][0] == ("0404",)
        assert result.page(200)[1][0] == ("0204",)
        assert result.page(400) == (["id"], [(f"{i:04}",) for i in range(4, -1, -1)], False)


@pytest.mark.parametrize("singletons", [False, True])
def test_distinct_counts_all_matching_rows_in_hidden_column(tmp_path, singletons):
    source = tmp_path / "input.csv"
    source.write_text("id,label,value\n" + "0,keep,x\n" * 201 + "1,keep,y\n2,keep,\n3,skip,y\n")
    selection = Selection((("label", "keep"),), visible_columns=("id",))
    result = prepare_result(source, selection, distinct="value", singletons=singletons)
    with result.scratch:
        expected = [("y", 1), (None, 1)]
        if not singletons:
            expected.insert(0, ("x", 201))
        assert result.page(0) == (["Value", "Frequency"], expected, False)


def test_cancelled_result_leaves_no_files(tmp_path, monkeypatch):
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    import tempfile

    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    source = tmp_path / "input.csv"
    source.write_bytes(b"id\n1\n")
    token = Cancellation()
    token.cancel()
    with pytest.raises(CancelledError):
        prepare_result(source, Selection(sort_column="id"), cancel=token)
    assert list(tmp_path.iterdir()) == [source]


def test_no_space_preserves_source(tmp_path, monkeypatch):
    source = tmp_path / "input.csv"
    original = b"id\n1\n"
    source.write_bytes(original)

    def full(**kwargs):
        raise OSError(errno.ENOSPC, "No space")

    monkeypatch.setattr("csv_workbench.core.TemporaryDirectory", full)
    with pytest.raises(OSError, match="No space"):
        prepare_result(source, Selection(sort_column="id"))
    assert source.read_bytes() == original


def test_polars_staging_matches_duckdb_ties_and_csv_quoting(tmp_path):
    from csv_workbench.core import read_page

    source = tmp_path / "data[1].csv"
    source.write_bytes(
        b'\xef\xbb\xbfid,key,text\r\n003,b,"line\r\nnext"\r\n'
        b'001,b,""\r\n002,a,"comma,here"\r\n004,,\r\n'
    )
    selection = Selection(sort_column="key", visible_columns=("id", "text"))
    result = prepare_result(source, selection)
    with result.scratch:
        assert result.page(0) == read_page(source, 0, selection)


def test_csv_blocks_keep_quoted_multiline_records_together():
    from io import BytesIO

    from csv_workbench.core import csv_blocks

    records = [b"id,text\r\n", b'001,"a\n\n\nb""c"\r\n', b"002,z"]
    blocks = list(csv_blocks(BytesIO(b"".join(records)), Cancellation(), block_bytes=5))
    assert blocks == records


def test_custom_quote_blocks_ignore_apostrophes_in_unquoted_fields():
    from io import BytesIO

    from csv_workbench.core import csv_blocks

    records = [b"1;O'Brien\n", b"2;'multi\nline and ''quoted'''\n", b"3;other\n"]
    for size in range(1, 35):
        blocks = list(
            csv_blocks(
                BytesIO(b"".join(records)), Cancellation(), block_bytes=size, quote=b"'", delimiter=b";"
            )
        )
        assert b"".join(blocks) == b"".join(records)
        assert all(block.endswith(tuple(records)) for block in blocks)
