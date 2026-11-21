## ADDED Requirements

### Requirement: Cell context shortcut with child editor focus

Shift+F10 and Menu SHALL open the cell actions menu exactly once when focus is in the CSV table or its inline cell editor, including a pinned column. These shortcuts SHALL NOT open the cell menu from unrelated input fields.

#### Scenario: Invoke from an inline editor

- **WHEN** an editable ordinary or pinned cell has an active inline editor and the user invokes Shift+F10 or Menu
- **THEN** the current input SHALL be committed before the cell actions menu opens
- **AND** the committed edit SHALL be reversible with one Undo

#### Scenario: Invoke outside the table

- **WHEN** focus is in the Find input and the user presses Shift+F10
- **THEN** the cell actions menu SHALL NOT open and the CSV SHALL remain unchanged
