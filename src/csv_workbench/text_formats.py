"""Text representations shared by range copying and file export."""

import csv
import html
import io
import json


def formatted_chunks(headers, rows, fmt):
    if fmt in ("TSV", "CSV"):
        output = io.StringIO(newline="")
        writer = csv.writer(output, delimiter="\t" if fmt == "TSV" else ",")
        for row in rows:
            output.seek(0)
            output.truncate()
            writer.writerow(row)
            yield output.getvalue()
    elif fmt == "JSON":
        yield "["
        separator = ""
        for row in rows:
            yield separator + json.dumps(dict(zip(headers, row, strict=True)), ensure_ascii=False)
            separator = ",\n"
        yield "]\n"
    elif fmt == "Markdown":
        def line(row):
            def escape(value):
                text = "" if value is None else str(value)
                return (html.escape(text, quote=False).replace("\\", "\\\\")
                        .replace("|", "\\|").replace("\r\n", "\n")
                        .replace("\r", "\n").replace("\n", "<br>"))
            return "| " + " | ".join(escape(value) for value in row) + " |\n"

        yield line(headers)
        yield line(["---"] * len(headers))
        for row in rows:
            yield line(row)
    else:
        raise ValueError("Unsupported copy format.")


def clipboard_text(headers, rows, fmt, limit=4 * 1024 * 1024):
    parts = []
    size = 0
    for chunk in formatted_chunks(headers, rows, fmt):
        size += len(chunk.encode("utf-8"))
        if size > limit:
            raise ValueError("Copy is limited to 4 MiB. Export larger selections to a file.")
        parts.append(chunk)
    return "".join(parts)
