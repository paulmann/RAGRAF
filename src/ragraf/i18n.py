"""Переводы интерфейса администратора RAGRAF.

Зачем отдельный модуль
    Панель была только на русском: все подписи лежали прямо в разметке и в
    ответах API. Перевести их «на месте» нельзя без второго источника правды —
    поэтому тексты собраны здесь, а разметка и `admin/static/app.js` берут их
    отсюда. Язык по умолчанию — английский, дополнительно поддержаны русский
    и китайский.

Как устроено
    Ключ — английский текст. Английского словаря нет намеренно: если перевода
    нет, показывается сам ключ, то есть английский. Это исключает класс ошибок
    «английский перевод разошёлся с ключом»: разойтись нечему.

    Словарь отдаётся клиенту целиком через `/api/i18n`. Смена языка не требует
    нового запроса состояния: выбор языка живёт в браузере.

Что НЕ переводится
    * имена конфигурационных ключей, значения перечислений (`docs`, `cli`,
      `read`), операции API, имена переменных окружения, пути, имена продуктов
      (`RAGRAF`, `AutoClaw`, `DeepSeek Harness`, `SagaAI`, `Joern`);
    * команды оболочки: `ragraf register`, `ragraf index`;
    * строки, которые сервер сравнивает как значения контракта
      (`settings.py` пишет `проект`/`глобально`, `db_access.py` — `environment`/
      `file`). Клиент переводит их через словарь `SERVER_TEXT`, не меняя сам
      контракт: иначе правка панели потребовала бы менять формат конфигов.

Проверка
    `python tools/check_admin_i18n.py` сверяет ключи, которые зовёт клиент,
    с этим словарём по каждому языку: пропущенный перевод — находка.
"""

from __future__ import annotations

# Языки панели. Первый в списке — язык по умолчанию.
LANGUAGES: tuple[str, ...] = ("en", "ru", "zh")
DEFAULT_LANGUAGE = "en"
LANGUAGE_TITLES: dict[str, str] = {
    "en": "English",
    "ru": "Русский",
    "zh": "中文",
}

#: Русский текст, приходящий от других модулей, → английский ключ.
#: Модули, которые пишут готовый текст для панели (`settings.py`,
#: `probes.py`, `client_setup.py`, `compute.py`, `db_collect.py`, `access.py`,
#: `facade.py`), не переписываются ради подписи: их ответы переводятся здесь.
#: Ключ — исходная русская строка, значение — ключ из `TRANSLATIONS`.
SERVER_TEXT: dict[str, str] = {
    # размеры баз (admin_server.storage_sizes)
    "граф кода": "code graph",
    "лексика и векторы": "lexicon and vectors",
    "журнал и телеметрия": "journal and telemetry",
    "конфиг не найден": "config not found (server)",
    # способ входа и имена в журнале доступа
    "администратор": "administrator",
    "токен сессии": "session token",
    "ни токена, ни парольной сессии": "no token and no password session",
    "не найдено": "not found",
    # источник значения настройки (settings.py)
    "проект": "set in the corpus config",
    "глобально": "inherited globally",
    # источник доступа к базам (db_access.py)
    "окружение": "environment",
    "файл": "file",
    # вычислитель векторов (compute.py)
    "torch не установлен": "torch is not installed",
    "torch не видит видеокарту": "torch does not see the GPU",
    "вычисление на видеокарте не работает": "GPU compute does not work",
    "sentence-transformers не установлен": "sentence-transformers is not installed",
    # качество индекса (facade.py)
    "векторный поиск недоступен: расширение vec0 не загружено":
        "vector search is unavailable: the vec0 extension is not loaded",
    # слой баз (db_ops.py, db_collect.py, admin_server.py)
    "слой баз для этой ревизии не собран":
        "the database layer for this revision is not collected",
    "в корпусе нет ревизии: сначала выполните index":
        "no revision in the corpus: run index first",
    "сбор баз выключен в настройках":
        "database collection is disabled in the settings",
    "включите сбор и сохраните настройки": "enable collection and save the settings",
    "включите сбор в разделе «Базы данных» панели":
        "enable collection in the “Databases” section of the panel",
    "клиент mysql не найден: установите его или добавьте в PATH":
        "the mysql client was not found: install it or add it to PATH",
    "клиент postgresql не найден: установите его или добавьте в PATH":
        "the postgresql client was not found: install it or add it to PATH",
    "проверьте установку СУБД": "check the DBMS installation",
    "доступ не задан: ни в настройках, ни в окружении":
        "no access value: neither in the settings nor in the environment",
    "заполните доступ в панели или задайте RAGRAF_DB_PASSWORD":
        "fill in the access value in the panel or set RAGRAF_DB_PASSWORD",
    "нет ответа": "no answer",
    "проверьте пользователя, значение доступа и права на чтение":
        "check the user, the access value and the read permissions",
    "проверьте доступ и адрес": "check the access value and the address",
    # пароль и токены (access.py)
    "пароль пуст": "the password is empty",
    "текущий пароль неверен": "current password is wrong",
    "значение не передано": "no value supplied",
    "значение не соответствует ни одному токену": "the value matches no token",
    "срок действия токена истёк": "the token has expired",
    "неверный пароль": "wrong password",
    "пароль не задан": "password is not set",
    # реестр (cli.py, probes.py)
    "нет вывода": "no output",
    # Подсказки к сбоям чтения состояния. Значение — тот же текст: сервер шлёт
    # ПОЛНЫЙ текст подсказки, и он же служит ключом перевода. Сокращённый ключ
    # (`hint_database_not_writable`) выглядел компактнее, но ломал английский:
    # `catalogue('en')` возвращает ключ как есть, поэтому в английской версии
    # подсказка выводилась сырым ключом (замечено владельцем 15.10.2026).
    "Каталог .ragraf этой разработки недоступен для записи. SQLite открывает "
    "базу с журналом, поэтому «только чтение» тоже требует права записи: "
    "проверьте права на каталог или запустите панель от имени, у которого "
    "они есть.":
        "Каталог .ragraf этой разработки недоступен для записи. SQLite открывает "
        "базу с журналом, поэтому «только чтение» тоже требует права записи: "
        "проверьте права на каталог или запустите панель от имени, у которого "
        "они есть.",
    "С базой этой разработки работает другой процесс. Дождитесь окончания "
    "индексации или закройте лишние окна панели.":
        "С базой этой разработки работает другой процесс. Дождитесь окончания "
        "индексации или закройте лишние окна панели.",
    "Файл или каталог защищён от записи: проверьте права доступа.":
        "Файл или каталог защищён от записи: проверьте права доступа.",
    "В базе нет ожидаемой таблицы: индекс собран другой версией RAGRAF. "
    "Переиндексируйте разработку.":
        "В базе нет ожидаемой таблицы: индекс собран другой версией RAGRAF. "
        "Переиндексируйте разработку.",
    "Базы этой разработки ещё не собраны. Нажмите «Переиндексировать», "
    "чтобы построить индекс: после этого разделы заполнятся.":
        "Базы этой разработки ещё не собраны. Нажмите «Переиндексировать», "
        "чтобы построить индекс: после этого разделы заполнятся.",
    # Тексты, которые сервер отдаёт клиенту как английские ключи с подстановками.
    "config file is missing: {path}": "config file is missing: {path}",
    "the registry entry has no config path": "the registry entry has no config path",
    "index directory was not removed": "index directory was not removed",
    "indexing is disabled for this development": "indexing is disabled for this development",
    "development not found: {name}": "development not found: {name}",
    "no developments need reindexing": "no developments need reindexing",
    "indexing already running": "indexing already running",
    "indexing failed": "indexing failed",
    "not found": "not found",
    # клиенты (client_setup.py)
    "Settings → Tools & connections → Add tool. Type: Online service. "
    "Значения ниже вводятся руками: AutoClaw регистрирует MCP-серверы "
    "только через свой интерфейс, файла конфигурации для них нет.":
        "Settings → Tools & connections → Add tool. Type: Online service. "
        "The values below are entered by hand: AutoClaw registers MCP servers "
        "only through its own interface and has no configuration file for them.",
    "значение доступа выдаётся в разделе «Доступ»: создайте "
    "отдельный токен с именем AutoClaw и вставьте в поле "
    "Bearer token — показывается один раз":
        "the access value is issued in the “Access” section: create a separate "
        "token named AutoClaw and paste it into the Bearer token field — it is "
        "shown once",
    "инструмент лежит в ЧУЖОМ проекте, и панель его не правит: "
    "показывается фактическое состояние и переменные, которыми он "
    "управляется. Правка — по отдельному указанию владельца.":
        "the tool lives in ANOTHER project and the panel does not change it: "
        "the actual state and the variables that control it are shown. "
        "Changing it requires a separate instruction from the owner.",
}

#: Английские тексты подсказок. Нужны потому, что ключом подсказки служит
#: русский текст, а для английского языка нет своего словаря — без этой карты
#: английская панель показывала бы русскую подсказку (замечено 15.10.2026).
HINT_TEXTS_EN: dict[str, str] = {
    "Каталог .ragraf этой разработки недоступен для записи. SQLite открывает "
    "базу с журналом, поэтому «только чтение» тоже требует права записи: "
    "проверьте права на каталог или запустите панель от имени, у которого "
    "они есть.":
        "The .ragraf directory of this development is not writable. SQLite opens "
        "the database together with its journal, so even read-only access needs "
        "write permission: check the directory permissions or start the panel "
        "under an account that has them.",
    "С базой этой разработки работает другой процесс. Дождитесь окончания "
    "индексации или закройте лишние окна панели.":
        "Another process is using this development's database. Wait for indexing "
        "to finish or close the extra panel windows.",
    "Файл или каталог защищён от записи: проверьте права доступа.":
        "The file or directory is protected from writing: check the access rights.",
    "В базе нет ожидаемой таблицы: индекс собран другой версией RAGRAF. "
    "Переиндексируйте разработку.":
        "The database has no expected table: the index was built by another "
        "version of RAGRAF. Reindex the development.",
    "Базы этой разработки ещё не собраны. Нажмите «Переиндексировать», "
    "чтобы построить индекс: после этого разделы заполнятся.":
        "The databases of this development have not been built yet. Click "
        "Reindex to build the index: the sections will fill in afterwards.",
}

