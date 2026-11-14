import csv

import pytest

from csv_workbench.core import Selection, count_rows, export_csv, prepare_result, read_page


@pytest.mark.parametrize(
    ("encoding", "value"),
    [
        ("utf-8-sig", "Привет, мир"),
        ("utf-16", "Привет, мир"),
        ("utf-32", "Привет, мир"),
        ("cp1251", "Привет, мир! Это проверка русского текста для таблицы"),
        ("cp1252", "Café, déjà vu, français et espagnol à la carte"),
    ],
)
def test_detected_encoding_is_used_across_operations(tmp_path, encoding, value):
    source = tmp_path / "source.csv"
    original = f'id,text\r\n001,"{value}"\r\n002,other\r\n'.encode(encoding)
    source.write_bytes(original)
    selection = Selection((("text", value.upper()),), sort_column="id")

    expected = (["id", "text"], [("001", value)], False)
    assert read_page(source, 0, selection) == expected
    assert count_rows(source) == 2
    result = prepare_result(source, selection)
    with result.scratch:
        assert result.page(0) == expected
    unique = prepare_result(source, selection, distinct="text")
    with unique.scratch:
        assert unique.page(0) == (["Value", "Frequency"], [(value, 1)], False)

    output = tmp_path / "export.csv"
    assert export_csv(source, output, selection) == 1
    with output.open(encoding="utf-8", newline="") as stream:
        assert list(csv.reader(stream)) == [["id", "text"], ["001", value]]
    assert source.read_bytes() == original


def test_binary_input_is_rejected(tmp_path):
    source = tmp_path / "binary.csv"
    source.write_bytes(b"id,text\x00\xff")
    with pytest.raises(ValueError, match="encoding"):
        read_page(source, 0)
