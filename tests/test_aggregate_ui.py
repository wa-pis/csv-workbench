from concurrent.futures import CancelledError
from decimal import Decimal
from threading import Event

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QItemSelection, QSettings
from PySide6.QtWidgets import QApplication, QMessageBox

from csv_workbench.aggregate import Aggregates
from csv_workbench.core import Selection
from csv_workbench.ui import MainWindow


def settle(grid):
    for _ in range(10):
        jobs = [job for job in grid.jobs() if job]
        if not jobs:
            return
        for _, future in jobs:
            try:
                future.result(timeout=5)
            except CancelledError:
                pass
        grid.collect_reads()
    raise AssertionError('jobs did not settle')


@pytest.fixture
def window(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication(['validation-test', '-platform', 'offscreen'])
    monkeypatch.setattr('csv_workbench.ui.QSettings', lambda *_: QSettings(
        str(tmp_path / 'settings.ini'), QSettings.Format.IniFormat))
    monkeypatch.setattr(MainWindow, 'recovery_root', property(lambda _: tmp_path / 'recovery'))
    monkeypatch.setattr(QMessageBox, 'question', lambda *_: QMessageBox.StandardButton.Yes)
    source = tmp_path / 'input.csv'
    source.write_text('id,value\n0,1\n1,2\n2,\n3,""\n4,bad\n')
    grid = MainWindow()
    grid.load_view(source, Selection(), new_file=True)
    settle(grid)
    yield grid
    grid.close()
    grid.executor.shutdown(wait=True)
    grid.deleteLater()
    app.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def select(window, top=0, left=1, bottom=4, right=1):
    window.table.set_selection(QItemSelection(window.model.index(top, left),
                                              window.model.index(bottom, right)))


def test_aggregate_action_results_edits_and_invalidation(window):
    assert not window.aggregate_action.isEnabled()
    select(window)
    assert window.aggregate_action.isEnabled()
    window.aggregate_action.trigger()
    settle(window)
    assert window.aggregate_result == Aggregates(5, 1, 1, 2, 1, Decimal(3), Decimal('1.5'),
                                                 Decimal(1), Decimal(2))
    assert 'Count 5' in window.aggregate_label.text()
    assert 'NULL 1' in window.aggregate_label.text()
    assert 'Empty 1' in window.aggregate_label.text()
    assert 'buffered' in window.aggregate_label.text().lower()
    window.model.setData(window.model.index(0, 1), '7')
    settle(window)
    assert window.aggregate_result is None
    select(window)
    before = window.history_pos
    window.start_aggregate()
    settle(window)
    assert window.aggregate_result.total == Decimal(9)
    assert window.history_pos == before
    select(window, bottom=0)
    assert window.aggregate_result is None
    assert window.aggregate_label.isHidden()


def test_filtered_sorted_reordered_pinned_values(window):
    selection = Selection(filters=(('value', '2'),), sort_column='id', descending=True,
                          column_order=('value', 'id'), pinned_columns=('value',))
    window.load_view(window.path, selection)
    settle(window)
    assert window.table.pinned.selectionModel() is window.table.selectionModel()
    select(window, left=0, bottom=0, right=0)
    window.model.setData(window.model.index(0, 0), '12')
    settle(window)
    select(window, left=0, bottom=0, right=0)
    window.start_aggregate()
    settle(window)
    assert window.aggregate_result.total == Decimal(12)
    assert window.aggregate_result.count == 1


@pytest.mark.parametrize('change', ['cancel', 'selection', 'edit', 'view', 'buffer', 'restart'])
def test_late_results_cannot_override_selection(window, monkeypatch, change):
    entered, release = Event(), Event()

    def delayed(*args, cancel, **kwargs):
        entered.set()
        assert release.wait(5)
        return Aggregates(999, 0, 0, 1, 0, Decimal(999), Decimal(999), Decimal(999), Decimal(999))

    monkeypatch.setattr('csv_workbench.ui.aggregate_selection', delayed)
    select(window)
    window.start_aggregate()
    token, future = window.aggregate_job
    assert entered.wait(5)
    try:
        if change == 'cancel':
            window.cancel_button.click()
        elif change == 'selection':
            select(window, bottom=0)
        elif change == 'edit':
            window.model.setData(window.model.index(0, 1), '8')
        elif change == 'view':
            window.load_view(window.path, Selection(sort_column='id'))
        elif change == 'buffer':
            window.model.insert_buffer_rows(5, [('5', '6')], [], 0)
        else:
            window.start_aggregate()
        with pytest.raises(CancelledError):
            token.check()
    finally:
        release.set()
    try:
        future.result(timeout=5)
    except CancelledError:
        pass
    if change == 'restart':
        # The newly submitted worker is allowed to publish, the old one is detached.
        window.invalidate_aggregate()
    settle(window)
    assert window.aggregate_result is None
    if change == 'cancel':
        assert 'cancelled' in window.aggregate_label.text()
