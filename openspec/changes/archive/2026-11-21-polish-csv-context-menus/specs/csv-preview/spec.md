## ADDED Requirements

### Requirement: Consistent CSV cell context actions

Cell context menus SHALL reuse existing editing actions, preserve a selected range when invoked inside it, select an outside target, and support keyboard invocation.

#### Scenario: Use the grid action

- **WHEN** the user invokes the documented action on the current CSV view
- **THEN** it SHALL follow the contract in GRID_POLISH.md without altering unrelated cells
- **AND** disabled or invalid actions SHALL leave the document unchanged
