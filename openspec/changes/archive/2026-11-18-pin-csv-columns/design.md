## Context

The CSV table displays a bounded buffer through a Qt model.

## Goals / Non-Goals

Freeze visible columns without reloading rows or changing the exported layout. Keep older session files compatible.

## Decisions

- A second Qt view overlays the pinned columns and shares the model, selection and vertical scroll position.
- Store pins as column names. A hidden column keeps its pin until shown again.
- Preserve the underlying column order. Pins affect only the on-screen layout.

## Risks / Trade-offs

When pinned columns exceed the view width, leave space to reach the remaining columns. Resize or unpin columns to expose more of the pinned area.
