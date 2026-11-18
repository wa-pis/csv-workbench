## ADDED Requirements

### Requirement: Transpose a selected CSV range

The application SHALL provide a read-only transposed view of a selected rectangular range with at most 10,000 cells, without reading further CSV rows.

#### Scenario: Inspect a range

- **WHEN** the user opens Transpose Selection for a contiguous range
- **THEN** the dialog shows fields vertically in current header order and records horizontally with selection-relative row labels
- **AND** values include unsaved edits
- **AND** the original selection, data and export are unchanged

#### Scenario: Reject an excessive or disjoint selection

- **WHEN** the selection contains more than 10,000 cells or multiple rectangles
- **THEN** the application explains the limit and does not create a transposed table
