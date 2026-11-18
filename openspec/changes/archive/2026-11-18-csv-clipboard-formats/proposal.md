## Why

Users need to share small CSV ranges as structured text without creating an export file.

## What Changes

Add Copy As choices for TSV, CSV, JSON and Markdown. Limit copying to 10,000 cells and 4 Mi UTF-8 bytes, preserving the clipboard on rejection. Keep ordinary Copy as TSV.

## Capabilities

### Modified Capabilities

- `csv-preview`: Copy selected ranges in common text formats.

## Impact

Shared text serializers, Edit menu and tests. No changes to paste or file formats.
