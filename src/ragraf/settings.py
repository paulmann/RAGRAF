"""Настройки RAGRAF: чтение, изменение и хранение параметров работы.

Назначение
    Администратору нужен способ менять параметры RAGRAF без правки файлов
    вручную: пороги авто-реиндексации, состав корпусов, режим Joern. Этот
    модуль — единственная точка изменения настроек, чтобы веб-интерфейс и CLI
    не расходились в поведении.

Два уровня настроек
    1. **Глобальные** — значения по умолчанию для всех разработок. Хранятся
       в `catalog/settings.json` рядом с реестром.
    2. **По разработке** — переопределяют глобальные для конкретного корпуса.
       Лежат в его YAML-конфиге (секция `index`).

Почему точечная правка YAML, а не перезапись
    Конфиги корпусов содержат комментарии с обоснованием (почему пороги такие,
    почему исключён каталог). Полный разбор и обратная запись через YAML-парсер
    **уничтожил бы все комментарии**. Поэтому значения меняются построчно:
    комментарии и структура сохраняются, а новый ключ добавляется в нужную
    секцию с пояснением.

Что НЕ делает
    Не применяет настройки к уже построенным индексам: изменение порогов
    влияет на будущие проверки, а не на текущую ревизию. Это важно понимать,
    и администратор видит это в интерфейсе.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from . import util

# Значения по умолчанию: те же, что в IndexConfig, и по тем же замерам
# (быстрая индексация 5,6 с против полной 20,8 с на корпусе 124 файла).
DEFAULTS: dict[str, Any] = {
    "reindex_after_changes": 5,
    "full_reindex_after_changes": 20,
    "reindex_interval_minutes": 15,
    "change_counter": True,
    "joern_enabled": True,
    "retain_revs": 3,
    "batch_size": 2000,
    # --- вычислитель для кодирования векторов ---
    # Замерено на RTX 5080: на пилотном корпусе (3936 векторов) видеокарта
    # сократила индексацию со 143,7 с до 101,6 с — экономия 42 с.
    "use_gpu": True,
    "gpu_min_texts": 1800,
    "gpu_batch_size": 128,
    # --- сервер Joern (долгоживущий процесс) ---
    # Замерено: каждый запуск Joern платит 5,53 с за загрузку классов,
    # а индексация запускает его дважды — около 11 с накладных.
    # Три прогона подряд: прежний путь 44,72 с, серверный 35,88 с — экономия 8,84 с.
    # При ОДНОМ прогоне сервер медленнее на 3,7 с: старт входит в него.
    "joern_server": True,
    "joern_server_port": 9500,
    "joern_heap_gb": 8,
    # --- анализ графа на видеокарте ---
    # Замерено: на 1059 вершинах процессор быстрее, на 5520 — быстрее карта
    # (PageRank: CPU 7,36 мс против GPU 1000 мс на первом вызове, в который
    # входит подготовка карты, и 5,87 мс против 6,44 мс после прогрева).
    # Решение владельца (9 октября 2026): считать на карте, порог 5000 вершин.
    # ИСПОЛНИТЕЛЯ В БИБЛИОТЕКЕ НЕТ: графовые алгоритмы реализованы только в
    # проверочных скриптах каталога инструментов, поэтому переключатель сам
    # ничего не считает (см. AGENTS.md).
    "graph_gpu": True,
    "graph_gpu_min_nodes": 5000,
    # --- схлопывание байт-идентичных копий ---
    # Замерено на C:\AutoClaw: 5559 документов дали 513 групп копий и 3856
    # лишних файлов (44,7 МБ против 2,3 МБ полезного текста). Худший случай —
    # один ADR в 104 копиях. Файлы на диске НЕ удаляются, они просто не идут
    # в индекс. Включение меняет состав индекса, поэтому по умолчанию выкл.
    "dedupe_enabled": False,
    "dedupe_scope": "docs",
    "dedupe_mode": "keep_one",
    "dedupe_keep": "cleanest",
    "dedupe_report": True,
    "dedupe_report": True,
    # Канал работы RAGRAF, который считается ожидаемым. Сам сервер MCP
    # поднимает клиент, поэтому настройка описывает ОЖИДАНИЕ и проверяется
    # пробой: панель сообщает расхождение с фактом, а не делает вид, что
    # режим всё включает сам. Значения: cli | mcp-stdio | mcp-http | both.
    "ragraf_mode": "cli",
}

# Допустимые границы: защита от опечатки вида «2000 правок» или «0 минут»,
# которая тихо выключила бы авто-реиндексацию.
BOUNDS: dict[str, tuple[int, int]] = {
    "reindex_after_changes": (1, 1000),
    "full_reindex_after_changes": (2, 5000),
    "reindex_interval_minutes": (1, 10080),   # до недели
    "retain_revs": (1, 100),
    "batch_size": (100, 100000),
    # Порог окупаемости видеокарты. Нижняя граница взята из замеров:
    # разовая подготовка карты 26 с, кодирование на ней 0,35 мс на текст
    # против 17,9 мс на процессоре. Ниже 1451 текста карта проигрывает,
    # поэтому меньше 500 ставить бессмысленно, а больше 200000 — вредно
    # (карта не включится никогда).
    "gpu_min_texts": (500, 200000),
    "gpu_batch_size": (16, 1024),
    # Порт сервера: ниже 1024 — служебные порты, их нельзя занимать.
    "joern_server_port": (1024, 65000),
    "joern_heap_gb": (1, 64),
    # Порог окупаемости графового анализа на видеокарте.
    # Замерено: GPU выигрывает от 10 000 вершин, ниже процессор быстрее.
    "graph_gpu_min_nodes": (100, 5000000),
}
# Ключи, которые администратору разрешено менять через интерфейс.
EDITABLE: tuple[str, ...] = (
    "reindex_after_changes",
    "full_reindex_after_changes",
    "reindex_interval_minutes",
    "change_counter",
    "joern_enabled",
    "retain_revs",
    "batch_size",
    "use_gpu",
    "gpu_min_texts",
    "gpu_batch_size",
    # Сервер Joern: долгоживущий процесс вместо запуска JVM на каждый вызов.
    "joern_server",
    "joern_server_port",
    "joern_heap_gb",
    # Анализ графа на видеокарте: на нашем размере графа процессор быстрее.
    "graph_gpu",
    "graph_gpu_min_nodes",
    # Схлопывание дублей: меняет состав индекса, поэтому переключатель
    # и правило выбора выведены в интерфейс.
    "dedupe_enabled",
    "dedupe_scope",
    "dedupe_mode",
    "dedupe_keep",
    "dedupe_report",
    # Канал работы: выбор режима в панели.
    "ragraf_mode",
)

# Настройки-перечисления: значение проверяется по списку, а не по границам.
# Нужно потому, что опечатка вида `dedupe_scope: doc` привела бы к ошибке
# при индексации — лучше отклонить её в интерфейсе.
ENUM_KEYS: dict[str, tuple[str, ...]] = {
    "dedupe_scope": ("docs", "all"),
    "dedupe_mode": ("keep_one", "skip_all"),
    "dedupe_keep": ("cleanest", "shortest", "longest", "newest"),
    # Канал работы. Список закрытый: опечатка вида `mcp_http` не должна
    # стать «новым режимом», который никто не проверяет.
    "ragraf_mode": ("cli", "mcp-stdio", "mcp-http", "both"),
}

# Настройки, которые НЕ хранятся в файле настроек, а относятся к конфигу
# корпуса. Нужны интерфейсу, чтобы показать их в разделе разработки.
PROJECT_ONLY: tuple[str, ...] = ("device",)

# Ключи-переключатели: проверяются как да/нет, а не как число.
BOOLEAN_KEYS: tuple[str, ...] = (
    "change_counter", "joern_enabled", "use_gpu", "joern_server", "graph_gpu",
    "dedupe_enabled", "dedupe_report",
)

# Пояснения для интерфейса: администратор должен понимать последствия.
DESCRIPTIONS: dict[str, str] = {
    "reindex_after_changes":
        "Сколько правок кода накапливается до быстрой переиндексации "
        "(без построения графа свойств кода). Быстрая занимает ~5,6 с на корпусе "
        "в 124 файла против 20,8 с у полной.",
    "full_reindex_after_changes":
        "Сколько правок накапливается до полного пересбора с графом свойств кода. "
        "Полный пересбор уточняет межпроцедурные связи вызовов.",
    "reindex_interval_minutes":
        "Предельный возраст индекса. По его достижении выполняется полный "
        "пересбор, даже если правок было мало: за длительное время структура кода "
        "могла уйти.",
    "change_counter":
        "Включён ли подсчёт накопления правок. Если выключить, переиндексация "
        "будет выполняться при каждом обнаруженном изменении.",
    "joern_enabled":
        "Строить ли граф свойств кода (CPG) при полной индексации. Выключение "
        "ускоряет индексацию в 3,7 раза, но теряются межпроцедурные связи.",
    "retain_revs":
        "Сколько последних ревизий хранить в графе. Влияет на размер базы.",
    "batch_size":
        "Размер порции при записи в базы. Больше — быстрее, но больше памяти.",
    "use_gpu":
        "Считать векторы на видеокарте. Замерено на RTX 5080: само кодирование "
        "быстрее в 38 раз (0,35 мс на текст против 13,7). НО есть разовая "
        "подготовка карты — около 26 с на запуск процесса, поэтому карта "
        "включается только когда работы больше порога окупаемости.",
    "gpu_min_texts":
        "Порог окупаемости видеокарты: сколько новых текстов надо закодировать "
        "в одном запуске, чтобы она окупилась. Замерено: точка равенства — "
        "1451 текст. При меньшем числе считается процессор. При обычной правке "
        "файла новых текстов единицы, поэтому быстрая переиндексация всегда идёт "
        "на процессоре — это правильное поведение.",
    "gpu_batch_size":
        "Партия кодирования на видеокарте. Больше партия — выше загрузка карты "
        "и меньше накладных расходов. На замерах разница между 64 и 256 мала.",
    "device":
        "Конкретное устройство из конфигурации корпуса: auto — выбрать "
        "видеокарту автоматически, cpu — только процессор, cuda:N — конкретная "
        "карта. Задаётся в конфиге корпуса, а не в общих настройках.",
    "joern_server":
        "Держать Joern долгоживущим процессом вместо запуска JVM на каждый "
        "вызов. Замерено: загрузка классов Joern занимает 5,53 с на КАЖДЫЙ "
        "запуск (чистый старт JVM — всего 64 мс), а индексация запускает его "
        "дважды — около 11 с накладных. Три прогона подряд: прежний путь "
        "44,72 с, серверный 35,88 с, экономия 8,84 с. ВАЖНО: при ПЕРВОМ прогоне "
        "сервер медленнее на 3,7 с, потому что в него входит старт. Выигрыш "
        "появляется со второго прогона в той же сессии.",
    "joern_server_port":
        "Порт сервера Joern. Диапазон 8090–8110 занят PHP-серверами стендов, "
        "поэтому по умолчанию 9500 с поиском свободного порта вверх. Сервер "
        "слушает только 127.0.0.1.",
    "joern_heap_gb":
        "Память JVM сервера. На крупном графе нужно больше: при 8 ГБ сервер "
        "строит граф корпуса на 90 тысяч вершин.",
    "graph_gpu":
        "Считать обходы графа и PageRank на видеокарте. Решение владельца: "
        "включено, порог 5000 вершин. Замерено: на 1059 вершинах процессор "
        "быстрее, на 5520 карта уже выигрывает — 7,36 мс у процессора против "
        "1000 мс у карты на первом вызове (в него входит подготовка карты) и "
        "5,87 мс против 6,44 мс после прогрева. RAPIDS cuGraph на Windows не "
        "собирается, поэтому используется torch.sparse. ВНИМАНИЕ: исполнителя в "
        "библиотеке пока нет — графовые алгоритмы реализованы только в "
        "проверочных скриптах инструментов, поэтому переключатель сам ничего не "
        "считает.",
    "graph_gpu_min_nodes":
        "Порог окупаемости для графового анализа: при меньшем числе вершин "
        "считается на процессоре. Замерено: на 5000 вершинах результат на "
        "границе (5,87 мс у карты против 6,44 мс у процессора), дальше карта "
        "выигрывает. Решение владельца — 5000.",
    "dedupe_enabled":
        "Схлопывать байт-идентичные копии файлов. Копия — файл с тем же "
        "содержимым (по контрольной сумме), лежащий по другому пути. "
        "Сравнение по содержимому, а не по имени: 103 разных README не "
        "схлопнутся. Файлы на диске НЕ удаляются, они просто не идут "
        "в индекс. Замер на C:\\AutoClaw: 3856 лишних копий (44,7 МБ).",
    "dedupe_scope":
        "Что схлопывать: «docs» — только документацию (md, markdown, rst, "
        "txt), это безопаснее всего; «all» — все файлы, включая копии кода "
        "(они тоже удваивают узлы и рёбра).",
    "dedupe_mode":
        "Что делать с группой копий: «keep_one» — оставить одну (обычный "
        "режим); «skip_all» — не индексировать ни одной (нужно для зеркал "
        "сторонних библиотек, которые не нужны в поиске вообще).",
    "dedupe_keep":
        "Какую копию считать главной. Замерено на 513 реальных группах "
        "(сколько групп получило «мусорный» путь): cleanest 38, newest 65, "
        "shortest 76, longest 242. «Мусорные» — это сегменты вроде backup, "
        "old, publish, release, out, copy, www, _to.upload.",
    "dedupe_report":
        "Записывать в ответ операции разбор групп: какие копии отброшены "
        "и почему. Нужно для проверяемости — без этого схлопывание "
        "невидимо и его нельзя подтвердить.",
}


@dataclass
class SettingsState:
    """Текущее состояние настроек: глобальные и по каждой разработке."""

    global_settings: dict[str, Any] = field(default_factory=dict)
    per_project: dict[str, dict[str, Any]] = field(default_factory=dict)
    defaults: dict[str, Any] = field(default_factory=lambda: dict(DEFAULTS))
    editable: list[str] = field(default_factory=lambda: list(EDITABLE))
    descriptions: dict[str, str] = field(default_factory=lambda: dict(DESCRIPTIONS))
    bounds: dict[str, list[int]] = field(
        default_factory=lambda: {k: list(v) for k, v in BOUNDS.items()}
    )
    # Настройки-перечисления: интерфейс должен рисовать их списком,
    # а не числовым полем. Без этого значения вида «docs» попадали бы
    # в input type=number и редактирование ломалось бы.
    enums: dict[str, list[str]] = field(
        default_factory=lambda: {k: list(v) for k, v in ENUM_KEYS.items()}
    )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class SettingsStore:
    """Хранилище глобальных настроек: один JSON рядом с реестром.

    Файл отдельный от каталога разработок: настройки и описания разработок —
    разные сущности, и их слияние усложнило бы обе.
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (Path(__file__).resolve().parents[2] / "catalog" / "settings.json")

    def load(self) -> dict[str, Any]:
        """Прочитать настройки.

        ВНИМАНИЕ про структуру файла. `save()` записывает значения ВЛОЖЕННО:
        `{"version": 1, "settings": {...}}`. Первая версия `load()` читала ключи
        с ВЕРХНЕГО уровня, поэтому сохранённое значение не находилось, и чтение
        всегда возвращало значение по умолчанию. Дефект найден проверкой
        tools/check_admin.py: запись отвечала «сохранено», а чтение отдавало
        прежнее число.

        Здесь читаются оба варианта: вложенный (текущий формат) и плоский
        (совместимость с файлом, записанным ранней версией).
        """
        raw = util.read_json(self.path, default=None)
        if not isinstance(raw, dict):
            raw = {}
        # Значения могут лежать в разделе settings (текущий формат) либо на
        # верхнем уровне (ранние записи).
        nested = raw.get("settings")
        source: dict[str, Any] = nested if isinstance(nested, dict) else raw

        out = dict(DEFAULTS)
        # Читаются только известные ключи: посторонние значения из файла
        # не попадают в настройки, иначе опечатка в ключе стала бы настройкой.
        for key in EDITABLE:
            if key in source:
                out[key] = source[key]
            elif key in raw:
                out[key] = raw[key]
        return out

    def save(self, values: dict[str, Any]) -> dict[str, Any]:
        """Сохранить настройки, проверив значения. Возвращает сохранённое.

        Ошибочное значение не «исправляется молча»: возвращается причина,
        и администратор видит, что именно не принято.
        """
        clean = self.load()
        problems = validate(values, current=clean)
        if problems:
            raise ValueError("; ".join(problems))
        for key, value in values.items():
            if key in EDITABLE:
                clean[key] = value
        util.write_json(
            self.path,
            {"version": 1, "settings": {k: clean[k] for k in EDITABLE}, "updated_at": util.now_iso()},
            indent=2,
        )
        return clean


