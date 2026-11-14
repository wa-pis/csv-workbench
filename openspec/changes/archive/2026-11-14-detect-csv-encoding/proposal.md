## Why
CSV из разных систем может использовать кодировку, отличную от UTF-8.

## What Changes
Распознавать кодировку при чтении, сохраняя общий текст для всех операций.

## Capabilities
### New Capabilities

### Modified Capabilities
- `csv-preview`: автоматическое определение кодировки.

## Impact
Общий вход CSV и зависимость charset-normalizer.
