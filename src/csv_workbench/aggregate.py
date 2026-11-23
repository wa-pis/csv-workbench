"""Read-only aggregates of a buffered selection without Qt dependencies."""

import math
import re
from dataclasses import dataclass
from decimal import MAX_EMAX, MIN_EMIN, Decimal, InvalidOperation, localcontext

from csv_workbench.validation import PATTERNS


@dataclass(frozen=True)
class Aggregates:
    count: int
    nulls: int
    empty: int
    numeric: int
    skipped: int
    total: Decimal | None
    average: Decimal | None
    minimum: Decimal | None
    maximum: Decimal | None


def aggregate_selection(rows, ranges, edits, *, offset=0, row_ids=(), columns=(), cancel):
    count = nulls = empty = numeric = skipped = 0
    total = Decimal(0)
    minimum = maximum = None
    with localcontext() as context:
        context.prec = 38
        context.Emax, context.Emin = MAX_EMAX, MIN_EMIN
        for row, values in enumerate(rows):
            cancel.check()
            intervals = sorted((left, right) for top, left, bottom, right in ranges
                               if top <= row <= bottom)
            end = -1
            for left, right in intervals:
                for column in range(max(left, end + 1), right + 1):
                    cancel.check()
                    key = (row_ids[row], columns[column]) if row_ids else (offset + row, column)
                    value = edits.get(key, values[column])
                    count += 1
                    if value is None:
                        nulls += 1
                    elif value == '':
                        empty += 1
                    else:
                        text = str(value)
                        number = None
                        if re.fullmatch(PATTERNS['number'], text) and math.isfinite(float(text)):
                            try:
                                number = Decimal(text)
                            except InvalidOperation:
                                pass
                        if number is None:
                            skipped += 1
                            continue
                        numeric += 1
                        total += number
                        minimum = number if minimum is None else min(minimum, number)
                        maximum = number if maximum is None else max(maximum, number)
                end = max(end, right)
        cancel.check()
        return Aggregates(count, nulls, empty, numeric, skipped, total if numeric else None,
                          total / numeric if numeric else None, minimum, maximum)