def validate(values: dict[str, Any], current: dict[str, Any] | None = None) -> list[str]:
    """Проверить значения настроек. Возвращает список причин отказа.

    Проверки обязательны: неверный порог тихо ломает поведение. Например,
    порог полного пересбора меньше порога быстрого означает, что быстрый режим
    никогда не сработает.

    Аргумент current — действующие значения (то, что уже записано). Он нужен
    для проверки согласованности при ЧАСТИЧНОЙ записи: если в запросе пришёл
    только порог полного пересбора, сравнивать его надо с уже сохранённым
    порогом быстрой. Без этого проверка проходила незаметно — дефект найден
    проверкой tools/check_admin_project_settings.py.
    """
    problems: list[str] = []
    for key, value in (values or {}).items():
        if key not in EDITABLE:
            problems.append(f"настройка «{key}» не поддерживается")
            continue
        if key in BOOLEAN_KEYS:
            if not isinstance(value, bool):
                problems.append(f"«{key}» принимает только да/нет")
            continue
        # Настройки-перечисления: значение обязано быть из списка.
        # Проверка здесь, а не при индексации: ошибку в опечатке надо видеть
        # сразу в интерфейсе, а не получать исключение через час работы.
        if key in ENUM_KEYS:
            allowed = ENUM_KEYS[key]
            if str(value).lower() not in allowed:
                problems.append(
                    f"«{key}»: допустимо {', '.join(allowed)}, "
                    f"получено {value!r}"
                )
            continue
        try:
            number = int(value)
        except (TypeError, ValueError):
            problems.append(f"«{key}» принимает только целое число")
            continue
        low, high = BOUNDS.get(key, (0, 10**9))
        if not (low <= number <= high):
            problems.append(f"«{key}»: допустимо от {low} до {high}, получено {number}")

    # Согласованность порогов: иначе быстрый режим недостижим.
    # Недостающие значения берутся из действующих настроек.
    base = dict(current or {})
    base.update(values or {})
    fast = base.get("reindex_after_changes")
    full = base.get("full_reindex_after_changes")
    if fast is not None and full is not None:
        try:
            if int(full) <= int(fast):
                problems.append(
                    "порог полного пересбора должен быть больше порога быстрой "
                    f"переиндексации ({full} ≤ {fast})"
                )
        except (TypeError, ValueError):
            pass
    return problems


