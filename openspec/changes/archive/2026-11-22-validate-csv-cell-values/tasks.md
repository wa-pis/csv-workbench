## 1. Implementation

- [x] Добавить типы и согласованные локальную/SQL проверки всей выборки.
- [x] Сохранить правила в обычных и многовкладочных сессиях с валидацией входа.
- [x] Сохранить различие NULL/empty во всех путях чтения, правок и записи.
- [x] Добавить настройки, подсветку и фоновый job с отменой/защитой от устаревшего результата.
- [x] Добавить юнит-тесты типов, неподгруженных строк, правок, сессий и отмены.
- [x] Выполнить проверки, обновить README и roadmap.

## Validation

- `ruff check src tests` — успешно.
- `pytest -q --ignore=tests/test_macos_accessibility.py` — 292 passed, включая 29 новых backend/UI тестов.
- Покрыты согласованность Python/SQL, скрытые столбцы, неподгруженные строки, дисковые правки/добавление/удаление, фильтры, сессии, NULL/empty через Polars/Save/Export, подсветка, Undo/Redo, отмена и запоздалые результаты.
- `openspec validate validate-csv-cell-values --strict` — успешно.
- Нативный Cocoa accessibility regression — 1 passed; всего 293 теста.
- `openspec validate --all --strict` и `git diff --check` — успешно.
