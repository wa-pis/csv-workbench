## ADDED Requirements

### Requirement: Complete cell values across the current view
The application SHALL offer Complete Value and Ctrl+Space in ordinary and pinned cell editors. Suggestions SHALL be distinct non-NULL values from the same column of the full current selection, including unloaded rows and committed unsaved edits. Prefix matching SHALL be literal and case sensitive, including an empty prefix. At most 100 values SHALL be returned in Unicode code point order and truncation SHALL be indicated.

#### Scenario: Complete an edited filtered column
- **WHEN** the user requests completion in a filtered or sorted view with unsaved edits
- **THEN** matching values SHALL include edited and unloaded rows that satisfy the current filters and SHALL exclude deleted or filtered-out rows

### Requirement: Cancellable bounded completion
Completion SHALL run in the background with bounded candidate storage and use the existing operation cancellation mechanism. Closing the editor or changing the prefix or document selection SHALL invalidate its request. Results SHALL NOT be applied to another editor, column, or tab.

#### Scenario: Cancel or supersede a request
- **WHEN** a request is cancelled or its editor, prefix or document changes before completion
- **THEN** the worker SHALL stop cooperatively and its result SHALL NOT open or alter a popup

### Requirement: Completion preserves cell editing history
Accepting a suggestion SHALL only replace editor text. Confirming the cell SHALL create one normal Undo operation; dismissing suggestions SHALL NOT commit a cell edit.

#### Scenario: Accept and undo a suggestion
- **WHEN** a user accepts a suggestion and confirms the cell
- **THEN** one Undo SHALL restore the previous value and Redo SHALL restore the accepted value
