from concurrent.futures import CancelledError

import duckdb
import pytest

from csv_workbench.core import PAGE_SIZE, Cancellation, query_csv


@pytest.fixture
def source(tmp_path):
    path = tmp_path / "source.csv"
    path.write_text(
        "id,label\n" + "".join(f"{i:04},Привет\n" for i in range(301)), encoding="utf-16"
    )
    return path


def test_sql_queries_entire_csv_and_limits_display(source):
    assert query_csv(source, "SELECT label, count(*) AS n FROM csv_data GROUP BY label") == (
        ["label", "n"],
        [("Привет", 301)],
        False,
    )
    headers, rows, more = query_csv(source, "SELECT id FROM csv_data ORDER BY id DESC;")
    assert headers == ["id"]
    assert rows == [(f"{i:04}",) for i in range(300, 300 - PAGE_SIZE, -1)]
    assert more


def test_sql_rejects_statements_with_side_effects(source):
    with pytest.raises(ValueError, match="one SELECT"):
        query_csv(source, "SELECT * FROM csv_data; DROP TABLE csv_data")


def test_sql_cannot_read_any_file_directly(source):
    with pytest.raises(duckdb.PermissionException):
        query_csv(source, f"SELECT * FROM read_csv('{source.as_posix()}')")


def test_cancelled_query_does_not_open_source(tmp_path):
    token = Cancellation()
    token.cancel()
    with pytest.raises(CancelledError):
        query_csv(tmp_path / "missing.csv", "SELECT * FROM csv_data", cancel=token)


def test_sql_chain_feeds_rows_from_one_step_into_the_next(source):
    assert query_csv(
        source,
        """
        WITH typed AS (
            SELECT CAST(id AS INTEGER) AS id, label FROM csv_data
        ), filtered AS (
            SELECT label FROM typed WHERE id >= 299
        )
        SELECT label, count(*) AS n FROM filtered GROUP BY label
        """,
    ) == (["label", "n"], [("Привет", 2)], False)