#: Ключ → перевод. Английский отсутствует: ключ и есть английский текст.
TRANSLATIONS: dict[str, dict[str, str]] = {
    "ru": {
        # --- шапка страницы -------------------------------------------------
        "RAGRAF — control panel": "RAGRAF — управление",
        "control panel": "панель управления",
        "loading…": "загрузка…",
        "Refresh": "Обновить",
        "Collecting information about developments…":
            "Собираю сведения о разработках…",
        "Collecting: developments {done} of {total}…":
            "Собираю сведения: разработок {done} из {total}…",
        "All indexes are up to date": "все индексы актуальны",
        "developments need reindexing: {count}":
            "разработок требуют реиндексации: {count}",
        "language": "язык",
        # --- единицы измерения ----------------------------------------------
        "B": "Б",
        "KB": "КБ",
        "MB": "МБ",
        "GB": "ГБ",
        # --- общие помощники ------------------------------------------------
        "response could not be parsed": "ответ не разобран",
        "status code {code}": "код {code}",
        "Could not reach the panel: {reason}":
            "Не удалось обратиться к панели: {reason}",
        "loading": "обновляю…",
        "load error": "ошибка загрузки",
        "Could not get state: {reason}": "Не удалось получить состояние: {reason}",
        "unknown reason": "неизвестная причина",
        "saving…": "сохраняю…",
        "settings saved": "настройки сохранены",
        "save error": "ошибка сохранения",
        "Could not save: {reason}": "Не удалось сохранить: {reason}",
        "not found": "не найдено",
        "none": "нет",
        "no data": "нет данных",
        "— total {size}": "— всего {size}",
        "including journal: {size}": "из них журнал: {size}",
        "file not found": "файл не найден",
        # --- реестр разработок ----------------------------------------------
        "No developments in the registry. Register a corpus with ":
            "В реестре нет разработок. Зарегистрируйте корпус командой ",
        "index is up to date": "индекс актуален",
        "reindexing required": "требуется реиндексация",
        "could not check: {reason}": "проверить не удалось: {reason}",
        "never indexed": "не индексировалась",
        "waiting for data…": "ожидаю данные…",
        "Developments": "Разработки",
        "files / nodes / edges · corpus checksum":
            "файлов / узлов / рёбер · контрольная сумма корпуса",
        "Development": "Разработка",
        "Revision": "Ревизия",
        "Index state": "Состояние индекса",
        "Size": "Размер",
        "Database size": "Размер баз",
        "Corpus checksum": "Сумма корпуса",
        # --- размеры баз ----------------------------------------------------
        "Database sizes": "Размеры баз",
        "first 24 tables of each database are shown":
            "показаны первые 24 таблицы каждой базы",
        "Database": "База",
        "File": "Файл",
        "Table": "Таблица",
        "Rows": "Строк",
        "code graph": "граф кода",
        "lexicon and vectors": "лексика и векторы",
        "journal and telemetry": "журнал и телеметрия",
        # --- использование --------------------------------------------------
        "Statistics unavailable: {reason}": "Статистика недоступна: {reason}",
        "Usage and effect": "Использование и эффект",
        "tokens and lines are estimates, calls and facts are measured":
            "токены и строки — оценка, обращения и факты — измерение",
        "Calls": "Обращений",
        "to facade operations": "к операциям фасада",
        "Sessions": "Сессий",
        "tool runs": "запусков инструмента",
        "Facts": "Фактов",
        "obtained from the index": "получено из индекса",
        "Verifiability": "Проверяемость",
        "with a file:line reference": "со ссылкой файл:строка",
        "Tokens (estimate)": "Токенов (оценка)",
        "by response size": "по объёму ответов",
        "Lines not read as files": "Не читано файлами",
        "lines of code (estimate)": "строк кода (оценка)",
        "autoclaw": "AutoClaw",
        "external": "внешний",
        "internal": "служебный",
        "By agent (who used RAGRAF)": "По агентам (кто использовал RAGRAF)",
        "Agent": "Агент",
        "Family": "Семейство",
        "Calls short": "Вызовов",
        "Facts short": "Фактов",
        "Lines not read": "Строк не читалось",
        "Errors": "Ошибок",
        "Agents": "Агентов",
        " — calls {count}": " — обращений {count}",
        "Operation": "Операция",
        "Found": "Найдено",
        "With reference": "Со строкой",
        "Average": "Среднее",
        "Target": "Объект",
        " {ms} ms": " {ms} мс",
        # --- качество -------------------------------------------------------
        "Index quality": "Качество индекса",
        "vector norms, chunk sizes, signature completeness":
            "нормы векторов, размеры чанков, полнота сигнатур",
        "no data available": "нет данных",
        "no problems found": "проблем не обнаружено",
        "problems: {count}": "проблемы: {count}",
        "Vectors": "Векторы",
        "normalized": "нормированы",
        "not normalized": "не нормированы",
        "none at all": "нет",
        "norm p50 {value}": "норма p50 {value}",
        "Chunks": "Чанки",
        "maximum {value} tokens": "максимум {value} токенов",
        "Over budget": "Сверх лимита",
        "chunks longer than {value}": "чанков длиннее {value}",
        "Files without symbols": "Файлов без символов",
        "wrapped into windows": "обёрнуты в окна",
        "Functions": "Функций",
        "with parameters parsed": "с параметрами разобрано",
        "Truncated signatures": "Обрезанных сигнатур",
        "parameters lost in the text": "параметры теряются в тексте",
        "Docblocks": "Docblock'ов",
        "parameter contracts": "контракты параметров",
        "GPU": "видеокарта",
        "CPU": "процессор",
        "not determined": "не определён",
        "Vector compute: ": "Вычислитель векторов: ",
        " {ms} ms per text": " {ms} мс на текст",
        "Detected: ": "Обнаружено: ",
        "Stage": "Стадия",
        "Time": "Время",
        "Items": "Элементов",
        " {s} s": " {s} с",
        "vector search is unavailable: the vec0 extension is not loaded":
            "векторный поиск недоступен: расширение vec0 не загружено",
        "torch is not installed": "torch не установлен",
        "torch does not see the GPU": "torch не видит видеокарту",
        "GPU compute does not work": "вычисление на видеокарте не работает",
        "sentence-transformers is not installed":
            "sentence-transformers не установлен",
        # --- настройки ------------------------------------------------------
        "Settings": "Настройки",
        "auto-reindex thresholds and indexing parameters":
            "пороги авто-реиндексации и параметры индексации",
        "Defaults": "Значения по умолчанию",
        "Save global settings": "Сохранить общие настройки",
        "they apply to developments where the key is not set separately":
            "действуют для разработок, где ключ не задан отдельно",
        "No developments": "Разработок нет",
        "Setting": "Настройка",
        "Value": "Значение",
        "Explanation": "Пояснение",
        "set in the corpus config": "задано в конфиге корпуса",
        "inherited globally": "унаследовано глобально",
        "Save to corpus config": "Сохранить в конфиг корпуса",
        "the change affects only future checks; the current revision is unchanged":
            "правка затрагивает только будущие проверки; текущая ревизия не меняется",
        "config found": "конфиг найден",
        "config not found": "конфиг не найден",
        "config not found (server)": "конфиг не найден",
        "enabled": "включено",
        "disabled": "выключено",
        "allowed range {low} to {high}. Available":
            "Допустимо от {low} до {high}.",
        "Global settings saved. {note}": "Общие настройки сохранены. {note}",
        "development settings saved": "настройки разработки сохранены",
        "Written to {config}: {keys}. A backup copy was created next to it.":
            "Записано в {config}: {keys}. Рядом создана резервная копия.",
        # --- режим и клиенты ------------------------------------------------
        "Mode and clients": "Режим и клиенты",
        "RAGRAF operating channel": "Канал работы RAGRAF",
        "The mode describes the EXPECTED channel and is verified by a probe. "
        "The other channel stays available: CLI and MCP do not conflict.":
            "Режим описывает ОЖИДАЕМЫЙ канал и проверяется пробой. "
            "Второй канал при этом остаётся доступен: CLI и MCP не конфликтуют.",
        "Apply and verify": "Применить и проверить",
        "Verify": "Проверить",
        "mode confirmed": "режим подтверждён",
        "mode NOT confirmed": "режим НЕ подтверждён",
        "verification not started": "проверка не запускалась",
        "Channels": "Каналы",
        "channel": "канал",
        "state": "состояние",
        "what is visible": "что видно",
        "what to do": "что делать",
        "Clients": "Клиенты",
        "client": "клиент",
        "AutoClaw: values for the “Add tool” form":
            "AutoClaw: значения для формы «Add tool»",
        "field": "поле",
        "value": "значение",
        "client name (for example AutoClaw)":
            "имя клиента (например AutoClaw)",
        "Grant access": "Выдать доступ",
        "DeepSeek Harness: profile block": "DeepSeek Harness: блок профиля",
        "profile: ": "профиль: ",
        "; connection is configured, transport {transport}":
            "; подключение заведено, транспорт {transport}",
        "; no connection": "; подключения нет",
        "Apply block": "Применить блок",
        "tool: ": "инструмент: ",
        "found": "найден",
        "not found ": "не найден",
        "; corpus config: ": "; конфиг корпуса: ",
        "in place": "на месте",
        "works": "работает",
        "connected": "подключён",
        "no probes yet": "проб ещё не было",
        "mode saved: {mode}": "режим сохранён: {mode}",
        "could not": "не удалось",
        "checking channels…": "проверяю каналы…",
        "verification finished": "проверка завершена",
        "the value is issued once, copy it now: {value}":
            "значение выдано один раз, скопируйте сейчас: {value}",
        "could not issue access": "не удалось выдать доступ",
        "block written, the patch is parsed by the application; restart Harness":
            "блок записан, патч разбирается приложением; перезапустите Harness",
        "not applied: {reason}": "не применено: {reason}",
        "reason not given": "причина не названа",
        # --- индексация из панели ---------------------------------------------
    "indexing allowed": "индексация разрешена",
    "indexing forbidden": "индексация запрещена",
    "Indexing": "Индексация",
    "reindex and include or exclude developments":
        "переиндексация, включение и исключение разработок",
    "Reindex": "Переиндексировать",
    "Delete index": "Удалить индекс",
    "Reindex all out of date ({count})":
        "Переиндексировать все отставшие ({count})",
    "Refresh state": "Обновить состояние",
    "indexing is idle": "индексация не выполняется",
    "no developments need reindexing": "переиндексировать нечего",
    "indexing already running": "индексация уже выполняется",
    "Out of date: ": "Отстали: ",
    "Indexing started.": "Индексация запущена.",
    "starting indexing…": "запускаю индексацию…",
    "Indexing of {name} started.": "Индексация «{name}» запущена.",
    "Indexing of {name} is allowed.": "Индексация «{name}» разрешена.",
    "Indexing of {name} is forbidden.": "Индексация «{name}» запрещена.",
    "Index of {name} deleted.": "Индекс «{name}» удалён.",
    "Indexing {done} of {total}: {current}…":
        "Индексация {done} из {total}: {current}…",
    "Indexing finished: {ok} of {total} succeeded":
        "Индексация завершена: успешно {ok} из {total}",
    "indexing finished": "индексация завершена",
    "indexing failed": "индексация не удалась",
    "indexing is disabled for this development":
        "индексация этой разработки запрещена",
    "index directory was not removed": "каталог индекса удалить не удалось",
    "the registry entry has no config path":
        "в записи реестра не указан путь к конфигу",
    "config file is missing: {path}": "файл конфига не найден: {path}",
    "Indexing is forbidden, but the index was not deleted: {reason}":
        "Индексация запрещена, но индекс удалить не удалось: {reason}",
    "Delete the collected index of {name}? "
    "The registry entry and history will remain.":
        "Удалить собранный индекс «{name}»? Запись в реестре и история останутся.",
    "Indexing of {name} will be forbidden. "
    "Delete the already collected index as well?":
        "Индексация «{name}» будет запрещена. Удалить и уже собранный индекс?",
    "Exclude {name} from indexing?": "Исключить «{name}» из индексации?",
    "The collected index will be deleted. The registry entry and "
    "history will remain.":
        "Собранный индекс будет удалён. Запись в реестре и история останутся.",
    # --- подсказки к сбоям чтения состояния --------------------------------
    # Здесь значение равно ключу НАМЕРЕННО: ключом подсказки служит сам русский
    # текст (сервер шлёт его в JSON), поэтому русский перевод и есть ключ.
    # Без этих записей наборы ключей ru и zh разошлись бы, а `translate()`
    # отвечал бы верно лишь по совпадению. Английский текст — в HINT_TEXTS_EN.
    "Каталог .ragraf этой разработки недоступен для записи. SQLite открывает "
    "базу с журналом, поэтому «только чтение» тоже требует права записи: "
    "проверьте права на каталог или запустите панель от имени, у которого "
    "они есть.":
        "Каталог .ragraf этой разработки недоступен для записи. SQLite открывает "
        "базу с журналом, поэтому «только чтение» тоже требует права записи: "
        "проверьте права на каталог или запустите панель от имени, у которого "
        "они есть.",
    "С базой этой разработки работает другой процесс. Дождитесь окончания "
    "индексации или закройте лишние окна панели.":
        "С базой этой разработки работает другой процесс. Дождитесь окончания "
        "индексации или закройте лишние окна панели.",
    "Файл или каталог защищён от записи: проверьте права доступа.":
        "Файл или каталог защищён от записи: проверьте права доступа.",
    "В базе нет ожидаемой таблицы: индекс собран другой версией RAGRAF. "
    "Переиндексируйте разработку.":
        "В базе нет ожидаемой таблицы: индекс собран другой версией RAGRAF. "
        "Переиндексируйте разработку.",
    "Базы этой разработки ещё не собраны. Нажмите «Переиндексировать», "
    "чтобы построить индекс: после этого разделы заполнятся.":
        "Базы этой разработки ещё не собраны. Нажмите «Переиндексировать», "
        "чтобы построить индекс: после этого разделы заполнятся.",
    "development not found: {name}": "разработка не найдена: {name}",
    # --- доступ ---------------------------------------------------------
    "Access": "Доступ",
    "Administrator password": "Пароль администратора",
        "administrator": "администратор",
        "session token": "токен сессии",
        "password is set": "пароль задан",
        "password is not set — sign-in is possible with the session token":
            "пароль не задан — вход возможен по токену сессии",
        "new password (at least {count} characters)":
            "новый пароль (не короче {count})",
        "current password (when changing)": "текущий пароль (при смене)",
        "Set password": "Задать пароль",
        "Clear password": "Снять пароль",
        "revoked": "отозван",
        "active": "действует",
        "inactive": "не действует",
        "restore": "восстановить",
        "revoke": "отозвать",
        "delete": "удалить",
        "no tokens issued": "токенов не выдано",
        "(no name)": "(без имени)",
        "no records": "записей нет",
        "(no access)": "(без доступа)",
        "Issued tokens": "Выданные токены",
        "issued to (required)": "кому выдан (обязательно)",
        "note": "примечание",
        "Create token": "Создать токен",
        "issued to": "кому выдан",
        "scopes": "права",
        "state short": "состояние",
        "uses": "использований",
        "last use": "последнее",
        "Calls by access": "Обращения по доступам",
        "access": "доступ",
        "successful": "успешных",
        "denied": "отказов",
        "errors": "ошибок",
        "Access log": "Журнал обращений",
        "when": "когда",
        "surface": "канал",
        "operation": "операция",
        "name": "имя",
        "sign-in method": "способ входа",
        "result": "результат",
        "reason": "причина",
        "password set": "пароль задан",
        "password cleared": "пароль снят",
        "access revoked": "доступ отозван",
        "access restored": "доступ восстановлен",
        "record deleted": "запись удалена",
        "could not create": "не удалось создать",
        "Delete this access record? The call history will remain.":
            "Удалить запись доступа? История обращений останется.",
        "no token and no password session": "ни токена, ни парольной сессии",
        "wrong password": "неверный пароль",
        "password is not set": "пароль не задан",
        "Sign in": "Войти",
        "Sign out": "Выйти",
        "Session token": "Токен сессии",
        "administrator password": "пароль администратора",
        "Use token": "Применить токен",
        "Forget token": "Забыть токен",
        "Signed in by the session token. Write actions are available.":
            "Вход выполнен по токену сессии. Изменяющие действия доступны.",
        "Write actions are unavailable: sign in with the administrator "
        "password or paste the session token printed when the panel started.":
            "Изменяющие действия недоступны: войдите по паролю администратора "
            "или вставьте токен сессии, напечатанный при запуске панели.",
        "No administrator password is set: sign-in is "
        "possible only with the session token printed when the panel started.":
            "Пароль администратора не задан: войти можно только по токену сессии, "
            "напечатанному при запуске панели.",
        "The value is printed at start-up and kept in .ragraf/admin/session_key, "
        "so it survives restarts.":
            "Значение печатается при запуске панели и лежит в файле "
            ".ragraf/admin/session_key: перезапуск его не меняет. Хранится только "
            "в этом браузере.",
        "Signed in. Write actions are available.":
            "Вход выполнен. Изменяющие действия доступны.",
        "Token accepted. Write actions are available.":
            "Токен принят. Изменяющие действия доступны.",
        "current password is wrong": "текущий пароль неверен",
        "the password is empty": "пароль пуст",
        "no value supplied": "значение не передано",
        "the value matches no token": "значение не соответствует ни одному токену",
        "the token has expired": "срок действия токена истёк",
        # --- базы данных ----------------------------------------------------
        "Databases": "Базы данных",
        "RAGRAF indexes the STRUCTURE of databases: databases, tables, columns, "
        "types, indexes, views and relations. Values of columns recognised as "
        "personal data or secrets are NOT collected at all — the field shows "
        "“hidden” instead. Write and schema-change operations are not supported: "
        "read only.":
            "RAGRAF индексирует СТРУКТУРУ баз: базы, таблицы, колонки, "
            "типы, индексы, представления и связи. Значения колонок, признанных "
            "персональными данными или секретами, <b>не собираются вовсе</b> — "
            "на их месте пометка «скрыто». Операции записи и изменения "
            "структуры не поддерживаются: только чтение.",
        "Connection works: {dialect}, server {server}. Databases visible: {databases}. "
        "Client: {client}.":
            "Подключение работает: {dialect}, сервер {server}. "
            "Баз видно: {databases}. Клиент: {client}.",
        "Connection does not work: {reason}": "Подключение не работает: {reason}",
        "reason not reported": "причина не сообщена",
        "What to do: {action}": "Что сделать: {action}",
        "Already in the index": "Что уже в индексе",
        "databases": "баз",
        "tables": "таблиц",
        "columns": "колонок",
        "views": "представлений",
        "relations": "связей",
        "closed as personal data": "закрыто как персональные",
        "Structure fingerprint: {fingerprint}":
            "Отпечаток структуры: {fingerprint}",
        " · collected {when}": " · собран {when}",
        "The freshness watcher computes the structure fingerprint the same way "
        "as the corpus checksum: if the structure changes, the index becomes "
        "stale and is rebuilt. Changing rows does not affect the fingerprint.":
            "Сторож свежести считает отпечаток структуры так же, как "
            "сумму корпуса: изменилась структура — индекс устареет и будет "
            "пересобран. Изменение строк на отпечаток не влияет.",
        "The database layer is not collected yet": "Слой баз ещё не собран",
        "Collection": "Сбор",
        "DBMS": "СУБД",
        "Host": "Хост",
        "Port": "Порт",
        "User": "Пользователь",
        "Access value": "Значение доступа",
        "not shown; leave empty to keep it unchanged":
            "не показывается; оставьте пустым, чтобы не менять",
        "Databases list": "Базы",
        "empty — all except system ones": "пусто — все, кроме служебных",
        "comma separated": "через запятую",
        "Exclude": "Исключить",
        "Access value does not have to be stored on disk at all: the environment "
        "variables {vars} take precedence over the file.":
            "Значение доступа можно не хранить на диске вовсе: "
            "переменные окружения {vars} важнее файла.",
        "Limits": "Пределы",
        "Rows in sample": "Строк в примере",
        "Bytes per sample": "Байт на пример",
        "Max tables": "Таблиц не более",
        "Timeout seconds": "Секунд ожидания",
        "collect sample rows": "собирать примеры строк",
        "(the only part that touches content; personal columns stay hidden)":
            "(единственная часть, которая касается содержимого; "
            "персональные колонки остаются скрытыми)",
        "Save settings": "Сохранить настройки",
        "Check connection": "Проверить подключение",
        "Index the structure": "Проиндексировать структуру",
        "The structure is read by the operations {ops}. A relation from a "
        "foreign key is a fact; a relation derived from a column name is marked "
        "as an inference with confidence at most 0.5.":
            "Структура читается операциями {ops}. Связь из внешнего ключа — "
            "факт; связь, выведенная по имени колонки, помечена как вывод с "
            "достоверностью не выше 0,5.",
        "set ({source})": "задано ({source})",
        "not set": "не задано",
        "environment": "окружение",
        "file": "файл",
        "saving database settings…": "сохраняю настройки баз…",
        "could not save": "не удалось сохранить",
        "checking connection…": "проверяю подключение…",
        "connection works": "подключение работает",
        "does not work: {reason}": "не работает: {reason}",
        "collecting structure and samples…": "собираю структуру и примеры…",
        "collecting structure…": "собираю структуру…",
        "collected and written: tables {tables}, relations {relations}, "
        "closed as personal data {sensitive}, samples {samples}":
            "собрано и записано: таблиц {tables}, связей {relations}, "
            "закрыто как персональные {sensitive}, примеров {samples}",
        "collection not performed": "сбор не выполнен",
        "the database layer for this revision is not collected":
            "слой баз для этой ревизии не собран",
        "no revision in the corpus: run index first":
            "в корпусе нет ревизии: сначала выполните index",
        "database collection is disabled in the settings":
            "сбор баз выключен в настройках",
        "enable collection and save the settings":
            "включите сбор и сохраните настройки",
        "enable collection in the “Databases” section of the panel":
            "включите сбор в разделе «Базы данных» панели",
        "the mysql client was not found: install it or add it to PATH":
            "клиент mysql не найден: установите его или добавьте в PATH",
        "the postgresql client was not found: install it or add it to PATH":
            "клиент postgresql не найден: установите его или добавьте в PATH",
        "check the DBMS installation": "проверьте установку СУБД",
        "no access value: neither in the settings nor in the environment":
            "доступ не задан: ни в настройках, ни в окружении",
        "fill in the access value in the panel or set RAGRAF_DB_PASSWORD":
            "заполните доступ в панели или задайте RAGRAF_DB_PASSWORD",
        "no answer": "нет ответа",
        "check the user, the access value and the read permissions":
            "проверьте пользователя, значение доступа и права на чтение",
        "check the access value and the address": "проверьте доступ и адрес",
        # --- пробы каналов и клиентов (probes.py) ---------------------------
        "Working through the shell (CLI)": "Работа через оболочку (CLI)",
        "MCP as a local process": "MCP как локальный процесс",
        "MCP over URL (Online service)": "MCP по URL (Online service)",
        "reference": "справочно",
        "no output": "нет вывода",
        "Settings → Tools & connections → Add tool. Type: Online service. "
        "The values below are entered by hand: AutoClaw registers MCP servers "
        "only through its own interface and has no configuration file for them.":
            "Settings → Tools & connections → Add tool. Type: Online service. "
            "Значения ниже вводятся руками: AutoClaw регистрирует MCP-серверы "
            "только через свой интерфейс, файла конфигурации для них нет.",
        "the access value is issued in the “Access” section: create a separate "
        "token named AutoClaw and paste it into the Bearer token field — it is "
        "shown once":
            "значение доступа выдаётся в разделе «Доступ»: создайте "
            "отдельный токен с именем AutoClaw и вставьте в поле "
            "Bearer token — показывается один раз",
        "the tool lives in ANOTHER project and the panel does not change it: "
        "the actual state and the variables that control it are shown. "
        "Changing it requires a separate instruction from the owner.":
            "инструмент лежит в ЧУЖОМ проекте, и панель его не правит: "
            "показывается фактическое состояние и переменные, которыми он "
            "управляется. Правка — по отдельному указанию владельца.",

        # --- тема оформления и разделы панели -------------------------------
        "Theme": "Тема",
        "Light": "Светлая",
        "Dark": "Тёмная",
        "System theme": "Как в системе",
        "Sections": "Разделы",
        "developments without index: {count}": "разработок без индекса: {count}",
        "Both forms are available: Online service and Local program.":
            "Доступны обе формы подключения: Online service и Local program.",
        "No connection from {name} yet": "Обращений от {name} не видно.",
        "The service is up, no calls from {name} yet":
            "Сервис поднят, но обращений от {name} пока нет.",
        "Connected: last call {ago} ago": "Связь есть: последнее обращение {ago} назад",
        "No calls for {ago}: the connection may be broken":
            "Обращений не было {ago}: возможно, связь потеряна.",
        "last tool: {tool}": "последний инструмент: {tool}",
        "{n} seconds": "{n} с",
        "{n} minutes": "{n} мин",
        "{n} hours": "{n} ч",
        "Connection in AutoClaw": "Подключение в AutoClaw",
        "Connection in HARNESS": "Подключение в HARNESS",
        "Start the service first:": "Сначала поднимите сервис:",
        "CLI needs no client: the agent calls":
            "CLI не требует клиента: агент вызывает",
        "This channel works without AutoClaw or HARNESS settings.":
            "Этот канал работает без настройки AutoClaw и HARNESS.",
        "AutoClaw connects over HTTP: the selected mode does not expect that "
        "channel. Start the service shown here or pick mcp-http or both.":
            "AutoClaw подключается по HTTP, а выбранный режим этот канал не "
            "ожидает: поднимите сервис по команде выше либо выберите mcp-http "
            "или both.",
        "The profile file:": "Файл профиля:",
        "Admin": "Администратор",
        "Guest": "Гость",
        "Account": "Учётная запись",
        "Not signed in": "Вход не выполнен",
        "Signed in as administrator": "Вход выполнен: администратор",
        "Token rejected: this value is not valid for this panel. Take it from "
        ".ragraf/admin/session_key or from the start-up output.":
            "Токен не принят: он не действует для этой панели. Откройте файл "
            ".ragraf/admin/session_key и вставьте его содержимое, либо возьмите "
            "строку «токен доступа» из вывода панели при запуске.",
        "Signed out.": "Вы вышли из системы.",
        "Token forgotten.": "Токен в браузере забыт.",
        # --- каркас панели: сайдбар, топбар, палитра (правка 15.10.2026) -----
        "Monitoring": "Мониторинг",
        "Index": "Индекс",
        "Observability": "Наблюдение",
        "System": "Система",
        "Overview": "Обзор",
        "Section, project or action…": "Раздел, разработка или действие…",
        "Command palette": "Палитра команд",
        "Language": "Язык",
        "Density": "Плотность",
        "Compact": "Плотная",
        "Comfortable": "Обычная",
        "Skip to content": "Перейти к содержимому",
        "Close": "Закрыть",
        "Cancel": "Отмена",
        "Confirm": "Подтвердить",
        "Copy": "Копировать",
        "Copied to clipboard": "Скопировано в буфер обмена",
        "Search": "Поиск",
        "navigation": "навигация",
        "run": "выполнить",
        "open": "открыть",
        "progress": "прогресс",
        "running": "идёт",
        "ready": "готово",
        "M": "млн",
        "K": "тыс.",
        # --- обзор ----------------------------------------------------------
        "Requires attention": "Требует внимания",
        "Errors and quality anomalies that affect the reliability of facts":
            "Ошибки и аномалии качества, влияющие на достоверность фактов",
        "Index states": "Состояние индексов",
        "Top developments by calls": "Топ разработок по обращениям",
        "who is really used by agents": "кто реально используется агентами",
        "Recent events": "Последние события",
        "access log of calls": "журнал обращений по доступам",
        "Facade operations": "Операции фасада",
        "aggregate across all developments: calls, facts, average duration":
            "агрегат по всем разработкам: вызовы, факты, средняя длительность",
        "Summary of corpora, index states and the effect for agents. "
        "Critical states come first.":
            "Сводка по корпусам, состоянию индексов и эффекту для агентов. "
            "Критичные состояния — вверху.",
        "by {count} developments": "по разработкам: {count}",
        "The corpus directory does not exist": "Каталог корпуса не существует",
        "code graph, lexicon and vectors, journal": "граф кода, лексика и векторы, журнал",
        "{nodes} nodes · {edges} edges": "узлов {nodes} · рёбер {edges}",
        "The values do not block facts but reduce the completeness of matches":
            "Значения не блокируют выдачу фактов, но снижают полноту совпадений",
        "Check the client connection or the access value":
            "Проверьте подключение клиента или значение доступа",
        # --- разработки -----------------------------------------------------
        "Files": "Файлы",
        "Nodes": "Узлы",
        "Edges": "Рёбра",
        "Total": "Всего",
        "Actions": "Действия",
        "Details": "Подробности",
        "Open": "Открыть",
        "Reset": "Сбросить",
        "Share": "Доля",
        "Search developments": "Поиск разработок",
        "Search by name, path, revision or checksum…":
            "Поиск по имени, пути, ревизии или сумме…",
        "Out of date": "Отстали",
        "Up to date": "Актуальны",
        "All": "Все",
        "Nothing found for the given conditions": "Ничего не найдено по заданным условиям",
        "No developments in the registry": "В реестре нет разработок",
        "Register a corpus with the command below": "Зарегистрируйте корпус командой ниже",
        "Selected": "Выбрано",
        "Select": "Выбрать",
        "Select all on page": "Выбрать всё на странице",
        "Reindex selected": "Переиндексировать выбранные",
        "Reindex: {count}": "Переиндексация: {count}",
        "Allow indexing": "Разрешить индексацию",
        "Forbid indexing": "Запретить индексацию",
        "Exclude {count} developments from indexing?":
            "Исключить из индексации разработок: {count}?",
        "{count} developments updated": "обновлено разработок: {count}",
        "Per page": "На странице",
        "Page {n}": "Страница {n}",
        "Previous page": "Предыдущая страница",
        "Next page": "Следующая страница",
        "capability": "полнота",
        "The index of this development has not been built yet":
            "Индекс этой разработки ещё не собран",
        "Reindex it to fill the sections in": "Переиндексируйте её, чтобы разделы заполнились",
        # --- индексация -----------------------------------------------------
        "Queue": "Очередь",
        "Run log": "Журнал выполнения",
        "Started": "Начато",
        "Finished": "Завершено",
        "State by development": "Состояние по разработкам",
        "revision, completeness and indexing permission":
            "ревизия, полнота и разрешение на индексацию",
        "{done} of {total} done": "выполнено {done} из {total}",
        "what the server reports about the current run":
            "что сервер сообщает о текущем прогоне",
        "Start a reindex: progress and the log will appear here.":
            "Запустите переиндексацию: здесь появится ход работы и журнал.",
        "Start a reindex to see the log.": "Запустите переиндексацию — журнал появится здесь.",
        "The task will be queued and performed with a full rebuild.":
            "Задача будет поставлена в очередь и выполнена с полным пересбором.",
        "Scan, AST, reference resolution, code property graph and vector encoding.":
            "Сканирование, AST, разрешение ссылок, граф свойств кода и кодирование векторов.",
        "The action is irreversible: the index data will be deleted.":
            "Действие необратимо: данные индекса будут удалены.",
        "The development files are not changed. The index will have to be built again.":
            "Файлы разработки не меняются. Индекс придётся строить заново.",
        # --- качество и использование --------------------------------------
        "Indexing stages": "Стадии индексации",
        "time and number of processed elements": "время и число обработанных элементов",
        "Problems found: {count}": "Найдено проблем: {count}",
        "Only with problems": "только с проблемами",
        "The section is filled in from a background snapshot.":
            "Раздел заполняется из фонового снимка.",
        "The quality section could not be read":
            "Раздел «Качество индекса» прочитать не удалось",
        "The usage section could not be read":
            "Раздел «Использование и эффект» прочитать не удалось",
        "Tokens": "Токены",
        "Tokens and lines are estimates, calls and facts are measured.":
            "Токены и строки — оценка, обращения и факты — измерение.",
        "calls, findings, duration": "вызовы, находки, длительность",
        "click a row to see the details": "нажмите строку, чтобы увидеть подробности",
        "registered connections and their agents":
            "зарегистрированные подключения и их агенты",
        # --- размеры баз и настройки ---------------------------------------
        "Distribution of volume": "Распределение объёма",
        "{count} largest developments, MB": "{count} крупнейших разработок, МБ",
        "{count} databases": "баз: {count}",
        "all databases of all developments": "все базы разработок",
        "journal": "журнал",
        "No database matches the filter": "Ни одна база не подходит под фильтр",
        "By development": "По разработкам",
        # --- режим, доступ, базы данных ------------------------------------
        "the result of the last probe for each transport":
            "результат последней пробы по каждому транспорту",
        "Sign in with the administrator password or paste the session token "
        "printed when the panel started.":
            "Войдите по паролю администратора или вставьте токен сессии, "
            "напечатанный при запуске панели.",
        "Write actions are unavailable": "Изменяющие действия недоступны",
        "Sign in to continue": "Войдите, чтобы продолжить",
        "administrator password, tokens, calls by access and the log":
            "пароль администратора, токены, обращения по доступам и журнал",
        "All channels": "Все каналы",
        "All results": "Все результаты",
        "operation, name or reason…": "операция, имя, причина…",
        "channel, operation, sign-in method and the reason for a refusal":
            "канал, операция, способ входа и причина отказа",
        "identical “access + result” pairs are collapsed; the repeat count is in the row":
            "одинаковые пары «доступ + результат» схлопнуты, счётчик повторов — в строке",
        "The value of a new token is shown once and is not stored by the panel":
            "Значение нового токена показывается один раз и панелью не хранится",
        "environment variables take precedence over the configuration file":
            "переменные окружения важнее файла конфигурации",
        "they limit the amount of collected samples":
            "ограничивают объём собираемых примеров",
        "the actual content of the structure index":
            "фактическое содержимое индекса структуры",
        "Structure fingerprint": "Отпечаток структуры",
        "Values of columns recognised as personal data or secrets are NOT "
        "collected at all":
            "Значения колонок, признанных персональными данными или секретами, "
            "НЕ собираются вовсе",
        "Write and schema-change operations are not supported: read only.":
            "Операции записи и изменения структуры не поддерживаются: только чтение.",
        "RAGRAF indexes the STRUCTURE of databases: databases, tables, columns, "
        "types, indexes, views and relations.":
            "RAGRAF индексирует СТРУКТУРУ баз: базы, таблицы, колонки, типы, "
            "индексы, представления и связи.",
    },
    "zh": {
        # --- 页面顶部 --------------------------------------------------------
        "RAGRAF — control panel": "RAGRAF — 管理面板",
        "control panel": "管理面板",
        "loading…": "加载中…",
        "Refresh": "刷新",
        "Collecting information about developments…": "正在收集开发项目信息…",
        "Collecting: developments {done} of {total}…":
            "正在收集：已完成 {done} / {total} 个开发项目…",
        "All indexes are up to date": "所有索引均为最新",
        "developments need reindexing: {count}":
            "个开发项目需要重新索引：{count}",
        "language": "语言",
        # --- 计量单位 --------------------------------------------------------
        "B": "字节",
        "KB": "KB",
        "MB": "MB",
        "GB": "GB",
        # --- 通用 ------------------------------------------------------------
        "response could not be parsed": "无法解析响应",
        "status code {code}": "状态码 {code}",
        "Could not reach the panel: {reason}": "无法连接到面板：{reason}",
        "loading": "正在更新…",
        "load error": "加载失败",
        "Could not get state: {reason}": "无法获取状态：{reason}",
        "unknown reason": "原因未知",
        "saving…": "正在保存…",
        "settings saved": "设置已保存",
        "save error": "保存失败",
        "Could not save: {reason}": "无法保存：{reason}",
        "not found": "未找到",
        "none": "无",
        "no data": "暂无数据",
        "— total {size}": "— 合计 {size}",
        "including journal: {size}": "其中日志：{size}",
        "file not found": "文件未找到",
        # --- 开发项目列表 ----------------------------------------------------
        "No developments in the registry. Register a corpus with ":
            "注册表中没有开发项目。请使用以下命令注册语料库 ",
        "index is up to date": "索引为最新",
        "reindexing required": "需要重新索引",
        "could not check: {reason}": "无法检查：{reason}",
        "never indexed": "从未索引",
        "waiting for data…": "等待数据…",
        "Developments": "开发项目",
        "files / nodes / edges · corpus checksum": "文件 / 节点 / 边 · 语料库校验和",
        "Development": "开发项目",
        "Revision": "修订版本",
        "Index state": "索引状态",
        "Size": "大小",
        "Database size": "数据库大小",
        "Corpus checksum": "语料库校验和",
        # --- 数据库大小 ------------------------------------------------------
        "Database sizes": "数据库大小",
        "first 24 tables of each database are shown": "每个数据库显示前 24 张表",
        "Database": "数据库",
        "File": "文件",
        "Table": "表",
        "Rows": "行数",
        "code graph": "代码图",
        "lexicon and vectors": "词法与向量",
        "journal and telemetry": "日志与遥测",
        # --- 使用情况 --------------------------------------------------------
        "Statistics unavailable: {reason}": "统计不可用：{reason}",
        "Usage and effect": "使用情况与效果",
        "tokens and lines are estimates, calls and facts are measured":
            "词元与行数为估计值，调用与事实为实测值",
        "Calls": "调用次数",
        "to facade operations": "对门面操作的调用",
        "Sessions": "会话数",
        "tool runs": "工具运行次数",
        "Facts": "事实数",
        "obtained from the index": "从索引获得",
        "Verifiability": "可验证性",
        "with a file:line reference": "带 file:line 引用",
        "Tokens (estimate)": "词元（估计）",
        "by response size": "按响应体积估算",
        "Lines not read as files": "未按文件读取的行数",
        "lines of code (estimate)": "代码行数（估计）",
        "autoclaw": "AutoClaw",
        "external": "外部",
        "internal": "内部",
        "By agent (who used RAGRAF)": "按智能体（谁使用了 RAGRAF）",
        "Agent": "智能体",
        "Family": "类别",
        "Calls short": "调用",
        "Facts short": "事实",
        "Lines not read": "未读取行数",
        "Errors": "错误",
        "Agents": "智能体数",
        " — calls {count}": " — 调用 {count}",
        "Operation": "操作",
        "Found": "命中",
        "With reference": "带引用",
        "Average": "平均",
        "Target": "对象",
        " {ms} ms": " {ms} 毫秒",
        # --- 质量 ------------------------------------------------------------
        "Index quality": "索引质量",
        "vector norms, chunk sizes, signature completeness":
            "向量范数、分块大小、签名完整度",
        "no data available": "暂无数据",
        "no problems found": "未发现问题",
        "problems: {count}": "问题：{count}",
        "Vectors": "向量",
        "normalized": "已归一化",
        "not normalized": "未归一化",
        "none at all": "无",
        "norm p50 {value}": "范数 p50 {value}",
        "Chunks": "分块",
        "maximum {value} tokens": "最多 {value} 个词元",
        "Over budget": "超出限额",
        "chunks longer than {value}": "长于 {value} 的分块",
        "Files without symbols": "无符号的文件",
        "wrapped into windows": "已包入窗口",
        "Functions": "函数",
        "with parameters parsed": "已解析参数的函数",
        "Truncated signatures": "被截断的签名",
        "parameters lost in the text": "参数在文本中丢失",
        "Docblocks": "文档块",
        "parameter contracts": "参数约定",
        "GPU": "显卡",
        "CPU": "处理器",
        "not determined": "未确定",
        "Vector compute: ": "向量计算设备：",
        " {ms} ms per text": " 每段文本 {ms} 毫秒",
        "Detected: ": "检测到：",
        "Stage": "阶段",
        "Time": "耗时",
        "Items": "条目数",
        " {s} s": " {s} 秒",
        "vector search is unavailable: the vec0 extension is not loaded":
            "向量搜索不可用：vec0 扩展未加载",
        "torch is not installed": "未安装 torch",
        "torch does not see the GPU": "torch 未检测到显卡",
        "GPU compute does not work": "显卡计算不可用",
        "sentence-transformers is not installed": "未安装 sentence-transformers",
        # --- 设置 ------------------------------------------------------------
        "Settings": "设置",
        "auto-reindex thresholds and indexing parameters":
            "自动重新索引阈值与索引参数",
        "Defaults": "默认值",
        "Save global settings": "保存全局设置",
        "they apply to developments where the key is not set separately":
            "适用于未单独设置该项的开发项目",
        "No developments": "没有开发项目",
        "Setting": "设置项",
        "Value": "值",
        "Explanation": "说明",
        "set in the corpus config": "已在语料库配置中设置",
        "inherited globally": "继承全局值",
        "Save to corpus config": "保存到语料库配置",
        "the change affects only future checks; the current revision is unchanged":
            "该修改仅影响后续检查；当前修订版本不会改变",
        "config found": "找到配置",
        "config not found": "未找到配置",
        "config not found (server)": "未找到配置",
        "enabled": "已启用",
        "disabled": "已禁用",
        "allowed range {low} to {high}. Available": "允许范围 {low} 至 {high}。",
        "Global settings saved. {note}": "全局设置已保存。{note}",
        "development settings saved": "开发项目设置已保存",
        "Written to {config}: {keys}. A backup copy was created next to it.":
            "已写入 {config}：{keys}。旁边已创建备份副本。",
        # --- 模式与客户端 ----------------------------------------------------
        "Mode and clients": "模式与客户端",
        "RAGRAF operating channel": "RAGRAF 工作通道",
        "The mode describes the EXPECTED channel and is verified by a probe. "
        "The other channel stays available: CLI and MCP do not conflict.":
            "模式描述的是预期通道，并由探测加以验证。另一个通道仍然可用："
            "CLI 与 MCP 并不冲突。",
        "Apply and verify": "应用并验证",
        "Verify": "验证",
        "mode confirmed": "模式已确认",
        "mode NOT confirmed": "模式未确认",
        "verification not started": "尚未开始验证",
        "Channels": "通道",
        "channel": "通道",
        "state": "状态",
        "what is visible": "可见内容",
        "what to do": "如何操作",
        "Clients": "客户端",
        "client": "客户端",
        "AutoClaw: values for the “Add tool” form":
            "AutoClaw：“Add tool”表单的取值",
        "field": "字段",
        "value": "值",
        "client name (for example AutoClaw)": "客户端名称（例如 AutoClaw）",
        "Grant access": "授予访问权限",
        "DeepSeek Harness: profile block": "DeepSeek Harness：配置文件片段",
        "profile: ": "配置文件：",
        "; connection is configured, transport {transport}":
            "; 连接已配置，传输方式 {transport}",
        "; no connection": "；未配置连接",
        "Apply block": "应用片段",
        "tool: ": "工具：",
        "found": "已找到",
        "not found ": "未找到",
        "; corpus config: ": "；语料库配置：",
        "in place": "就位",
        "works": "可用",
        "connected": "已连接",
        "no probes yet": "尚未探测",
        "mode saved: {mode}": "模式已保存：{mode}",
        "could not": "失败",
        "checking channels…": "正在检查通道…",
        "verification finished": "验证完成",
        "the value is issued once, copy it now: {value}":
            "该值仅显示一次，请立即复制：{value}",
        "could not issue access": "无法授予访问权限",
        "block written, the patch is parsed by the application; restart Harness":
            "片段已写入，补丁由应用解析；请重启 Harness",
        "not applied: {reason}": "未应用：{reason}",
        "reason not given": "未说明原因",
        # --- 面板内的索引操作 --------------------------------------------------
        "indexing allowed": "允许索引",
        "indexing forbidden": "禁止索引",
        "Indexing": "索引",
        "reindex and include or exclude developments": "重新索引，以及启用或排除开发项目",
        "Reindex": "重新索引",
        "Delete index": "删除索引",
        "Reindex all out of date ({count})": "重新索引所有过期项（{count}）",
        "Refresh state": "刷新状态",
        "indexing is idle": "当前没有索引任务",
        "no developments need reindexing": "没有需要重新索引的项目",
        "indexing already running": "索引任务已在运行",
        "Out of date: ": "已过期：",
        "Indexing started.": "索引已启动。",
        "starting indexing…": "正在启动索引…",
        "Indexing of {name} started.": "已启动对 {name} 的索引。",
        "Indexing of {name} is allowed.": "已允许对 {name} 建立索引。",
        "Indexing of {name} is forbidden.": "已禁止对 {name} 建立索引。",
        "Index of {name} deleted.": "已删除 {name} 的索引。",
        "Indexing {done} of {total}: {current}…":
            "正在索引第 {done} / {total} 项：{current}…",
        "Indexing finished: {ok} of {total} succeeded":
            "索引完成：{total} 项中成功 {ok} 项",
        "indexing finished": "索引已完成",
        "indexing failed": "索引失败",
        "indexing is disabled for this development": "该开发项目的索引已被禁止",
        "index directory was not removed": "未能删除索引目录",
        "the registry entry has no config path": "注册表记录中未指定配置文件路径",
        "config file is missing: {path}": "未找到配置文件：{path}",
        "Indexing is forbidden, but the index was not deleted: {reason}":
            "已禁止索引，但未能删除索引：{reason}",
        "Delete the collected index of {name}? "
        "The registry entry and history will remain.":
            "删除 {name} 已建立的索引？注册表记录与历史将保留。",
        "Indexing of {name} will be forbidden. "
        "Delete the already collected index as well?":
            "将禁止对 {name} 建立索引。是否同时删除已建立的索引？",
        "Exclude {name} from indexing?": "将 {name} 排除在索引之外？",
        "The collected index will be deleted. The registry entry and "
        "history will remain.":
            "已建立的索引将被删除。注册表记录与历史将保留。",
        # --- 读取状态失败的提示 ------------------------------------------------
        # 键与服务器发送的文本一致：客户端用 t() 显示 error_hint，
        # 英文即为键本身。
        "Каталог .ragraf этой разработки недоступен для записи. SQLite открывает "
        "базу с журналом, поэтому «только чтение» тоже требует права записи: "
        "проверьте права на каталог или запустите панель от имени, у которого "
        "они есть.":
            "该开发项目的 .ragraf 目录不可写入。SQLite 打开数据库时需要日志文件，"
            "因此“只读”同样需要写权限：请检查目录权限，或以具备权限的账户启动面板。",
        "С базой этой разработки работает другой процесс. Дождитесь окончания "
        "индексации или закройте лишние окна панели.":
            "另一个进程正在使用该项目的数据库。请等待索引结束，或关闭多余的面板窗口。",
        "Файл или каталог защищён от записи: проверьте права доступа.":
            "文件或目录被禁止写入：请检查访问权限。",
        "В базе нет ожидаемой таблицы: индекс собран другой версией RAGRAF. "
        "Переиндексируйте разработку.":
            "数据库中缺少预期的表：索引由其他版本的 RAGRAF 建立。请重新索引该项目。",
        "Базы этой разработки ещё не собраны. Нажмите «Переиндексировать», "
        "чтобы построить индекс: после этого разделы заполнятся.":
            "该项目的数据库尚未建立。请点击“重新索引”来建立索引：之后这些区块会填充数据。",
        "development not found: {name}": "未找到开发项目：{name}",
        # --- 访问 ------------------------------------------------------------
        "Access": "访问",
        "Administrator password": "管理员密码",
        "administrator": "管理员",
        "session token": "会话令牌",
        "password is set": "密码已设置",
        "password is not set — sign-in is possible with the session token":
            "未设置密码 — 可使用会话令牌登录",
        "new password (at least {count} characters)":
            "新密码（至少 {count} 个字符）",
        "current password (when changing)": "当前密码（修改时填写）",
        "Set password": "设置密码",
        "Clear password": "清除密码",
        "revoked": "已撤销",
        "active": "有效",
        "inactive": "无效",
        "restore": "恢复",
        "revoke": "撤销",
        "delete": "删除",
        "no tokens issued": "尚未发放令牌",
        "(no name)": "（无名称）",
        "no records": "无记录",
        "(no access)": "（无访问权限）",
        "Issued tokens": "已发放的令牌",
        "issued to (required)": "发放对象（必填）",
        "note": "备注",
        "Create token": "创建令牌",
        "issued to": "发放对象",
        "scopes": "权限",
        "state short": "状态",
        "uses": "使用次数",
        "last use": "最近使用",
        "Calls by access": "按访问权限统计的调用",
        "access": "访问权限",
        "successful": "成功",
        "denied": "被拒绝",
        "errors": "错误",
        "Access log": "访问日志",
        "when": "时间",
        "surface": "通道",
        "operation": "操作",
        "name": "名称",
        "sign-in method": "登录方式",
        "result": "结果",
        "reason": "原因",
        "password set": "密码已设置",
        "password cleared": "密码已清除",
        "access revoked": "访问权限已撤销",
        "access restored": "访问权限已恢复",
        "record deleted": "记录已删除",
        "could not create": "无法创建",
        "Delete this access record? The call history will remain.":
            "删除此访问记录？调用历史将保留。",
        "no token and no password session": "既无令牌也无密码会话",
        "Sign in": "登录",
        "Sign out": "退出登录",
        "Session token": "会话令牌",
        "administrator password": "管理员密码",
        "Use token": "使用令牌",
        "Forget token": "忘记令牌",
        "Signed in by the session token. Write actions are available.":
            "已通过会话令牌登录。现在可以执行写入操作。",
        "Write actions are unavailable: sign in with the administrator "
        "password or paste the session token printed when the panel started.":
            "写入操作不可用：请使用管理员密码登录，或粘贴面板启动时输出的会话令牌。",
        "No administrator password is set: sign-in is "
        "possible only with the session token printed when the panel started.":
            "未设置管理员密码：只能使用面板启动时输出的会话令牌登录。",
        "The value is printed at start-up and kept in .ragraf/admin/session_key, "
        "so it survives restarts.":
            "该值在面板启动时输出，并保存在文件 .ragraf/admin/session_key 中："
            "重启不会改变它。它只保存在当前浏览器中。",
        "Signed in. Write actions are available.":
            "已登录。现在可以执行写入操作。",
        "Token accepted. Write actions are available.":
            "令牌已通过。现在可以执行写入操作。",
        "wrong password": "密码错误",
        "password is not set": "未设置密码",
        "current password is wrong": "当前密码不正确",
        "the password is empty": "密码为空",
        "no value supplied": "未提供值",
        "the value matches no token": "该值与任何令牌都不匹配",
        "the token has expired": "令牌已过期",
        # --- 数据库 ----------------------------------------------------------
        "Databases": "数据库",
        "RAGRAF indexes the STRUCTURE of databases: databases, tables, columns, "
        "types, indexes, views and relations. Values of columns recognised as "
        "personal data or secrets are NOT collected at all — the field shows "
        "“hidden” instead. Write and schema-change operations are not supported: "
        "read only.":
            "RAGRAF 索引数据库的<strong>结构</strong>：库、表、列、类型、索引、"
            "视图与关系。被认定为个人数据或密钥的列，其取值<strong>完全不采集</strong>"
            " — 该字段显示“已隐藏”。不支持写入与结构变更操作：只读。",
        "Connection works: {dialect}, server {server}. Databases visible: {databases}. "
        "Client: {client}.":
            "连接可用：{dialect}，服务器 {server}。可见数据库：{databases} 个。"
            "客户端：{client}。",
        "Connection does not work: {reason}": "连接不可用：{reason}",
        "reason not reported": "未报告原因",
        "What to do: {action}": "应如何处理：{action}",
        "Already in the index": "索引中已有内容",
        "databases": "数据库",
        "tables": "表",
        "columns": "列",
        "views": "视图",
        "relations": "关系",
        "closed as personal data": "按个人数据关闭",
        "Structure fingerprint: {fingerprint}": "结构指纹：{fingerprint}",
        " · collected {when}": " · 采集于 {when}",
        "The freshness watcher computes the structure fingerprint the same way "
        "as the corpus checksum: if the structure changes, the index becomes "
        "stale and is rebuilt. Changing rows does not affect the fingerprint.":
            "新鲜度监测器计算结构指纹的方式与语料库校验和相同：结构一旦变化，"
            "索引即过期并会重建。行数据变化不影响该指纹。",
        "The database layer is not collected yet": "数据库层尚未采集",
        "Collection": "采集",
        "DBMS": "数据库类型",
        "Host": "主机",
        "Port": "端口",
        "User": "用户",
        "Access value": "访问凭据",
        "not shown; leave empty to keep it unchanged": "不予显示；留空则保持不变",
        "Databases list": "数据库范围",
        "empty — all except system ones": "留空 — 除系统库以外的全部",
        "comma separated": "以逗号分隔",
        "Exclude": "排除",
        "Access value does not have to be stored on disk at all: the environment "
        "variables {vars} take precedence over the file.":
            "访问凭据完全可以不保存在磁盘上：环境变量 {vars} 优先于文件。",
        "Limits": "上限",
        "Rows in sample": "示例行数",
        "Bytes per sample": "每个示例的字节数",
        "Max tables": "表数量上限",
        "Timeout seconds": "等待秒数",
        "collect sample rows": "采集示例行",
        "(the only part that touches content; personal columns stay hidden)":
            "（唯一涉及内容的环节；个人数据列仍然隐藏）",
        "Save settings": "保存设置",
        "Check connection": "检查连接",
        "Index the structure": "索引结构",
        "The structure is read by the operations {ops}. A relation from a "
        "foreign key is a fact; a relation derived from a column name is marked "
        "as an inference with confidence at most 0.5.":
            "结构由操作 {ops} 读取。来自外键的关系是事实；由列名推断出的关系"
            "标记为推断，置信度不高于 0.5。",
        "set ({source})": "已设置（{source}）",
        "not set": "未设置",
        "environment": "环境变量",
        "file": "文件",
        "saving database settings…": "正在保存数据库设置…",
        "could not save": "无法保存",
        "checking connection…": "正在检查连接…",
        "connection works": "连接可用",
        "does not work: {reason}": "不可用：{reason}",
        "collecting structure and samples…": "正在采集结构与示例…",
        "collecting structure…": "正在采集结构…",
        "collected and written: tables {tables}, relations {relations}, "
        "closed as personal data {sensitive}, samples {samples}":
            "已采集并写入：表 {tables} 张，关系 {relations} 条，"
            "按个人数据关闭 {sensitive} 列，示例 {samples} 个",
        "collection not performed": "未执行采集",
        "the database layer for this revision is not collected":
            "该修订版本的数据库层尚未采集",
        "no revision in the corpus: run index first":
            "语料库中没有修订版本：请先运行 index",
        "database collection is disabled in the settings":
            "设置中已关闭数据库采集",
        "enable collection and save the settings": "请启用采集并保存设置",
        "enable collection in the “Databases” section of the panel":
            "请在面板的“数据库”部分启用采集",
        "the mysql client was not found: install it or add it to PATH":
            "未找到 mysql 客户端：请安装或将其加入 PATH",
        "the postgresql client was not found: install it or add it to PATH":
            "未找到 postgresql 客户端：请安装或将其加入 PATH",
        "check the DBMS installation": "请检查数据库系统是否安装",
        "no access value: neither in the settings nor in the environment":
            "未设置访问凭据：设置和环境变量中都没有",
        "fill in the access value in the panel or set RAGRAF_DB_PASSWORD":
            "请在面板中填写访问凭据，或设置 RAGRAF_DB_PASSWORD",
        "no answer": "无响应",
        "check the user, the access value and the read permissions":
            "请检查用户、访问凭据与读取权限",
        "check the access value and the address": "请检查访问凭据与地址",
        # --- 通道与客户端探测 ------------------------------------------------
        "Working through the shell (CLI)": "通过命令行工作（CLI）",
        "MCP as a local process": "以本地进程方式运行的 MCP",
        "MCP over URL (Online service)": "通过 URL 的 MCP（在线服务）",
        "reference": "仅供参考",
        "no output": "无输出",
        "Settings → Tools & connections → Add tool. Type: Online service. "
        "The values below are entered by hand: AutoClaw registers MCP servers "
        "only through its own interface and has no configuration file for them.":
            "Settings → Tools & connections → Add tool。类型：Online service。"
            "下列取值需手工填写：AutoClaw 只通过自身界面注册 MCP 服务器，"
            "没有对应的配置文件。",
        "the access value is issued in the “Access” section: create a separate "
        "token named AutoClaw and paste it into the Bearer token field — it is "
        "shown once":
            "访问凭据在“访问”部分发放：请创建一个名为 AutoClaw 的独立令牌，"
            "并粘贴到 Bearer token 字段 — 该值只显示一次",
        "the tool lives in ANOTHER project and the panel does not change it: "
        "the actual state and the variables that control it are shown. "
        "Changing it requires a separate instruction from the owner.":
            "该工具位于另一个项目中，面板不会修改它：此处显示实际状态以及控制它的"
            "变量。如需修改，须由所有者单独指示。",

        # --- тема оформления и разделы панели -------------------------------
        "Theme": "主题",
        "Light": "浅色",
        "Dark": "深色",
        "System theme": "跟随系统",
        "Sections": "分区",
        "developments without index: {count}": "尚未建立索引的研制项：{count}",
        "Both forms are available: Online service and Local program.":
            "两种连接方式都可用：Online service 与 Local program。",
        "No connection from {name} yet": "尚未看到来自 {name} 的请求。",
        "The service is up, no calls from {name} yet":
            "服务已启动，但还没有来自 {name} 的请求。",
        "Connected: last call {ago} ago": "已连接：最后一次请求在 {ago} 前",
        "No calls for {ago}: the connection may be broken":
            "已有 {ago} 没有请求：连接可能已断开。",
        "last tool: {tool}": "最后调用的工具：{tool}",
        "{n} seconds": "{n} 秒",
        "{n} minutes": "{n} 分钟",
        "{n} hours": "{n} 小时",
        "Connection in AutoClaw": "在 AutoClaw 中连接",
        "Connection in HARNESS": "在 HARNESS 中连接",
        "Start the service first:": "请先启动服务：",
        "CLI needs no client: the agent calls": "CLI 无需客户端：智能体调用",
        "This channel works without AutoClaw or HARNESS settings.":
            "该通道无需配置 AutoClaw 和 HARNESS。",
        "AutoClaw connects over HTTP: the selected mode does not expect that "
        "channel. Start the service shown here or pick mcp-http or both.":
            "AutoClaw 通过 HTTP 连接，而当前模式不包含该通道：请按上方命令启动服务，"
            "或选择 mcp-http / both。",
        "The profile file:": "配置文件：",
        "Admin": "管理员",
        "Guest": "访客",
        "Account": "账户",
        "Not signed in": "未登录",
        "Signed in as administrator": "已以管理员身份登录",
        "Token rejected: this value is not valid for this panel. Take it from "
        ".ragraf/admin/session_key or from the start-up output.":
            "令牌被拒绝：它对本次面板无效。请打开文件 .ragraf/admin/session_key "
            "并粘贴其内容，或从面板启动输出中取用访问令牌一行。",
        "Signed out.": "已退出登录。",
        "Token forgotten.": "已清除浏览器中的令牌。",
        # --- 面板框架：侧边栏、顶栏、命令面板（2026-10-15）-------------------
        "Monitoring": "监控",
        "Index": "索引",
        "Observability": "观测",
        "System": "系统",
        "Overview": "总览",
        "Section, project or action…": "页面、项目或操作…",
        "Command palette": "命令面板",
        "Language": "语言",
        "Density": "密度",
        "Compact": "紧凑",
        "Comfortable": "标准",
        "Skip to content": "跳到主要内容",
        "Close": "关闭",
        "Cancel": "取消",
        "Confirm": "确认",
        "Copy": "复制",
        "Copied to clipboard": "已复制到剪贴板",
        "Search": "搜索",
        "navigation": "导航",
        "run": "执行",
        "open": "打开",
        "progress": "进度",
        "running": "进行中",
        "ready": "就绪",
        "M": "百万",
        "K": "千",
        # --- 总览 -----------------------------------------------------------
        "Requires attention": "需要关注",
        "Errors and quality anomalies that affect the reliability of facts":
            "影响事实可靠性的错误与质量异常",
        "Index states": "索引状态",
        "Top developments by calls": "调用最多的项目",
        "who is really used by agents": "代理实际使用的项目",
        "Recent events": "最近事件",
        "access log of calls": "按访问方式的调用日志",
        "Facade operations": "门面操作",
        "aggregate across all developments: calls, facts, average duration":
            "所有项目的汇总：调用、事实、平均耗时",
        "Summary of corpora, index states and the effect for agents. "
        "Critical states come first.":
            "语料库、索引状态与对代理效果的汇总。关键状态排在最前。",
        "by {count} developments": "项目数：{count}",
        "The corpus directory does not exist": "语料库目录不存在",
        "code graph, lexicon and vectors, journal": "代码图、词法与向量、日志",
        "{nodes} nodes · {edges} edges": "节点 {nodes} · 边 {edges}",
        "The values do not block facts but reduce the completeness of matches":
            "这些值不会阻止事实输出，但会降低匹配的完整性",
        "Check the client connection or the access value":
            "请检查客户端连接或访问值",
        # --- 项目 -----------------------------------------------------------
        "Files": "文件",
        "Nodes": "节点",
        "Edges": "边",
        "Total": "合计",
        "Actions": "操作",
        "Details": "详情",
        "Open": "打开",
        "Reset": "重置",
        "Share": "占比",
        "Search developments": "搜索项目",
        "Search by name, path, revision or checksum…":
            "按名称、路径、修订或校验和搜索…",
        "Out of date": "已过期",
        "Up to date": "最新",
        "All": "全部",
        "Nothing found for the given conditions": "按给定条件未找到任何内容",
        "No developments in the registry": "注册表中没有项目",
        "Register a corpus with the command below": "用下面的命令注册语料库",
        "Selected": "已选",
        "Select": "选择",
        "Select all on page": "选择本页全部",
        "Reindex selected": "重新索引所选",
        "Reindex: {count}": "重新索引：{count}",
        "Allow indexing": "允许索引",
        "Forbid indexing": "禁止索引",
        "Exclude {count} developments from indexing?":
            "要将 {count} 个项目排除在索引之外吗？",
        "{count} developments updated": "已更新项目：{count}",
        "Per page": "每页",
        "Page {n}": "第 {n} 页",
        "Previous page": "上一页",
        "Next page": "下一页",
        "capability": "完整度",
        "The index of this development has not been built yet":
            "该项目的索引尚未构建",
        "Reindex it to fill the sections in": "重新索引后各分区即会填充",
        # --- 索引 -----------------------------------------------------------
        "Queue": "队列",
        "Run log": "运行日志",
        "Started": "已开始",
        "Finished": "已完成",
        "State by development": "按项目查看状态",
        "revision, completeness and indexing permission":
            "修订、完整度与索引许可",
        "{done} of {total} done": "已完成 {done} / {total}",
        "what the server reports about the current run":
            "服务器对本次运行所报告的内容",
        "Start a reindex: progress and the log will appear here.":
            "启动重新索引：进度与日志将显示在此处。",
        "Start a reindex to see the log.": "启动重新索引后即可看到日志。",
        "The task will be queued and performed with a full rebuild.":
            "任务将进入队列并以完整重建方式执行。",
        "Scan, AST, reference resolution, code property graph and vector encoding.":
            "扫描、AST、引用解析、代码属性图与向量编码。",
        "The action is irreversible: the index data will be deleted.":
            "此操作不可撤销：索引数据将被删除。",
        "The development files are not changed. The index will have to be built again.":
            "项目文件不会改动。索引需要重新构建。",
        # --- 质量与使用 ------------------------------------------------------
        "Indexing stages": "索引阶段",
        "time and number of processed elements": "各阶段耗时与处理元素数",
        "Problems found: {count}": "发现问题：{count}",
        "Only with problems": "仅显示有问题的",
        "The section is filled in from a background snapshot.":
            "该分区由后台快照填充。",
        "The quality section could not be read": "无法读取“索引质量”分区",
        "The usage section could not be read": "无法读取“使用与效果”分区",
        "Tokens": "令牌",
        "Tokens and lines are estimates, calls and facts are measured.":
            "令牌与行数为估算，调用与事实为实测。",
        "calls, findings, duration": "调用、命中、耗时",
        "click a row to see the details": "点击行可查看详情",
        "registered connections and their agents": "已注册的连接及其代理",
        # --- 数据库大小与设置 -------------------------------------------------
        "Distribution of volume": "容量分布",
        "{count} largest developments, MB": "最大的 {count} 个项目，MB",
        "{count} databases": "数据库：{count}",
        "all databases of all developments": "所有项目的全部数据库",
        "journal": "日志",
        "No database matches the filter": "没有数据库符合筛选条件",
        "By development": "按项目",
        # --- 模式、访问、数据库 -----------------------------------------------
        "the result of the last probe for each transport":
            "每种传输方式的最近一次探测结果",
        "Sign in with the administrator password or paste the session token "
        "printed when the panel started.":
            "请使用管理员密码登录，或粘贴面板启动时打印的会话令牌。",
        "Write actions are unavailable": "写操作当前不可用",
        "Sign in to continue": "请登录后继续",
        "administrator password, tokens, calls by access and the log":
            "管理员密码、令牌、按访问的调用与日志",
        "All channels": "全部通道",
        "All results": "全部结果",
        "operation, name or reason…": "操作、名称或原因…",
        "channel, operation, sign-in method and the reason for a refusal":
            "通道、操作、登录方式与拒绝原因",
        "identical “access + result” pairs are collapsed; the repeat count is in the row":
            "相同的「访问 + 结果」组合已合并，重复次数显示在行内",
        "The value of a new token is shown once and is not stored by the panel":
            "新令牌的值只显示一次，面板不会保存它",
        "environment variables take precedence over the configuration file":
            "环境变量优先于配置文件",
        "they limit the amount of collected samples": "限制采集的示例数量",
        "the actual content of the structure index": "结构索引的实际内容",
        "Structure fingerprint": "结构指纹",
        "Values of columns recognised as personal data or secrets are NOT "
        "collected at all":
            "被识别为个人数据或机密的数据列，其取值完全不予采集",
        "Write and schema-change operations are not supported: read only.":
            "不支持写入与结构变更操作：仅可读取。",
        "RAGRAF indexes the STRUCTURE of databases: databases, tables, columns, "
        "types, indexes, views and relations.":
            "RAGRAF 只索引数据库的结构：数据库、表、列、类型、索引、视图和关系。",
    },
}


