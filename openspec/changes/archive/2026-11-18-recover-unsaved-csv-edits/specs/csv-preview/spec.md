## ADDED Requirements

### Requirement: Recover unsaved CSV edits

The application SHALL checkpoint committed unsaved edits separately from the source CSV and offer restoration at startup.

#### Scenario: Restore a crashed document

- **WHEN** a valid checkpoint matches the saved source revision and the user chooses Restore
- **THEN** the document restores edits, added and deleted rows, format, view settings and Undo/Redo history
- **AND** the source remains unchanged until explicit Save

#### Scenario: Protect incompatible or active recovery data

- **WHEN** a checkpoint is corrupt, its source is missing or changed, or another live application holds its lock
- **THEN** the application SHALL NOT apply or silently delete that checkpoint

#### Scenario: Save or discard changes

- **WHEN** the user saves the document, returns to the clean state, or explicitly discards edits
- **THEN** the obsolete checkpoint is removed
- **AND** choosing Later at startup keeps recovery data for a later launch

#### Scenario: Recovery storage fails

- **WHEN** a checkpoint cannot be written
- **THEN** the application reports that recovery is unavailable while keeping current in-memory edits
