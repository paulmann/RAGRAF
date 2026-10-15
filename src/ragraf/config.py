"""Конфигурация корпуса (ФТ-37).

Читает config/project.yaml, валидирует и даёт явные значения по умолчанию.
Конфиг влияет на: отбор файлов (include/exclude/secrets_exclude), языки,
секции эмбеддингов и чанкинга, политику ревизий.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import util

DEFAULT_EXCLUDES = [
    # --- история версий и зависимости ---
    "**/.git/**",
    "**/node_modules/**",
    "**/vendor/**",
    "**/bower_components/**",
    "**/.nuget/**",
    "**/packages/**",
    # --- служебные каталоги RAGRAF и агента ---
    # `.ragraf` — индекс, ревизии и история. Обязателен: без него индексация
    # читала бы собственные базы и меняла свой же вход (индекс попал бы в себя).
    "**/.ragraf/**",
    "**/.zwork/**",
    "**/.agents/**",
    "**/.dev_agent/**",
    "**/.pi/**",
    # --- кеши и окружения ---
    # Назначение этих каталогов однозначно: внутри нет исходников проекта.
    # Проверено на пяти корпусах: при внесении в список выпадает 0 файлов
    # (tools/measure_safe_excludes.py). Внесены потому, что раньше они
    # исключались ТОЛЬКО при создании корпуса командой init — корпус,
    # подключённый вручную, начинал индексировать кеши и окружения.
    "**/__pycache__/**",
    "**/.pytest_cache/**",
    "**/.mypy_cache/**",
    "**/.venv/**",
    "**/venv/**",
    "**/.idea/**",
    "**/.vscode/**",
    "**/coverage/**",
    "**/TestResults/**",
    # --- сборка готовых артефактов ---
    # `var`, `dist`, `build`, `target` — выход сборки и временные данные.
    "**/var/**",
    "**/dist/**",
    "**/build/**",
    "**/target/**",
    # --- резервные копии и минифицированные файлы ---
    "**/*_bak*",
    "**/*backup*/**",
    "**/*.min.js",
    "**/*.min.css",
]

# Каталоги, которые НЕ вносятся в список выше, хотя по имени кажутся
# служебными. Причина одна: среди них есть каталоги с ТЕМ ЖЕ именем, но
# с рабочим кодом проекта внутри — и шаблон исключения их не различает.
#
#   bin     — revoap.com: 6 рабочих PHP-скриптов (cache-purge.php,
#             revoap-worker.php и др.); Clean_BOM_Senior: bom.js,
#             postinstall.js. В RdpMON тот же `bin` — сборка .NET.
#   cache   — revoap.com: 15 файлов кеша ответов модели. Исключение
#             безвредно, но каталог не в базовом списке, чтобы случайно
#             не задеть другой проект с рабочим кодом в `cache`.
#   obj     — RdpMON: 168 сгенерированных .cs. В индексе это шум (файлы
#             вида AssemblyInfo.cs), но исключать глобально небезопасно:
#             в другом проекте имя может означать рабочее.
#   publish — RdpMON: 406 файлов сборки, среди них .html/.css/.js.
#
# Такие каталоги исключаются в конфиге КОНКРЕТНОГО корпуса (в RdpMON это
# сделано) — там видно назначение, и ошибка не задевает другие проекты.
#
# ВАЖНО про механизм: сопоставление шаблонов РЕГИСТРОЗАВИСИМО (проверено
# вызовом, tools/check_case_sensitivity.py). Поэтому `**/cache/**` не ловит
# `src/Cache/*.php`, а `**/bin/**` ловит `bin/cache-purge.php`. Это
# спасает рабочий код в `src/Cache` и `src/Logs`, но не спасает `bin`.
NON_DEFAULT_EXCLUDES = ("bin", "cache", "obj", "publish", "logs",
                        "storage", "uploads", "tmp", "temp")

DEFAULT_SECRETS = [
    "**/config/*.local.php",
    "**/*.local.php",
    "**/.env",
    "**/.env.*",
    "**/*.key",
    "**/*.pem",
    "**/auth.json",
    "**/credentials*",
]

LANG_BY_EXT = {
    ".php": "php",
    ".phtml": "php",
    ".inc": "php",
    ".md": "markdown",
    # SQL. Важен не сам по себе, а как источник миграций: изменения схемы
    # в проектах идут только файлами миграций, и по ним задают вопросы.
    # Грамматики SQL у пакета нет, поэтому — текстовые чанки.
    ".sql": "sql",
    # Разметка и стили подключаются как отдельные языки. Причина: в этих
    # корпусах значительная часть интерфейса живёт вне PHP, и без их разбора
    # из индекса выпадает целый слой — классы оформления и их использование
    # в разметке. Именно так были найдены два реальных дефекта: 9 классов
    # страницы входа и 32 класса подсистемы agreements использовались в
    # разметке, но НИ ОДНОГО правила для них в стилях не существовало
    # (страницы при этом работали и выглядели «как-то»).
    ".css": "css",
    ".htm": "html",
    ".html": "html",
    # JavaScript. Добавлен после разбора проекта MD.Viewer: там 4 файла .js,
    # и без их учёта из индекса выпадает весь слой поведения интерфейса.
    # В пилоте revoap.com разметка собирается JS-шаблонами — без JS имена
    # классов выглядели неиспользуемыми (383 из 474).
    #
    # Раньше JavaScript шёл текстом: считалось, что разборщика нет, а для
    # поиска имён классов хватит чанков. Это оказалось неверно. Замер на
    # реальных файлах (tools/check_parse_real.py) показал, что грамматика
    # JavaScript в установленном пакете есть и разбирает 12 из 12 файлов
    # без ошибок. Поэтому JS переведён на разбор деревом (см. generic.py).
    ".js": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".jsx": "javascript",
    # --- Языки, добавленные после сканирования стека разработки ---
    #
    # Сканирование C:\AutoClaw и C:\1st_RdpMON\Service (tools/scan_scripts.py)
    # показало, что вне индекса оставались целые проекты:
    #   Perl 1235 файлов, Python 1337, C# 1217 (в 1st_RdpMON/Service —
    #   src 775 + tests 370), shell 212, PowerShell 96, TypeScript 146,
    #   C/C++ 384.
    #
    # Про C#: в C:\1st_RdpMON\Service 1217 файлов .cs — это основной язык
    # проекта, и он не индексировался вовсе. Статические операции RAGRAF
    # (symbol, callers, code) по нему не работали — то есть RAGRAF был
    # бесполезен ровно там, где нужен больше всего.
    ".cs": "csharp",
    ".csproj": "xml",
    ".sln": "text",
    ".ps1": "powershell",
    ".psm1": "powershell",
    ".psd1": "powershell",
    ".sh": "bash",
    ".bash": "bash",
    ".zsh": "bash",
    ".pl": "perl",
    ".pm": "perl",
    ".t": "perl",
    ".py": "python",
    ".pyi": "python",
    ".ts": "typescript",
    ".mts": "typescript",
    ".tsx": "typescript",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    ".java": "java",
    ".go": "go",
    # Rust: файлов в просканированных каталогах не найдено, профиля грамматики
    # тоже нет. Оставлен в карте, чтобы такие файлы попадали хотя бы в поиск
    # текстом, а не исчезали из индекса молча.
    ".rs": "rust",
    ".rb": "ruby",
    ".lua": "lua",
    # --- Конфигурация и данные ---
    # Раньше эти типы не индексировались, и по настройкам проекта поиск
    # ничего не находил. Между тем именно в них лежат имена параметров
    # (например joern_server, gpu_min_texts), по которым задают вопросы.
    ".yml": "yaml",
    ".yaml": "yaml",
    ".json": "json",
    ".toml": "toml",
    ".ini": "ini",
    ".cfg": "ini",
    ".conf": "ini",
    ".xml": "xml",
    ".manifest": "xml",
    ".props": "xml",
    ".markdown": "markdown",
    ".rst": "rst",
    ".txt": "text",
    # Batch и SQL остаются текстовыми: их грамматики на реальных файлах дали
    # ошибки разбора (batch — 7 из 7, SQL — 12 из 12). Разбор деревом по ним
    # дал бы мусорные узлы, поэтому они идут текстовыми чанками, но попадают
    # в поиск — а раньше не попадали вовсе.
    ".bat": "batch",
    ".cmd": "batch",
}

# Языки, для которых есть разбор деревом.
#
# Список хранит ИМЕНА ЯЗЫКОВ, а не расширения, и расширения выводятся из него
# автоматически ниже. Это принципиально: первый вариант хранил здесь второй
# ручной список расширений, и тест сразу поймал расхождение — семь расширений
# (.psd1, .zsh, .t, .pyi, .mts, .cc, .cxx) были в карте, но забыты здесь.
# Два ручных списка одного и того же всегда расходятся — это лишь вопрос времени.
#
# ВАЖНО: этот набор обязан совпадать с PROFILES в extract/generic.py плюс
# три языка со своими извлекателями. Совпадение проверяется тестом
# tests/test_lang_routing.py, а не поддерживается на честном слове.
TREE_LANGS = frozenset({
    "php", "css", "html",
    "javascript", "typescript", "python", "csharp", "perl", "bash",
    "powershell", "ruby", "lua", "go", "java", "c", "cpp",
})

# Расширения, по которым будет разбор деревом. Выводятся из карты выше:
# ошибка в одном месте больше невозможна.
PARSE_LANGS_EXT: dict[str, str] = {
    ext: lang for ext, lang in LANG_BY_EXT.items() if lang in TREE_LANGS
}


def is_parse_lang(path: str) -> bool:
    """Можно ли разбирать файл деревом. Решение принимается по расширению."""
    low = path.lower()
    return any(low.endswith(ext) for ext in PARSE_LANGS_EXT)


class ConfigError(Exception):
    """Ошибка конфигурации: неверное значение или отсутствующий файл."""


@dataclass
class DbConfig:
    enabled: bool = False
    # Реквизиты НЕ хранятся в конфиге: значения берутся из окружения (env-переменные).
    # Имена env-переменных — данные, значения — секрет (НФТ-3).
    host_env: str = "RAGRAF_DB_HOST"
    port_env: str = "RAGRAF_DB_PORT"
    user_env: str = "RAGRAF_DB_USER"
    password_env: str = "RAGRAF_DB_PASSWORD"
    database_env: str = "RAGRAF_DB_NAME"
    default_host: str = "127.0.0.1"
    default_port: int = 3306
    schema_sources: list[str] = field(default_factory=lambda: ["information_schema"])


@dataclass
class EmbeddingConfig:
    model: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    dim: int = 384
    batch_size: int = 64
    # --- вычислитель для кодирования векторов ---
    # Замерено на RTX 5080: само кодирование на видеокарте — 0,25 с на 717 текстов
    # против 17,9 с на процессоре (70 раз быстрее).
    #
    # НО есть разовая цена за запуск процесса — 15 с, и она больше самого
    # кодирования на мелких корпусах. Разложено замером:
    #     инициализация CUDA (маленький тензор)  2,1 с
    #     первый проход модели на видеокарте    13,1 с  ← основная часть
    #     импорт библиотек                       6,4 с
    #     чтение весов модели                     4,1 с
    # Ни отключение подбора алгоритмов, ни прогрев тензором эту цену не снимают:
    # это подготовка ядер под архитектуру sm_120, и она платится один раз на процесс.
    #
    # Отсюда правило: видеокарта выгодна, когда работы МНОГО. Порог окупаемости
    # считается по числу текстов, которые надо закодировать в этом запуске.
    #
    # ВАЖНО, проверено замером: векторы ONNX и torch СОПОСТАВИМЫ, близость
    # 0,999997. Поэтому вычислитель можно переключать на существующем индексе,
    # без перекодирования всех векторов.
    use_gpu: bool = True
    device: str = "auto"
    # Партия для кодирования на видеокарте: больше партия — меньше накладных
    # расходов на запуск ядер и выше загрузка.
    gpu_batch_size: int = 128
    # Порог окупаемости: при меньшем числе текстов считается на процессоре.
    # ОБОСНОВАНИЕ ПО РЕАЛЬНОЙ ИНДЕКСАЦИИ, а не по изолированному замеру.
    #
    # Первая версия порога (1000) была получена на ПРОГРЕТОЙ модели и оказалась
    # неверной: при реальном прогоне корпуса видеокарта проиграла 6,5 с
    # (стадия векторов 27,4 с против 20,8 с). Потому что в одном процессе
    # платится вся разовая подготовка:
    #     импорт torch                    1,8 с
    #     импорт sentence-transformers    4,7 с
    #     чтение весов модели             4,1 с
    #     первый проход модели на карте  15,1 с
    #     ─────────────────────────────────────
    #     итого                         ~26 с
    #
    # Скорости самого кодирования (замерены отдельно):
    #     на процессоре 17,9 мс на текст
    #     на видеокарте 0,35 мс на текст
    # Точка равенства: 1451 текст. Порог берётся с запасом 1800, чтобы на границе
    # не проиграть из-за разброса замеров.
    #
    # Следствия, которые важно понимать:
    #   * корпус z.batteryservice.ru (около 1078 новых векторов при ПОЛНОЙ
    #     индексации) остаётся на процессоре — и это правильно;
    #   * корпус revoap.com (около 4000 векторов) выигрывает 45 с;
    #   * при обычной правке файла новых текстов единицы, поэтому быстрая
    #     переиндексация всегда идёт на процессоре.
    gpu_min_texts: int = 1800


# Опорная стоимость полного пересбора, из которой выведены базовые пороги
# 5 / 20 / 15 минут (замерено на корпусе z.batteryservice.ru, 124 файла).
REFERENCE_SECONDS = 20.81

# --- автоподбор порогов накопления (пункт 1.3.5) ---


def measured_full_seconds(cfg: ProjectConfig) -> float:
    """Фактическая стоимость полного пересбора корпуса, в секундах.

    Источник — журнал прогонов реестра: там лежат времена всех индексаций с их
    триггером. Это честнее расчёта «по числу файлов»: стоимость зависит не
    только от числа файлов, но и от доли PHP (разбор и граф свойств кода дороже),
    размера файлов и включён ли построение графа свойств вообще.

    Берётся МЕДИАНА полных прогонов, а не среднее: одноразовый пересбор с
    очисткой кеша (например, первый на холодной машине) иначе тянул бы
    оценку вверх.

    Возвращается 0.0, если данных нет: вызывающий код в этом случае
    оставляет базовые пороги — выдумывать стоимость нельзя.
    """
    samples: list[float] = []
    try:
        from .catalog import Catalog

        entry = Catalog().get(cfg.project)
        for run in (getattr(entry, "runs", []) or []) if entry else []:
            trigger = str(getattr(run, "trigger", "") or "")
            seconds = float(getattr(run, "seconds", 0.0) or 0.0)
            # Быстрые прогоны (без графа свойств кода) в оценку не берутся:
            # они дешевле полного в 3,7 раза и занизили бы оценку.
            if seconds <= 0 or "fast" in trigger:
                continue
            if str(getattr(run, "capability", "") or "") == "degraded":
                continue
            samples.append(seconds)
    except Exception:
        return 0.0

    if not samples:
        return 0.0
    samples.sort()
    mid = len(samples) // 2
    if len(samples) % 2:
        return samples[mid]
    return (samples[mid - 1] + samples[mid]) / 2.0


def auto_thresholds(
    cfg: ProjectConfig, measured: float | None = None
) -> tuple[int, int, int, dict]:
    """Подобрать пороги накопления по измеренной стоимости пересбора.

    Возвращает (порог быстрой, порог полного, интервал в минутах, пояснение).

    Почему масштаб — квадратный корень, а не линейная зависимость. Порог — это
    компромисс между частотой пересбора и свежестью фактов. При линейном масштабе
    корпус в 10 раз тяжелее получал бы порог в 10 раз больше: пересбор стал бы
    настолько редким, что устаревшие факты перестали бы замечаться. Корень даёт
    рост в 3,2 раза — цена пересбора растёт, но и частота падает умеренно.

    Интервал тоже масштабируется: если стоимость пересбора больше опорной,
    возраст, после которого индекс считается устаревшим, увеличивается — не
    честно требовать обновления каждые 15 минут от корпуса, который обновляется
    3 минуты.
    """
    base_fast = max(1, int(cfg.index.reindex_after_changes))
    base_full = max(2, int(cfg.index.full_reindex_after_changes))
    base_interval = max(1, int(cfg.index.reindex_interval_minutes))
    info: dict = {
        "applied": False,
        "measured_seconds": 0.0,
        "reference_seconds": REFERENCE_SECONDS,
        "factor": 1.0,
        "base": {"fast": base_fast, "full": base_full, "interval": base_interval},
    }

    if not cfg.index.auto_thresholds:
        info["reason"] = "автоподбор выключен в конфигурации"
        return base_fast, base_full, base_interval, info

    value = measured if measured is not None else measured_full_seconds(cfg)
    info["measured_seconds"] = round(float(value or 0.0), 2)
    if not value or value <= 0:
        info["reason"] = (
            "нет замеров полной индексации в реестре: остаются базовые пороги"
        )
        return base_fast, base_full, base_interval, info

    import math

    factor = math.sqrt(float(value) / REFERENCE_SECONDS)
    info["factor"] = round(factor, 3)

    fast = int(round(base_fast * factor))
    full = int(round(base_full * factor))
    interval = int(round(base_interval * factor))

    # Границы: защита от выброса в замерах (например, разовая индексация
    # на загруженной машине не должна отключить переиндексацию навсегда).
    fast = max(int(cfg.index.auto_min_fast), min(int(cfg.index.auto_max_fast), fast))
    full = max(int(cfg.index.auto_min_full), min(int(cfg.index.auto_max_full), full))
    # Согласованность: полный порог обязан быть больше быстрого.
    if full <= fast:
        full = fast + 1

    # Чем дороже пересбор, тем больше допустимый возраст индекса. Нижняя
    # граница — базовое значение из конфига: ускорять обновление на дешёвых
    # корпусах смысла нет.
    interval = max(base_interval, min(base_interval * 8, interval))

    info["applied"] = (fast, full, interval) != (base_fast, base_full, base_interval)
    info["reason"] = (
        f"по замеру {value:.1f} с: множитель {factor:.2f} к опорным "
        f"{REFERENCE_SECONDS:.2f} с"
    )
    return fast, full, interval, info


@dataclass
class ChunkingConfig:
    by_symbol: bool = True
    max_chunk_lines: int = 120
    min_chunk_lines: int = 3
    include_comments: bool = False  # текст комментариев идёт отдельным слоем (ФТ-9)


@dataclass
class IndexConfig:
    retain_revs: int = 3
    batch_size: int = 2000
    joern_enabled: bool = True

    # --- порог накопления правок (авто-реиндексация) ---
    # Замерено на корпусе z.batteryservice.ru (124 файла):
    #   * сканирование корпуса (проверка суммы)   — 0,16 с;
    #   * индексация без CPG (AST+схема+лексика)  — 5,6 с;
    #   * индексация с CPG (граф свойств кода)    — 20,8 с.
    # Отсюда логика: мелкие правки в ходе сессии перекрываются быстрым режимом,
    # а полный пересбор оправдан реже — когда правок накопилось много или когда
    # изменились объявления (структура вызовов), которые CPG и уточняет.
    #
    # Правила выбраны из этих чисел, а не на глаз:
    #   * 5 правок — порог быстрой реиндексации: после пяти правок граф AST
    #     отстаёт заметно, а 5,6 с незаметны на фоне работы агента;
    #   * 20 правок — порог полного пересбора: к этому моменту стоит пересобрать
    #     и CPG, чтобы межпроцедурные связи не оставались устаревшими;
    #   * 15 минут — временной предел: даже при редких правках индекс не должен
    #     отставать дольше, чем на четверть часа.
    #
    # Значения выше — база для корпуса в 124 файла. Для большого корпуса они
    # пересчитываются автоподбором (см. `auto_thresholds`).
    reindex_after_changes: int = 5
    full_reindex_after_changes: int = 20
    reindex_interval_minutes: int = 15
    # Накопление измеряется окном от последней индексации: как только счётчик
    # в окне достигает порога, выполняется переиндексация нужной глубины.
    change_counter: bool = True

    # --- бюджет и предел объёма первой индексации (P0-2) ---
    # Зачем. `ensure` вызывается первым шагом работы агента и обязан ОТВЕЧАТЬ.
    # Замер 2026-10-14 на корпусе BWO (5601 файл; дерево 38 675 файлов / 2,3 ГБ,
    # из них 27 254 бинарные выгрузки .FBO в uploads/): индексация с Joern и GPU
    # не завершилась за ~11 минут, вызов ушёл в таймаут, и агент получил не факт,
    # а молчание. Поэтому у прогона есть предел по времени и по числу файлов:
    #   * `budget_seconds` — сколько секунд разрешено потратить на один прогон;
    #     при исчерпании прогон прерывается ДО закрепления ревизии (прежний индекс
    #     остаётся рабочим), а ответ говорит, что именно не успело собраться;
    #   * `max_files` — сколько файлов корпуса допустимо индексировать; больше —
    #     отказ с числами и подсказкой про exclude, а не многоминутная работа.
    budget_seconds: float = 120.0
    max_files: int = 20000

    # --- автоподбор порогов по замерам (пункт 1.3.5) ---
    # Зачем. Пороги 5/20/15 подобраны для корпуса в 124 файла. На корпусе в 5000
    # файлов полный пересбор займёт минуты, и те же числа заставят платить эту
    # цену слишком часто. Критерий должен зависеть от ФАКТИЧЕСКОЙ стоимости
    # индексации конкретного корпуса, а не от числа файлов в голове разработчика.
    #
    # Как считается. Берётся опорная стоимость полного пересбора (REFERENCE_SECONDS)
    # и пороги масштабируются как квадратный корень из отношения:
    #     множитель = sqrt(измеренная / опорная)
    # Квадратный корень, а не линейная зависимость: порог — это компромисс между
    # частотой пересбора и свежестью фактов, а ущерб от устаревших фактов растёт
    # медленнее, чем падает частота пересбора. Линейная зависимость давала бы
    # слишком редкий пересбор на больших корпусах.
    #
    # Примеры (опора 20,81 с):
    #     5 с  → ×0,49 → пороги  3 / 10
    #    20,81 с → ×1,00 → пороги  5 / 20   (совпадает с замеренным оптимумом)
    #    83 с  → ×2,00 → пороги 10 / 40
    #   187 с  → ×3,00 → пороги 15 / 60
    auto_thresholds: bool = True
    # --- сервер Joern (долгоживущий процесс) ---
    # Замерено на этой машине: индексация запускает Joern ДВАЖДЫ (построение
    # графа и экспорт), и каждый запуск платит 5,53 с за загрузку классов Joern
    # (чистый старт JVM — всего 64 мс). Итого около 11 с накладных расходов
    # на индексацию. Экспорт готового графа: 6,03 с, из них 5,53 с — старт
    # и только 0,50 с — работа, то есть 92% времени уходит на запуск.
    #
    # Серверный режим убирает эти накладные: сервер поднимается один раз,
    # а запросы к нему занимают 22–38 мс вместо 5530 мс. Построение графа
    # через сервер заняло 2,11 с вместо 5,53 с только на старт.
    #
    # Настройка включена по умолчанию: выигрыш подтверждён замерами,
    # а при недоступности сервера код автоматически возвращается к запуску
    # отдельным процессом.
    joern_server: bool = True
    # Порт сервера. Диапазон 8090–8110 на этой машине занят PHP-серверами
    # стендов, поэтому берётся 9500 с поиском свободного порта вверх.
    joern_server_port: int = 9500
    # Объём памяти JVM сервера: на крупном графе нужно больше.
    joern_heap_gb: int = 8
    # --- анализ графа на видеокарте ---
    # Замерено: на графе 1059 вершин процессор быстрее, на 5520 — быстрее уже
    # видеокарта (PageRank: CPU 7,36 мс против GPU 1000 мс на первом вызове,
    # потому что в него входит подготовка карты, и 5,87 мс против 6,44 мс после
    # прогрева). Решение владельца (9 октября 2026): считать на карте, порог
    # окупаемости — 5000 вершин.
    #
    # ВАЖНО И ЧЕСТНО: исполнителя у этой настройки в библиотеке пока нет.
    # Графовых алгоритмов (PageRank, обходы) в `src/ragraf` не реализовано:
    # замеры и код живут только в проверочных скриптах каталога инструментов
    # (`tools/analyze_gpu_graph.py`, `tools/check_graph_gpu_alt.py`). Поэтому
    # включение меняет только значение: чтобы карта действительно считала граф,
    # нужна реализация, а не переключатель. Это записано в AGENTS.md.
    graph_gpu: bool = True
    # Порог окупаемости для графового анализа: при меньшем числе вершин
    # считается на процессоре. Замерено: на 5000 вершинах результат на границе,
    # дальше карта выигрывает. Решение владельца — 5000.
    graph_gpu_min_nodes: int = 5000
    # Границы автоподбора: защита от абсурдных значений при выбросе в замерах.
    auto_min_fast: int = 2
    auto_max_fast: int = 40
    auto_min_full: int = 6
    auto_max_full: int = 150
    # Если стоимость пересбора превышает это значение, интервал обновления
    # поднимается автоматически: иначе на большом корпусе полный пересбор
    # каждые 15 минут съедал бы заметную долю времени работы.
    auto_interval_floor_factor: int = 10
    # Схлопывание байт-идентичных копий при индексации.
    #
    # Зачем. Замер по C:\AutoClaw: 5559 документов дали 513 групп копий
    # и 3856 лишних файлов (44,7 МБ вместо 2,3 МБ полезного текста).
    # Худший случай — 104 копии одного ADR, размноженные вложением каталогов
    # (`revoap/www/www/www/www/...`). В подключённых корпусах тоже есть
    # лишнее: revoap 125 копий, z 11.
    #
    # Что это меняет. Каждая копия даёт свои чанки и свои векторы, то есть
    # поиск тратит время на обработку одного текста десятки раз, а выдача
    # заполняется его копиями. Сами файлы НЕ удаляются: они остаются на диске,
    # и это важно — инструмент только перестаёт считать их отдельными
    # сущностями индекса.
    #
    # Почему выключено по умолчанию. Схлопывание меняет СОСТАВ индекса: после
    # его включения сумма корпуса изменится, и сторож свежести запустит
    # переиндексацию. Это осознанное действие, а не побочный эффект обновления.
    dedupe_enabled: bool = False
    # Что именно схлопывать. `docs` — только документация (md, txt, rst):
    # самый безопасный режим, потому что копия документа не меняет поведения
    # кода. `all` — все файлы: копии кода тоже удваивают узел и его рёбра.
    dedupe_scope: str = "docs"
    # Что делать с группой копий: `keep_one` — оставить одну (выбор по правилу
    # ниже), `skip_all` — не индексировать ни одну.
    dedupe_mode: str = "keep_one"
    # Правило выбора «главной» копии. Замерено на 513 реальных группах
    # (tools/measure_dedupe_rules.py): сколько групп получило мусорный путь
    # при выборе:
    #   без мусорных сегментов — 38  ← выбран
    #   самый короткий путь     — 76
    #   самый свежий            — 65
    #   самый длинный           — 242
    dedupe_keep: str = "cleanest"
    # Записывать ли в ответ операции, что именно схлопнуто. Нужно для
    # проверяемости: иначе схлопывание невидимо и его нельзя подтвердить.
    dedupe_report: bool = True


@dataclass
class ProjectConfig:
    project: str
    root: Path
    languages: list[str]
    include: list[str]
    exclude: list[str]
    secrets_exclude: list[str]
    db: DbConfig
    embedding: EmbeddingConfig
    chunking: ChunkingConfig
    index: IndexConfig
    config_path: Path
    raw: dict[str, Any] = field(default_factory=dict)

    # ---------------- производные ----------------

    @property
    def ragraf_dir(self) -> Path:
        return self.root / ".ragraf"

    @property
    def index_dir(self) -> Path:
        return self.ragraf_dir / "index"

    @property
    def graph_db(self) -> Path:
        return self.index_dir / "graph" / "graph.sqlite"

    @property
    def lexvec_db(self) -> Path:
        return self.index_dir / "lex" / "lexvec.sqlite"

    @property
    def control_db(self) -> Path:
        return self.ragraf_dir / "control.sqlite"

    @property
    def rev_file(self) -> Path:
        return self.index_dir / "rev.json"

    @property
    def drift_dir(self) -> Path:
        return self.ragraf_dir / "drift"

    @property
    def ledger_md(self) -> Path:
        return self.drift_dir / "LEDGER.md"

    @property
    def archive_dir(self) -> Path:
        return self.drift_dir / "archive"

    @property
    def logs_dir(self) -> Path:
        return self.ragraf_dir / "logs"

    @property
    def config_hash(self) -> str:
        """Хэш конфигурации: изменение конфига делает ревизию несопоставимой."""
        return util.short_hash(
            util.dumps(
                {
                    "languages": self.languages,
                    "include": self.include,
                    "exclude": self.exclude,
                    "secrets_exclude": self.secrets_exclude,
                    "chunking": {
                        "by_symbol": self.chunking.by_symbol,
                        "max_chunk_lines": self.chunking.max_chunk_lines,
                    },
                    "embedding": {"model": self.embedding.model, "dim": self.embedding.dim},
                    "db_sources": self.db.schema_sources,
                    "root": util.to_posix(self.root),
                }
            ),
            length=16,
        )

    def language_of(self, rel_path: str) -> str | None:
        return LANG_BY_EXT.get(Path(rel_path).suffix.lower())

    @staticmethod
    def language_of_static(rel_path: str) -> str | None:
        """Язык по расширению без объекта конфига.

        Нужен там, где конфига под рукой нет (например, при построении текстовых
        чанков): раньше язык угадывался по расширению прямо в индексаторе и для
        всего непонятного подставлялся «sql» — из-за этого непонятные файлы
        помечались миграциями.
        """
        return LANG_BY_EXT.get(Path(rel_path).suffix.lower())

    def ensure_dirs(self) -> None:
        for path in (
            self.index_dir / "graph",
            self.index_dir / "lex",
            self.drift_dir / "archive",
            self.logs_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)


def _as_list(value: Any, default: list[str]) -> list[str]:
    if value is None:
        return list(default)
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(v) for v in value]
    raise ConfigError(f"ожидался список или строка, получено: {type(value).__name__}")


def load(config_path: str | Path) -> ProjectConfig:
    """Загрузить и провалидировать конфигурацию корпуса."""
    path = Path(config_path).resolve()
    if not path.exists():
        raise ConfigError(f"конфигурация не найдена: {path}")

    try:
        import yaml
    except ImportError as exc:  # pragma: no cover
        raise ConfigError("не установлен PyYAML (см. requirements.txt)") from exc

    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        raise ConfigError(f"не удалось разобрать YAML {path}: {exc}") from exc

    if not isinstance(data, dict):
        raise ConfigError("корень конфигурации должен быть отображением (mapping)")

    project = str(data.get("project") or "").strip()
    if not project:
        raise ConfigError("не задано поле 'project'")

    root_raw = data.get("root") or "."
    root = Path(str(root_raw)).expanduser()
    if not root.is_absolute():
        root = (path.parent / root).resolve()
    if not root.exists():
        raise ConfigError(f"каталог корпуса не существует: {root}")

    languages = [str(x).lower() for x in _as_list(data.get("languages"), ["php"])]
    include = _as_list(data.get("include"), ["**/*.php"])
    exclude = list(DEFAULT_EXCLUDES) + _as_list(data.get("exclude"), [])
    secrets = list(DEFAULT_SECRETS) + _as_list(data.get("secrets_exclude"), [])

    db_raw = data.get("db") or {}
    db = DbConfig(
        enabled=bool(db_raw.get("enabled", False)),
        host_env=str(db_raw.get("host_env", "RAGRAF_DB_HOST")),
        port_env=str(db_raw.get("port_env", "RAGRAF_DB_PORT")),
        user_env=str(db_raw.get("user_env", "RAGRAF_DB_USER")),
        password_env=str(db_raw.get("password_env", "RAGRAF_DB_PASSWORD")),
        database_env=str(db_raw.get("database_env", "RAGRAF_DB_NAME")),
        default_host=str(db_raw.get("default_host", "127.0.0.1")),
        default_port=int(db_raw.get("default_port", 3306)),
        schema_sources=_as_list(db_raw.get("schema_sources"), ["information_schema"]),
    )

    emb_raw = data.get("embedding") or {}
    embedding = EmbeddingConfig(
        model=str(emb_raw.get("model", EmbeddingConfig.model)),
        dim=int(emb_raw.get("dim", EmbeddingConfig.dim)),
        batch_size=int(emb_raw.get("batch_size", EmbeddingConfig.batch_size)),
        use_gpu=bool(emb_raw.get("use_gpu", True)),
        device=str(emb_raw.get("device", EmbeddingConfig.device)),
        gpu_batch_size=max(1, int(emb_raw.get(
            "gpu_batch_size", EmbeddingConfig.gpu_batch_size))),
        gpu_min_texts=max(1, int(emb_raw.get(
            "gpu_min_texts", EmbeddingConfig.gpu_min_texts))),
    )

    ch_raw = data.get("chunking") or {}
    chunking = ChunkingConfig(
        by_symbol=bool(ch_raw.get("by_symbol", True)),
        max_chunk_lines=int(ch_raw.get("max_chunk_lines", 120)),
        min_chunk_lines=int(ch_raw.get("min_chunk_lines", 3)),
        include_comments=bool(ch_raw.get("include_comments", False)),
    )

    ix_raw = data.get("index") or {}
    index = IndexConfig(
        retain_revs=int(ix_raw.get("retain_revs", 3)),
        batch_size=int(ix_raw.get("batch_size", 2000)),
        joern_enabled=bool(ix_raw.get("joern_enabled", True)),
        # Порог накопления правок: значения читаются из конфига корпуса, потому
        # что стоимость индексации зависит от размера корпуса. Для большого
        # проекта разумны другие пороги, чем для 124 файлов.
        reindex_after_changes=max(1, int(ix_raw.get("reindex_after_changes", 5))),
        full_reindex_after_changes=max(
            2, int(ix_raw.get("full_reindex_after_changes", 20))
        ),
        reindex_interval_minutes=max(1, int(ix_raw.get("reindex_interval_minutes", 15))),
        change_counter=bool(ix_raw.get("change_counter", True)),
        # Бюджет прогона и предел числа файлов: нужны, чтобы `ensure` отвечал
        # всегда, а не уходил в многоминутную индексацию крупного корпуса.
        budget_seconds=max(5.0, float(ix_raw.get("budget_seconds", 120.0))),
        max_files=max(50, int(ix_raw.get("max_files", 20000))),
        # Автоподбор порогов: по умолчанию включён — базовые числа подобраны
        # для корпуса в 124 файла и без пересчёта вредны на больших.
        auto_thresholds=bool(ix_raw.get("auto_thresholds", True)),
        auto_min_fast=max(1, int(ix_raw.get("auto_min_fast", 2))),
        auto_max_fast=max(2, int(ix_raw.get("auto_max_fast", 40))),
        auto_min_full=max(2, int(ix_raw.get("auto_min_full", 6))),
        auto_max_full=max(3, int(ix_raw.get("auto_max_full", 150))),
        auto_interval_floor_factor=max(
            1, int(ix_raw.get("auto_interval_floor_factor", 10))
        ),
        # Схлопывание дублей: по умолчанию ВЫКЛЮЧЕНО, потому что меняет
        # состав индекса и вызывает переиндексацию.
        dedupe_enabled=bool(ix_raw.get("dedupe_enabled", False)),
        dedupe_scope=str(ix_raw.get("dedupe_scope", "docs")).lower(),
        dedupe_mode=str(ix_raw.get("dedupe_mode", "keep_one")).lower(),
        dedupe_keep=str(ix_raw.get("dedupe_keep", "cleanest")).lower(),
        dedupe_report=bool(ix_raw.get("dedupe_report", True)),
        # Сервер Joern: долгоживущий процесс вместо запуска JVM на каждый вызов.
        joern_server=bool(ix_raw.get("joern_server", True)),
        joern_server_port=max(1024, int(ix_raw.get("joern_server_port", 9500))),
        joern_heap_gb=max(1, int(ix_raw.get("joern_heap_gb", 8))),
        # Анализ графа на видеокарте: решение владельца — включено, порог 5000
        # вершин. Отсутствие ключа в конфиге трактуется как пропуск, а не как
        # «выключено»: иначе корпус без явной настройки молча отключал бы карту.
        graph_gpu=bool(ix_raw.get("graph_gpu", True)),
        graph_gpu_min_nodes=max(100, int(ix_raw.get("graph_gpu_min_nodes", 5000))),
    )
    # Порог полного пересбора обязательно больше порога быстрого: иначе
    # быстрый режим недостижим, а это скрытая ошибка настройки.
    if index.full_reindex_after_changes <= index.reindex_after_changes:
        index.full_reindex_after_changes = index.reindex_after_changes + 1

    if embedding.dim != 384:
        raise ConfigError(
            "размерность эмбеддингов в релизе 1 фиксирована (384): "
            "смена модели требует миграции схемы vec0"
        )

    return ProjectConfig(
        project=project,
        root=root,
        languages=languages,
        include=include,
        exclude=exclude,
        secrets_exclude=secrets,
        db=db,
        embedding=embedding,
        chunking=chunking,
        index=index,
        config_path=path,
        raw=data,
    )
