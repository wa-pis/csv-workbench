# CSV Workbench — roadmap

Локальное настольное приложение на Python для просмотра, отбора, сортировки и экспорта CSV на macOS, Windows и Linux. Исходные файлы меняются только при явном сохранении. Базовый стек: Python 3.12+, uv, PySide6, DuckDB, pytest и Ruff.

v0.1: запланированные этапы закрыты. [GitHub Actions](https://github.com/wa-pis/csv-workbench/actions/runs/43586328856) подтвердил сборку и основной сценарий на Windows, Linux и macOS; архивы доступны в разделе Artifacts. Отдельный GitHub Release не публиковался.

| Этап | OpenSpec change | Критерий готовности | Статус |
| --- | --- | --- | --- |
| 1. Просмотр | [`open-csv-preview`](openspec/changes/archive/2026-11-14-open-csv-preview/proposal.md) | `uv run csv-workbench` открывает окно; локальный CSV можно открыть и листать без загрузки всей таблицы в GUI. | Закрыт; проверен на macOS |
| 2. Выборка | [`filter-sort-csv`](openspec/changes/archive/2026-11-14-filter-sort-csv/proposal.md) | Визуальный фильтр и сортировка применяются ко всему набору; пользователь может оставить только нужные столбцы. | Закрыт; проверен тестами |
| Просмотр с прокруткой | [`lazy-csv-preview`](openspec/changes/archive/2026-11-14-lazy-csv-preview/proposal.md) | Фоновая догрузка при прокрутке и статистика файла. | Закрыт; проверен тестами |
| 3. Экспорт (v0.1) | [`export-csv-view`](openspec/changes/archive/2026-11-14-export-csv-view/proposal.md) | Все строки текущей выборки экспортируются в отдельный CSV в заданном порядке, включая строки ещё не загруженные в таблицу. Основной сценарий проверен на трёх ОС. | Закрыт; сценарий экспорта прошёл в CI на трёх ОС |
| 4. Большие файлы | [`scale-csv-operations`](openspec/changes/archive/2026-11-14-scale-csv-operations/proposal.md) | Интерфейс реагирует во время тяжёлых операций, их можно отменить; опубликованы замеры времени, RAM и временного диска на небольшом и многогигабайтном CSV. | Закрыт; [замеры на macOS](BENCHMARK.md) |
| 5. Поставка | [`package-desktop-app`](openspec/changes/archive/2026-11-14-package-desktop-app/proposal.md) | Отдельная сборка запускается без установки Python на каждой из трёх ОС. | Закрыт; сборки и проверка бинарника прошли в CI на трёх ОС |

**Следующий приоритет после v0.1:** [`large-csv-sort-and-distinct`](openspec/changes/archive/2026-11-14-large-csv-sort-and-distinct/proposal.md). Открытие CSV на 5 млн записей, контрольный прогон на 20 млн, сортировка всей выборки и уникальные значения с частотой. Реализованы буфер на 600 строк, дисковый результат сортировки и уникальные значения. [Замеры на 5 и 20 млн](BENCHMARK.md) подтвердили снижение пика RAM после внедрения Polars: сортировка — 941 МиБ, unique по ID — 402 МиБ на широком файле из 20 млн строк.

Условия AND/OR: [`combine-csv-filters`](openspec/changes/archive/2026-11-14-combine-csv-filters/proposal.md) закрыт.

Предпросмотр SQL: [`preview-selection-sql`](openspec/changes/archive/2026-11-14-preview-selection-sql/proposal.md) закрыт.

Редактор SQL: [`query-csv-with-sql`](openspec/changes/archive/2026-11-14-query-csv-with-sql/proposal.md) закрыт.

Сохраняемые сессии: [`save-csv-session`](openspec/changes/archive/2026-11-14-save-csv-session/proposal.md) закрыт.

Цепочки преобразований: [`reusable-csv-transformations`](openspec/changes/archive/2026-11-14-reusable-csv-transformations/proposal.md) закрыт. Запланированные этапы выполнены. Поддержку внешних форматов и второй движок отложить до конкретного сценария.

Сохранение CSV и история правок: [`save-and-undo-csv`](openspec/changes/archive/2026-11-15-save-and-undo-csv/proposal.md) закрыт.

Операции с диапазонами и строками: [`edit-csv-ranges-and-rows`](openspec/changes/archive/2026-11-15-edit-csv-ranges-and-rows/proposal.md) закрыт.

Элементы в заголовках и составная сортировка: [`column-header-controls`](openspec/changes/archive/2026-11-15-column-header-controls/proposal.md) закрыт.

Сборка macOS Intel: [`package-macos-intel`](openspec/changes/archive/2026-11-15-package-macos-intel/proposal.md) закрыт; [CI](https://github.com/wa-pis/csv-workbench/actions/runs/43642899420) подтвердил сборки Windows x64, Linux x64, macOS x64 и macOS ARM64.

Настройка формата открытия и предпросмотр CSV/TSV: [`configure-csv-format`](openspec/changes/archive/2026-11-15-configure-csv-format/proposal.md) закрыт.

Ручной выбор кодировки и символа кавычек: [`csv-encoding-and-quote`](openspec/changes/archive/2026-11-15-csv-encoding-and-quote/proposal.md) закрыт.

Настройки формата экспорта CSV/TSV: [`configure-csv-export`](openspec/changes/archive/2026-11-15-configure-csv-export/proposal.md) закрыт.

Пресеты формата открытия и экспорта: [`csv-format-presets`](openspec/changes/archive/2026-11-15-csv-format-presets/proposal.md) закрыт.

Управление пресетами CSV/TSV: [`manage-csv-format-presets`](openspec/changes/archive/2026-11-15-manage-csv-format-presets/proposal.md) закрыт.

Перенос пресетов CSV/TSV через JSON: [`transfer-csv-format-presets`](openspec/changes/archive/2026-11-15-transfer-csv-format-presets/proposal.md) закрыт.

Недавние файлы CSV/TSV: [`recent-csv-files`](openspec/changes/archive/2026-11-15-recent-csv-files/proposal.md) закрыт.

Управление недавними файлами: [`manage-recent-csv-files`](openspec/changes/archive/2026-11-15-manage-recent-csv-files/proposal.md) закрыт.

Roadmap задаёт порядок и критерии готовности. Каждый этап начинается с предложения, сценариев, дизайна и списка задач в `openspec/changes/`. После реализации и проверки изменение архивируется, а согласованное поведение попадает в `openspec/specs/`.
