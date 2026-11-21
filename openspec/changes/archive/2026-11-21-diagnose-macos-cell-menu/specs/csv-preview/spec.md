## ADDED Requirements

### Requirement: Opt-in keyboard routing diagnostics

The application SHALL support an opt-in JSONL trace of keyboard codes, native codes, modifiers, focus classes, shortcut ambiguity and cell menu stages. Diagnostics SHALL NOT consume events or record event text, document values or file names. Storage SHALL be bounded to a 1 MiB log and one backup. An unavailable output path SHALL NOT prevent application startup.

#### Scenario: Trace keyboard menu routing

- **WHEN** CSV_WORKBENCH_KEY_LOG names a writable file and the user sends Shift+F10
- **THEN** delivered Qt events and any shortcut/menu invocation SHALL be logged with focus and receiver classes
- **AND** absence of a delivered key event SHALL NOT be reported as a proven QAction failure

#### Scenario: Diagnostics disabled

- **WHEN** CSV_WORKBENCH_KEY_LOG is unset
- **THEN** the application SHALL NOT install a keyboard trace or create a trace file

### Requirement: Verify the actual cell actions popup

Keyboard menu regression tests SHALL verify the real active and visible QMenu, including pinned cells and inline editors. A main-window screenshot without the popup SHALL NOT by itself establish a keyboard routing failure.

#### Scenario: Open the real popup

- **WHEN** Shift+F10 is delivered to an ordinary or pinned cell or its inline editor
- **THEN** a visible QMenu SHALL become the active popup exactly once
- **AND** the trace SHALL record request, valid target, opening and closing in order
