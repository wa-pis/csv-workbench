## ADDED Requirements

### Requirement: Bounded macOS accessible CSV viewport

On macOS the application SHALL expose visible CSV cells, their values, coordinates, selection and focus through a bounded accessible viewport, retaining keyboard navigation and native editor access without enumerating all buffered cells or disabling Accessibility.

#### Scenario: Wide selected buffer
- **WHEN** an accessibility client traverses a 200-row, 2000-column CSV buffer with all cells selected
- **THEN** the hierarchy and retained cell cache are bounded by the viewport and the application remains responsive to operation cancellation

#### Scenario: Changed viewport and pinned edits
- **WHEN** the user scrolls, changes columns, resets the model or edits a pinned cell
- **THEN** accessible cells represent valid current indices and values, native editors remain reachable and stale cells do not accumulate
