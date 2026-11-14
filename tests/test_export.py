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
