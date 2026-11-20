from concurrent.futures import CancelledError
from pathlib import Path

import pytest

from csv_workbench.core import Cancellation, CsvFormat, Selection
from csv_workbench.replace import preview_replacement, replace_literal


@pytest.mark.parametrize("before,needle,replacement,expected,count", [
    ("ΟΣ İt οσ it", "οσ it", "X", "X X", 2),
    ("aaa", "aa", "b", "ba", 1),
    ("A%_a%_", "%_", "$\\1", "A$\\1a$\\1", 2),
    ("Hi hi", "hi", "HIhi", "HIhi HIhi", 2),
    ("Привет\nПРИВЕТ", "привет", "", "\n", 2),
])
def test_literal_replacement_semantics(before, needle, replacement, expected, count):
    assert replace_literal(before, needle, replacement) == (expected, count)


def test_preview_full_view_respects_edits_filters_columns_and_sort(tmp_path):
    source = tmp_path / "data.csv"
    source.write_text("id,note,group,hidden\n" + "".join(
        f"{i:04},hit HIT,yes,hit\n" for i in range(1201)
    ) + "9999,hit,no,hit\n")
    edits = {(1200, 1): "edited HiT", (1199, 2): "no"}
    selection = Selection((("group", "yes"),), "id", True, ("id", "note"))
    plan = preview_replacement(source, selection, edits, "hit", "new")
    try:
        assert plan.cells == 1200 and plan.occurrences == 2399
        assert len(plan.preview) == 100
        assert plan.preview[0] == (1, "note", "edited HiT", "edited new")
        changes = list(plan.iter_changes(cancel=Cancellation()))
        assert len(changes) == 1200
        assert changes[0] == ((1200, 1), "edited HiT", "edited new")
        assert changes[-1] == ((0, 1), "hit HIT", "new new")
        assert all(column == 1 for (_, column), _, _ in changes)
    finally:
        plan.scratch.cleanup()


def test_first_replaces_one_occurrence_in_first_cell_in_view_order(tmp_path):
    source = tmp_path / "data.csv"
    source.write_text("id,note,other\n1,hit hit,hit\n2,HIT hit,hit\n")
    plan = preview_replacement(
        source, Selection(sort_column="id", descending=True), {}, "hit", "new", first=True,
    )
    try:
        assert plan.occurrences == plan.cells == 1
        assert list(plan.iter_changes(cancel=Cancellation())) == [((1, 1), "HIT hit", "new hit")]
    finally:
        plan.scratch.cleanup()


def test_null_empty_output_noop_and_manual_encoding(tmp_path):
    source = tmp_path / "data.csv"
    source.write_bytes("id;note\n1;\n2;Привет\n".encode("cp1251"))
    fmt = CsvFormat(";", encoding="cp1251")
    plan = preview_replacement(source, Selection(), {}, "привет", "", fmt=fmt)
    try:
        assert list(plan.iter_changes(cancel=Cancellation())) == [((1, 1), "Привет", None)]
    finally:
        plan.scratch.cleanup()
    plan = preview_replacement(source, Selection(), {}, "Привет", "Привет", fmt=fmt)
    try:
        assert plan.occurrences == 1 and plan.cells == 0
    finally:
        plan.scratch.cleanup()


def test_cancelled_or_invalid_preparation_does_not_change_csv(tmp_path):
    source = tmp_path / "data.csv"
    source.write_text("note\nhit\n")
    cancel = Cancellation()
    cancel.cancel()
    with pytest.raises(CancelledError):
        preview_replacement(source, Selection(), {}, "hit", "new", cancel=cancel)
    with pytest.raises(ValueError, match="nonempty"):
        preview_replacement(source, Selection(), {}, "", "new")
    assert source.read_text() == "note\nhit\n"


def test_inserted_deleted_rows_and_column_order(tmp_path):
    from csv_workbench.core import DELETED_COLUMN, NEW_ROW

    source = tmp_path / 'data.csv'
    source.write_text('a,b\nhit,hit\nhit,hit\n')
    edits = {(0, DELETED_COLUMN): '1', (NEW_ROW, 0): 'new hit', (NEW_ROW, 1): 'hit'}
    plan = preview_replacement(source, Selection(column_order=('b', 'a')), edits, 'hit', 'x')
    try:
        changes = list(plan.iter_changes(cancel=Cancellation()))
        assert [key for key, _, _ in changes] == [(1, 1), (1, 0), (NEW_ROW, 1), (NEW_ROW, 0)]
    finally:
        plan.scratch.cleanup()


def test_cancel_during_scan_cleans_plan(tmp_path, monkeypatch):
    import csv_workbench.replace as module

    source = tmp_path / 'data.csv'
    source.write_text('note\nhit\nhit\n')
    cancel = Cancellation()
    original = module.replace_literal
    scratches = []
    temporary = module.TemporaryDirectory

    def track(**kwargs):
        scratch = temporary(dir=tmp_path, **kwargs)
        scratches.append(scratch.name)
        return scratch

    def interrupt(*args, **kwargs):
        result = original(*args, **kwargs)
        cancel.cancel()
        return result

    monkeypatch.setattr(module, 'TemporaryDirectory', track)
    monkeypatch.setattr(module, 'replace_literal', interrupt)
    with pytest.raises(CancelledError):
        preview_replacement(source, Selection(), {}, 'hit', 'new', cancel=cancel)
    assert scratches and not Path(scratches[0]).exists()
    assert source.read_text() == 'note\nhit\nhit\n'
