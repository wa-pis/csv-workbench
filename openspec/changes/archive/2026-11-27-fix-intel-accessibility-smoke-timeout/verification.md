# Проверка таймаута Accessibility macOS Intel — 2026-11-27

Исправление `db01999ed2eb3be4b55ebbb7a691c4f67bf81b87` прошло [Desktop builds 45956278622](https://github.com/wa-pis/csv-workbench/actions/runs/45956278622) на всех четырёх платформах с первой попытки. Source и frozen Cocoa smoke на Intel и Apple Silicon завершились успешно при прежнем внешнем лимите 60 с.

## Исходный CI

Проверялся `2cc02d761dcdb3a7a2cad0c95b6962a50999ba06`, [Desktop builds 45952329831](https://github.com/wa-pis/csv-workbench/actions/runs/45952329831). Этот коммит меняет документы; runtime совпадает с `1b7832e`.

| Попытка Intel | Фактический результат |
| --- | --- |
| [1, job 140350804861](https://github.com/wa-pis/csv-workbench/actions/runs/45952329831/job/140350804861) | Source `python -m csv_workbench.main --accessibility-smoke-test` превысил 60 с; `stdout_seq=[]`, `stderr_seq=[]`. Итог pytest: 1 failed, 419 passed за 168,79 с. |
| [2, job 140352356435](https://github.com/wa-pis/csv-workbench/actions/runs/45952329831/job/140352356435) | Source pytest: 420 passed за 158,19 с. Затем собранный `.app/Contents/MacOS/csv-workbench --accessibility-smoke-test` превысил 59,99985 с; шаг упаковки завершился ошибкой. |

Повторный успешный source-прогон не считается исправлением frozen-проверки. Прежний успешный [Intel CI `1b7832e`](https://github.com/wa-pis/csv-workbench/actions/runs/45897916923/job/140176297104) потратил 39,66 с до первого busy native-обхода; максимальный отдельный обход занял 1,398 с.

## Причина и независимые подтверждения

Официальные исходники Qt 6.12.0 показывают три звена:

- [QTableView::selectionChanged](https://github.com/qt/qtbase/blob/v6.12.0/src/widgets/itemviews/qtableview.cpp#L3370-L3394) при активной Accessibility вызывает `selected.indexes().value(0)` и `deselected.indexes().value(0)` ради первого индекса для уведомления.
- [QItemSelection::indexes и построение индексов диапазона](https://github.com/qt/qtbase/blob/v6.12.0/src/corelib/itemmodels/qitemselectionmodel.cpp#L280-L305) перебирают ячейки и проверяют `model()->flags`.
- [QAbstractItemView::selectionChanged](https://github.com/qt/qtbase/blob/v6.12.0/src/widgets/itemviews/qabstractitemview.cpp#L3554-L3560) сохраняет базовое обновление областей viewport без этого списка индексов.

Точный Intel-артефакт `1b7832eefc164da3c721f47dfd72ae27a9508c1b`: [15154088472](https://github.com/wa-pis/csv-workbench/actions/runs/45897916923/artifacts/15154088472), SHA-256 внутреннего ZIP `ca9b06ea41bec016bab9e6857b98fc4d40ae40549dcb2eabd782f03bace7d768`. Запускался через `arch -x86_64` под Rosetta на arm64/macOS 26.6.2; это не CI macOS 15.7.9 x86_64.

Локальные исходные записи: `/private/tmp/csv-intel-1b7832e/native.result.json` и `/private/tmp/csv-intel-1b7832e/native.sample-30s.txt`. Sample на ~30 с содержит цепочку `selectAll` → `emitSelectionChanged` → `QTableView::selectionChanged` → `QItemSelection::indexes`; она присутствует в 267 из 274 samples основного потока, внутри видны Python-backed `flags`/индексы модели. Это локализует разворачивание полного выделения до первого измеряемого native-обхода.

Старый bundle в этом Rosetta-прогоне в итоге завершился успешно: 15 настоящих операций worker до отмены, максимум native-обхода 1,01293 с. Наблюдаемые 48,573 с включают синхронные вызовы `/usr/bin/sample` и не являются чистым замером производительности. Sample на 15 с показывает холодный импорт frozen-модулей; traceback на 45 с не даёт надёжной Python-атрибуции. Polars предупреждал об особенностях CPU под Rosetta. Эти ограничения не отменяют подтверждённую цепочку Qt, но не позволяют переносить локальное время на Intel CI.

Детерминированный счётчик `flags` на буфере 200 × 2000 с общей selection model основной и закреплённой таблиц:

| Обработчик | Полное выделение | Переход к одной ячейке |
| --- | ---: | ---: |
| Старый путь QTableView | 800 000 | 800 002 |
| Прямой базовый QAbstractItemView | 0 | 4 |

Подмена только Python `selectedIndexes()` этого C++-пути не обнаруживает.

## Исправление и регрессии

На macOS `AccessibleTableView.selectionChanged` вызывает базовый `QAbstractItemView` для обоих pane. Существующий Grouping-интерфейс и ограниченные Accessibility-уведомления сохранены; на других ОС используется прежний `super()`. Реальный Cocoa, QTBUG-149612, wide viewport, фокус обычной/закреплённой ячейки, редактор, живой CSV worker и его отмена не удалены. Внешние ограничения 60 с не увеличены; skip и retry в качестве исправления не добавлены.

Диагностика немедленно сбрасывает JSON-фазы до/после создания QApplication, legacy-части, выделения/смены текущей ячейки, native-обходов, fixture, worker/cancel и cleanup. При таймауте сохраняется последняя достигнутая фаза; ошибка не превращается в успех. Время фаз начинается после импорта модуля smoke и не включает весь холодный запуск frozen-процесса.

Регрессия с активной Accessibility и счётчиком `flags` до исправления, при восстановленном старом callback, дала **2 failed**, каждый на 800 000 вызовов. После исправления **10 accessibility-тестов прошли за 1,16 с**, включая выделение, доступные состояния и Paint обеих областей. **3 диагностических теста прошли за 0,46 с**, проверив flush и сохранение результата/ошибки probe. Полный локальный pytest: **425 passed за 25,97 с**, включая настоящий Cocoa smoke; Ruff, `git diff --check` и строгая проверка OpenSpec прошли. После добавления assertion `QAccessible.isActive()` в unit/native-проверки **14 targeted tests прошли за 3,45 с**, включая Cocoa; full-suite результат выше получен до этого assertion. После замены списка вызовов `flags` на счётчик с постоянной памятью **13 ненативных тестов прошли за 1,13 с**.

Дополнительный source Cocoa-прогон ARM с проверкой `QAccessible.isActive()` завершил cleanup за 2,334 с по внутренним фазам: select-all — 0 `flags` / 0,0000507 с; смена текущей ячейки при worker — 5 `flags` / 0,001019 с; максимум native-обхода — 0,423614 с, число узлов 194/194/195/194/194/194/194. Wide-часть заняла 1,75969 с. Локальный журнал: `/private/tmp/csv-intel-selection-fix-arm-source.log`. Это source ARM, не исправленный frozen Intel и не новый CI.

## Приёмка CI

Все задания [run 45956278622](https://github.com/wa-pis/csv-workbench/actions/runs/45956278622) проверены на одном SHA `db01999ed2eb3be4b55ebbb7a691c4f67bf81b87`, attempt 1. В каждом прошли source pytest, сборка PyInstaller, smoke executable и upload artifact. На обеих macOS дополнительно прошёл обязательный frozen Cocoa smoke; ошибка этого subprocess по-прежнему блокирует сборку.

| Платформа | CI job | Автономная сборка |
| --- | --- | --- |
| Windows x64 | [140363371116](https://github.com/wa-pis/csv-workbench/actions/runs/45956278622/job/140363371116) — success | [15178668740](https://github.com/wa-pis/csv-workbench/actions/runs/45956278622/artifacts/15178668740) |
| Linux x64 | [140363371125](https://github.com/wa-pis/csv-workbench/actions/runs/45956278622/job/140363371125) — success | [15179560154](https://github.com/wa-pis/csv-workbench/actions/runs/45956278622/artifacts/15179560154) |
| macOS Intel | [140363371120](https://github.com/wa-pis/csv-workbench/actions/runs/45956278622/job/140363371120) — success | [15178389570](https://github.com/wa-pis/csv-workbench/actions/runs/45956278622/artifacts/15178389570) |
| macOS Apple Silicon | [140363370685](https://github.com/wa-pis/csv-workbench/actions/runs/45956278622/job/140363370685) — success | [15179590020](https://github.com/wa-pis/csv-workbench/actions/runs/45956278622/artifacts/15179590020) |

GitHub API подтвердил все четыре артефакта и их принадлежность этому SHA; на момент проверки ни один не истёк. Новая ручная GUI-приёмка, полный VoiceOver и benchmark 5/20 млн строк этим отчётом не заявляются.

Фазы frozen Cocoa smoke из полных журналов CI:

| Метрика | macOS Intel | Apple Silicon |
| --- | ---: | ---: |
| Source pytest | 425 passed / 90,00 с | 425 passed / 62,25 с |
| От первой фазы до table-cleanup-end | 8,72849 с | 5,58922 с |
| Wide-часть | 6,45981 с | 4,06338 с |
| Максимальный native-обход (12 обходов) | 1,43887 с | 0,76981 с |
| selectAll: flags / время обработчика | 0 / 0,000201 с | 0 / 0,000087 с |
| Переход к одной ячейке при worker: flags / время | 5 / 0,002867 с | 5 / 0,001855 с |
| Worker cancel-start → cancel-end | 0,010110 с | 0,004459 с |

В обоих frozen-прогонах `busy_native_cancelled=true`, по две завершённые реальные CSV-операции до отмены. Время до cleanup измерено внутренним monotonic clock фаз: оно исключает импорт модулей и запуск процесса и не подменяет внешний лимит 60 с. Native-обход и фокус/редактор выполнялись при живом worker. Локальная копия полных журналов и извлечённых записей: `/private/tmp/csv-db01999-ci/` (`intel-full.log`, `arm-full.log`, `report.json`).
