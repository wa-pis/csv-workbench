import csv

import pytest

from csv_workbench.core import Selection, export_csv, read_page


def test_export_full_selection(tmp_path):
    source = tmp_path / "source.csv"
    source.write_text(
        "id,filter,note\n"
        + "".join(f'{i:05},Match,"Привет, {i}"\n' for i in range(1, 202))
        + "99999,other,excluded\n",
        encoding="utf-8",
    )
    destination = tmp_path / "result's.csv"
    selection = Selection((("filter", "match"),), "id", True, ("id", "note"))

    assert len(read_page(source, 0, selection)[1]) == 200
    assert export_csv(source, destination, selection) == 201
    with destination.open(encoding="utf-8", newline="") as handle:
        assert list(csv.reader(handle)) == [
            ["id", "note"],
            *[[f"{i:05}", f"Привет, {i}"] for i in range(201, 0, -1)],
        ]


def test_export_empty_selection(tmp_path):
    source = tmp_path / "source.csv"
    source.write_text("id,label\n001,match\n", encoding="utf-8")
    destination = tmp_path / "result.csv"
    assert (
        export_csv(source, destination, Selection((("label", "absent"),), visible_columns=("id",)))
        == 0
    )
    assert destination.read_text(encoding="utf-8") == "id\n"


def test_export_cannot_replace_source_or_alias(tmp_path):
    source = tmp_path / "source.csv"
    original = b"id\n001\n"
    source.write_bytes(original)
    alias = tmp_path / "alias.csv"
    alias.hardlink_to(source)
    for destination in (source, alias):
        with pytest.raises(ValueError, match="other than the source"):
            export_csv(source, destination, Selection())
        assert destination.read_bytes() == original


def test_failed_export_keeps_destination(tmp_path):
    source = tmp_path / "source.csv"
    source.write_text("id\n001\n", encoding="utf-8")
    destination = tmp_path / "result.csv"
    destination.write_bytes(b"previous export")
    with pytest.raises(ValueError, match="Unknown CSV column"):
        export_csv(source, destination, Selection(sort_column="missing"))
    assert destination.read_bytes() == b"previous export"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["result.csv", "source.csv"]


@pytest.mark.parametrize("delimiter", ["\t", "|"])
def test_configured_export_includes_all_matching_rows_and_escapes_values(tmp_path, delimiter):
    from csv_workbench.core import CsvFormat

    source = tmp_path / "source.csv"
    labels = [f"Привет {n}{delimiter}'да'\nещё" for n in range(201)]
    with source.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter=";")
        writer.writerow(["id", "label", "group"])
        writer.writerows((f"{n:03}", value, "yes") for n, value in enumerate(labels))
        writer.writerow(["999", "excluded", "no"])
    destination = tmp_path / "result"
    selection = Selection((("group", "yes"),), "id", True, ("label",))
    output_format = CsvFormat(delimiter, False, "cp1251", "'")
    count = export_csv(
        source, destination, selection, fmt=CsvFormat(";"), output_format=output_format
    )
    assert count == 201
    with destination.open(encoding="cp1251", newline="") as handle:
        assert list(csv.reader(handle, delimiter=delimiter, quotechar="'")) == [
            [label] for label in reversed(labels)
        ]
    assert "''да''".encode("cp1251") in destination.read_bytes()


@pytest.mark.parametrize("edited", [False, True])
def test_export_encoding_failure_preserves_existing_destination(tmp_path, edited):
    from csv_workbench.core import CsvFormat, export_edited_csv, file_revision

    source = tmp_path / "source.csv"
    source.write_text("id,label\n1,ok\n2,🙂\n", encoding="utf-8")
    destination = tmp_path / "result.csv"
    destination.write_bytes(b"previous export")
    output_format = CsvFormat(encoding="cp1251")
    with pytest.raises(UnicodeEncodeError):
        if edited:
            export_edited_csv(
                source, destination, {(0, 1): "edit"}, file_revision(source),
                output_format=output_format,
            )
        else:
            export_csv(source, destination, Selection(), output_format=output_format)
    assert destination.read_bytes() == b"previous export"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["result.csv", "source.csv"]


def test_empty_export_without_header(tmp_path):
    from csv_workbench.core import CsvFormat

    source = tmp_path / "source.csv"
    source.write_text("id,label\n001,match\n", encoding="utf-8")
    destination = tmp_path / "result.tsv"
    selection = Selection((("label", "absent"),))
    assert export_csv(
        source, destination, selection, output_format=CsvFormat("\t", False, "utf-16")
    ) == 0
    assert destination.read_bytes() == b""
