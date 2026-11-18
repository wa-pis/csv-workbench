## ADDED Requirements

### Requirement: Inspect a CSV record

The application SHALL provide a read-only view of the current CSV row as field and value pairs.

#### Scenario: Inspect the current selection

- **WHEN** the user opens Record View with a current row in the CSV table
- **THEN** the dialog shows the row number in the current selection and each visible field in table order
- **AND** it shows current cell values including unsaved edits and multiline text
- **AND** opening it does not load other rows or alter the table