# --------------------------------------------------------------------------
# Правка конфигурации корпуса
# --------------------------------------------------------------------------

KEY_RE = re.compile(r"^(?P<indent>\s*)(?P<key>[a-z_][a-z0-9_]*)\s*:\s*(?P<value>.*)$")


def read_project_settings(config_path: Path) -> dict[str, Any]:
    """Прочитать настройки секции `index` из конфигурации корпуса.

    Значения возвращаются в том же виде, что глобальные настройки: интерфейсу
    не нужно знать, откуда они пришли.
    """
    out: dict[str, Any] = {}
    try:
        text = Path(config_path).read_text(encoding="utf-8")
    except OSError:
        return out
    in_index = False
    for line in text.splitlines():
        m = KEY_RE.match(line)
        if not m:
            continue
        key = m.group("key")
        if key == "index" and not line.startswith(" "):
            in_index = True
            continue
        if in_index and not line.startswith(" "):
            break   # секция закончилась: следующий ключ верхнего уровня
        if in_index and key in EDITABLE:
            out[key] = _parse_value(m.group("value"))
    return out


def _parse_value(raw: str) -> Any:
    """Разобрать значение YAML простых типов: bool, int, строка."""
    value = raw.split("#", 1)[0].strip()
    if value.lower() in ("true", "yes", "on"):
        return True
    if value.lower() in ("false", "no", "off"):
        return False
    if re.fullmatch(r"-?\d+", value):
        return int(value)
    return value.strip("\"'")


