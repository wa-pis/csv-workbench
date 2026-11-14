import pytest
from PySide6.QtCore import Qt

from csv_workbench.core import export_edited_csv, file_revision, read_page
from csv_workbench.ui import PageModel


def test_edit_survives_buffer_replacement_and_exports_all_rows(tmp_path):
    source = tmp_path / "source.csv"
    original = "id,label\n" + "".join(f"{i:03},old\n" for i in range(201)) + '201,\n202,""\n'
    source.write_text(original, encoding="utf-8")
    revision = file_revision(source)
    headers, rows, _ = read_page(source, 0)
    model = PageModel()
    model.editable = True
    model.replace(headers, rows)
    assert model.setData(model.index(0, 1), 'Привет, "мир"\nстрока')
    model.replace(headers, [("200", "old")], 200)
    assert model.setData(model.index(0, 1), "")
    model.replace(headers, rows)
    assert model.data(model.index(0, 1), Qt.ItemDataRole.EditRole) == 'Привет, "мир"\nстрока'

    output = tmp_path / "copy.csv"
    assert export_edited_csv(source, output, model.edits, revision) == 203
    assert read_page(output, 0)[1][0] == ("000", 'Привет, "мир"\nстрока')
    assert read_page(output, 200)[1] == [("200", None), ("201", None), ("202", None)]
    assert source.read_text(encoding="utf-8") == original
    with pytest.raises(ValueError, match="other than the source"):
        export_edited_csv(source, source, model.edits, revision)
    source.write_text("id,label\n000,external change\n", encoding="utf-8")
    with pytest.raises(ValueError, match="source CSV changed"):
        export_edited_csv(source, output, model.edits, revision)


def test_duplicate_records_and_colliding_internal_header_keep_separate_ids(tmp_path):
    from csv_workbench.core import Cancellation, Selection, read_editable_page

    source = tmp_path / "source.csv"
    source.write_text("__csv_source_row,label\nsame,yes\nsame,yes\nsame,no\n", encoding="utf-8")
    selection = Selection((("label", "yes"),), visible_columns=("__csv_source_row",))
    headers, rows, more = read_editable_page(
        source, 0, selection, {(1, 1): "no", (2, 1): "YES"}, cancel=Cancellation()
    )
    assert headers[0] != headers[1]
    assert rows == [(0, "same"), (2, "same")]
    assert not more


def test_save_in_place_is_atomic_and_rejects_stale_revision(tmp_path):
    from concurrent.futures import CancelledError

    from csv_workbench.core import Cancellation, save_csv

    source = tmp_path / "source.csv"
    source.write_text("id,value\n1,old\n2,hidden\n", encoding="utf-8")
    revision = file_revision(source)
    token = Cancellation()
    token.cancel()
    with pytest.raises(CancelledError):
        save_csv(source, source, {(0, 1): "new"}, revision, cancel=token)
    assert read_page(source, 0)[1] == [("1", "old"), ("2", "hidden")]
    assert save_csv(source, source, {(0, 1): "new"}, revision) == (
        source.resolve(),
        file_revision(source),
    )
    with pytest.raises(ValueError, match="source CSV changed"):
        save_csv(source, source, {(0, 1): "stale"}, revision)
    assert read_page(source, 0)[1] == [("1", "new"), ("2", "hidden")]
