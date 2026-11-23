from concurrent.futures import CancelledError
from decimal import Decimal

import pytest

from csv_workbench.aggregate import Aggregates, aggregate_selection
from csv_workbench.core import Cancellation
from csv_workbench.edits import DiskEdits


def test_mixed_values_and_overlapping_ranges_count_once():
    rows = [(None, '', '0.1', '0.2', '-2', '+3e1', 'text', 'NaN', 'inf', ' 1', '١', '1e999')]
    result = aggregate_selection(rows, [(0, 0, 0, 11), (0, 2, 0, 5)], {}, cancel=Cancellation())
    assert result == Aggregates(12, 1, 1, 4, 6, Decimal('28.3'), Decimal('7.075'),
                                Decimal(-2), Decimal(30))


def test_decimal_precision_and_no_numbers_differs_from_zero():
    result = aggregate_selection([('0.1', '0.2', '0.3')], [(0, 0, 0, 2)], {}, cancel=Cancellation())
    assert result.total == Decimal('0.6')
    assert result.average == Decimal('0.2')
    result = aggregate_selection([('1', '0', '0')], [(0, 0, 0, 2)], {}, cancel=Cancellation())
    assert str(result.average) == '0.' + '3' * 38
    empty = aggregate_selection([(None, '', 'false')], [(0, 0, 0, 2)], {}, cancel=Cancellation())
    assert (empty.total, empty.average, empty.minimum, empty.maximum) == (None,) * 4
    zero = aggregate_selection([('0',)], [(0, 0, 0, 0)], {}, cancel=Cancellation())
    assert zero.total == zero.minimum == zero.maximum == 0
    assert aggregate_selection([], [], {}, cancel=Cancellation()).count == 0


def test_disk_edits_source_row_column_mapping_and_offset():
    edits = DiskEdits()
    edits[(901, 3)] = '12.5'
    edits[(901, 0)] = None
    before = dict(edits)
    result = aggregate_selection([('bad', '4')], [(0, 0, 0, 1)], edits.copy(),
                                 row_ids=(901,), columns=(3, 0), cancel=Cancellation())
    assert result.count == 2 and result.nulls == 1 and result.total == Decimal('12.5')
    assert dict(edits) == before
    edits[(600, 0)] = ''
    assert aggregate_selection([('1',)], [(0, 0, 0, 0)], edits, offset=600,
                               cancel=Cancellation()).empty == 1


def test_cancel_before_and_during_reading():
    token = Cancellation()
    token.cancel()
    with pytest.raises(CancelledError):
        aggregate_selection([('1',)], [(0, 0, 0, 0)], {}, cancel=token)
    token = Cancellation()

    class CancellingEdits(dict):
        def get(self, key, default):
            token.cancel()
            return default

    with pytest.raises(CancelledError):
        aggregate_selection([('1', '2')], [(0, 0, 0, 1)], CancellingEdits(), cancel=token)
