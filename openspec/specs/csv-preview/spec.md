# csv-preview Specification

## Purpose
Открывать локальные CSV и просматривать их с подгрузкой при прокрутке без изменения исходных файлов.

## Requirements

### Requirement: Открытие локального CSV
Приложение SHALL открывать выбранный пользователем локальный CSV с автоматически определяемой кодировкой, с разделителем и режимом заголовка, выбранными при открытии, без записи в исходный файл.

#### Scenario: Открытие корректного файла
- **WHEN** пользователь выбирает CSV с заголовками, экранированной запятой внутри поля и значением `00123`
- **THEN** приложение показывает заголовки и первую порцию, сохраняя запятую внутри поля и начальные нули

#### Scenario: CSV только с заголовком
- **WHEN** файл содержит строку заголовка без записей
- **THEN** таблица показывает столбцы без строк и дополнительные строки не подгружаются

#### Scenario: CSV в другой кодировке
- **WHEN** пользователь открывает CSV в UTF-16 с BOM или в распознаваемой однобайтовой кодировке
- **THEN** просмотр, сортировка, частоты и экспорт используют декодированный текст, а исходник остаётся неизменным

#### Scenario: Настройка формата
- **WHEN** пользователь выбирает файл с запятыми, точкой с запятой или табуляцией
- **THEN** приложение предлагает разделитель по образцу и показывает предпросмотр
- **AND** пользователь может изменить разделитель и отключить заголовок перед открытием

#### Scenario: Файл без заголовка
- **WHEN** пользователь отключает заголовок
- **THEN** первая запись остаётся данными, а столбцы получают стабильные имена
- **AND** Save сохраняет разделитель и отсутствие заголовка, а сессия запоминает настройки

#### Scenario: Свой разделитель
- **WHEN** пользователь вводит один печатный ASCII-символ, отличный от двойной кавычки, в режиме Custom
- **THEN** предпросмотр и операции над файлом используют этот разделитель
- **AND** Save и Save As сохраняют разделитель, а сессия запоминает его

#### Scenario: Недопустимый разделитель
- **WHEN** свой разделитель пустой или не поддерживается
- **THEN** диалог показывает ошибку и блокирует открытие до исправления

#### Scenario: Ручная кодировка и кавычки
- **WHEN** пользователь выбирает кодировку или меняет символ кавычек
- **THEN** приложение сразу обновляет предпросмотр и применяет настройки при чтении всего файла
- **AND** в случае ошибки декодирования или недопустимой кавычки диалог показывает ошибку и позволяет изменить настройки

#### Scenario: Сохранение ручного формата
- **WHEN** пользователь сохраняет CSV через Save или Save As
- **THEN** приложение записывает файл с выбранной кавычкой и ручной кодировкой, а при автоматической кодировке пишет UTF-8
- **AND** если правку нельзя закодировать, исходный файл и существующий файл назначения остаются неизменными

#### Scenario: Повторное открытие сессии
- **WHEN** пользователь открывает сохранённую сессию с ручным форматом
- **THEN** приложение восстанавливает кодировку и символ кавычек, а у старых сессий без этих настроек использует Auto и двойную кавычку

#### Scenario: Открытие перетаскиванием
- **WHEN** пользователь перетаскивает один локальный файл CSV или TSV в окно приложения
- **THEN** приложение показывает тот же диалог формата с предпросмотром, что и при выборе файла через Open CSV
- **AND** отмена диалога оставляет текущую таблицу без изменений

#### Scenario: Неподдерживаемое перетаскивание
- **WHEN** пользователь перетаскивает несколько файлов, каталог, удалённый URL или файл другого типа
- **THEN** приложение игнорирует сброс

### Requirement: Видимые ошибки чтения
Приложение SHALL сообщать об ошибке открытия или догрузки строк и сохранять последнюю успешно показанную таблицу и позицию.

#### Scenario: Неудачная замена открытого файла
- **WHEN** пользователь выбирает недоступный файл при уже открытой таблице
- **THEN** приложение показывает ошибку, а текущая таблица и позиция прокрутки остаются на месте

### Requirement: Выборка по всему CSV
Приложение SHALL применять несколько текстовых условий по выбранным столбцам, текстовую сортировку по одному столбцу и выбор видимых столбцов до разбиения результата на страницы. Фильтр по подстроке не учитывает регистр; настройки фильтра и сортировки можно сбросить.

#### Scenario: Строки за пределами первой страницы
- **WHEN** пользователь применяет фильтр и сортировку, а совпадения находятся в том числе после исходной строки 200
- **THEN** результат начинается с первой страницы отфильтрованных и отсортированных строк, включая совпадения за пределами исходной страницы

#### Scenario: Видимые столбцы
- **WHEN** пользователь оставляет хотя бы один столбец и скрывает остальные
- **THEN** таблица отображает только выбранные столбцы в исходном порядке; фильтр и сортировка продолжают работать по столбцам источника

#### Scenario: Фильтр без совпадений
- **WHEN** фильтр не находит ни одной строки
- **THEN** таблица показывает выбранные заголовки без строк; дополнительные строки не подгружаются

#### Scenario: Несколько условий
- **WHEN** пользователь задаёт несколько буквальных подстрок и выбирает AND или OR
- **THEN** строка включается при совпадении всех или хотя бы одного условия соответственно; одинаковая выборка используется для просмотра, сортировки, unique и экспорта независимо от загруженных строк

