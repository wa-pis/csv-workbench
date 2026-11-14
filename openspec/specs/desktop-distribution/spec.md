# desktop-distribution Specification

## Purpose
Запускать CSV Workbench без отдельно установленного Python.

## Requirements

### Requirement: Автономная сборка
Проект SHALL собираться отдельно на Windows, Linux, macOS Apple Silicon и macOS Intel с включёнными Python, Qt и DuckDB.

#### Scenario: Проверка бинарника
- **WHEN** автономный бинарник запускается на целевой платформе
- **THEN** он инициализирует Qt, открывает CSV через DuckDB и экспортирует выборку

#### Scenario: Выбор пакета macOS
- **WHEN** матрица CI завершает обе сборки macOS
- **THEN** она публикует отдельные артефакты ARM64 и X64
