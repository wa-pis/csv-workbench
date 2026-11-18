## Context

Cancellation connections bound query-engine memory to 512 MiB and allow spilling. DiskResult already reads bounded pages from Parquet.

## Goals / Non-Goals

Compare all saved records by one exact string key, independently of table filters and edits. Show every difference with before/after values. No merge, fuzzy match, composite key or schema migration in this stage.

## Decisions

- Require equal column-name sets; the second file may reorder them. Validate the key exists in both.
- Reject null, empty or repeated keys in either input, reporting which file is invalid.
- Stage both saved inputs to Parquet with the existing encoding conversion and CSV format settings.
- Full outer join by key, compare cells with IS DISTINCT FROM, and write only differing records to Parquet. Use deterministic key order.
- Show Status, Key, and Before/After pairs for non-key columns. Key-only files still show additions and removals.
- Return counts and an initial bounded page. Subsequent pages load asynchronously into a read-only dialog. Close cancels pending page reads and releases temporary results.
- Check both source revisions before returning. Global Cancel interrupts preparation. The command explicitly compares saved files and ignores filters and unsaved edits.

## Risks / Trade-offs

Temporary disk holds both staged inputs and the differences. The current stage requires compatible schemas and a unique nonempty key.
