"""Non-coercing column rules and bounded full-selection validation."""

import math
import re
from dataclasses import dataclass, replace
from datetime import date

TYPES = ('text', 'integer', 'number', 'date', 'boolean')
PATTERNS = {
    'integer': r'[+-]?[0-9]+',
    'number': r'[+-]?([0-9]+(\.[0-9]*)?|\.[0-9]+)([eE][+-]?[0-9]+)?',
    'date': r'[0-9]{4}-[0-9]{2}-[0-9]{2}',
    'boolean': r'[tT][rR][uU][eE]|[fF][aA][lL][sS][eE]',
}


def parse_rules(rules):
    if not isinstance(rules, (tuple, list)):
        raise ValueError('Invalid column validation rules.')  # noqa: TRY004
    names = set()
    for rule in rules:
        if (not isinstance(rule, (tuple, list)) or len(rule) != 3
                or not isinstance(rule[0], str) or not rule[0]
                or rule[1] not in TYPES or type(rule[2]) is not bool or rule[0] in names):
            raise ValueError('Invalid column validation rule.')
        names.add(rule[0])
    return tuple(tuple(rule) for rule in rules)


def cell_error(value, kind='text', nullable=True):
    if value is None:
        return None if nullable else 'NULL is not allowed'
    if kind == 'text':
        return None
    valid = re.fullmatch(PATTERNS[kind], value) is not None
    if valid and kind == 'number':
        valid = math.isfinite(float(value))
    if valid and kind == 'date':
        try:
            date.fromisoformat(value)
        except ValueError:
            valid = False
    return None if valid else f'Expected {kind}' + (' (YYYY-MM-DD)' if kind == 'date' else '')


def invalid_sql(name, kind, nullable):
    valid = 'true'
    if kind != 'text':
        # All patterns are constants, never user-supplied expressions.
        valid = f"regexp_full_match({name}, '{PATTERNS[kind]}')"
        if kind == 'number':
            valid += f' AND isfinite(try_cast({name} AS DOUBLE))'
        if kind == 'date':
            valid += (f' AND try_cast({name} AS DATE) IS NOT NULL'
                      f' AND year(try_cast({name} AS DATE)) BETWEEN 1 AND 9999')
    return f'CASE WHEN {name} IS NULL THEN {str(not nullable).lower()} ELSE NOT coalesce(({valid}), false) END'


@dataclass(frozen=True)
class ValidationResult:
    rows: int
    errors: tuple[tuple[str, int], ...]

    @property
    def cells(self):
        return sum(count for _, count in self.errors)


def validate_csv_values(path, selection, edits, *, cancel, fmt=None):
    from csv_workbench.core import (
        CsvFormat,
        csv_columns,
        csv_pattern,
        editable_query,
        quote_identifier,
        utf8_source,
    )

    rules = parse_rules(selection.column_types)
    fmt = fmt or CsvFormat()
    # Check configured hidden columns too; ordering cannot change aggregate counts.
    unordered = replace(selection, visible_columns=None, column_order=(), sort_column=None,
                        descending=False, extra_sort=())
    with utf8_source(path, cancel, fmt) as source, cancel.connection() as connection:
        columns = csv_columns(connection, csv_pattern(source), fmt)
        if missing := {name for name, _, _ in rules}.difference(columns):
            raise ValueError(f'Unknown validation column: {min(missing)}')
        sql, params = editable_query(connection, source, unordered, edits, fmt, cancel=cancel,
                                     ordered=False)
        counts = ['count(*)'] + [
            f'count(*) FILTER (WHERE {invalid_sql(quote_identifier(name), kind, nullable)})'
            for name, kind, nullable in rules
        ]
        cancel.check()
        row_count, *errors = connection.execute(
            f'SELECT {", ".join(counts)} FROM ({sql})', params,
        ).fetchone()
        cancel.check()
        return ValidationResult(row_count, tuple((rule[0], count)
                                                for rule, count in zip(rules, errors, strict=True)))
