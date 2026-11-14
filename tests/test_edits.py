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
