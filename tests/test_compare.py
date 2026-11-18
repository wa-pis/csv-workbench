from concurrent.futures import CancelledError

import pytest

from csv_workbench.compare import compare_csv
from csv_workbench.core import Cancellation, CsvFormat


def test_compare_handles_differences_nulls_unicode_and_column_order(tmp_path):
    left, right = tmp_path / "before.csv", tmp_path / "after.csv"
    left.write_text('id,note\n001,same\n002,before\n003,gone\n004,\n005,NULL\n')
    right.write_bytes('note;id\nsame;001\nПривет;002\nnew;006\nNULL;004\n;005\n'.encode("cp1251"))
    result, counts, (headers, rows, more) = compare_csv(
        left, right, "id", right_format=CsvFormat(";", encoding="cp1251")
    )
    try:
        assert counts == {"Added": 1, "Removed": 1, "Changed": 3}
        assert headers == ["Status", "Key", "Before: note", "After: note"]
        assert rows == [
            ("Changed", "002", "before", "Привет"),
            ("Removed", "003", "gone", None),
            ("Changed", "004", None, "NULL"),
            ("Changed", "005", "NULL", None),
            ("Added", "006", None, "new"),
        ]
        assert not more
        assert sorted(p.name for p in result.path.parent.iterdir()) == ["result.parquet"]
    finally:
        result.scratch.cleanup()


@pytest.mark.parametrize("right,match", [
    ("id,note\n1,a\n1,b\n", "Duplicate or empty"),
    ("id,note\n,a\n", "Duplicate or empty"),
    ('id,note\n"",a\n', "Duplicate or empty"),
    ("other,note\n1,a\n", "missing"),
    ("id,different\n1,a\n", "must match"),
])
def test_compare_rejects_ambiguous_keys_and_schemas(tmp_path, right, match):
    left_path, right_path = tmp_path / "a.csv", tmp_path / "b.csv"
    left_path.write_text("id,note\n1,a\n")
    right_path.write_text(right)
    with pytest.raises(ValueError, match=match):
        compare_csv(left_path, right_path, "id")


def test_compare_result_pages_beyond_preview_and_key_only_files(tmp_path):
    left, right = tmp_path / "a.csv", tmp_path / "b.csv"
    left.write_text("id\n" + "".join(f"{i:04}\n" for i in range(201)))
    right.write_text("id\n" + "".join(f"{i:04}\n" for i in range(201, 402)))
    result, counts, (_, rows, more) = compare_csv(left, right, "id")
    try:
        assert counts == {"Added": 201, "Removed": 201}
        assert len(rows) == 200 and more
        _, tail, more = result.page(400)
        assert tail == [("Added", "0400"), ("Added", "0401")]
        assert not more
    finally:
        result.scratch.cleanup()


def test_identical_files_have_empty_result(tmp_path):
    path = tmp_path / "a.csv"
    path.write_text("id,note\n1,a\n")
    result, counts, (headers, rows, more) = compare_csv(path, path, "id")
    try:
        assert counts == {}
        assert headers == ["Status", "Key", "Before: note", "After: note"]
        assert rows == [] and not more
    finally:
        result.scratch.cleanup()


def test_compare_cancellation_cleans_scratch(tmp_path, monkeypatch):
    import csv_workbench.compare as module

    path = tmp_path / "a.csv"
    path.write_text("id\n1\n")
    token = Cancellation()
    scratch = module.TemporaryDirectory(dir=tmp_path)
    monkeypatch.setattr(module, "TemporaryDirectory", lambda **_: scratch)
    real_check = token.check
    checks = 0

    def check():
        nonlocal checks
        checks += 1
        if checks >= 4:
            token.cancel()
        real_check()

    monkeypatch.setattr(token, "check", check)
    with pytest.raises(CancelledError):
        compare_csv(path, path, "id", cancel=token)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["a.csv"]


def test_compare_rejects_changes_during_read(tmp_path, monkeypatch):
    import csv_workbench.compare as module

    left, right = tmp_path / "a.csv", tmp_path / "b.csv"
    left.write_text("id\n1\n")
    right.write_text("id\n2\n")
    real_revision = module.file_revision
    calls = 0

    def revision(path):
        nonlocal calls
        calls += 1
        value = real_revision(path)
        return (*value[:-1], value[-1] + 1) if calls > 2 else value

    monkeypatch.setattr(module, "file_revision", revision)
    with pytest.raises(ValueError, match="changed during"):
        compare_csv(left, right, "id")
