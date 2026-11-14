from concurrent.futures import CancelledError, ThreadPoolExecutor
from threading import Event

import duckdb
import pytest

from csv_workbench.core import Cancellation, Selection, export_csv


def test_cancel_interrupts_active_connection(monkeypatch):
    token = Cancellation()
    started, interrupted = Event(), Event()

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def execute(self):
            started.set()
            assert interrupted.wait(5)
            raise duckdb.InterruptException("Interrupted")

        def interrupt(self):
            interrupted.set()

    monkeypatch.setattr(duckdb, "connect", lambda **kwargs: Connection())

    def query():
        with token.connection() as connection:
            connection.execute()

    with ThreadPoolExecutor() as pool:
        future = pool.submit(query)
        assert started.wait(5)
        token.cancel()
        with pytest.raises(CancelledError):
            future.result(timeout=5)


def test_export_cancellation_before_publish_keeps_destination(tmp_path, monkeypatch):
    source = tmp_path / "source.csv"
    destination = tmp_path / "existing.csv"
    source.write_text("id\n001\n", encoding="utf-8")
    destination.write_bytes(b"old export")
    token = Cancellation()
    publish = token.publish

    def cancel_before_publish(output, destination):
        assert output.is_file()
        token.cancel()
        publish(output, destination)

    monkeypatch.setattr(token, "publish", cancel_before_publish)
    with pytest.raises(CancelledError):
        export_csv(source, destination, Selection(), cancel=token)
    assert destination.read_bytes() == b"old export"
    assert source.read_bytes() == b"id\n001\n"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["existing.csv", "source.csv"]
