## Implementation
- [x] 1. Запрос подсказок по всей выборке с правками и ограниченной памятью.
- [x] 2. Редактор, Complete Value/Ctrl+Space, фоновые задачи, отмена и защита от устаревших ответов.
- [x] 3. Юнит-тесты движка и Qt, включая закрепление, Undo и отмену.
- [x] 4. Замеры времени, RAM и временного диска на 5 и 20 млн строк, низкая/высокая кардинальность.
- [x] 5. Проверки, документация, roadmap и архив OpenSpec.

## Validation

259 pytest tests passed (16 new), Ruff passed, strict OpenSpec and git diff --check passed. Qt offscreen tests exercise the real popup, ordinary/pinned cells, Ctrl+Space, debounce, cancelled/queued work, stale editors, tab switches and one-step Undo/Redo. Native packaged UI was not manually exercised in this stage.

[Benchmark report](benchmarks.md) and [raw measurements](benchmarks.json): 5M and 20M rows; low/high cardinality, last-row edited prefix and active cancellation. At 20M, high cardinality: 6.66 s, 410 MiB process peak RSS, 93.5 MiB sampled temporary disk.
