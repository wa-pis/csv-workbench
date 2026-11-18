## Why

Large filtered CSV selections should be shareable as JSON or Markdown without copying the whole result into memory.

## What Changes

Add background JSON and Markdown file exports of the full current selection, including unloaded rows and unsaved edits.

## Capabilities

### Modified Capabilities

- `csv-preview`: Stream selection exports in JSON and Markdown.

## Impact

Core export writer and File menu; reuse existing query, cancellation, destination checks and serializers.