def catalogue(lang: str) -> dict[str, str]:
    """Словарь переводов для языка плюс служебная таблица `_server`.

    `_server` переводит текст, пришедший от модулей, которые пишут по-русски
    (`settings.py`, `probes.py`, `compute.py` и другие). Значения у каждого
    языка свои: для английского это английский ключ, для русского — сам русский
    текст, для китайского — китайский перевод. Без этой разницы смена языка на
    русский превращала бы русские ответы сервера в английские.

    Подсказки к сбоям — особый случай: их ключом служит русский текст, поэтому
    английский перевод берётся из `HINT_TEXTS_EN`, а не из словаря языка.
    """
    code = normalize(lang)
    table = dict(TRANSLATIONS.get(code, {}))
    server_map: dict[str, str] = {}
    for raw, key in SERVER_TEXT.items():
        if code == DEFAULT_LANGUAGE:
            server_map[raw] = HINT_TEXTS_EN.get(raw, key)
        else:
            server_map[raw] = table.get(raw, table.get(key, key))
    table["_server"] = server_map
    return table


def all_catalogues() -> dict[str, dict[str, str]]:
    return {lang: catalogue(lang) for lang in LANGUAGES}


def normalize(lang: str | None) -> str:
    """Привести запрошенный язык к поддерживаемому. Неизвестный — язык по умолчанию."""
    tag = str(lang or "").strip().lower().replace("_", "-")
    if not tag:
        return DEFAULT_LANGUAGE
    if tag in LANGUAGES:
        return tag
    base = tag.split("-", 1)[0]
    return base if base in LANGUAGES else DEFAULT_LANGUAGE


def pick_language(header: str | None) -> str:
    """Выбрать язык по заголовку `Accept-Language`.

    Разбирается без внешних библиотек: строка вида `ru-RU,ru;q=0.9,en;q=0.8`.
    Учитывается вес (`q`), записи с `q=0` пропускаются как «недопустимые».
    """
    raw = str(header or "")
    if not raw:
        return DEFAULT_LANGUAGE
    best = ""
    best_q = -1.0
    for part in raw.split(","):
        piece = part.strip()
        if not piece:
            continue
        tag, _, params = piece.partition(";")
        quality = 1.0
        for param in params.split(";"):
            key, _, value = param.partition("=")
            if key.strip().lower() == "q":
                try:
                    quality = float(value.strip())
                except ValueError:
                    quality = 0.0
        if quality <= 0:
            continue
        base = tag.strip().lower().replace("_", "-").split("-")[0]
        if base not in LANGUAGES:
            continue
        if quality > best_q:
            best, best_q = base, quality
    return best or DEFAULT_LANGUAGE


def translate(text: str, lang: str) -> str:
    """Перевести строку. Нет перевода — вернуть исходный текст (английский)."""
    return TRANSLATIONS.get(normalize(lang), {}).get(text, text)
