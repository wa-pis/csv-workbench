## 1. Implementation

- [x] Сохранить NULL/empty при разборе предпросмотра, включая настройки и границы записей.
- [x] Добавить backend и UI регрессионные тесты.
- [x] Прогнать проверки и зафиксировать прошлую ручную проверку в roadmap.

## Validation

- `pytest -q tests/test_csv_format.py tests/test_ui.py` — 69 passed.
- `pytest -q --ignore=tests/test_macos_accessibility.py` — 304 passed (12 новых параметризованных backend/UI сценариев).
- `ruff check src tests`, `git diff --check` — успешно.
- `openspec validate distinguish-preview-null-empty --strict` — успешно.
- В roadmap записана выполненная 2026-11-23 ручная проверка CI b25a343 на 5 млн строк и ссылка на подробный отчёт. Новый код предпросмотра проверен тестами.

- [x] Ручная проверка новой автономной сборки Apple Silicon `0419d57` выполнена 2026-11-23: NULL/empty, запятая/semicolon, двойная кавычка/апостроф и обратное переключение; результат успешный, без падений. Подробности и ограничения: [manual-check.md](manual-check.md).
