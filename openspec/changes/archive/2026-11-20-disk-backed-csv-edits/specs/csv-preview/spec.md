## ADDED Requirements

### Requirement: Disk backed edit history

The application SHALL store cell patches and Undo/Redo values on disk, with memory usage independent of the number of cells changed by a bulk replacement.

#### Scenario: Replace millions of cells

- **WHEN** a prepared replacement is applied to a large current selection
- **THEN** the plan SHALL be consumed in bounded batches and published atomically
- **AND** one Undo or Redo SHALL switch the complete operation without loading its cells into RAM
- **AND** filters, sorting, unique, search, Save and export SHALL include the disk backed patches

#### Scenario: Cancel or fail preparation

- **WHEN** preparation is cancelled or disk writing fails
- **THEN** the prior document and its history SHALL remain intact

#### Scenario: Recover disk backed edits

- **WHEN** a recovery checkpoint is written and reopened
- **THEN** patches and history SHALL be saved and restored without materializing all cell values in RAM
- **AND** legacy version 1 checkpoints SHALL remain readable
