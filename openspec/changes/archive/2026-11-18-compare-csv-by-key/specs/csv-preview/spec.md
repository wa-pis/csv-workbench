## ADDED Requirements

### Requirement: Compare saved CSV files by key

The application SHALL compare two saved CSV files using one unique nonempty key column and show added, removed and changed rows. Filters and unsaved table edits SHALL not affect this operation.

#### Scenario: Compare compatible files

- **WHEN** the files have matching column names and a valid selected key
- **THEN** exact key and null-safe value comparisons identify all differing records
- **AND** the read-only result shows counts and before/after values in bounded asynchronously loaded pages

#### Scenario: Reject ambiguous keys or incompatible schemas

- **WHEN** either file contains duplicate or empty keys, lacks the key, or has different column names
- **THEN** comparison reports the problem instead of producing ambiguous matches

#### Scenario: Cancel comparison

- **WHEN** the user cancels comparison or closes a result with a pending page read
- **THEN** pending work is cancelled without modifying either source
- **AND** temporary resources are released
