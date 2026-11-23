## 1. Implementation

- [x] Добавить агрегатор со снимком данных, объединением диапазонов, правками и отменой.
- [x] Добавить команду и нижнюю панель результата, фоновые задачи и инвалидирование.
- [x] Добавить unit/backend и UI тесты чисел, NULL/empty, правок, выделения, отмены и устаревших результатов.
- [x] Обновить README и roadmap, выполнить проверки и закрыть OpenSpec.

## Validation

- `pytest -q --ignore=tests/test_macos_accessibility.py`: 316 passed (12 новых тестов агрегатов).
- `ruff check src tests`: passed; `git diff --check`: passed.
- Native Cocoa Accessibility и ручная проверка автономной сборки в этот прогон не входили.
