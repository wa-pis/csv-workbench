## ADDED Requirements

### Requirement: Pin CSV columns

The application SHALL let users pin or unpin a visible CSV column from its header menu. Pinning SHALL display that column on the left without changing the order of saved or exported columns.

#### Scenario: Scroll with pinned columns

- **WHEN** the user pins a column and scrolls the table horizontally
- **THEN** the pinned area remains at the left and the other columns scroll
- **AND** vertical scrolling keeps both areas on the same rows

#### Scenario: Restore pinned column settings

- **WHEN** the user saves and restores a document or workspace session
- **THEN** each tab restores its pins by column name
- **AND** an older session without pin settings opens with no pins
