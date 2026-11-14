# desktop-distribution Specification

## Purpose
Запускать CSV Workbench без отдельно установленного Python.

## Requirements

### Requirement: Автономная сборка
Проект SHALL собираться отдельно на macOS, Windows и Linux с включёнными Python, Qt и DuckDB.

#### Scenario: Проверка бинарника
- **WHEN** автономный бинарник запускается на целевой платформе
- **THEN** он инициализирует Qt, открывает CSV через DuckDB и экспортирует выборку
