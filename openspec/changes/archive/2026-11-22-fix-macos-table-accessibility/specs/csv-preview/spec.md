## ADDED Requirements

### Requirement: Native macOS table accessibility remains valid

Приложение SHALL сохранять валидный accessibility-интерфейс обычной и закреплённой таблицы при чтении выбранных ячеек и смене столбцов. Accessibility MUST оставаться включённой.

#### Scenario: Native client reads selected cells after model changes

- **WHEN** нативный клиент читает строки, выделяет строки и запрашивает выбранные ячейки при смене числа столбцов
- **THEN** интерфейс таблицы остаётся валидным, выбранные ячейки доступны, процесс не завершается

#### Scenario: Standalone macOS build is verified

- **WHEN** собирается автономное приложение macOS
- **THEN** нативная регрессионная проверка выполняется на упакованном Qt, а ошибка блокирует сборку