#### Scenario: Условия удалены
- **WHEN** пользователь удаляет все условия или оставляет подстроки пустыми
- **THEN** фильтрация не исключает строки

### Requirement: Подгрузка при прокрутке
Приложение SHALL показывать первую порцию строк и добавлять последующие порции в конец таблицы при прокрутке к нижней границе без замены уже загруженных строк.

#### Scenario: Последняя запись
- **WHEN** пользователь прокручивает таблицу с 201 записью к концу первых 200 строк
- **THEN** строка 201 добавляется в конец, а строки 1–200 остаются доступны прокруткой

### Requirement: Статистика файла
Приложение SHALL показывать размер исходного файла, число исходных записей и число строк, уже загруженных в текущую таблицу.

#### Scenario: Подсчёт и догрузка
- **WHEN** открыт CSV из 201 записи и подгружена последняя строка
- **THEN** после завершения подсчёта статистика показывает 201 исходную и 201 загруженную строку

### Requirement: Экспорт текущей выборки в CSV
Приложение SHALL экспортировать все строки текущей выборки с учётом фильтра, сортировки и видимых столбцов в отдельный CSV или TSV. До выбора пути приложение SHALL позволять указать разделитель, кодировку, символ кавычек и наличие заголовка независимо от формата открытого файла. По умолчанию используются UTF-8 без BOM, запятая, двойные кавычки и заголовок.

#### Scenario: Часть строк ещё не подгружена
- **WHEN** пользователь экспортирует выборку, содержащую больше строк, чем загружено в таблицу
- **THEN** CSV содержит все совпадения в порядке выборки и только видимые столбцы

#### Scenario: Нет совпадений
- **WHEN** выборка пуста
- **THEN** CSV содержит только заголовки видимых столбцов, если они включены, иначе файл пуст

#### Scenario: Защита исходника и назначения
- **WHEN** выбран исходный файл или его псевдоним либо запись завершается ошибкой
- **THEN** приложение сообщает об ошибке, не меняя исходник и существующее назначение

#### Scenario: Свой формат экспорта
- **WHEN** пользователь выбирает табуляцию или свой разделитель, кодировку и кавычки, а также отключает заголовок
- **THEN** экспорт использует эти настройки для всех записей текущей выборки, включая несохранённые правки
- **AND** при табуляции приложение предлагает имя файла с расширением .tsv

#### Scenario: Символ недоступен в кодировке
- **WHEN** выбранная кодировка не может представить выгружаемый текст
- **THEN** приложение сообщает об ошибке и оставляет прежний файл назначения без изменений

### Requirement: Фоновые операции и отмена
Приложение SHALL выполнять открытие CSV, смену выборки, догрузку, подсчёт и экспорт вне потока интерфейса и предоставлять отмену активных операций.

#### Scenario: Отмена смены выборки
- **WHEN** пользователь отменяет выполняющуюся выборку
- **THEN** предыдущая таблица и её настройки остаются доступны, а отменённый результат не применяется

#### Scenario: Отмена экспорта
- **WHEN** пользователь отменяет экспорт до атомарной публикации результата
- **THEN** исходник и существующее назначение остаются неизменными

#### Scenario: Тяжёлые операции в нескольких вкладках
- **WHEN** вкладки запрашивают одновременно операции над исходными файлами
- **THEN** окно выполняет не более одной тяжёлой операции одновременно и показывает ожидающие задачи
- **AND** вкладки остаются доступны для переключения и чтения страниц готовых результатов

#### Scenario: Отмена активной или ожидающей операции
- **WHEN** пользователь нажимает Cancel в выбранной вкладке
- **THEN** её ожидающие операции отменяются, а выполняющиеся получают сигнал отмены и освобождают созданные ими временные файлы
- **AND** операции и результаты других вкладок не отменяются

### Requirement: Ограниченная память просмотра
Таблица SHALL хранить ограниченное число строк в памяти независимо от глубины прокрутки и позволять вернуться к ранее просмотренным строкам.

#### Scenario: Длительная прокрутка
- **WHEN** пользователь прокручивает дальше размера буфера и возвращается назад
- **THEN** приложение снова показывает соответствующие строки, а размер буфера остаётся ограниченным

### Requirement: Уникальные значения столбца
Приложение SHALL показывать различные значения выбранного столбца и их частоту по всей текущей выборке. Пользователь может оставить только значения с частотой один.

#### Scenario: Повторы за пределами видимой таблицы
- **WHEN** одно значение встречается как в загруженных, так и в ещё не загруженных строках выборки
- **THEN** список показывает значение один раз с полной частотой, а режим одиночных значений его исключает

### Requirement: Предпросмотр SQL выборки
Приложение SHALL показывать доступный для копирования SQL текущей визуальной выборки и порядок его параметров без выполнения запроса или повторного чтения файла.

#### Scenario: Активные фильтры, сортировка и видимые столбцы
- **WHEN** пользователь открывает предпросмотр SQL для текущей выборки
- **THEN** SQL описывает выбранные столбцы, условия AND/OR и сортировку по всему CSV без ограничений буфера
- **AND** параметры показаны отдельно от текста SQL, включая обозначение входного CSV в UTF-8

### Requirement: Редактор SQL для открытого CSV
Приложение SHALL выполнять один запрос SELECT к таблице csv_data со всеми строками открытого CSV в фоновом режиме с возможностью отмены. Пользовательский запрос не должен получать доступ к внешним файлам.

