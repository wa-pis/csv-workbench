# Bound macOS CSV accessibility

## Why

Cocoa hierarchy queries on a buffered table of 200 rows × 2000 columns block the UI before an aggregate can be cancelled. The recorded sample is in Qt's synthetic table hierarchy, not the aggregate worker.

## What changes

- On macOS expose CSV views as accessible groups of visible cells, with a bounded cache, cell coordinates/names/values, selection/focus and actions. Keep native editors and scrollbars accessible. Do not disable Accessibility globally.
- Preserve shared pinned selection, edits, scrolling and model resets. Other platforms keep Qt's standard table interface.
- Add deterministic viewport and native Cocoa regressions, rebuild Apple Silicon and repeat manual aggregate cancellation.
