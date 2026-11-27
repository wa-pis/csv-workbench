## 1. Diagnose and Implement

- [x] 1.1 Точные source/frozen таймауты Intel `2cc02d7` и CI jobs записаны в verification.md; добавлена сбрасываемая диагностика фаз.
- [x] 1.2 Подтвердить полное разворачивание выделения в Qt по Rosetta sample точного Intel-bundle `1b7832e` и исходникам Qt 6.12; записать цепочку, метрики и ограничения macOS 26/CI 15 в design.md.
- [x] 1.3 На macOS вызван базовый `QAbstractItemView.selectionChanged`; нативные проверки, базовая перерисовка и обычный путь остальных ОС сохранены.
- [x] 1.4 Регрессия flags/selection/Paint двух pane: до исправления 2 failed на 800 000 flags, после 10 passed за 1,16 с. Диагностика: 3 passed за 0,46 с; retry/skip не добавлены.
- [x] 1.5 Полный pytest: 425 passed за 25,97 с, включая native Cocoa; Ruff, git diff --check и строгая проверка OpenSpec прошли. После дополнительного assertion активности AX — 14 targeted tests passed за 3,45 с, см. verification.md.

## 2. Standalone Verification and Closure

- [x] 2.1 Нативный source smoke и smoke автономного PyInstaller-bundle на macOS Intel прошли с прежним лимитом 60 с; фазы и длительности см. verification.md.
- [x] 2.2 Все четыре автономные сборки `db01999` прошли с первой попытки в CI 45956278622, включая frozen Cocoa smoke на обеих macOS; артефакты подтверждены API.
- [x] 2.3 Подтверждённые причина, исправление, CI/артефакты и ограничения записаны в verification.md; roadmap обновлён, этап архивирован 2026-11-27.
