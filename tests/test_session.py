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
