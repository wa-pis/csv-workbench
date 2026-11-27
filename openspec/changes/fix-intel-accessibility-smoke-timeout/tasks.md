## 1. Diagnose and Implement

- [x] 1.1 Точные source/frozen таймауты Intel `2cc02d7` и CI jobs записаны в verification.md; добавлена сбрасываемая диагностика фаз.
- [x] 1.2 Подтвердить полное разворачивание выделения в Qt по Rosetta sample точного Intel-bundle `1b7832e` и исходникам Qt 6.12; записать цепочку, метрики и ограничения macOS 26/CI 15 в design.md.
- [x] 1.3 На macOS вызван базовый `QAbstractItemView.selectionChanged`; нативные проверки, базовая перерисовка и обычный путь остальных ОС сохранены.
- [x] 1.4 Регрессия flags/selection/Paint двух pane: до исправления 2 failed на 800 000 flags, после 10 passed за 1,16 с. Диагностика: 3 passed за 0,46 с; retry/skip не добавлены.
- [x] 1.5 Полный pytest: 425 passed за 25,97 с, включая native Cocoa; Ruff, git diff --check и строгая проверка OpenSpec прошли. Дополнительный assertion активности AX проверяется отдельно, см. verification.md.

## 2. Standalone Verification and Closure

- [ ] 2.1 Подтвердить нативный source smoke и smoke автономного PyInstaller-bundle на macOS Intel; записать фазы и длительности.
- [ ] 2.2 Подтвердить автономные сборки одного итогового SHA на Windows, Linux, macOS Intel и Apple Silicon, включая обязательный frozen Cocoa smoke на обеих macOS.
- [ ] 2.3 Зафиксировать подтверждённую причину, исправление, CI/артефакты и ограничения; обновить roadmap и архивировать этап.
