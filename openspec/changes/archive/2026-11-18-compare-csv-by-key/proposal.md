## Why

Users need to identify changes between two saved CSV versions without loading millions of rows into the GUI.

## What Changes

Compare saved files by one chosen key and show added, removed and changed records. Read both file formats independently. Reject duplicate or empty keys and incompatible schemas. Keep the result on disk and page through it.

## Capabilities

### Modified Capabilities

- `csv-preview`: Compare saved CSV files by key.

## Impact

Core comparison query, a background File command and a paged read-only result dialog.
