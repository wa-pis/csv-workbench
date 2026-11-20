## ADDED Requirements

### Requirement: Explicit bounded CSV selection

The application SHALL provide row, column, extend and shrink selection actions, scoped explicitly to buffered rows; selection SHALL remain bounded and report its size.

#### Scenario: Use the grid action

- **WHEN** the user invokes the documented action on the current CSV view
- **THEN** it SHALL follow the contract in GRID_POLISH.md without altering unrelated cells
- **AND** disabled or invalid actions SHALL leave the document unchanged
