## ADDED Requirements

### Requirement: Export the selection as JSON or Markdown

The application SHALL stream the full current CSV selection to a UTF-8 JSON or Markdown file, including filters, sorting, visible column order and unsaved edits.

#### Scenario: Export unloaded rows

- **WHEN** the user exports a selection larger than the preview buffer
- **THEN** all matching rows are written in selection order using bounded output batches
- **AND** JSON preserves nulls and Markdown escapes cell content

#### Scenario: Cancel or fail an export

- **WHEN** the export is cancelled, fails, or the source revision changes
- **THEN** the previous destination remains intact and temporary output is removed
- **AND** the source CSV and its aliases cannot be overwritten
