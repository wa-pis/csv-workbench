import pytest

from csv_workbench.core import (
    Cancellation,
    CsvFormat,
    Selection,
    count_rows,
    detect_delimiter,
    export_csv,
    file_revision,
    prepare_editable_result,
    prepare_result,
    preview_format,
    query_csv,
    read_format_sample,
    read_page,
    save_csv,
)
from csv_workbench.session import load_session, save_session


@pytest.mark.parametrize(("delimiter", "header"), [(";", True), ("\t", False), (",", False), ("|", True), ("'", False)])
def test_format_is_shared_by_preview_sort_unique_export_and_save(tmp_path, delimiter, header):
    path = tmp_path / "input.csv"
    prefix = f"id{delimiter}label\r\n" if header else ""
    path.write_bytes((prefix + f'2{delimiter}"чай, кофе"\r\n1{delimiter}чaj\r\n').encode("utf-16"))
    fmt = CsvFormat(delimiter, header)
    sample = read_format_sample(path)
    if delimiter in ",;\t":
        assert detect_delimiter(sample) == delimiter
    columns = ["id", "label"] if header else ["column0", "column1"]
    assert preview_format(sample, fmt) == (columns, [["2", "чай, кофе"], ["1", "чaj"]])
    assert count_rows(path, fmt=fmt) == 2
    assert read_page(path, 0, fmt=fmt)[0] == columns
    selection = Selection(sort_column=columns[0], visible_columns=(columns[1],))
    assert read_page(path, 0, selection, fmt=fmt)[1] == [("чaj",), ("чай, кофе",)]
    result = prepare_editable_result(path, selection, {(0, 0): "0"}, cancel=Cancellation(), fmt=fmt)
    with result.scratch:
        assert result.page(0)[1] == [(0, "чай, кофе"), (1, "чaj")]
    unique = prepare_result(path, Selection(), distinct=columns[1], fmt=fmt)
    with unique.scratch:
        assert dict(unique.page(0)[1]) == {"чай, кофе": 1, "чaj": 1}
    assert query_csv(path, "SELECT count(*) FROM csv_data", fmt=fmt)[1] == [(2,)]

    output = tmp_path / "export.csv"
    export_csv(path, output, selection, fmt=fmt)
    assert read_page(output, 0) == ([columns[1]], [("чaj",), ("чай, кофе",)], False)
    save_csv(path, path, {(0, 1): "new"}, file_revision(path), fmt=fmt)
    assert read_page(path, 0, fmt=fmt) == (columns, [("2", "new"), ("1", "чaj")], False)
    session = tmp_path / "saved.csvsession"
    save_session(session, path, selection, fmt)
    assert load_session(session) == (path.resolve(), selection, fmt)


def test_manual_format_and_bounded_preview():
    sample = ('1;"line\ncontinued"\n2;next\n3;"partial\n', True)
    assert detect_delimiter(sample) == ";"
    assert preview_format(sample, CsvFormat(";", False))[1] == [
        ["1", "line\ncontinued"],
        ["2", "next"],
    ]
    assert preview_format(sample, CsvFormat(";", True))[0] == ["1", "line\ncontinued"]
    assert len(preview_format(("a,b\n" * 40, False), CsvFormat())[1]) == 10


def test_old_session_defaults_to_comma_and_header(tmp_path):
    import json

    session = tmp_path / "old.csvsession"
    source = tmp_path / "source.csv"
    save_session(session, source, Selection())
    data = json.loads(session.read_text(encoding="utf-8"))
    del data["format"]
    session.write_text(json.dumps(data), encoding="utf-8")
    assert load_session(session) == (source.resolve(), Selection(), CsvFormat())


@pytest.mark.parametrize("delimiter", ["", "||", '"', "\n", "\x00", "§", None])
def test_invalid_custom_delimiter(delimiter):
    with pytest.raises(ValueError, match="delimiter"):
        CsvFormat(delimiter)