#### Scenario: Фоновая агрегация
- **WHEN** пользователь запускает запрос с группировкой
- **THEN** приложение показывает первые 200 строк результата и отмечает наличие дополнительных строк
- **AND** текущая визуальная выборка остаётся неизменной

#### Scenario: Доступ за пределами открытого CSV
- **WHEN** запрос пытается прочитать другой файл или содержит несколько инструкций
- **THEN** приложение сообщает об ошибке и не выполняет запрещённое действие

### Requirement: Сохранение визуальной сессии
Приложение SHALL сохранять ссылку на исходный CSV и настройки визуальной выборки в отдельном файле и восстанавливать выборку при его открытии.

#### Scenario: Восстановление выборки
- **WHEN** пользователь открывает сохранённую сессию
- **THEN** CSV открывается с сохранёнными фильтрами AND/OR, сортировкой и видимыми столбцами

#### Scenario: Ошибка открытия
- **WHEN** файл сессии повреждён или исходный CSV недоступен
- **THEN** приложение сообщает об ошибке и сохраняет текущее представление

### Requirement: Повторно используемые цепочки преобразований
Редактор SHALL сохранять и открывать UTF-8 файлы SQL с цепочкой преобразований для таблицы csv_data.

#### Scenario: Повторное применение
- **WHEN** пользователь открывает сохранённую цепочку с несколькими этапами WITH
- **THEN** текст загружается без выполнения
- **AND** при нажатии Run этапы применяются к текущему CSV

### Requirement: Компактное представление таблицы
Приложение SHALL размещать команды в компактной верхней панели, таблицу в свободной области окна и статистику файла внизу.

#### Scenario: Действия со столбцом
- **WHEN** пользователь нажимает кнопку сортировки или фильтра в заголовке столбца CSV
- **THEN** соответствующее действие применяется ко всему файлу
- **AND** заголовки показывают активный фильтр и направление сортировки

### Requirement: Редактирование ячеек исходного CSV
Приложение SHALL позволять редактировать значения в исходном представлении и экспортировать исправленную копию без изменения оригинала.

#### Scenario: Экспорт после прокрутки
- **WHEN** пользователь редактирует ячейку, прокручивает таблицу и экспортирует CSV
- **THEN** копия включает изменённое значение и все строки, включая ещё не загруженные в таблицу

### Requirement: Редактирование текущей выборки CSV
Приложение SHALL позволять редактировать ячейки отфильтрованной и отсортированной таблицы и применять правки до фильтрации, сортировки и экспорта, сохраняя привязку к исходной записи.

#### Scenario: Изменение ключа сортировки или фильтра
- **WHEN** пользователь меняет значение в текущей выборке
- **THEN** строка занимает новое место либо исчезает согласно текущим условиям
- **AND** при изменении условий предыдущие правки остаются доступны без повторного открытия файла

### Requirement: Сохранение всего CSV и история правок
Приложение SHALL сохранять все строки и столбцы в исходном порядке через Save и Save As, независимо от активной выборки. Экспорт SHALL использовать текущую выборку и оставаться отдельной операцией.

#### Scenario: Сохранение при активном фильтре
- **WHEN** пользователь сохраняет исправленный CSV при активном фильтре и сортировке
- **THEN** файл содержит все строки и столбцы с правками в исходном порядке

#### Scenario: Отмена и повтор
- **WHEN** пользователь отменяет или повторяет правку ячейки
- **THEN** значение соответствующей исходной записи и текущая выборка обновляются
- **AND** история доступна после сохранения

### Requirement: Операции с диапазонами и строками
Таблица SHALL поддерживать копирование и вставку диапазонов, очистку выделенных ячеек, добавление, дублирование и удаление строк.

#### Scenario: Пакетная правка
- **WHEN** пользователь вставляет блок либо меняет выделенные ячейки или строки
- **THEN** текущая выборка пересчитывается и вся операция отменяется или повторяется одним шагом
- **AND** Save сохраняет весь изменённый CSV, а экспорт ограничен выборкой

### Requirement: Отдельные действия сортировки и фильтра в заголовке
Приложение SHALL показывать отдельные элементы сортировки и фильтра для каждого столбца CSV.

#### Scenario: Цикл сортировки
- **WHEN** пользователь нажимает стрелку сортировки
- **THEN** состояние циклически меняется от возрастания к убыванию и отсутствию сортировки
- **AND** обычный клик убирает остальные ключи, а Alt сохраняет их и добавляет или обновляет ключ нажатого столбца

#### Scenario: Кнопка фильтра
- **WHEN** пользователь нажимает кнопку фильтра
- **THEN** открываются условия столбца без изменения ключей сортировки

### Requirement: Пресеты формата CSV и TSV
Приложение SHALL сохранять именованные наборы из разделителя, кодировки, символа кавычек и наличия заголовка. Списки открытия и экспорта SHALL храниться отдельно и быть доступны после перезапуска.

#### Scenario: Повторное открытие с пресетом
- **WHEN** пользователь выбирает сохранённый пресет в диалоге открытия
- **THEN** приложение восстанавливает все четыре поля и сразу обновляет предпросмотр с выбранным форматом

#### Scenario: Повторный экспорт с пресетом
- **WHEN** пользователь выбирает сохранённый пресет в диалоге экспорта
- **THEN** приложение восстанавливает все четыре поля и использует их при экспорте текущей выборки

#### Scenario: Повторное имя
- **WHEN** пользователь сохраняет пресет под именем, которое уже есть в текущем списке
- **THEN** приложение заменяет пресет только после подтверждения