def write_project_settings(config_path: Path, values: dict[str, Any]) -> dict[str, Any]:
    """Записать настройки в секцию `index` конфигурации корпуса.

    Правка построчная: комментарии и структура файла сохраняются. Отсутствующий
    ключ добавляется в секцию `index`; если секции нет, она создаётся в конце
    файла. Перед записью создаётся копия — настройки корпуса это его конфигурация,
    и потерять её нельзя.

    Возвращает фактически записанные значения.
    """
    path = Path(config_path)
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    # Действующие значения берутся из файла: проверка согласованности должна
    # учитывать то, что уже записано, а не только присланные поля.
    current = read_project_settings(path)
    problems = validate(values, current=current)
    if problems:
        raise ValueError("; ".join(problems))

    # Копия с отметкой времени: откат должен быть возможен всегда.
    from datetime import datetime

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = path.with_name(f"{path.name}.bak.{stamp}-settings")
    backup.write_text(text, encoding="utf-8", newline="\n")

    # Границы секции index.
    start = None
    end = len(lines)
    for i, line in enumerate(lines):
        m = KEY_RE.match(line)
        if m and m.group("key") == "index" and not line.startswith(" "):
            start = i
            continue
        if start is not None and i > start and line.strip() and not line.startswith(" "):
            end = i
            break

    if start is None:
        # Секции нет: она добавляется в конец с пояснением.
        lines.append("")
        lines.append("# Пороги авто-реиндексации: значения по умолчанию, изменение —")
        lines.append("# через интерфейс администратора RAGRAF или правкой этого файла.")
        lines.append("index:")
        for key, value in values.items():
            lines.append(f"  {key}: {_format_value(value)}")
        path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
        return dict(values)

    written: dict[str, Any] = {}
    present: set[str] = set()
    for i in range(start + 1, end):
        line = lines[i]
        m = KEY_RE.match(line)
        if not m or not line.startswith(" "):
            continue
        key = m.group("key")
        if key in values:
            # Значение заменяется, комментарий в конце строки сохраняется.
            tail = ""
            if "#" in line:
                tail = "  #" + line.split("#", 1)[1]
            lines[i] = f"{m.group('indent')}{key}: {_format_value(values[key])}{tail}"
            written[key] = values[key]
            present.add(key)

    # Недостающие ключи добавляются в конец секции.
    missing = [(k, v) for k, v in values.items() if k not in present]
    if missing:
        insert_at = end
        add: list[str] = []
        for key, value in missing:
            if key in DESCRIPTIONS:
                add.append(f"  # {DESCRIPTIONS[key]}")
            add.append(f"  {key}: {_format_value(value)}")
        lines[insert_at:insert_at] = add
        for key, value in missing:
            written[key] = value

    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return written


def _format_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def state(catalog_entries: dict[str, dict[str, Any]], repo_root: Path | None = None) -> SettingsState:
    """Собрать полное состояние настроек для интерфейса.

    Для каждой разработки настройки читаются из её конфига; если ключ там
    не задан, показывается унаследованное глобальное значение — администратор
    должен видеть действующее значение, а не пустоту.
    """
    store = SettingsStore()
    glob = store.load()

    per_project: dict[str, dict[str, Any]] = {}
    for name, entry in (catalog_entries or {}).items():
        config_path = Path(str(entry.get("config") or ""))
        own = read_project_settings(config_path) if config_path.exists() else {}
        merged: dict[str, Any] = {}
        for key in EDITABLE:
            if key in own:
                merged[key] = own[key]
                merged[f"{key}__source"] = "проект"
            elif key in glob:
                merged[key] = glob[key]
                merged[f"{key}__source"] = "глобально"
        merged["config_path"] = str(config_path)
        merged["config_exists"] = config_path.exists()
        per_project[name] = merged

    return SettingsState(global_settings=glob, per_project=per_project)
