## Context

The table already exposes selected model indexes and live edited cell values.

## Goals / Non-Goals

Transpose one contiguous selected rectangle using only buffered rows. No full-file transpose, editing or persistence of this temporary view.

## Decisions

Check selection ranges and cell count before creating cell items. Allow at most 10,000 cells. Reject disjoint selections rather than filling unselected cells. Order fields by the current header layout, and label record columns with current selection-relative row numbers. Snapshot cell display values so unsaved edits and NULL match the table. Reuse a modal read-only QTableWidget as in Record View.
