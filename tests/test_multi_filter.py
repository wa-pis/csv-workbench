from csv import reader

import pytest

from csv_workbench.core import Selection, export_csv, prepare_result, read_page


@pytest.mark.parametrize(
    ("match_any", "ids", "counts"),
    [
        (False, ["0204"], [("A", 1)]),
        (True, ["0205", "0204", "0203"], [("A", 1), ("B", 2)]),
    ],
)
def test_filter_groups_across_all_csv_paths(tmp_path, match_any, ids, counts):
    source = tmp_path / "rows.csv"
    source.write_text(
        "id,tag,text,group\n"
        + "".join(f"{i:04},skip,no,Z\n" for i in range(203))
        + "0203,TaG,,B\n0204,tag,100%_,A\n0205,,100%_,B\n0206,,100XX,C\n",
        encoding="utf-8",
    )
    selection = Selection(
        (("tag", "tag"), ("text", "%_"), ("group", "")),
        "id",
        True,
        ("id",),
        match_any=match_any,
    )
    expected = (["id"], [(value,) for value in ids], False)
    assert read_page(source, 0, selection) == expected

    result = prepare_result(source, selection)
    with result.scratch:
        assert result.page(0) == expected

    unique = prepare_result(source, selection, distinct="group")
    with unique.scratch:
        assert unique.page(0) == (["Value", "Frequency"], counts, False)

    destination = tmp_path / "export.csv"
    assert export_csv(source, destination, selection) == len(ids)
    with destination.open(newline="", encoding="utf-8") as handle:
        assert list(reader(handle)) == [["id"], *[[value] for value in ids]]


def test_empty_conditions_do_not_filter_even_in_or_mode(tmp_path):
    source = tmp_path / "rows.csv"
    source.write_text("id,text\n001,match\n002,\n", encoding="utf-8")
    selection = Selection((("text", ""),), match_any=True)
    assert read_page(source, 0, selection) == read_page(source, 0)
    result = prepare_result(source, selection)
    with result.scratch:
        assert result.page(0) == read_page(source, 0)


def test_unicode_filter_is_identical_across_engines(tmp_path):
    source = tmp_path / "rows.csv"
    source.write_text("id,text\n001,ΟΣ\n002,other\n", encoding="utf-8")
    selection = Selection((("text", "οσ"), ("id", "999")), match_any=True)
    expected = (["id", "text"], [("001", "ΟΣ")], False)
    assert read_page(source, 0, selection) == expected
    result = prepare_result(source, selection)
    with result.scratch:
        assert result.page(0) == expected


def test_staging_writes_only_matching_rows_and_needed_columns(tmp_path):
    import duckdb

    from csv_workbench.core import Cancellation, stage_selection

    source = tmp_path / "rows.csv"
    source.write_text("id,text,tag\n001,ΟΣ,keep\n002,other,keep\n003,ΟΣ,skip\n", encoding="utf-8")
    selection = Selection((("text", "οσ"), ("tag", "keep")), visible_columns=("id",))
    columns, files = stage_selection(source, selection, None, tmp_path, Cancellation())
    assert columns == ["id"]
    with duckdb.connect() as connection:
        result = connection.execute("SELECT * FROM read_parquet(?)", [files])
        assert [column[0] for column in result.description] == ["id"]
        assert result.fetchall() == [("001",)]
    assert not (tmp_path / "input-block.parquet").exists()