#### Scenario: Переименование
- **WHEN** пользователь задаёт новое свободное имя для пресета
- **THEN** приложение сохраняет его формат под новым именем, сохраняя назначение по умолчанию, если оно было
- **AND** имя другого пресета не перезаписывается

#### Scenario: Удаление
- **WHEN** пользователь подтверждает удаление пресета
- **THEN** приложение удаляет пресет и сбрасывает назначение по умолчанию, если оно указывало на этот пресет

#### Scenario: Отдельные значения по умолчанию
- **WHEN** пользователь назначает пресет по умолчанию для открытия или экспорта
- **THEN** при следующем показе соответствующего диалога он применяется автоматически, при открытии обновляя предпросмотр
- **AND** назначение другого диалога не изменяется

#### Scenario: Сброс назначения
- **WHEN** пользователь очищает назначение по умолчанию
- **THEN** при следующем показе диалог использует обычный стартовый формат, а пресет остаётся в списке

#### Scenario: Перенос между компьютерами
- **WHEN** пользователь экспортирует пресеты и импортирует файл на другом компьютере
- **THEN** JSON переносит оба списка и оба назначения по умолчанию, включая отсутствие назначения
- **AND** локальные пресеты без совпадений остаются в списках

#### Scenario: Совпадение при импорте
- **WHEN** в JSON и локальных настройках есть одноимённый пресет в одном списке
- **THEN** приложение показывает конфликты и заменяет записи только после подтверждения
- **AND** отказ оставляет оба списка и назначения без изменений

#### Scenario: Ошибка в JSON
- **WHEN** JSON не читается, содержит неподдерживаемую версию, некорректный пресет или назначение на отсутствующий пресет
- **THEN** приложение сообщает об ошибке и не изменяет ни один список

### Requirement: Недавние файлы CSV и TSV
Приложение SHALL хранить до десяти незакреплённых недавних CSV/TSV, все закреплённые записи и форматы их открытия локально между запусками.

#### Scenario: Повторное открытие
- **WHEN** пользователь выбирает файл из File → Open Recent
- **THEN** файл открывается с последним разделителем, кодировкой, символом кавычек и настройкой заголовка
- **AND** успешное открытие перемещает незакреплённую запись в начало обычной истории без дублирования, а у закреплённой сохраняет позицию

#### Scenario: Сохранение под новым именем
- **WHEN** Save As успешно сохраняет документ по новому пути
- **THEN** новый путь появляется в обычной истории с форматом сохранённого файла или обновляет уже существующую запись

#### Scenario: Ошибка повторного открытия
- **WHEN** недавний файл удалён, перемещён или не читается
- **THEN** приложение сообщает об ошибке, сохраняет текущую таблицу и не обновляет порядок истории

#### Scenario: Закрепление и открепление
- **WHEN** пользователь закрепляет недавний файл
- **THEN** запись появляется над обычной историей, сохраняется между запусками и не вытесняется новыми файлами
- **AND** открепление возвращает запись в начало обычной истории

#### Scenario: Удаление отдельной записи
- **WHEN** пользователь удаляет закреплённую или обычную запись
- **THEN** запись исчезает из истории, а исходный файл остаётся на диске

#### Scenario: Очистка истории
- **WHEN** пользователь очищает незакреплённую историю
- **THEN** приложение удаляет только незакреплённые записи и сохраняет закреплённые вместе с их форматом

### Requirement: Вкладки CSV и TSV
Приложение SHALL поддерживать несколько CSV/TSV в одном окне с независимыми настройками формата, выборкой и несохранёнными правками.

#### Scenario: Открытие и переключение
- **WHEN** пользователь открывает ещё один файл через диалог, историю, сессию или перетаскивание и возвращается на прежнюю вкладку
- **THEN** обе таблицы остаются открытыми, а фильтры, сортировка, несохранённые правки и история отмены сохраняются отдельно

#### Scenario: Повторное открытие
- **WHEN** пользователь открывает уже открытый документ
- **THEN** приложение выбирает его вкладку и сохраняет её состояние

#### Scenario: Закрытие с правками
- **WHEN** пользователь закрывает изменённую вкладку или окно с изменёнными документами
- **THEN** приложение требует подтверждения перед отбрасыванием правок
- **AND** отказ сохраняет вкладки и их правки

#### Scenario: Путь назначения открыт
- **WHEN** пользователь пытается сохранить или экспортировать данные в файл, открытый или записываемый в другой вкладке
- **THEN** приложение сообщает об этом и оставляет файл без изменений

### Requirement: Сессия вкладок окна
Приложение SHALL сохранять в одном файле сессии порядок открытых вкладок, активную вкладку, путь, формат, фильтры, сортировку и видимые столбцы каждого файла. Сессия SHALL ссылаться на исходные файлы и не записывать несохранённые правки ячеек.

#### Scenario: Восстановление окна
- **WHEN** пользователь восстанавливает сохранённую сессию
- **THEN** файлы открываются в сохранённом порядке, с их форматами и выборками
- **AND** приложение выбирает сохранённую активную вкладку

#### Scenario: Защита старого окна
- **WHEN** восстановление отменено, любой файл сессии не открывается или пользователь отказывается отбросить несохранённые правки
- **THEN** текущие вкладки и их правки остаются на месте

#### Scenario: Одиночная сессия
- **WHEN** пользователь восстанавливает одиночную сессию предыдущего формата
- **THEN** приложение восстанавливает её как окно с одной вкладкой

