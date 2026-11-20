## ADDED Requirements

### Requirement: Explicit cell editing keyboard contract

Enter, F2 and double click SHALL start editing an editable cell; Enter SHALL confirm one Undo operation and Escape SHALL cancel without changing the document.

#### Scenario: Use the grid action

- **WHEN** the user invokes the documented action on the current CSV view
- **THEN** it SHALL follow the contract in GRID_POLISH.md without altering unrelated cells
- **AND** disabled or invalid actions SHALL leave the document unchanged
