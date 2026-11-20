## ADDED Requirements

### Requirement: Keyboard navigation across buffered CSV rows

Arrow, Tab, reverse Tab and page movement SHALL work across buffer boundaries without loading the whole selection; Go to Row SHALL accept a visible column and row.

#### Scenario: Use the grid action

- **WHEN** the user invokes the documented action on the current CSV view
- **THEN** it SHALL follow the contract in GRID_POLISH.md without altering unrelated cells
- **AND** disabled or invalid actions SHALL leave the document unchanged