### Requirement: Навигация по CSV и вкладкам с клавиатуры
Приложение SHALL поддерживать стандартные сочетания открытия файла, управления вкладками и перехода к краям текущей выборки.

#### Scenario: Постраничная прокрутка с подгрузкой
- **WHEN** пользователь нажимает Page Down и таблица подгружает строки или удаляет строки из начала буфера
- **THEN** видимая позиция и текущая ячейка сохраняются, пока соответствующая строка остаётся в буфере
- **AND** следующее нажатие продолжает прокрутку вниз

#### Scenario: Переход к краям выборки
- **WHEN** в таблице нажимают Home или End либо платформенное сочетание перехода к началу или концу
- **THEN** приложение переходит к первой или последней строке всей текущей выборки, при необходимости подгружая её в фоне
- **AND** число строк в буфере остаётся ограниченным

#### Scenario: Управление вкладками
- **WHEN** пользователь вызывает стандартную команду открытия, создания, закрытия или переключения вкладки
- **THEN** открывается диалог выбора CSV/TSV либо выполняется соответствующее действие с вкладкой
- **AND** закрытие учитывает несохранённые правки и оставляет фокус в текущей вкладке

### Requirement: Выравнивание вкладок CSV и TSV
Приложение SHALL располагать вкладки CSV и TSV у левого края окна.

#### Scenario: Открытие второй вкладки
- **WHEN** пользователь открывает второй файл CSV или TSV в том же окне
- **THEN** вторая вкладка появляется справа от первой
- **AND** левая граница первой вкладки остаётся на месте

### Requirement: Переход к строке CSV по номеру
Приложение SHALL позволять перейти к строке по номеру начиная с 1 в текущей выборке активной вкладки.

#### Scenario: Строка вне буфера
- **WHEN** пользователь вводит существующий номер строки, которая ещё не подгружена
- **THEN** приложение подгружает нужную страницу отменяемой фоновой операцией и выделяет строку
- **AND** нумерация учитывает порядок фильтрации и сортировки, а буфер остаётся ограниченным

#### Scenario: Неверный номер
- **WHEN** пользователь вводит неверный номер или номер за пределами выборки
- **THEN** приложение сообщает об ошибке и сохраняет текущую таблицу и выделение

### Requirement: Поиск по текущей выборке CSV
Приложение SHALL открывать панель Find по платформенному сочетанию Ctrl/⌘+F, искать текст без учёта регистра в видимых столбцах и подсвечивать совпадающие ячейки.

#### Scenario: Совпадение вне буфера
- **WHEN** пользователь ищет следующее или предыдущее совпадение
- **THEN** приложение находит ячейку во всей текущей выборке с учётом фильтров, сортировки и несохранённых правок
- **AND** при необходимости подгружает строку и выделяет ячейку, сохраняя ограниченный буфер

#### Scenario: Отмена и смена данных
- **WHEN** пользователь отменяет поиск, меняет текст, правит данные или меняет выборку во время поиска
- **THEN** устаревший результат не меняет выделение или таблицу

### Requirement: Многострочный редактор ячейки CSV
Приложение SHALL открывать отдельный многострочный редактор текущей редактируемой ячейки по Shift+Enter.

#### Scenario: Подтверждение значения
- **WHEN** пользователь подтверждает текст с переносом строки
- **THEN** приложение меняет только выбранную ячейку одной операцией Undo
- **AND** Save и Export сохраняют перенос внутри поля CSV

#### Scenario: Отмена диалога
- **WHEN** пользователь нажимает Escape или Cancel
- **THEN** значение и история правок не меняются

### Requirement: Управление столбцами CSV
Приложение SHALL позволять скрывать столбцы из заголовка, находить столбцы по имени и возвращать их в представление. В представлении SHALL оставаться хотя бы один видимый столбец.

#### Scenario: Изменение и восстановление раскладки
- **WHEN** пользователь перетаскивает столбец и затем сохраняет и открывает сессию
- **THEN** выборка сохраняет видимость и порядок столбцов
- **AND** экспорт следует этому порядку, а Save сохраняет исходный порядок файла и применяет правки к нужным ячейкам

### Requirement: Быстрый фильтр из ячейки CSV
Приложение SHALL предлагать в контекстном меню ячейки точный фильтр «равно» или «не равно» её текущему значению с учётом несохранённых правок.

#### Scenario: Единая выборка
- **WHEN** пользователь применяет быстрый фильтр
- **THEN** условие применяется ко всему CSV и учитывается при unique и экспорте
- **AND** условие показывается отдельно в Selection, откуда его можно удалить, и сохраняется в сессии

#### Scenario: Пустое значение
- **WHEN** пользователь фильтрует по пустой ячейке
- **THEN** сравнение отличает пустые ячейки от буквального текста NULL
- **AND** «не равно» возвращает строки, для которых точное сравнение «равно» не выполняется

### Requirement: Pin CSV columns

The application SHALL let users pin or unpin a visible CSV column from its header menu. Pinning SHALL display that column on the left without changing the order of saved or exported columns.

#### Scenario: Scroll with pinned columns

- **WHEN** the user pins a column and scrolls the table horizontally
- **THEN** the pinned area remains at the left and the other columns scroll
- **AND** vertical scrolling keeps both areas on the same rows

#### Scenario: Restore pinned column settings

- **WHEN** the user saves and restores a document or workspace session
- **THEN** each tab restores its pins by column name
- **AND** an older session without pin settings opens with no pins

### Requirement: Inspect a CSV record

The application SHALL provide a read-only view of the current CSV row as field and value pairs.

