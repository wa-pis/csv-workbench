## Context

read_format_sample already reads at most 128 Ki characters using the chosen or detected encoding.

## Goals / Non-Goals

Inspect source text while preserving table filters, selection and edits. Do not edit raw text or imply that unsaved table changes are part of the saved source.

## Decisions

Use a stacked table/text widget and a toolbar selector. Read a fresh sample in a cancellable background job each time Text is selected. Explicitly label it as saved source text, outside table filters and unsaved edits, and disclose truncation. Cancel and discard text work on returning to Table or loading a different view. Sessions do not persist this temporary preview mode.

## Risks / Trade-offs

This is a leading sample rather than a full-file text editor. Opening Text again refreshes external changes without applying them to the table.
