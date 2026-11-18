## ADDED Requirements

### Requirement: Preview saved CSV text

The application SHALL let users switch between the table and a read-only preview of the saved source text using the file format encoding.

#### Scenario: Inspect source text

- **WHEN** the user selects Text
- **THEN** at most 128 Ki characters are read in the background
- **AND** the view identifies the saved source and discloses truncation
- **AND** it explains that filters and unsaved table edits are not represented

#### Scenario: Return to the table

- **WHEN** the user selects Table
- **THEN** the existing table state and unsaved edits remain intact
- **AND** pending text reads cannot replace the table