#### Scenario: Inspect the current selection

- **WHEN** the user opens Record View with a current row in the CSV table
- **THEN** the dialog shows the row number in the current selection and each visible field in table order
- **AND** it shows current cell values including unsaved edits and multiline text
- **AND** opening it does not load other rows or alter the table

### Requirement: Transpose a selected CSV range

The application SHALL provide a read-only transposed view of a selected rectangular range with at most 10,000 cells, without reading further CSV rows.

#### Scenario: Inspect a range

- **WHEN** the user opens Transpose Selection for a contiguous range
- **THEN** the dialog shows fields vertically in current header order and records horizontally with selection-relative row labels
- **AND** values include unsaved edits
- **AND** the original selection, data and export are unchanged

#### Scenario: Reject an excessive or disjoint selection

- **WHEN** the selection contains more than 10,000 cells or multiple rectangles
- **THEN** the application explains the limit and does not create a transposed table

### Requirement: Preview saved CSV text

The application SHALL let users switch between the table and a read-only preview of the saved source text using the file format encoding.

#### Scenario: Inspect source text

- **WHEN** the user selects Text
- **THEN** at most 128 Ki characters are read in the background
- **AND** the view identifies the saved source and discloses truncation
- **AND** it explains that filters and unsaved table edits are not represented

#### Scenario: Return to the table

- **WHEN** the user selects Table
- **THEN** the existing table state and unsaved edits remain intact
- **AND** pending text reads cannot replace the table

### Requirement: Copy ranges in selectable formats

The application SHALL provide Copy As TSV, CSV, JSON and Markdown for selected buffered cells, using visible column order and current edits. Standard Copy SHALL remain TSV.

#### Scenario: Copy structured values

- **WHEN** the user chooses a Copy As format
- **THEN** CSV/TSV quote delimiters and newlines, JSON retains null values, and Markdown escapes cell content
- **AND** only the selected range is copied without reading more rows

#### Scenario: Clipboard copy is too large

- **WHEN** the range exceeds 10,000 cells or the output exceeds 4 Mi UTF-8 bytes
- **THEN** the application warns and leaves the clipboard unchanged

### Requirement: Export the selection as JSON or Markdown

The application SHALL stream the full current CSV selection to a UTF-8 JSON or Markdown file, including filters, sorting, visible column order and unsaved edits.

#### Scenario: Export unloaded rows

- **WHEN** the user exports a selection larger than the preview buffer
- **THEN** all matching rows are written in selection order using bounded output batches
- **AND** JSON preserves nulls and Markdown escapes cell content

#### Scenario: Cancel or fail an export

- **WHEN** the export is cancelled, fails, or the source revision changes
- **THEN** the previous destination remains intact and temporary output is removed
- **AND** the source CSV and its aliases cannot be overwritten

### Requirement: Compare saved CSV files by key

The application SHALL compare two saved CSV files using one unique nonempty key column and show added, removed and changed rows. Filters and unsaved table edits SHALL not affect this operation.

#### Scenario: Compare compatible files

- **WHEN** the files have matching column names and a valid selected key
- **THEN** exact key and null-safe value comparisons identify all differing records
- **AND** the read-only result shows counts and before/after values in bounded asynchronously loaded pages

#### Scenario: Reject ambiguous keys or incompatible schemas

- **WHEN** either file contains duplicate or empty keys, lacks the key, or has different column names
- **THEN** comparison reports the problem instead of producing ambiguous matches

#### Scenario: Cancel comparison

- **WHEN** the user cancels comparison or closes a result with a pending page read
- **THEN** pending work is cancelled without modifying either source
- **AND** temporary resources are released

### Requirement: Recover unsaved CSV edits

The application SHALL checkpoint committed unsaved edits separately from the source CSV and offer restoration at startup.

#### Scenario: Restore a crashed document

- **WHEN** a valid checkpoint matches the saved source revision and the user chooses Restore
- **THEN** the document restores edits, added and deleted rows, format, view settings and Undo/Redo history
- **AND** the source remains unchanged until explicit Save

#### Scenario: Protect incompatible or active recovery data

- **WHEN** a checkpoint is corrupt, its source is missing or changed, or another live application holds its lock
- **THEN** the application SHALL NOT apply or silently delete that checkpoint

#### Scenario: Save or discard changes

- **WHEN** the user saves the document, returns to the clean state, or explicitly discards edits
- **THEN** the obsolete checkpoint is removed
- **AND** choosing Later at startup keeps recovery data for a later launch

#### Scenario: Recovery storage fails

- **WHEN** a checkpoint cannot be written
- **THEN** the application reports that recovery is unavailable while keeping current in-memory edits

### Requirement: Replace text across the current CSV view

The application SHALL preview and apply literal case-insensitive replacement across all rows and visible columns of the current selection, including unloaded rows and unsaved edits.

#### Scenario: Preview and replace

- **WHEN** the user previews replacement of the first occurrence or all occurrences
- **THEN** the preview shows before/after values and counts without changing the document
- **AND** confirmation applies only the prepared snapshot, using the existing filter and sort order
- **AND** Undo reverts the complete replacement as one operation and Redo reapplies it

#### Scenario: Cancel or invalidate a preview

- **WHEN** preparation is cancelled, the replacement options change, or the source or document no longer matches the preview
- **THEN** stale changes SHALL NOT be applied and the original edits remain intact

#### Scenario: Literal replacement semantics

