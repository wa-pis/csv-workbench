import csv
from concurrent.futures import CancelledError

import duckdb
import pytest

from csv_workbench.core import (
    DELETED_COLUMN,
    NEW_ROW,
    Cancellation,
    CsvFormat,
    Selection,
    export_csv,
    file_revision,
    prepare_result,
    read_page,
    save_csv,
)
from csv_workbench.edits import DiskEdits
from csv_workbench.session import load_workspace_session, save_workspace_session
from csv_workbench.validation import (
    TYPES,
    cell_error,
    invalid_sql,
    parse_rules,
    validate_csv_values,
)

VALUES = [None, '', 'text', '0', '-123', '+001', '12.5', '.5', '1.', '1e3', '1e999',
          'NaN', 'inf', ' 2', '2 ', '１２', '١', '2024-02-29', '2023-02-29',
          '0000-01-01', '0001-01-01', '9999-12-31', '2024-1-1', 'true', 'FALSE',
          'True', 'yes', 'false\n', '1_000', '\n']


@pytest.mark.parametrize('kind', TYPES)
@pytest.mark.parametrize('nullable', [False, True])
def test_python_and_sql_rules_agree(kind, nullable):
    with duckdb.connect() as connection:
        for value in VALUES:
            invalid = connection.execute(f'SELECT {invalid_sql("v", kind, nullable)} '
                                         'FROM (SELECT ?::VARCHAR AS v)', [value]).fetchone()[0]
            assert invalid == (cell_error(value, kind, nullable) is not None), (kind, value)


@pytest.mark.parametrize('rules', [None, {}, [('a', 'unknown', True)], [('a', 'text', 1)],
                                  [('a', 'text', True), ('a', 'number', False)], [['a']],
                                  [[None, 'text', True]], [('a', [], True)]])
def test_invalid_rules_rejected(rules):
    with pytest.raises(ValueError, match='validation rule'):
        parse_rules(rules)


def test_full_selection_includes_unloaded_hidden_columns_and_disk_edits(tmp_path):
    source = tmp_path / 'values.csv'
    with source.open('w', newline='') as handle:
        writer = csv.writer(handle, quoting=csv.QUOTE_NOTNULL)
        writer.writerow(['id', 'n', 'tag'])
        writer.writerows((str(i), str(i), 'keep') for i in range(600))
        writer.writerows([('600', 'bad', 'keep'), ('601', None, 'keep'),
                          ('602', '', 'keep'), ('603', 'NaN', 'skip'), ('604', 'nope', 'keep')])
    original = source.read_bytes()
    selection = Selection(filters=(('tag', 'keep'),), sort_column='id', descending=True,
                          visible_columns=('id',), column_types=(('n', 'number', False),))
    result = validate_csv_values(source, selection, {}, cancel=Cancellation())
    assert result.rows == 604 and result.errors == (('n', 4),) and result.cells == 4
    edits = DiskEdits().updated([((600, 1), '12'), ((601, DELETED_COLUMN), None),
                                ((NEW_ROW, 0), 'new'), ((NEW_ROW, 1), 'invalid'),
                                ((NEW_ROW, 2), 'keep')], cancel=Cancellation())
    changed = validate_csv_values(source, selection, edits, cancel=Cancellation())
    assert changed.rows == 604 and changed.cells == 3
    assert source.read_bytes() == original
    token = Cancellation()
    token.cancel()
    with pytest.raises(CancelledError):
        validate_csv_values(source, selection, edits, cancel=token)
    with pytest.raises(ValueError, match='Unknown validation column'):
        validate_csv_values(source, Selection(column_types=(('missing', 'text', True),)),
                            {}, cancel=Cancellation())


def test_empty_selection_and_quoted_column_names(tmp_path):
    source = tmp_path / 'quoted.csv'
    source.write_text('"a\"\"b",tag\nno,keep\n', encoding='utf-8')
    selection = Selection(filters=(('tag', 'absent'),), column_types=(('a"b', 'integer', False),))
    result = validate_csv_values(source, selection, {}, cancel=Cancellation())
    assert result.rows == result.cells == 0
    assert result.errors == (('a"b', 0),)


@pytest.mark.parametrize('single_column', [False, True])
def test_null_and_empty_round_trip_save_export_and_polars(tmp_path, single_column):
    source = tmp_path / 'values.csv'
    source.write_text('v\n\n""\nNULL\n' if single_column
                      else 'v,id\n,0\n"",1\nNULL,2\n', encoding='utf-8')
    expected = [(None,), ('',), ('NULL',)] if single_column else [(None, '0'), ('', '1'), ('NULL', '2')]
    assert read_page(source, 0)[1] == expected
    exported = tmp_path / 'export.csv'
    export_csv(source, exported, Selection())
    assert read_page(exported, 0)[1] == expected
    save_csv(source, source, {}, file_revision(source))
    assert read_page(source, 0)[1] == expected
    result = prepare_result(source, Selection(sort_column='v'), cancel=Cancellation())
    assert result.page(0)[1] == [expected[1], expected[2], expected[0]]


def test_rules_session_per_tab_and_legacy_defaults(tmp_path):
    destination = tmp_path / 'tabs.csvsession'
    a = Selection(column_types=(('n', 'number', False),))
    b = Selection(column_types=(('when', 'date', True),))
    save_workspace_session(destination, [(tmp_path / 'a.csv', a, CsvFormat()),
                                         (tmp_path / 'b.csv', b, CsvFormat(';'))], 1)
    tabs, active = load_workspace_session(destination)
    assert active == 1
    assert tabs[0][1].column_types == a.column_types
    assert tabs[1][1].column_types == b.column_types
    # Rules are view metadata, not a trigger for a materialized CSV query.
    assert a == Selection()
    import json
    data = json.loads(destination.read_text())
    del data['tabs'][0]['selection']['column_types']
    destination.write_text(json.dumps(data))
    assert load_workspace_session(destination)[0][0][1].column_types == ()
    data['tabs'][0]['selection']['column_types'] = [['n', 'number', 'yes']]
    destination.write_text(json.dumps(data))
    with pytest.raises(ValueError, match='validation rule'):
        load_workspace_session(destination)
