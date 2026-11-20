## ADDED Requirements

### Requirement: Replace text across the current CSV view

The application SHALL preview and apply literal case-insensitive replacement across all rows and visible columns of the current selection, including unloaded rows and unsaved edits.

#### Scenario: Preview and replace

- **WHEN** the user previews replacement of the first occurrence or all occurrences
- **THEN** the preview shows before/after values and counts without changing the document
- **AND** confirmation applies only the prepared snapshot, using the existing filter and sort order
- **AND** Undo reverts the complete replacement as one operation and Redo reapplies it

#### Scenario: Cancel or invalidate a preview

- **WHEN** preparation is cancelled, the replacement options change, or the source or document no longer matches the preview
- **THEN** stale changes SHALL NOT be applied and the original edits remain intact

#### Scenario: Literal replacement semantics

- **WHEN** text contains Unicode, multiple matches, newlines, or an empty replacement
- **THEN** matches use the existing search case rules, inserted text is not searched again, and unrelated or hidden cells remain unchanged
