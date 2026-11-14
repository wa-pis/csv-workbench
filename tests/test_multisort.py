from csv_workbench.core import (
    Cancellation,
    Selection,
    export_csv,
    prepare_editable_result,
    prepare_result,
    read_page,
)
from csv_workbench.session import load_session, save_session


def test_cycle_stack_preserves_priority_and_can_remove_primary():
    selection = Selection().cycle_sort("group").cycle_sort("id", append=True)
    assert selection.sort_keys == (("group", False), ("id", False))
    selection = selection.cycle_sort("group", append=True)
    assert selection.sort_keys == (("group", True), ("id", False))
    assert selection.cycle_sort("group", append=True).sort_keys == (("id", False),)
    assert selection.cycle_sort("id").sort_keys == (("id", True),)
    assert not selection.cycle_sort("group").sort_keys


def test_multisort_uses_hidden_keys_and_stable_ties_in_all_outputs(tmp_path):
    source = tmp_path / "source.csv"
    source.write_text("group,rank,label\nb,3,first\na,1,low\na,2,high\na,2,tie\n", encoding="utf-8")
    selection = Selection(visible_columns=("label",)).cycle_sort("group")
    selection = selection.cycle_sort("rank", append=True).cycle_sort("rank", append=True)
    expected = (["label"], [("high",), ("tie",), ("low",), ("first",)], False)
    assert read_page(source, 0, selection) == expected
    result = prepare_result(source, selection)
    with result.scratch:
        assert result.page(0) == expected
    output = tmp_path / "output.csv"
    assert export_csv(source, output, selection) == 4
    assert read_page(output, 0) == expected
    session = tmp_path / "test.csvsession"
    save_session(session, source, selection)
    assert load_session(session) == (source.resolve(), selection)


def test_edit_changes_secondary_sort_key(tmp_path):
    source = tmp_path / "data.csv"
    source.write_text("group,rank,label\na,1,first\na,2,second\n", encoding="utf-8")
    selection = (
        Selection(visible_columns=("label",)).cycle_sort("group").cycle_sort("rank", append=True)
    )
    result = prepare_editable_result(source, selection, {(0, 1): "3"}, cancel=Cancellation())
    with result.scratch:
        assert result.page(0)[1] == [(1, "second"), (0, "first")]
