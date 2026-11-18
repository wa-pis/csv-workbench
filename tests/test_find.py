from concurrent.futures import CancelledError

import pytest

from csv_workbench.core import Cancellation, Selection, find_csv_cell, prepare_editable_result


@pytest.mark.parametrize("cached", [False, True])
def test_find_cells_follow_selection_and_unsaved_edits(tmp_path, cached):
    source = tmp_path / "find.csv"
    source.write_text(
        "id,label,note,hidden\n"
        "1,keep,plain,HIT\n"
        "2,skip,hit,hit\n"
        "3,keep,plain,x\n"
        "4,keep,İt,hit\n",
        encoding="utf-8",
    )
    selection = Selection((("label", "keep"),), "id", True, ("id", "label", "note"))
    edits = {(2, 1): "keep HIT", (2, 2): "new hit", (3, 1): "keep"}
    columns = ("id", "label", "note")
    result = (
        prepare_editable_result(source, selection, edits, cancel=Cancellation()) if cached else None
    )

    def search(anchor=(-1, -1), forward=True, needle="HiT"):
        return find_csv_cell(
            source, selection, edits, columns, needle, anchor, forward,
            cancel=Cancellation(), result=result,
        )

    assert search() == (1, 1)
    assert search((1, 1)) == (1, 2)
    assert search((1, 2), False) == (1, 1)
    assert search((1, 2)) is None  # Skip filtered rows and hidden columns.
    assert search((1, 1), False) is None
    assert search(needle="new %") is None  # Literal text, not a wildcard.


def test_find_case_and_pre_cancelled_search(tmp_path):
    source = tmp_path / "case.csv"
    source.write_text("note\nΟΣ İt\n", encoding="utf-8")
    arguments = source, Selection(), {}, ("note",), "οσ it", (-1, -1), True
    assert find_csv_cell(*arguments, cancel=Cancellation()) == (0, 0)
    cancel = Cancellation()
    cancel.cancel()
    with pytest.raises(CancelledError):
        find_csv_cell(*arguments, cancel=cancel)
