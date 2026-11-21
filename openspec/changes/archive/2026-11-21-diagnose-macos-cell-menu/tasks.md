## 1. Implementation

- [x] 1.1 Добавить включаемую диагностику событий, фокуса и этапов меню.
- [x] 1.2 Проверить отсутствие текста, ротацию и ошибки пути юнит-тестами.
- [x] 1.3 Получить журнал нативного воспроизведения macOS и установить причину.
- [x] 1.4 Устранить ложный отрицательный результат проверки: тестировать реальный popup и трассу, не менять подтверждённо рабочую маршрутизацию.
- [x] 1.5 Выполнить pytest, Ruff, OpenSpec и обновить README/roadmap.

Validation: 243 pytest passed; Ruff and strict OpenSpec passed. Startup smoke tests passed with writable and unavailable log paths. Native cocoa trace on 5M-row CSV confirmed Shift+F10 -> QAction -> QMenu -> Edit Cell, including reopening from the editor.
