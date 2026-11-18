## Context

Copy currently writes selected cells as TSV, filling gaps with empty text.

## Goals / Non-Goals

Keep standard Copy and TSV paste compatible. Offer explicit alternate formats with visible column order and unsaved values. No full-file clipboard copies.

## Decisions

Use a chunk generator for serialization and bound the resulting clipboard text before replacing the clipboard. Limit the bounding rectangle before expanding selected indexes. CSV/TSV omit headers as before; JSON is an array of objects and Markdown includes headers. JSON preserves NULL as null. Markdown escapes backslashes, pipes, HTML characters and line breaks. Gaps in a sparse selection remain empty strings.

## Risks / Trade-offs

An individual large cell already exists in the table buffer; serialization still needs transient storage for that cell. The output limit prevents installing an oversized clipboard value.
