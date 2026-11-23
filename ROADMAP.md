# CSV Workbench — roadmap

Локальное настольное приложение на Python для просмотра, отбора, сортировки и экспорта CSV на macOS, Windows и Linux. Исходные файлы меняются только при явном сохранении. Базовый стек: Python 3.12+, uv, PySide6, DuckDB, pytest и Ruff.

**[v0.1.0](https://github.com/wa-pis/csv-workbench/releases/tag/v0.1.0) выпущен 16 ноября 2026 года.** Первый публичный релиз включает просмотр и редактирование CSV/TSV, фильтры, сортировку, уникальные значения, настраиваемый экспорт и сессии вкладок. В релизе доступны автономные сборки для Windows x64, Linux x64, macOS Intel и macOS Apple Silicon. [CI релизного коммита](https://github.com/wa-pis/csv-workbench/actions/runs/43760681420) прошёл на всех четырёх платформах.

## v0.2: повседневная работа с CSV

Ориентир — [Edit as Table в IntelliJ IDEA](https://www.jetbrains.com/help/idea/editing-csv-and-tsv-files.html) и [редактор данных DataGrip](https://www.jetbrains.com/help/datagrip/data-editor-and-viewer.html). Уже есть редактирование ячеек и диапазонов, операции со строками, Undo/Redo, составная сортировка через Alt, фильтры, выбор видимых столбцов и экспорт CSV/TSV. Для больших файлов сохраняем ограниченный буфер и отменяемые фоновые операции.

| Порядок | OpenSpec change | Что ещё отличается и как проверить готовность | Статус |
| --- | --- | --- | --- |
| 1. Большие файлы во вкладках | [`large-csv-multitab-performance`](openspec/changes/archive/2026-11-16-large-csv-multitab-performance/proposal.md) | Сортировка и unique не блокируют переключение вкладок. Отмена действует на активные и ожидающие задачи. [Замеры на 5 и 20 млн строк](BENCHMARK.md#v02-сортировка-и-unique-в-двух-вкладках). | Закрыт |
| 2. Переход к строке | [`go-to-csv-row`](openspec/changes/archive/2026-11-18-go-to-csv-row/proposal.md) | Переход по номеру в текущей выборке с фоновой подгрузкой и ограниченным буфером. | Закрыт; проверен тестами |
| 3. Поиск по таблице | [`find-in-csv-view`](openspec/changes/archive/2026-11-18-find-in-csv-view/proposal.md) | Ctrl/⌘+F, подсветка и переход в обе стороны по всей текущей выборке с правками. Отмена оставляет таблицу на месте. | Закрыт; тесты и поиск последней строки на 20 млн |
| 4. Редактор значения | [`edit-csv-cell-value`](openspec/changes/archive/2026-11-18-edit-csv-cell-value/proposal.md) | Shift+Enter открывает многострочный редактор. Подтверждение добавляет одну операцию Undo, Escape отменяет правку, Save и Export сохраняют переносы. | Закрыт; проверен тестами |
| 5. Управление столбцами | [`manage-csv-columns`](openspec/changes/archive/2026-11-18-manage-csv-columns/proposal.md) | Скрытие из заголовка, поиск, восстановление и перетаскивание столбцов. Сессия хранит раскладку; экспорт следует ей, Save сохраняет исходный порядок файла. | Закрыт; проверен тестами |
| 6. Быстрый фильтр из ячейки | [`quick-filter-csv-values`](openspec/changes/archive/2026-11-18-quick-filter-csv-values/proposal.md) | Точное сравнение из контекстного меню, отдельное удаление условий в Selection; один предикат для таблицы, unique и экспорта, включая пустые значения. | Закрыт; проверен тестами |

Все запланированные этапы v0.2 закрыты.

**После v0.2:** [`pin-csv-columns`](openspec/changes/archive/2026-11-18-pin-csv-columns/proposal.md) закрыт. Столбцы можно закрепить слева через меню заголовка; настройки сохраняются в сессии.

[`csv-record-view`](openspec/changes/archive/2026-11-18-csv-record-view/proposal.md) закрыт. Карточка показывает видимые поля текущей строки с несохранёнными правками.

[`transpose-csv-range`](openspec/changes/archive/2026-11-18-transpose-csv-range/proposal.md) закрыт. Просмотр транспонированного прямоугольного диапазона до 10 000 ячеек с текущими правками без подгрузки CSV.

[`csv-text-preview`](openspec/changes/archive/2026-11-18-csv-text-preview/proposal.md) закрыт. Переключатель Table/Text показывает первые 128 Ки символов сохранённого файла в выбранной кодировке; чтение фоновое, правки таблицы сохраняются.

[`csv-clipboard-formats`](openspec/changes/archive/2026-11-18-csv-clipboard-formats/proposal.md) закрыт. Copy As поддерживает TSV, CSV, JSON и Markdown с текущими правками; лимиты 10 000 ячеек и 4 МиБ.

[`export-json-markdown`](openspec/changes/archive/2026-11-18-export-json-markdown/proposal.md) закрыт. Потоковый экспорт всей выборки в JSON и Markdown, включая неподгруженные строки и правки; отмена сохраняет прежний файл.

[`compare-csv-by-key`](openspec/changes/archive/2026-11-18-compare-csv-by-key/proposal.md) закрыт. Сравнение сохранённых CSV по уникальному непустому ключу: добавленные, удалённые и изменённые записи, значения до/после, дисковый результат и фоновое чтение страниц. Наборы имён столбцов должны совпадать; порядок может отличаться.

[`recover-unsaved-csv-edits`](openspec/changes/archive/2026-11-18-recover-unsaved-csv-edits/proposal.md) закрыт. Фоновый журнал несохранённых правок отдельно от CSV; предложение восстановления при запуске, проверка ревизии источника, история Undo/Redo и операции со строками. Тестами проверены аварийное завершение процесса, несколько вкладок и ошибки записи.

[`replace-in-csv-view`](openspec/changes/archive/2026-11-20-replace-in-csv-view/proposal.md) закрыт. Поиск и замена первого/всех совпадений во всей текущей выборке с правками: предпросмотр, отмена, проверка устаревшего плана и один шаг Undo/Redo. Предпросмотр ограничен 100 ячейками; применение охватывает все найденные изменения.

[`disk-backed-csv-edits`](openspec/changes/archive/2026-11-20-disk-backed-csv-edits/proposal.md) закрыт. Правки и Undo/Redo на диске, потоковая массовая замена, Parquet для запросов, восстановление без загрузки всех ячеек в RAM. [Замеры на 5 и 20 млн строк](openspec/changes/archive/2026-11-20-disk-backed-csv-edits/benchmarks.md).

Базовые этапы завершены. Четыре этапа полировки взаимодействия с таблицей также завершены (ниже).

Подключения к СУБД, импорт в базу и функции самой IDE вне этого плана.

Исправление фокуса клавиатурного меню ячейки: [`fix-csv-cell-menu-focus`](openspec/changes/archive/2026-11-21-fix-csv-cell-menu-focus/proposal.md) — завершено; 235 тестов прошли.

Диагностика macOS и проверка реального popup: [`diagnose-macos-cell-menu`](openspec/changes/archive/2026-11-21-diagnose-macos-cell-menu/proposal.md) — закрыт; 243 теста прошли. [Ручная приёмка Apple Silicon](openspec/changes/archive/2026-11-21-diagnose-macos-cell-menu/manual-validation.md) CI-сборки `3a52c0c` от 21 ноября 2026 года подтвердила все четыре случая на CSV с 5 млн строк: обычная и закреплённая ячейка, каждая до и во время редактирования. Реальный QMenu подтверждён журналом доставки Down/Enter и Escape.

## Полировка таблицы по сценариям JetBrains

[Сравнение, источники и намеренные отличия](GRID_POLISH.md). Этапы выполняются по порядку через OpenSpec и юнит-тесты.

| Порядок | OpenSpec change | Критерий готовности | Статус |
|---|---|---|---|
| 1 | `polish-csv-keyboard-navigation` | Стрелки, Tab и страницы проходят границы буфера; Go to Row поддерживает column:row | [Завершён](openspec/changes/archive/2026-11-20-polish-csv-keyboard-navigation/proposal.md) |
| 2 | `polish-csv-selection` | Select Row/Column и Extend/Shrink, понятные границы и размер выделения | [Завершён](openspec/changes/archive/2026-11-21-polish-csv-selection/proposal.md) |
| 3 | `polish-csv-cell-editing` | Enter/F2, двойной щелчок, Enter/Escape и один Undo | [Завершён](openspec/changes/archive/2026-11-21-polish-csv-cell-editing/proposal.md) |
| 4 | `polish-csv-context-menus` | Общие действия Edit, корректная цель ПКМ, Menu/Shift+F10 | [Завершён](openspec/changes/archive/2026-11-21-polish-csv-context-menus/proposal.md) |

## Автодополнение значений ячейки

Этап [`complete-csv-cell-values`](openspec/changes/archive/2026-11-21-complete-csv-cell-values/proposal.md) — **закрыт**. Ориентир: [автодополнение значений столбца в DataGrip](https://www.jetbrains.com/help/datagrip/settings-tools-database-data-views.html). Особенно полезно для категорий, статусов и других повторяющихся текстовых значений.

Реализовано и проверено юнит-тестами:

- Команда Complete Value в редакторе обычной и закреплённой ячейки; Ctrl+Space там, где сочетание не перехватывает ОС. Команда доступна из меню независимо от системной раскладки.
- Подсказки по буквальному префиксу из того же столбца всей текущей выборки, включая неподгруженные строки и несохранённые правки. Пустой префикс допустим; повторы исключены, порядок детерминирован, максимум 100 подсказок с указанием ограничения.
- Поиск выполняется в фоне с ограниченной памятью; закрытие редактора, изменение префикса или выборки отменяет/делает устаревшим предыдущий запрос. Результат не попадает в другую ячейку или вкладку.
- Стрелки выбирают подсказку, принятие вставляет её в редактор; только подтверждение ячейки создаёт один Undo. Escape закрывает подсказки без подтверждения правки.
- Юнит-тесты покрывают выборку с фильтром/сортировкой, правки, Unicode, закрепление, отмену и устаревшие ответы. Отдельный замер на 5 и 20 млн строк проверяет время, пик RAM и временный диск при низкой и высокой кардинальности столбца.

[Замеры на 5 и 20 млн строк](openspec/changes/archive/2026-11-21-complete-csv-cell-values/benchmarks.md): на 20 млн category — 3,42 с / 275 МиБ RSS / 0,58 МиБ временного диска; id — 6,66 с / 410 МиБ / 93,5 МиБ. Отмена активного запроса — около 2 мс в этом прогоне.

Агрегаты выделения реализованы отдельным этапом `aggregate-csv-selection` (см. ниже). Следующий кандидат, пока без утверждённого срока: виртуальное выделение диапазонов за пределами буфера в 600 строк. Перед реализацией каждого — отдельные OpenSpec proposal, сценарии и задачи.

## История v0.1

| Этап | OpenSpec change | Критерий готовности | Статус |
| --- | --- | --- | --- |
| 1. Просмотр | [`open-csv-preview`](openspec/changes/archive/2026-11-14-open-csv-preview/proposal.md) | `uv run csv-workbench` открывает окно; локальный CSV можно открыть и листать без загрузки всей таблицы в GUI. | Закрыт; проверен на macOS |
| 2. Выборка | [`filter-sort-csv`](openspec/changes/archive/2026-11-14-filter-sort-csv/proposal.md) | Визуальный фильтр и сортировка применяются ко всему набору; пользователь может оставить только нужные столбцы. | Закрыт; проверен тестами |
| Просмотр с прокруткой | [`lazy-csv-preview`](openspec/changes/archive/2026-11-14-lazy-csv-preview/proposal.md) | Фоновая догрузка при прокрутке и статистика файла. | Закрыт; проверен тестами |
| 3. Экспорт (v0.1) | [`export-csv-view`](openspec/changes/archive/2026-11-14-export-csv-view/proposal.md) | Все строки текущей выборки экспортируются в отдельный CSV в заданном порядке, включая строки ещё не загруженные в таблицу. Основной сценарий проверен на трёх ОС. | Закрыт; сценарий экспорта прошёл в CI на трёх ОС |
| 4. Большие файлы | [`scale-csv-operations`](openspec/changes/archive/2026-11-14-scale-csv-operations/proposal.md) | Интерфейс реагирует во время тяжёлых операций, их можно отменить; опубликованы замеры времени, RAM и временного диска на небольшом и многогигабайтном CSV. | Закрыт; [замеры на macOS](BENCHMARK.md) |
| 5. Поставка | [`package-desktop-app`](openspec/changes/archive/2026-11-14-package-desktop-app/proposal.md) | Отдельная сборка запускается без установки Python на каждой из трёх ОС. | Закрыт; сборки и проверка бинарника прошли в CI на трёх ОС |

**Поддержка больших CSV в v0.1.0:** [`large-csv-sort-and-distinct`](openspec/changes/archive/2026-11-14-large-csv-sort-and-distinct/proposal.md). Открытие CSV на 5 млн записей, контрольный прогон на 20 млн, сортировка всей выборки и уникальные значения с частотой. Реализованы буфер на 600 строк, дисковый результат сортировки и уникальные значения. [Замеры на 5 и 20 млн](BENCHMARK.md) подтвердили снижение пика RAM после внедрения Polars: сортировка — 941 МиБ, unique по ID — 402 МиБ на широком файле из 20 млн строк.

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

Открытие CSV/TSV перетаскиванием: [`drop-csv-files`](openspec/changes/archive/2026-11-15-drop-csv-files/proposal.md) закрыт.

Вкладки CSV/TSV: [`tabbed-csv-documents`](openspec/changes/archive/2026-11-16-tabbed-csv-documents/proposal.md) закрыт.

Сохранение и восстановление вкладок: [`workspace-csv-sessions`](openspec/changes/archive/2026-11-16-workspace-csv-sessions/proposal.md) закрыт.

Roadmap задаёт порядок и критерии готовности. Каждый этап начинается с предложения, сценариев, дизайна и списка задач в `openspec/changes/`. После реализации и проверки изменение архивируется, а согласованное поведение попадает в `openspec/specs/`.


Исправление macOS Accessibility: [`fix-macos-table-accessibility`](openspec/changes/archive/2026-11-22-fix-macos-table-accessibility/proposal.md) выполнено. Upstream-дефект Qt воспроизведён до исправления; официальный Qt 6.12.1 закреплён для macOS. Нативная регрессия включена в автономную сборку; ручная проверка подсказок, правок, Undo и активной отмены на 20 млн строк прошла без падений. После выхода стабильного PySide6 6.12.1 заменить временный snapshot.

## Проверка значений столбцов

[`validate-csv-cell-values`](openspec/changes/archive/2026-11-22-validate-csv-cell-values/proposal.md) закрыт: типы text/integer/number/date/boolean, nullable, локальная подсветка и объяснение ошибок; правила сохраняются по вкладкам. Фоновая отменяемая проверка всей выборки учитывает скрытые настроенные столбцы, неподгруженные строки и правки. NULL и пустые строки различаются при чтении, редактировании, Undo/Redo, Save и Export. Проверено юнит-тестами. [Ручная проверка автономной сборки Apple Silicon b25a343 от 2026-11-23](openspec/changes/archive/2026-11-22-validate-csv-cell-values/manual-check.md) прошла на 5 млн строк / 182 777 804 байт: типы и подсветка, NULL/empty в основной таблице, полный проход (4 ожидаемые ошибки), активная отмена и восстановление всех правил/nullable из workspace. После восстановления повторный проход дал те же 4 ошибки. Падений и видимого зависания не наблюдалось; новые замеры пика RAM/диска не выполнялись.

[`distinguish-preview-null-empty`](openspec/changes/archive/2026-11-23-distinguish-preview-null-empty/proposal.md) закрыт: найденный при ручной проверке недочёт предпросмотра формата исправлен. Предпросмотр различает NULL и `""` с ручными разделителем, кодировкой и кавычками, включая одноколоночные файлы и переключение настроек. Исправление проверено backend/UI юнит-тестами. [Ручная проверка автономной сборки Apple Silicon 0419d57 от 2026-11-23](openspec/changes/archive/2026-11-23-distinguish-preview-null-empty/manual-check.md) прошла: NULL и пустая строка различаются; смена разделителя `,` ↔ `;` и кавычек `"` ↔ `'` сразу обновляет предпросмотр, включая обратное переключение. Падений не наблюдалось. TSV, другие кодировки и остальные ОС в этом ручном прогоне не проверялись.

## Агрегаты выделения

[`aggregate-csv-selection`](openspec/changes/archive/2026-11-24-aggregate-csv-selection/proposal.md) закрыт: команда в Edit/меню ячейки, нижняя панель count/NULL/empty/numeric/skipped и числовых sum/average/min/max. Расчёт учитывает правки в обычных и закреплённых столбцах, работает в фоне с отменой и сбросом устаревших результатов. Пересечения диапазонов не дублируют ячейки; арифметика sum/average — 38 значащих десятичных цифр. Проверено backend/UI юнит-тестами. Граница этапа — выбранные ячейки текущего буфера; виртуальные диапазоны всего CSV остаются следующим кандидатом. Ручной прогон автономной сборки в этот этап не входил.
