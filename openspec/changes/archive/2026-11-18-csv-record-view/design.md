## Context

The preview model already keeps the current buffered row and overlays unsaved edits.

## Goals / Non-Goals

Display the current record in table field order without reading extra rows. Inline editing and navigation inside the dialog are out of scope for this stage.

## Decisions

Use a modal read-only table with Field and Value columns. Take a snapshot of the visible fields in header order, including pinned columns and unsaved edits, when the command opens. Use the existing row header for the selection-relative row number.
