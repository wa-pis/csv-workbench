# Apple Silicon: проверка Shift+F10

Дата: 21 ноября 2026 года.
Коммит: 3a52c0cd5d8119a0f31fe7d7fc8c5a847ee63cc6.
CI: https://github.com/wa-pis/csv-workbench/actions/runs/44901113272
Артефакт: csv-workbench-macOS-ARM64 (14705178587).
CSV: 5 000 000 строк, 221 116 698 байт.
Журнал: [manual-validation.jsonl](manual-validation.jsonl), Qt cocoa. Сохранены интервалы четырёх вызовов меню из исходной трассы.

Для включения CSV_WORKBENCH_KEY_LOG добавлена LSEnvironment в локальный Info.plist; локальная копия повторно подписана ad hoc. Исходный ZIP сохранён. Код приложения не редактировался. Проверен путь исполняемого процесса.

| Ячейка | Фокус при вызове | Popup | Подтверждение |
|---|---|---|---|
| Обычная name | CsvTable | Успешно | QMenu получил Down + Return (Edit Cell); opening/closed |
| Обычная name | QLineEdit | Успешно | QMenu получил Escape; opening/closed |
| Закреплённая name | CsvTable | Успешно | QMenu получил Down + Return (Edit Cell); opening/closed |
| Закреплённая name | QLineEdit | Успешно | QMenu получил Escape; opening/closed |

Все четыре shortcut: Shift+F10, ambiguous=false. Все цели valid=true; закрепление подтверждено pinned=true. В сценариях без редактора Down/Return выполнили Edit Cell и закрыли меню с focus=QLineEdit; при вызове из редактора Escape был получен QMenu и закрыл его. Проверка не опирается на снимок главного окна.

CSV не сохранялся, значения ячеек не изменялись. Тестовый экземпляр закрыт, запись остановлена. Закрепление настроено через отдельный тестовый файл pinned.csvsession.

Итог: все четыре сценария ручной приёмки пройдены; этап закрыт. Проверка относится к Apple Silicon и функциональности popup: исходная подпись CI не проверялась после локальной переподписи для диагностики.