- **WHEN** text contains Unicode, multiple matches, newlines, or an empty replacement
- **THEN** matches use the existing search case rules, inserted text is not searched again, and unrelated or hidden cells remain unchanged

### Requirement: Disk backed edit history

The application SHALL store cell patches and Undo/Redo values on disk, with memory usage independent of the number of cells changed by a bulk replacement.

#### Scenario: Replace millions of cells

- **WHEN** a prepared replacement is applied to a large current selection
- **THEN** the plan SHALL be consumed in bounded batches and published atomically
- **AND** one Undo or Redo SHALL switch the complete operation without loading its cells into RAM
- **AND** filters, sorting, unique, search, Save and export SHALL include the disk backed patches

#### Scenario: Cancel or fail preparation

- **WHEN** preparation is cancelled or disk writing fails
- **THEN** the prior document and its history SHALL remain intact

#### Scenario: Recover disk backed edits

- **WHEN** a recovery checkpoint is written and reopened
- **THEN** patches and history SHALL be saved and restored without materializing all cell values in RAM
- **AND** legacy version 1 checkpoints SHALL remain readable

### Requirement: Keyboard navigation across buffered CSV rows

Arrow, Tab, reverse Tab and page movement SHALL work across buffer boundaries without loading the whole selection; Go to Row SHALL accept a visible column and row.

#### Scenario: Use the grid action

- **WHEN** the user invokes the documented action on the current CSV view
- **THEN** it SHALL follow the contract in GRID_POLISH.md without altering unrelated cells
- **AND** disabled or invalid actions SHALL leave the document unchanged

### Requirement: Explicit bounded CSV selection

The application SHALL provide row, column, extend and shrink selection actions, scoped explicitly to buffered rows; selection SHALL remain bounded and report its size.

#### Scenario: Use the grid action

- **WHEN** the user invokes the documented action on the current CSV view
- **THEN** it SHALL follow the contract in GRID_POLISH.md without altering unrelated cells
- **AND** disabled or invalid actions SHALL leave the document unchanged

### Requirement: Explicit cell editing keyboard contract

Enter, F2 and double click SHALL start editing an editable cell; Enter SHALL confirm one Undo operation and Escape SHALL cancel without changing the document.

#### Scenario: Use the grid action

- **WHEN** the user invokes the documented action on the current CSV view
- **THEN** it SHALL follow the contract in GRID_POLISH.md without altering unrelated cells
- **AND** disabled or invalid actions SHALL leave the document unchanged

### Requirement: Consistent CSV cell context actions

Cell context menus SHALL reuse existing editing actions, preserve a selected range when invoked inside it, select an outside target, and support keyboard invocation.

#### Scenario: Use the grid action

- **WHEN** the user invokes the documented action on the current CSV view
- **THEN** it SHALL follow the contract in GRID_POLISH.md without altering unrelated cells
- **AND** disabled or invalid actions SHALL leave the document unchanged

### Requirement: Cell context shortcut with child editor focus

Shift+F10 and Menu SHALL open the cell actions menu exactly once when focus is in the CSV table or its inline cell editor, including a pinned column. These shortcuts SHALL NOT open the cell menu from unrelated input fields.

#### Scenario: Invoke from an inline editor

- **WHEN** an editable ordinary or pinned cell has an active inline editor and the user invokes Shift+F10 or Menu
- **THEN** the current input SHALL be committed before the cell actions menu opens
- **AND** the committed edit SHALL be reversible with one Undo

#### Scenario: Invoke outside the table

- **WHEN** focus is in the Find input and the user presses Shift+F10
- **THEN** the cell actions menu SHALL NOT open and the CSV SHALL remain unchanged

### Requirement: Opt-in keyboard routing diagnostics

The application SHALL support an opt-in JSONL trace of keyboard codes, native codes, modifiers, focus classes, shortcut ambiguity and cell menu stages. Diagnostics SHALL NOT consume events or record event text, document values or file names. Storage SHALL be bounded to a 1 MiB log and one backup. An unavailable output path SHALL NOT prevent application startup.

#### Scenario: Trace keyboard menu routing

- **WHEN** CSV_WORKBENCH_KEY_LOG names a writable file and the user sends Shift+F10
- **THEN** delivered Qt events and any shortcut/menu invocation SHALL be logged with focus and receiver classes
- **AND** absence of a delivered key event SHALL NOT be reported as a proven QAction failure

#### Scenario: Diagnostics disabled

- **WHEN** CSV_WORKBENCH_KEY_LOG is unset
- **THEN** the application SHALL NOT install a keyboard trace or create a trace file

### Requirement: Verify the actual cell actions popup

Keyboard menu regression tests SHALL verify the real active and visible QMenu, including pinned cells and inline editors. A main-window screenshot without the popup SHALL NOT by itself establish a keyboard routing failure.

#### Scenario: Open the real popup

- **WHEN** Shift+F10 is delivered to an ordinary or pinned cell or its inline editor
- **THEN** a visible QMenu SHALL become the active popup exactly once
- **AND** the trace SHALL record request, valid target, opening and closing in order

### Requirement: Complete cell values across the current view
The application SHALL offer Complete Value and Ctrl+Space in ordinary and pinned cell editors. Suggestions SHALL be distinct non-NULL values from the same column of the full current selection, including unloaded rows and committed unsaved edits. Prefix matching SHALL be literal and case sensitive, including an empty prefix. At most 100 values SHALL be returned in Unicode code point order and truncation SHALL be indicated.

