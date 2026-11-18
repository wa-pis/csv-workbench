## Context

CSV export already runs query execution in the background and atomically publishes a temporary file. Text format chunk generators exist for the clipboard.

## Goals / Non-Goals

Export every selected row with filters, sort, visible column order and edits. Keep Python output memory bounded by a 1,024-row batch. Do not impose clipboard size limits on files.

## Decisions

Reuse selection_query or editable_query according to whether an edit snapshot is present. Stream fetched rows through formatted_chunks into a UTF-8 temporary file in the destination directory. Check cancellation between chunks and source revision before publishing. Reject the source or its aliases as destinations. Existing cross-tab destination checks apply.

## Risks / Trade-offs

Sorting still consumes query-engine memory and temporary disk as it does for CSV export. JSON is an array of objects; Markdown is a table. Markdown renders null as an empty cell.
