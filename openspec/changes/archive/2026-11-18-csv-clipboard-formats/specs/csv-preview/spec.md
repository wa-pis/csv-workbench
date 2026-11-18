## ADDED Requirements

### Requirement: Copy ranges in selectable formats

The application SHALL provide Copy As TSV, CSV, JSON and Markdown for selected buffered cells, using visible column order and current edits. Standard Copy SHALL remain TSV.

#### Scenario: Copy structured values

- **WHEN** the user chooses a Copy As format
- **THEN** CSV/TSV quote delimiters and newlines, JSON retains null values, and Markdown escapes cell content
- **AND** only the selected range is copied without reading more rows

#### Scenario: Clipboard copy is too large

- **WHEN** the range exceeds 10,000 cells or the output exceeds 4 Mi UTF-8 bytes
- **THEN** the application warns and leaves the clipboard unchanged
