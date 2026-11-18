import json

import pytest

from csv_workbench.core import CsvFormat, Selection
from csv_workbench.session import load_session, save_session


def test_round_trip(tmp_path):
    source = tmp_path / "input.csv"
    session = tmp_path / "saved.csvsession"
    selection = Selection((("label", "Привет"), ("id", "01")), "id", True, ("label",), True)
    save_session(session, source, selection)
    assert load_session(session) == (source.resolve(), selection, CsvFormat())


def test_session_rejects_invalid_filter_shape(tmp_path):
    source = tmp_path / "input.csv"
    session = tmp_path / "saved.csvsession"
    save_session(session, source, Selection())
    data = json.loads(session.read_text(encoding="utf-8"))
    data["selection"]["filters"] = ["id"]
    session.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid session selection"):
        load_session(session)


def test_session_cannot_overwrite_source(tmp_path):
    source = tmp_path / "input.csvsession"
    source.write_text("id\n001\n", encoding="utf-8")
    with pytest.raises(ValueError, match="other than the source"):
        save_session(source, source, Selection())
    assert source.read_text(encoding="utf-8") == "id\n001\n"


def test_workspace_session_round_trips_order_and_active_tab(tmp_path):
    from csv_workbench.session import load_workspace_session, save_workspace_session

    first = tmp_path / "input.csv"
    second = tmp_path / "input.tsv"
    path = tmp_path / "workspace.csvsession"
    selection = Selection(
        (("city", "Москва"), ("flag", "yes")), "city", True, ("city",), True, (("id", False),)
    )
    tabs = [
        (second, selection, CsvFormat("\t", False, "cp1251", "'")),
        (first, Selection(), CsvFormat(";")),
    ]
    save_workspace_session(path, tabs, 1)
    assert load_workspace_session(path) == (
        [(source.resolve(), view, fmt) for source, view, fmt in tabs],
        1,
    )

    legacy = tmp_path / "legacy.csvsession"
    save_session(legacy, first, selection)
    assert load_workspace_session(legacy) == ([(first.resolve(), selection, CsvFormat())], 0)


@pytest.mark.parametrize("invalid", ["active", "duplicate", "tab"])
def test_workspace_session_rejects_invalid_documents(tmp_path, invalid):
    from csv_workbench.session import load_workspace_session, save_workspace_session

    path = tmp_path / "workspace.csvsession"
    save_workspace_session(path, [(tmp_path / "source.csv", Selection(), CsvFormat())], 0)
    data = json.loads(path.read_text(encoding="utf-8"))
    if invalid == "active":
        data["active"] = True
    elif invalid == "duplicate":
        data["tabs"].append(data["tabs"][0])
    else:
        data["tabs"].append(None)
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError):
        load_workspace_session(path)


def test_workspace_pins_are_per_tab_and_legacy_sessions_have_no_pins(tmp_path):
    from csv_workbench.session import load_workspace_session, save_workspace_session

    workspace = tmp_path / "workspace.csvsession"
    first, second = tmp_path / "a.csv", tmp_path / "b.csv"
    save_workspace_session(workspace, [
        (first, Selection(pinned_columns=("id", "city")), CsvFormat()),
        (second, Selection(pinned_columns=("name",)), CsvFormat()),
    ], 0)
    tabs, _ = load_workspace_session(workspace)
    assert [selection.pinned_columns for _, selection, _ in tabs] == [("id", "city"), ("name",)]

    data = json.loads(workspace.read_text(encoding="utf-8"))
    data["tabs"][0]["selection"].pop("pinned_columns")
    workspace.write_text(json.dumps(data), encoding="utf-8")
    tabs, _ = load_workspace_session(workspace)
    assert tabs[0][1].pinned_columns == ()
    data["tabs"][0]["selection"]["pinned_columns"] = ["id", "id"]
    workspace.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid session selection"):
        load_workspace_session(workspace)
