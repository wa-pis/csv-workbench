import pytest

from csv_workbench.core import Selection, read_page


@pytest.mark.parametrize("count", [200, 201])
def test_csv_pages(tmp_path, count):
    path = tmp_path / "data[1]'s.csv"
    original = ("id,name\n" + "".join(f'{i:05},"Имя, {i}"\n' for i in range(1, count + 1))).encode(
        "utf-8-sig"
    )
    path.write_bytes(original)
    (tmp_path / "data1's.csv").write_text("wrong,columns\n1,2\n", encoding="utf-8")

    headers, rows, has_next = read_page(path, 0)
    assert headers == ["id", "name"]
    assert rows == [(f"{i:05}", f"Имя, {i}") for i in range(1, 201)]
    assert has_next is (count == 201)
    if has_next:
        assert read_page(path, 200) == (headers, [("00201", "Имя, 201")], False)
    assert path.read_bytes() == original


def test_header_only(tmp_path):
    path = tmp_path / "empty.csv"
    path.write_text("id,name\n", encoding="utf-8")
    assert read_page(path, 0) == (["id", "name"], [], False)


def test_missing_csv(tmp_path):
    with pytest.raises(FileNotFoundError):
        read_page(tmp_path / "missing.csv", 0)


def test_filter_and_sort_before_pagination(tmp_path):
    path = tmp_path / "all.csv"
    path.write_text(
        "id,label\n" + "".join(f"{i:05},MaTcH%_\n" for i in range(1, 202)) + "99999,matchXX\n",
        encoding="utf-8",
    )
    selection = Selection("label", "match%_", "id", True, ("id",))
    headers, rows, has_next = read_page(path, 0, selection)
    assert headers == ["id"]
    assert rows == [(f"{i:05}",) for i in range(201, 1, -1)]
    assert has_next
    assert read_page(path, 200, selection) == (headers, [("00001",)], False)
    assert read_page(path, 0)[0] == ["id", "label"]
    assert read_page(path, 0)[1][0] == ("00001", "MaTcH%_")


def test_sort_ties_nulls_and_column_order(tmp_path):
    path = tmp_path / "sort.csv"
    path.write_text('id,"a""b",note\n1,b,first\n2,,empty\n3,a,third\n4,b,last\n')
    selection = Selection(sort_column='a"b', visible_columns=("note", "id"))
    assert read_page(path, 0, selection) == (
        ["id", "note"],
        [("3", "third"), ("1", "first"), ("4", "last"), ("2", "empty")],
        False,
    )


def test_no_matches_keeps_visible_headers(tmp_path):
    path = tmp_path / "empty-view.csv"
    path.write_text("id,label\n001,hello\n", encoding="utf-8")
    selection = Selection("label", "missing", visible_columns=("id",))
    assert read_page(path, 0, selection) == (["id"], [], False)
    with pytest.raises(ValueError, match="at least one"):
        read_page(path, 0, Selection(visible_columns=()))