#### Scenario: Complete an edited filtered column
- **WHEN** the user requests completion in a filtered or sorted view with unsaved edits
- **THEN** matching values SHALL include edited and unloaded rows that satisfy the current filters and SHALL exclude deleted or filtered-out rows

### Requirement: Cancellable bounded completion
Completion SHALL run in the background with bounded candidate storage and use the existing operation cancellation mechanism. Closing the editor or changing the prefix or document selection SHALL invalidate its request. Results SHALL NOT be applied to another editor, column, or tab.

#### Scenario: Cancel or supersede a request
- **WHEN** a request is cancelled or its editor, prefix or document changes before completion
- **THEN** the worker SHALL stop cooperatively and its result SHALL NOT open or alter a popup

### Requirement: Completion preserves cell editing history
Accepting a suggestion SHALL only replace editor text. Confirming the cell SHALL create one normal Undo operation; dismissing suggestions SHALL NOT commit a cell edit.

#### Scenario: Accept and undo a suggestion
- **WHEN** a user accepts a suggestion and confirms the cell
- **THEN** one Undo SHALL restore the previous value and Redo SHALL restore the accepted value

### Requirement: Native macOS table accessibility remains valid

Приложение SHALL сохранять валидный accessibility-интерфейс обычной и закреплённой таблицы при чтении выбранных ячеек и смене столбцов. Accessibility MUST оставаться включённой.

#### Scenario: Native client reads selected cells after model changes

- **WHEN** нативный клиент читает строки, выделяет строки и запрашивает выбранные ячейки при смене числа столбцов
- **THEN** интерфейс таблицы остаётся валидным, выбранные ячейки доступны, процесс не завершается

#### Scenario: Standalone macOS build is verified

- **WHEN** собирается автономное приложение macOS
- **THEN** нативная регрессионная проверка выполняется на упакованном Qt, а ошибка блокирует сборку

### Requirement: Column validation rules

Приложение SHALL позволять задать text/integer/number/date/boolean и допустимость NULL для каждого столбца без преобразования значений. Настройки SHALL сохраняться отдельно для каждой вкладки в сессии.

#### Scenario: Invalid buffered value

- **WHEN** значение ячейки не соответствует правилу
- **THEN** таблица подсвечивает её и показывает объяснение ошибки, сохраняя возможность редактировать и сохранять CSV

#### Scenario: Session round trip

- **WHEN** сессия сохранена и восстановлена
- **THEN** правила и допустимость NULL восстановлены для каждой вкладки, а старые сессии открываются без правил

### Requirement: Full selection validation is cancellable

Приложение SHALL проверять все строки текущей выборки в фоне с учётом правок, добавлений, удалений и настроенных скрытых столбцов; отмена SHALL сохранять таблицу и правки.

#### Scenario: Invalid unloaded edited row

- **WHEN** некорректное значение находится за пределами буфера или внесено правкой
- **THEN** общий счётчик ошибок включает его без загрузки всей выборки в память UI

#### Scenario: Cancel or change during validation

- **WHEN** пользователь отменяет проверку либо меняет выборку, правила или правки
- **THEN** запрос останавливается и его запоздалый результат не отображается как актуальный

### Requirement: NULL differs from empty text

Приложение SHALL различать NULL и пустую строку при чтении, отображении, правках, Undo/Redo, Save и Export.

#### Scenario: Empty value round trip

- **WHEN** NULL и пустая строка сохранены и открыты повторно
- **THEN** пустое поле без кавычек читается как NULL, а заключённое в кавычки пустое поле — как пустая строка, включая CSV с единственным столбцом

### Requirement: Format preview preserves missing and empty values

Предпросмотр формата SHALL показывать незаключённое пустое поле как NULL, а заключённое пустое поле как `""`, используя выбранные разделитель, кодировку, кавычки и наличие заголовка. Переключение настроек SHALL обновлять различие без изменения исходного файла.

#### Scenario: Missing and empty fields

- **WHEN** пользователь открывает образец с пустым полем и заключённым пустым полем
- **THEN** предпросмотр отображает NULL и `""` отдельно, включая одноколоночный файл

#### Scenario: Quoted multiline and truncated input

- **WHEN** поля содержат разделители, удвоенные кавычки, переносы либо последняя запись образца обрезана
- **THEN** полные записи разбираются без потери семантики NULL/empty, а неполная последняя запись не показывается

### Requirement: Cancellable buffered selection aggregates

Приложение SHALL рассчитывать count всех выбранных ячеек, NULL, пустых строк, чисел и пропущенных прочих значений, а также числовые sum, average и min/max для выделения текущего буфера с учётом правок. Числовая арифметика SHALL использовать 38 значащих десятичных цифр; отсутствие чисел SHALL отличаться от числового нуля.

#### Scenario: Mixed edited cells

- **WHEN** выделение содержит числа, текст, NULL, пустые строки и несохранённые правки
- **THEN** каждая выбранная ячейка учитывается один раз, категории считаются отдельно, числовые агрегаты учитывают только числа и правки, источник и Undo не меняются

#### Scenario: Cancel or supersede calculation

- **WHEN** пользователь отменяет фоновый расчёт либо меняет выделение, данные или выборку
- **THEN** старый запрос отменяется и его запоздалый результат не отображается как актуальный

#### Scenario: Shared pinned selection

- **WHEN** пользователь выделяет обычные и закреплённые столбцы отфильтрованной и отсортированной таблицы
- **THEN** агрегаты используют реальные выбранные значения и текущие правки, независимо от позиции исходных строк и столбцов
