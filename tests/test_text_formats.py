import csv
import io
import json

import pytest

from csv_workbench.text_formats import clipboard_text


@pytest.mark.parametrize("fmt,delimiter", [("CSV", ","), ("TSV", "\t")])
def test_delimited_copy_round_trip(fmt, delimiter):
    text = clipboard_text(["a", "b"], [('a,\t"\n', None)], fmt)
    assert list(csv.reader(io.StringIO(text), delimiter=delimiter)) == [['a,\t"\n', ""]]


def test_json_copy_preserves_unicode_and_null():
    assert json.loads(clipboard_text(["id", "note"], [("Привет", None)], "JSON")) == [
        {"id": "Привет", "note": None}
    ]


def test_markdown_escapes_structure_and_raw_html():
    text = clipboard_text(["a|b"], [("x\\y|z\r\n<script>",), (None,)], "Markdown")
    assert text == "| a\\|b |\n| --- |\n| x\\\\y\\|z<br>&lt;script&gt; |\n|  |\n"


def test_clipboard_limit_counts_utf8_bytes_and_stops_consuming():
    def rows():
        yield ("я" * 5,)
        raise AssertionError("Should stop before reading the next row")

    with pytest.raises(ValueError, match="4 MiB"):
        clipboard_text(["a"], rows(), "TSV", limit=9)
