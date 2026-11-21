## 1. Implementation

- [x] 1.1 Добавить включаемую диагностику событий, фокуса и этапов меню.
- [x] 1.2 Проверить отсутствие текста, ротацию и ошибки пути юнит-тестами.
- [x] 1.3 Получить журнал нативного воспроизведения macOS и установить причину.
- [x] 1.4 Устранить ложный отрицательный результат проверки: тестировать реальный popup и трассу, не менять подтверждённо рабочую маршрутизацию.
- [x] 1.5 Выполнить pytest, Ruff, OpenSpec и обновить README/roadmap.

Validation: 243 pytest passed; Ruff and strict OpenSpec passed. Startup smoke tests passed with writable and unavailable log paths. Native cocoa trace on 5M-row CSV confirmed Shift+F10 -> QAction -> QMenu -> Edit Cell, including reopening from the editor.

## 2. Ручная приёмка автономной сборки

- [x] 2.1 Скачать Apple Silicon CI-сборку `3a52c0c` и включить диагностику.
- [x] 2.2 Проверить Shift+F10 в обычной и закреплённой ячейке на CSV с 5 млн строк.
- [x] 2.3 Повторить оба случая из встроенного редактора и подтвердить реальный QMenu по журналу.
- [x] 2.4 Сохранить [отчёт и трассу](manual-validation.md), обновить roadmap и закрыть этап.

Ручная приёмка завершена 21 ноября 2026 года: все четыре случая успешны. CSV не изменялся; тестовый экземпляр закрыт.
