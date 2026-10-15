"""MCP-сервер RAGRAF: операции фасада как инструменты агента (ФТ-22).

Протокол: JSON-RPC 2.0 поверх stdio (MCP, версия 2024-11-05).
Реализованы методы: initialize, notifications/initialized, tools/list, tools/call, ping.

Принципиальные ограничения (архитектура §7, ФТ-15):
  * наружу выставляются ТОЛЬКО фиксированные операции фасада;
  * отсутствует инструмент «выполнить запрос к графу» — языков запросов нет;
  * каждый ответ сохраняет конверт с провенансом и пометками устаревших фактов.

Запуск (пример конфигурации MCP-клиента):
    python tools/ragraf-mcp.py --project config/revoap.yaml
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
import traceback
from typing import Any, Callable

from . import __version__
from . import contact as contact_mod
from .config import ProjectConfig, load as load_config
from .config import ConfigError
from pathlib import Path

from .facade import Facade

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "ragraf"

# Инструкции сервера: клиент обязан показать их агенту (поле `instructions`
# ответа `initialize`). Это ответ на B1: корпус определяется по рабочему
# каталогу ПРОЦЕССА сервера, а конфигурация клиента задаёт этот каталог
# (в HARNESS профиле — `cwd: C:\AutoClaw\RAGRAF`), поэтому сессия с другим
# рабочим каталогом получала бы факты о чужом проекте молча. Инструкция
# говорит, чем это проверить, и не заменяет серверную гарантию: сторож
# свежести работает на каждом вызове независимо от того, прочитал ли клиент
# это поле.
INSTRUCTIONS = (
    "RAGRAF — графово-векторный индекс кода проекта. "
    "1. Перед первой операцией вызовите ragraf_ensure: он сверяет контрольную "
    "сумму корпуса с проиндексированной и сам пересобирает отставший индекс. "
    "2. Если ответ ссылается на пути, которых нет в текущем проекте, вызовите "
    "ragraf_context: корпус мог быть определён по рабочему каталогу процесса "
    "сервера, а не по проекту задачи, — тогда выберите корпус через "
    "ragraf_use_project (список даёт ragraf_catalog). "
    "3. Факт без ссылки файл:строка — догадка; пустой результат — это "
    "found=false, а не ошибка. "
    "4. Перед записью в базу вызовите ragraf_write_check, перед правкой прав — "
    "ragraf_policy_trace. "
    "5. Поле stale=true означает, что код ушёл вперёд индекса: сначала "
    "ragraf_ensure, потом выводы."
)

# Ресурсы (MCP resources): состояние и реестр читаются клиентом по требованию,
# а не занимают место в списке инструментов и не стоят вызова.
STATUS_URI = "ragraf://status"
CATALOG_URI = "ragraf://catalog"
CORPUS_MAP_URI = "ragraf://corpus/{name}/map"
CORPUS_MAP_URI_PREFIX = "ragraf://corpus/"


def force_utf8_stdio() -> None:
    """Сделать потоки UTF-8, не полагаясь на локаль процесса и окружение.

    Зачем. MCP-клиент запускает сервер как дочерний процесс и передаёт ему
    УРЕЗАННОЕ окружение: официальный SDK наследует только `APPDATA`,
    `HOMEDRIVE`, `HOMEPATH`, `LOCALAPPDATA`, `PATH`, `PROCESSOR_ARCHITECTURE`,
    `SYSTEMDRIVE`, `SYSTEMROOT`, `TEMP`, `USERNAME`, `USERPROFILE`,
    `PROGRAMFILES` — ни `PYTHONUTF8`, ни `PYTHONIOENCODING` туда не попадают
    (замерено по `@modelcontextprotocol/client/dist/stdio.mjs`). Оболочка
    AutoClaw ставит обе переменные, поэтому дефект был невидим; DeepSeek
    Harness добавляет к этому чистку `PYTHON*` — и сервер падал на ПЕРВОМ же
    ответе с русским текстом: `UnicodeEncodeError: 'charmap' codec can't
    encode characters` в `send()` на кодировке cp1252, то есть клиент видел
    не инструменты, а мёртвый процесс.

    Русский текст есть и в описаниях инструментов (`tools/list`), и в ответах
    операций, поэтому полагаться на окружение нельзя: потоки перенастраиваются
    явно. stdin тоже: аргументы запроса приходят в UTF-8, а декодированные
    в cp1252 превратились бы в мусор, и поиск вернул бы не то — без единой
    ошибки.

    Ошибка замены не глушится: если потока нет или он без `reconfigure`
    (вывод перенаправлен в объект без него), это нормальный случай.
    """
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass

# Описание инструментов: имя = имя операции фасада.
TOOLS: list[dict[str, Any]] = [
    {
        "name": "ragraf_index_status",
        "description": (
            "Состояние индекса кода: ревизия, полнота разбора, доступные возможности, "
            "размер журнала расхождений. ВЫЗЫВАТЬ ПЕРВЫМ перед любой работой с кодом."
        ),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "ragraf_context",
        "description": (
            "КАКОЙ КОРПУС ВЫБРАН: имя разработки, корень, ревизия, свежесть, режим "
            "и на каком основании корпус определён (аргумент запуска, рабочий каталог "
            "процесса, реестр или use_project). Вызывать, когда ответ ссылается на "
            "пути, которых нет в текущем проекте: молчаливая подмена корпуса — "
            "источник ложных фактов, а не мелкое неудобство."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "probe": {
                    "type": "boolean",
                    "default": True,
                    "description": (
                        "сверить контрольную сумму корпуса с кодом (может занять "
                        "время); false — только состояние индекса без сверки"
                    ),
                },
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "ragraf_drift_report",
        "description": (
            "Журнал расхождений индекса и кода. Если непуст — факты по перечисленным "
            "путям могли устареть: сначала вызовите live_read по этим файлам, "
            "и только потом делайте выводы."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"since": {"type": "integer", "description": "начиная с id записи"}},
            "additionalProperties": False,
        },
    },
    {
        "name": "ragraf_search",
        "description": (
            "Гибридный поиск по коду и документации (лексика + семантика). "
            "Возвращает файл, диапазон строк, причину совпадения и пометку устаревания."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "запрос: имя, фраза, описание задачи"},
                "limit": {"type": "integer", "default": 20},
                "with_text": {
                    "type": "boolean",
                    "default": False,
                    "description": "добавить к каждому хиту фрагмент текста из индекса — экономит второй вызов",
                },
                "text_width": {"type": "integer", "default": 160},
                "answer": {
                    "type": "boolean", "default": False,
                    "description": ("вернуть сформулированный ответ со ссылками вместо "
                                    "структурного результата (P2-2): ≤ 1–2 КБ"),
                },
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    {
        "name": "ragraf_search_multi",
        "description": (
            "Поиск одним ответом по НЕСКОЛЬКИМ запросам: литеральные вхождения "
            "(не токены — находится и `'MCH', 'MOL'`), кодировка каждого файла, "
            "группировка по семействам копий (Files.php…Files7.php) с указанием, "
            "какой файл активен и чем это доказано. Экономит 2–4 вызова вместо "
            "последовательного поиска грепом."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "queries": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "запросы: строки или регулярные выражения",
                },
                "limit": {"type": "integer", "default": 20},
                "scope": {"type": "string", "description": "каталог внутри корпуса"},
                "regex": {
                    "type": "boolean",
                    "default": False,
                    "description": "считать запросы регулярными выражениями",
                },
                "answer": {
                    "type": "boolean", "default": False,
                    "description": "вернуть сформулированный ответ со ссылками (P2-2)",
                },
            },
            "required": ["queries"],
            "additionalProperties": False,
        },
    },
    {
        "name": "ragraf_duplicates",
        "description": (
            "Группы копий файлов: байт-идентичные и «одно имя с разными номерами» "
            "(php/Wizard/Files.php…Files7.php, import1.pl/import2.pl). У каждой группы "
            "назван главный файл и доказательство его активности — ссылка path:line."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "scope": {"type": "string", "description": "каталог внутри корпуса"},
                "limit": {"type": "integer", "default": 100},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "ragraf_entrypoints",
        "description": (
            "Точки входа: маршруты, HTTP-обработчики (чтение $_GET/$_POST/$_SERVER), "
            "CLI-скрипты, endpoint'ы из разметки и скриптов. С параметром path — "
            "достижим ли конкретный файл от точек входа и кто на него ссылается."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "разобрать один файл"},
                "scope": {"type": "string", "description": "каталог внутри корпуса"},
                "limit": {"type": "integer", "default": 200},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "ragraf_dead_code",
        "description": (
            "Файлы и подпрограммы без входящих ссылок и вне достижимости от точек "
            "входа. Вердикт выносится только при совпадении двух проверок (граф и "
            "текст); кандидаты, оправданные найденной ссылкой, перечислены в refuted."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "scope": {"type": "string", "description": "каталог внутри корпуса"},
                "limit": {"type": "integer", "default": 200},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "ragraf_diff",
        "description": (
            "Что изменилось между ревизиями индекса (P2-1): файлы, символы, рёбра, "
            "а также РЕГРЕССИИ — символы, потерявшие всех вызывающих, и таблицы, "
            "переставшие заполняться. Сравниваются факты двух ревизий; режим "
            "каждой назван в `modes`."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "since": {"type": "string",
                          "description": "ревизия для сравнения (по умолчанию — предыдущая)"},
                "limit": {"type": "integer", "default": 50},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "ragraf_note",
        "description": (
            "Записать короткую заметку о решении (P2-3): знание, которое должно "
            "пережить сессию («в этой базе часть колонок генерируемые — писать "
            "нельзя»). Заметка — знание агента, а не факт индекса."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "текст заметки"},
                "task": {"type": "string", "description": "метка задачи (RAGRAF_TASK)"},
                "tags": {"type": "array", "items": {"type": "string"}},
                "path": {"type": "string", "description": "файл, к которому относится"},
            },
            "required": ["text"],
            "additionalProperties": False,
        },
    },
    {
        "name": "ragraf_notes",
        "description": (
            "Прочитать заметки (P2-3): свежие первыми, с фильтром по задаче и "
            "подстроке текста. Каждая запись помечена `source: note`."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {"type": "string"},
                "query": {"type": "string", "description": "подстрока текста заметки"},
                "limit": {"type": "integer", "default": 50},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "ragraf_task",
        "description": (
            "Назвать текущую задачу — так работает `RAGRAF_TASK`, когда клиент "
            "не может передать метку в долгоживущий MCP-процесс. Метка собирает "
            "все обращения к индексу в одну задачу (её читают и MCP-сервер, и "
            "CLI, и хук гейта) и видна в `ragraf_task_stats`. Пустое имя или "
            "`clear=true` снимает метку. Переменная окружения `RAGRAF_TASK`, "
            "если задана при старте клиента, имеет приоритет над названной."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "метка задачи (пусто — снять)"},
                "clear": {"type": "boolean", "default": False,
                          "description": "снять метку задачи"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "ragraf_task_stats",
        "description": (
            "Готовый отчёт по задаче (§3.4): сколько было обращений к индексу, "
            "сколько фактов, сколько вызовов заменили чтение файлов, были ли "
            "ответы по устаревшей ревизии, что решил гейт использования индекса. "
            "Числа берутся из журнала обращений и состояния гейта — их видно "
            "командой `ragraf_usage`, а отчёт добавляет решения гейта и заметки. "
            "Пусто по задаче — это находка (`calls: 0`), а не отсутствие данных."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "метка задачи (по умолчанию текущая)"},
                "format": {"type": "string", "enum": ["markdown", "json"], "default": "markdown"},
                "limit": {"type": "integer", "default": 10},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "ragraf_symbol",
        "description": "Найти символ (класс, метод, функцию) по имени или полному имени.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "path": {"type": "string", "description": "ограничить файлом"},
                "limit": {"type": "integer", "default": 20},
                "answer": {
                    "type": "boolean", "default": False,
                    "description": "вернуть сформулированный ответ со ссылками (P2-2)",
                },
            },
            "required": ["name"],
            "additionalProperties": False,
        },
    },
    {
        "name": "ragraf_references",
        "description": "Все места использования символа с указанием файла и строки.",
        "inputSchema": {
            "type": "object",
            "properties": {"fqn": {"type": "string"}, "limit": {"type": "integer", "default": 100}},
            "required": ["fqn"],
            "additionalProperties": False,
        },
    },
    {
        "name": "ragraf_callers",
        "description": (
            "Кто вызывает символ. hard_only=true — только доказанные связи. "
            "Поле `name_links` перечисляет вызовы, назвавшие это имя, но не "
            "привязанные к определению (межфайловое разрешение по имени не "
            "делается): пустой `count` не означает «вызовов нет»."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "fqn": {"type": "string"},
                "depth": {"type": "integer", "default": 1},
                "hard_only": {"type": "boolean", "default": False},
                "limit": {"type": "integer", "default": 100},
                "answer": {
                    "type": "boolean", "default": False,
                    "description": "вернуть сформулированный ответ со ссылками (P2-2)",
                },
            },
            "required": ["fqn"],
            "additionalProperties": False,
        },
    },
    {
        "name": "ragraf_callees",
        "description": "Кого вызывает символ.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "fqn": {"type": "string"},
                "depth": {"type": "integer", "default": 1},
                "hard_only": {"type": "boolean", "default": False},
                "limit": {"type": "integer", "default": 100},
            },
            "required": ["fqn"],
            "additionalProperties": False,
        },
    },
    {
        "name": "ragraf_impact",
        "description": (
            "Что затронет изменение символа или таблицы. Доказанные связи — отдельно от "
            "эвристических. Используйте ПЕРЕД правкой, чтобы не сломать потребителей."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "символ, имя файла или table:имя"},
                "mode": {"type": "string", "enum": ["hard", "full"], "default": "full"},
                "depth": {"type": "integer", "default": 3},
                "limit": {"type": "integer", "default": 200},
                "answer": {
                    "type": "boolean", "default": False,
                    "description": "вернуть сформулированный ответ со ссылками (P2-2)",
                },
            },
            "required": ["target"],
            "additionalProperties": False,
        },
    },
    {
        "name": "ragraf_slice",
        "description": (
            "Структурный срез вокруг символа: только релевантные узлы и связи со строками, "
            "БЕЗ тел функций. Доступно только при полной сборке (capability=full)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "fqn": {"type": "string"},
                "direction": {"type": "string", "enum": ["back", "forward"], "default": "back"},
                "limit": {"type": "integer", "default": 200},
            },
            "required": ["fqn"],
            "additionalProperties": False,
        },
    },
    {
        "name": "ragraf_code",
        "description": (
            "Тела символов — только по явному запросу. По умолчанию текст берётся "
            "из индекса; вложенные спаны не дублируются."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "fqn": {"type": "string"},
                "path": {"type": "string"},
                "entry_ids": {
                    "type": "string",
                    "description": "node_id и/или chunk_id из хитов search — адрес второго шага",
                },
                "start": {"type": "integer"},
                "end": {"type": "integer"},
                "style": {"type": "string", "enum": ["compact", "signatures"], "default": "compact"},
                "source": {"type": "string", "enum": ["auto", "index", "disk"], "default": "auto"},
                "include_containers": {"type": "boolean", "default": False},
                "limit": {"type": "integer", "default": 5},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "ragraf_repo_map",
        "description": "Карта репозитория (модули → файлы → ключевые символы) в заданном бюджете токенов.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "scope": {"type": "string", "description": "подкаталог"},
                "budget_tokens": {"type": "integer", "default": 2000},
                "detail": {"type": "string", "enum": ["normal", "deep"], "default": "normal"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "ragraf_schema",
        "description": "Схема БД из индекса: таблицы, колонки, ключи, источник схемы.",
        "inputSchema": {
            "type": "object",
            "properties": {"table": {"type": "string"}, "limit": {"type": "integer", "default": 200}},
            "additionalProperties": False,
        },
    },
    {
        "name": "ragraf_table_usage",
        "description": (
            "Кто читает и кто пишет таблицу БД: символы и SQL-обращения со строками. "
            "В каждом факте есть write и access (select/insert/update/delete/ddl), "
            "поэтому параметром mode можно отобрать только читающие (read) или "
            "только изменяющие (write) обращения. Поле unresolved_tables перечисляет "
            "таблицы, которых нет в схеме из миграций: это признак опечатки в SQL "
            "либо обращения к системной таблице."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "table": {"type": "string"},
                "limit": {"type": "integer", "default": 200},
                "mode": {"type": "string", "enum": ["all", "read", "write"],
                         "default": "all"},
            },
            "required": ["table"],
            "additionalProperties": False,
        },
    },
    {
        "name": "ragraf_policy_trace",
        "description": (
            "ТРАССИРОВКА РЕШЕНИЙ О ПРАВАХ: где функция прав возвращает разрешение "
            "и где отказ, какая ветвь case к какому действию относится, и что "
            "происходит, если действие не описано явно. Вызывать при проверке или "
            "правке матрицы прав, маршрутов согласования и условий доступа. "
            "Поле default_behavior = allow означает дефект: неизвестное действие "
            "получает право. Поле callers показывает точки применения решения."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "action": {"type": "string", "description": "действие для отбора ветвей"},
                "depth": {"type": "integer", "default": 3},
                "limit": {"type": "integer", "default": 300},
            },
            "required": ["symbol"],
            "additionalProperties": False,
        },
    },
    {
        "name": "ragraf_class_usage",
        "description": (
            "КЛАССЫ ОФОРМЛЕНИЯ: где класс определён в стилях, где использован "
            "в разметке и что расходится. Вызывать при правке интерфейса и шаблонов. "
            "Режим missing возвращает классы, которые используются в разметке, "
            "но НЕ описаны в стилях: страница при этом работает и выглядит "
            "«как-то» за счёт оформления браузера по умолчанию, поэтому ошибка "
            "не проявляется при запуске. Режим unused — описанные, но не "
            "используемые классы."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "имя класса без точки"},
                "mode": {"type": "string", "enum": ["all", "missing", "unused"],
                         "default": "all"},
                "limit": {"type": "integer", "default": 200},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "ragraf_write_check",
        "description": (
            "СВЕРКА ПЛАНА ЗАПИСИ СО СХЕМОЙ до выполнения запроса. Вызывать перед INSERT/UPDATE:"
            " возвращает отсутствующие колонки с ближайшими существующими именами, "
            "обязательные колонки и первичный ключ. Предотвращает ошибки вроде "
            "«Unknown column» на первом сохранении."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "table": {"type": "string", "description": "имя таблицы"},
                "columns": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "колонки, которые собираетесь записать",
                },
                "operation": {
                    "type": "string",
                    "enum": ["insert", "update", "delete"],
                    "default": "insert",
                },
                "values": {
                    "type": "integer",
                    "description": "сколько значений в запросе (ловит Column count doesn't match)",
                },
            },
            "required": ["table"],
            "additionalProperties": False,
        },
    },
    {
        "name": "ragraf_usage",
        "description": (
            "Статистика обращений к индексу: сколько вызовов, по каким операциям и "
            "объектам, сколько фактов получено и сколько объёма не пришлось читать "
            "файлами. Возвращает разбивку по агентам (кто именно использовал "
            "RAGRAF) и по семействам агентов. Фильтры agent и agent_kind "
            "позволяют смотреть отдельного агента или всех агентов AutoClaw. "
            "scope=session отвечает на вопрос «видит ли журнал МОИ вызовы»: "
            "в этом режиме заполнено calls_in_session и session_id, а поле "
            "write_failures показывает, сколько записей журнал не смог сохранить. "
            "Нужна для оценки эффекта от использования индекса."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "scope": {
                    "type": "string",
                    "enum": ["all", "session", "agent"],
                    "default": "all",
                    "description": (
                        "область сводки: all — весь журнал, session — только эта "
                        "сессия процесса (проверка «журнал видит мои вызовы»), "
                        "agent — только агент этого процесса"
                    ),
                },
                "session_id": {
                    "type": "string",
                    "description": (
                        "конкретная сессия процесса; без него берётся текущая. "
                        "Идентификатор текущей сессии виден в ответе (поле session_id)"
                    ),
                },
                "agent": {
                    "type": "string",
                    "description": (
                        "только этот агент, например AutoClaw, SagaAI "
                        "или MD.Viewer (без параметра — все агенты)"
                    ),
                },
                "agent_kind": {
                    "type": "string",
                    "enum": ["autoclaw", "external", "internal"],
                    "description": (
                        "только это семейство агентов: autoclaw — все агенты "
                        "AutoClaw вместе, external — сторонние, "
                        "internal — служебные проверки"
                    ),
                },
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "ragraf_freshness",
        "description": (
            "СВЕЖЕСТЬ ИНДЕКСА: совпадает ли контрольная сумма кода с проиндексированной. "
            "Если код менялся после индексации, операция сама выполняет реиндексацию. "
            "Вызывать после правок кода, чтобы убедиться, что факты актуальны."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "force": {"type": "boolean", "default": False,
                          "description": "проверить минуя кэш"}
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "ragraf_use_project",
        "description": (
            "Выбрать корпус, с которым работает этот сервер: имя разработки из реестра "
            "или путь к каталогу проекта. Нужно, когда один сервер обслуживает несколько "
            "проектов: клиент запускает сервер один раз, а корпус задаётся по задаче. "
            "Ответ подтверждает ревизию и режим выбранного корпуса."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {
                    "type": "string",
                    "description": "имя разработки в реестре (например revoap.com) или путь к каталогу проекта",
                },
            },
            "required": ["project"],
            "additionalProperties": False,
        },
    },
    {
        "name": "ragraf_ensure",
        "description": (
            "ПРИВЕСТИ КОРПУС В АКТУАЛЬНОЕ СОСТОЯНИЕ — вызывать ПЕРВЫМ шагом работы. "
            "Сверяет контрольную сумму кода с проиндексированной и при расхождении "
            "пересобирает индекс сам. Без аргумента проверяет корпус, к которому "
            "подключён этот сервер; с аргументом `root` — корпус указанного каталога "
            "(если корпуса нет, он будет создан для каталога, похожего на проект). "
            "Ответ: status = actual | reindexed | created | not_a_project | error."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "root": {
                    "type": "string",
                    "description": "каталог проекта; по умолчанию корень своего корпуса",
                },
                "no_create": {
                    "type": "boolean",
                    "default": False,
                    "description": "не создавать корпус для незнакомого каталога",
                },
                "no_index": {
                    "type": "boolean",
                    "default": False,
                    "description": "только проверить состояние, не индексировать",
                },
                "force": {
                    "type": "boolean",
                    "default": False,
                    "description": "проверить свежесть минуя кэш (сразу после правок)",
                },
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "ragraf_catalog",
        "description": (
            "РЕЕСТР РАЗРАБОТОК: какие проекты индексированы, когда, с какими "
            "контрольными суммами и актуален ли каждый индекс. Показывает, что "
            "разные разработки учитываются раздельно."
        ),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "ragraf_routes",
        "description": "HTTP-маршруты и точки входа с обработчиками.",
        "inputSchema": {
            "type": "object",
            "properties": {"path": {"type": "string"}, "limit": {"type": "integer", "default": 200}},
            "additionalProperties": False,
        },
    },
    {
        "name": "ragraf_live_read",
        "description": (
            "Чтение АКТУАЛЬНОГО файла с указанием, отличается ли он от индекса. "
            "Обязательно, когда drift_report или stale_warnings затронули этот файл."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "start": {"type": "integer"},
                "end": {"type": "integer"},
            },
            "required": ["path"],
            "additionalProperties": False,
        },
    },
    {
        "name": "ragraf_drift_ack",
        "description": (
            "Отметить записи журнала расхождений как учтённые. Записи при этом НЕ удаляются: "
            "история сохраняется."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "entry_ids": {"type": "array", "items": {"type": "integer"}},
                "note": {"type": "string"},
            },
            "required": ["entry_ids"],
            "additionalProperties": False,
        },
    },
    {
        "name": "ragraf_db_schema",
        "description": (
            "СТРУКТУРА ЖИВЫХ БАЗ ДАННЫХ: базы, таблицы, колонки с типами, "
            "индексы, комментарии. Источник — сама СУБД, а не файлы миграций; "
            "для миграций есть отдельный инструмент ragraf_schema. Расхождение "
            "между ними само по себе находка: оно показывает, что миграции и база "
            "разошлись. Колонки, помеченные как персональные данные, присутствуют "
            "в ответе, но их значения не собирались вовсе."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "table": {"type": "string", "description": "только эта таблица"},
                "database": {"type": "string", "description": "только эта база"},
                "limit": {"type": "integer", "default": 200},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "ragraf_db_table",
        "description": (
            "ОДНА ТАБЛИЦА ПОДРОБНО: колонки с типами, индексы и уникальные "
            "ограничения, ключи, комментарии, связи и список колонок, значения "
            "которых скрыты как персональные данные (с причиной). Вызывать, "
            "когда нужно точно знать устройство таблицы."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "table": {"type": "string", "description": "имя таблицы"},
                "database": {"type": "string", "description": "уточнение по базе"},
            },
            "required": ["table"],
            "additionalProperties": False,
        },
    },
    {
        "name": "ragraf_db_relations",
        "description": (
            "СВЯЗИ ТАБЛИЦЫ, РАЗДЕЛЁННЫЕ ПО ПРОИСХОЖДЕНИЮ. «stated» — объявлено в "
            "базе внешним ключом, это ФАКТ. «derived» — выведено по имени колонки "
            "(например `user_id` → `users`), это ВЫВОД с достоверностью не выше "
            "0,5, и ссылаться на него как на структуру нельзя. В живых базах "
            "внешние ключи часто не объявлены, поэтому вывод — единственный способ "
            "увидеть связи, и он всегда помечен."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "table": {"type": "string", "description": "имя таблицы"},
            },
            "required": ["table"],
            "additionalProperties": False,
        },
    },
    {
        "name": "ragraf_db_sample",
        "description": (
            "ПРИМЕР СТРОК ТАБЛИЦЫ: сборный пример, где по каждой колонке взято "
            "первое НЕПУСТОЕ значение из просмотренных строк, плюс домены значений "
            "(статусы, типы) с частотами. Значения колонок, признанных "
            "персональными данными, НЕ СОБИРАЮТСЯ: на их месте пометка «скрыто» — "
            "не считайте такое поле пустым. Пример — СНИМОК на момент индексации: "
            "строки меняются, структура нет."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "table": {"type": "string", "description": "имя таблицы"},
                "database": {"type": "string", "description": "уточнение по базе"},
            },
            "required": ["table"],
            "additionalProperties": False,
        },
    },
    {
        "name": "ragraf_db_drift",
        "description": (
            "ЧТО ИЗМЕНИЛОСЬ В СТРУКТУРЕ БАЗ между ревизиями: добавленные и "
            "удалённые таблицы, колонки, связи. Сравнивается СТРУКТУРА, а не "
            "данные: изменение строк здесь не показывается намеренно. Если "
            "предыдущая ревизия не найдена, ответ это называет — пустой список "
            "изменений тогда ничего не доказывает."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "previous_rev": {"type": "string",
                                 "description": "с чем сравнивать (по умолчанию — предыдущая ревизия)"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "ragraf_db_cross",
        "description": (
            "СВЕРКА «КОД ↔ БАЗА»: «used_missing» — имя таблицы встречается в коде, "
            "но таблицы НЕТ в базе (признак устаревшего кода или неверного "
            "окружения); «unused» — таблица есть в базе, но код её не касается "
            "(кандидат на удаление). Это статическая сверка по именам: она "
            "показывает, куда смотреть, а не что удалять."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
]


# ---------------------------------------------------------------------------
# Классификация инструментов для клиента: `title` и `annotations`.
#
# Зачем. Клиент по протоколу MCP различает «прочитать» и «изменить» ТОЛЬКО по
# полям `annotations` (`readOnlyHint`, `destructiveHint`, `idempotentHint`,
# `openWorldHint`). Пока их не было, для клиента все инструменты выглядели
# одинаково, и он не мог построить политику подтверждений: `ragraf_ensure`
# пересобирает индекс (22 с работы и запись ревизии), но снаружи был неотличим
# от `ragraf_search`. Замерено в сессии HARNESS 13.10.2026.
#
# Таблица отдельная, а не поля в каждом описании, по двум причинам:
#   * одно место правки, и весь набор виден сразу — пропущенный инструмент
#     ловится проверкой `_apply_tool_meta`, а не глазами;
#   * описания инструментов менять нельзя (на имена и схемы завязаны правило
#     `AGENTS.md` §6, навык ragraf-core, AutoClaw и SagaAI), а `annotations` —
#     аддитивное поле.
#
# Что означает каждое поле применительно к RAGRAF:
#   * `readOnlyHint` — не меняет ни индекс, ни состояние сервера. Запись
#     телеметрии о самом вызове сюда не считается: это не данные владельца,
#     и её делают все операции одинаково.
#   * `destructiveHint` — ничего не удаляет и не перезаписывает безвозвратно.
#     Такого нет ни у одного инструмента: `ensure` пересобирает индекс из кода,
#     `drift_ack` только помечает записи (журнал append-only).
#   * `idempotentHint` — повтор с теми же аргументами даёт то же состояние.
#   * `openWorldHint` — выходит за пределы индекса: файловая система, сеть
#     (модель эмбеддингов), внешние СУБД.
# ---------------------------------------------------------------------------
_TOOL_META: dict[str, dict[str, Any]] = {
    "ragraf_index_status": {
        "title": "Состояние индекса: ревизия, полнота, возможности",
        "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True,
    },
    "ragraf_context": {
        "title": "Какой корпус выбран: имя, корень, ревизия, свежесть",
        "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True,
    },
    "ragraf_drift_report": {
        "title": "Журнал расхождений индекса и кода",
        "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True,
    },
    "ragraf_search": {
        "title": "Гибридный поиск по коду и документации",
        "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True,
    },
    "ragraf_search_multi": {
        "title": "Поиск по нескольким запросам с группировкой копий",
        "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True,
    },
    "ragraf_duplicates": {
        "title": "Группы копий файлов и доказательство активности",
        "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True,
    },
    "ragraf_entrypoints": {
        "title": "Точки входа и достижимость файлов",
        "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True,
    },
    "ragraf_dead_code": {
        "title": "Файлы и подпрограммы без ссылок",
        "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True,
    },
    "ragraf_symbol": {
        "title": "Найти символ по имени или полному имени",
        "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True,
    },
    "ragraf_references": {
        "title": "Все места использования символа",
        "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True,
    },
    "ragraf_callers": {
        "title": "Кто вызывает символ",
        "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True,
    },
    "ragraf_diff": {
        "title": "Что изменилось между ревизиями индекса",
        "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True,
    },
    "ragraf_note": {
        "title": "Записать заметку о решении (знание между сессиями)",
        "readOnlyHint": False, "destructiveHint": False, "idempotentHint": False,
    },
    "ragraf_notes": {
        "title": "Прочитать заметки о решениях",
        "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True,
    },
    "ragraf_task_stats": {
        "title": "Отчёт по задаче: обращения к индексу и решения гейта",
        "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True,
    },
    "ragraf_task": {
        "title": "Назвать текущую задачу (RAGRAF_TASK)",
        "readOnlyHint": False, "destructiveHint": False, "idempotentHint": False,
    },
    "ragraf_callees": {
        "title": "Кого вызывает символ",
        "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True,
    },
    "ragraf_impact": {
        "title": "Что затронет правка символа или таблицы",
        "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True,
    },
    "ragraf_slice": {
        "title": "Структурный срез вокруг символа (без тел функций)",
        "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True,
    },
    "ragraf_code": {
        "title": "Тела символов по явному запросу",
        "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True,
    },
    "ragraf_repo_map": {
        "title": "Карта репозитория в заданном бюджете токенов",
        "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True,
    },
    "ragraf_schema": {
        "title": "Схема БД из миграций",
        "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True,
    },
    "ragraf_table_usage": {
        "title": "Кто читает и кто пишет таблицу БД",
        "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True,
    },
    "ragraf_policy_trace": {
        "title": "Трассировка решений о правах",
        "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True,
    },
    "ragraf_routes": {
        "title": "HTTP-маршруты и обработчики",
        "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True,
    },
    "ragraf_class_usage": {
        "title": "Классы оформления: определение, применение, расхождения",
        "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True,
    },
    "ragraf_db_schema": {
        "title": "Схема живой базы данных",
        "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True,
        "openWorldHint": True,
    },
    "ragraf_db_table": {
        "title": "Описание живой таблицы: колонки, ключи, связи",
        "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True,
        "openWorldHint": True,
    },
    "ragraf_db_relations": {
        "title": "Связи живой таблицы (stated и derived)",
        "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True,
        "openWorldHint": True,
    },
    "ragraf_db_sample": {
        "title": "Пример строк живой таблицы без персональных данных",
        "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True,
        "openWorldHint": True,
    },
    "ragraf_db_drift": {
        "title": "Изменения структуры живых баз между ревизиями",
        "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True,
        "openWorldHint": True,
    },
    "ragraf_db_cross": {
        "title": "Сверка «код ↔ база»: какие таблицы упоминает код",
        "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True,
        "openWorldHint": True,
    },
    "ragraf_write_check": {
        "title": "Сверка плана записи со схемой (до SQL)",
        "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True,
    },
    "ragraf_usage": {
        "title": "Статистика обращений к индексу",
        "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True,
    },
    # Свежесть может запустить переиндексацию — это не только чтение.
    "ragraf_freshness": {
        "title": "Свежесть индекса (может пересобрать его)",
        "readOnlyHint": False, "destructiveHint": False, "idempotentHint": True,
        "openWorldHint": True,
    },
    "ragraf_catalog": {
        "title": "Реестр разработок: какие проекты проиндексированы",
        "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True,
    },
    # Переключение корпуса меняет состояние СЕРВЕРА, а не диска.
    "ragraf_use_project": {
        "title": "Выбрать корпус для этого сервера",
        "readOnlyHint": False, "destructiveHint": False, "idempotentHint": True,
    },
    "ragraf_ensure": {
        "title": "Привести корпус в актуальное состояние (пересобрать индекс)",
        "readOnlyHint": False, "destructiveHint": False, "idempotentHint": True,
        "openWorldHint": True,
    },
    "ragraf_live_read": {
        "title": "Прочитать актуальный файл и сравнить с индексом",
        "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True,
        "openWorldHint": True,
    },
    "ragraf_drift_ack": {
        "title": "Отметить записи журнала расхождений учтёнными",
        "readOnlyHint": False, "destructiveHint": False, "idempotentHint": True,
    },
}


# Схема машинного ответа (`structuredContent`). Она общая для всех операций,
# потому что конверт ответа у них один — это контракт проекта, а не совпадение:
# `op`, `index_rev`, `capability`, `provenance`, `found`, `freshness`,
# `stale_warnings`, `drift`, `corpus`, `result`. Поле `result` намеренно без
# ограничений: у каждой операции своя форма, и описывать её в одном месте
# значило бы обещать больше, чем проверяется.
#
# Зачем это нужно. До сих пор ответ уходил ОДНИМ текстовым блоком (JSON плюс
# шапка), и клиент не мог ничего проверить машиной: ни наличие `stale`, ни
# `capability`. Теперь тот же объект приходит ещё и как `structuredContent`.
#
# Ограничение клиента HARNESS, измеренное по его коду: он принимает схему
# только после своей проверки (`assertSupportedJsonSchema`), иначе молча
# отбрасывает её; а модель всё равно видит ТОЛЬКО текстовые блоки
# (`extractText` склеивает блоки `text`). Поэтому схема намеренно простая —
# без объединений типов вида `["string","null"]`, — а факты по-прежнему
# обязаны лежать в `content[].text`.
_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "op": {"type": "string"},
        "index_rev": {},
        "capability": {"type": "string"},
        "provenance": {"type": "string"},
        "found": {"type": "boolean"},
        "result": {},
        "freshness": {},
        "drift": {},
        "stale_warnings": {"type": "array"},
        "corpus": {"type": "object"},
        "error": {},
        "brief": {"type": "boolean"},
    },
    "required": ["op"],
    "additionalProperties": True,
}

# Общий аргумент всех инструментов: убрать служебную часть ответа.
# `brief` НЕ трогает факты (ссылки `path:line`, найденные символы, строки схемы):
# он убирает книжку учёта — суммы, подробности журнала расхождений, длинные
# хвосты предупреждений. Размер ответа ограничивается не «на глаз», а тем, что
# именно выброшено: список выброшенного виден в поле `brief_omitted`.
_BRIEF_PROP: dict[str, Any] = {
    "type": "boolean",
    "default": False,
    "description": (
        "краткий ответ: убрать служебные поля (суммы контрольных сумм, "
        "подробности журнала расхождений, хвост предупреждений), не трогая "
        "факты. Что именно убрано — перечислено в поле brief_omitted"
    ),
}


def _apply_tool_meta() -> list[str]:
    """Проставить `title`, `annotations`, `outputSchema` и аргумент `brief`.

    Возвращает имена инструментов, оставшихся без классификации. Молча отдать
    инструмент без `annotations` нельзя: клиент тогда не отличит запись от
    чтения, а это ровно тот дефект, ради которого таблица заведена. Поэтому
    неполнота видна и в stderr, и в отпечатке сборки (проверка чекера).
    """
    missing: list[str] = []
    for tool in TOOLS:
        name = str(tool.get("name"))
        meta = _TOOL_META.get(name)
        if meta is None:
            missing.append(name)
            tool.setdefault("title", name)
            tool["annotations"] = {}
        else:
            tool["title"] = meta["title"]
            tool["annotations"] = {
                key: meta[key]
                for key in ("readOnlyHint", "destructiveHint", "idempotentHint",
                            "openWorldHint")
                if key in meta
            }
        # Схема вывода и режим `brief` — общие для всех инструментов, поэтому
        # проставляются здесь, а не в тридцати описаниях: расхождение между
        # описаниями и кодом ловится проверкой, а не глазами.
        tool["outputSchema"] = _OUTPUT_SCHEMA
        props = tool.get("inputSchema", {}).get("properties")
        if isinstance(props, dict):
            props.setdefault("brief", dict(_BRIEF_PROP))
    return missing


UNCLASSIFIED_TOOLS = _apply_tool_meta()


def build_fingerprint() -> str:
    """Короткий отпечаток СБОРКИ сервера: содержимое файла + версия пакета.

    Зачем. Измеренное расхождение: в исходнике 30 инструментов, а клиент показал
    24 — живой процесс MCP был старше правки кода, и увидеть это было нечем:
    `serverInfo` нёс только `version` пакета, одинаковую у всех сборок. Теперь
    отпечаток живого процесса можно сравнить с отпечатком файла на диске и
    сразу понять, что процесс устарел.

    Считается по СОДЕРЖИМОМУ файла, а не по времени правки: время меняется при
    копировании и не меняется при откате, а содержимое — всегда.
    """
    digest = hashlib.sha256()
    digest.update(__version__.encode("utf-8"))
    try:
        digest.update(Path(__file__).read_bytes())
    except OSError:
        # Файл недоступен (запуск без исходников): отпечаток остаётся
        # отпечатком версии, а не выдумкой.
        digest.update(b"no-source")
    return digest.hexdigest()[:12]


def _note_contact(agent: str, method: str, tool: str = "",
                  client: str = "") -> None:
    """Записать след обращения клиента.

    `client` — имя из `clientInfo` запроса `initialize`: оно важнее
    параметра запуска, потому что HTTP-сервис поднимается с
    `--agent AutoClaw`.

    Панель показывает по этим следам состояние подключения: сервис поднят, но
    обращений нет (жёлтое), обращение было (зелёное). Сбой учёта не должен
    ломать сервер — поэтому исключение гасится здесь, а не в обработчике.
    """
    try:
        contact_mod.record(agent, method, tool, client_name=client)
    except Exception:
        pass


class McpServer:
    """Обработчик JSON-RPC: чтение строк из stdin, запись ответов в stdout.

    Учёт агента. Сервер должен отвечать на вопрос владельца «какой агент
    использовал RAGRAF». Имя берётся из двух источников, в порядке
    надёжности:

      1. параметр запуска `--agent` (и `--agent-kind`) — задаёт владелец
         в конфигурации MCP, и он же может дать понятное имя;
      2. поле `clientInfo.name` из запроса `initialize` — по протоколу MCP
         клиент обязан себя назвать.

    Раньше не использовалось НИ ОДНО из них: фасад создавался без указания
    себя, и все вызовы агентов попадали в журнал как `unknown` (замер:
    113 из 138 вызовов в корпусе z). Из-за этого фильтрация статистики
    по агенту была невозможна.
    """

    def __init__(self, config_path: str | None, agent: str = "unknown",
                 agent_kind: str = "unknown",
                 access_label: str = "",
                 corpus_source: str = "",
                 watch: bool = True) -> None:
        self.config_path = config_path
        self.cfg: ProjectConfig | None = None
        self._facade: Facade | None = None
        # Имя из параметров запуска: считается приоритетным, потому что
        # задано владельцем явно и не зависит от клиента.
        self.agent_arg = (agent or "").strip() or "unknown"
        self.agent_kind_arg = (agent_kind or "").strip() or "unknown"
        # ACCESS-LABEL-PATCH: метка выданного доступа. Пусто — вызов без доступа
        # (локальный CLI, тесты). Значение доступа здесь НЕ хранится:
        # серверу нужна только метка для статистики.
        self.access_label = (access_label or "").strip()
        # Метка задачи (P0-4): `RAGRAF_TASK` выставляет вызывающий агент на ход
        # работы. Без неё журнал обращений не отвечает на вопрос «на какую
        # задачу ушёл индекс» — все записи из HARNESS шли как «(без задачи)».
        # Читается при создании сервера: переменная окружения — свойство
        # ПРОЦЕССА (как и метка доступа), а не отдельного вызова.
        self.task = (os.environ.get("RAGRAF_TASK") or "").strip()
        # Имя, полученное от клиента в initialize.
        self.agent_client = "unknown"
        self.agent_kind_client = "unknown"
        # Откуда взялся корпус (B1): "arg" — задан владельцем в конфигурации
        # клиента, "auto" — найден по рабочему каталогу процесса, "none" — не
        # выбран вовсе. Это НЕ украшение ответа: конфигурация MCP-клиента задаёт
        # рабочий каталог дочернего процесса (`cwd`), поэтому при "auto" сервер
        # видит каталог СВОЕГО процесса, а не проект сессии. Замерено в HARNESS:
        # `cwd: C:\AutoClaw\RAGRAF`, а задача была о другом проекте — факты о
        # чужом корпусе приходили молча. Теперь источник корпуса виден в каждом
        # ответе, а неподтверждённый корпус называется явно.
        self.corpus_source = (
            (corpus_source or "").strip()
            or ("arg" if (config_path or "").strip() else "none")
        )
        # Имя разработки в реестре для выбранного корпуса: считается один раз
        # (это чтение реестра), а нужно в каждом ответе.
        self._category_cache: str | None = None
        # Сторож свежести в фасаде. True — рабочее поведение: перед работой
        # сверяются суммы и при расхождении индекс пересобирается. False нужен
        # проверкам, которые обязаны быть воспроизводимыми и не запускать
        # индексацию (она занимает десятки секунд и пишет ревизию).
        self.watch_enabled = bool(watch)
        # Корни (roots), если клиент их пришлёт. HARNESS объявляет
        # `capabilities: {}`, поэтому сейчас их не бывает; читать их всё равно
        # надо — клиент, который их отдаёт, решает B1 сам, без угадывания.
        self.roots: list[str] = []
        self.corpus_mismatch: list[str] = []
        # Отпечаток сборки: различить устаревший живой процесс и текущий файл.
        self.build = build_fingerprint()
        # Фоновая переиндексация: идентификатор запроса, отменившего её, и
        # признак того, что отмена запрошена. Нужны для `notifications/cancelled`.
        self._cancelled_reindex: str = ""

    # ---------------- состояние ----------------

    @property
    def agent(self) -> str:
        """Действующее имя агента: параметр запуска важнее clientInfo."""
        if self.agent_arg != "unknown":
            return self.agent_arg
        return self.agent_client

    @property
    def agent_kind(self) -> str:
        """Действующее семейство агента.

        Если владелец не задал явно, семейство ВЫВОДИТСЯ из имени:
        клиенты AutoClaw содержат в clientInfo имя платформы, а всё прочее
        считается внешним агентом. Это нужно, чтобы общая статистика
        «все агенты AutoClaw» работала без ручной настройки каждого клиента.
        """
        if self.agent_kind_arg != "unknown":
            return self.agent_kind_arg
        if self.agent_kind_client != "unknown":
            return self.agent_kind_client
        name = self.agent.lower()
        if "autoclaw" in name or "autoclaw" in name.replace("-", ""):
            return "autoclaw"
        if name == "unknown":
            return "unknown"
        return "external"

    def note_client(self, params: dict) -> None:
        """Запомнить, кто представился в initialize.

        Разбор терпимый: поля может не быть, оно может быть не строкой,
        а имя бывает вида `AutoClaw/2.0` или `autoclaw-desktop`.
        Приводится к короткому виду: отбрасывается всё после разделителя
        версии и лишние пробелы.
        """
        info = params.get("clientInfo")
        if not isinstance(info, dict):
            return
        raw = info.get("name")
        if not isinstance(raw, str) or not raw.strip():
            return
        name = raw.strip()
        # Отбрасывается только хвост ВЕРСИИ: `AutoClaw/1.2.3` → `AutoClaw`.
        #
        # По дефису и пробелу резать НЕЛЬЗЯ: первая версия делала это и
        # превращала `autoclaw-desktop` в `autoclaw`, а `Claude Desktop`
        # в `Claude` — то есть теряла часть имени клиента, по которой
        # владелец мог хотеть различать агентов.
        short = name.split("/", 1)[0].strip()
        self.agent_client = short or "unknown"

    def _reset_facade(self) -> None:
        """Забыть текущий фасад (закрыв его), чтобы следующий вызов создал новый.

        Нужно и при смене корпуса, и после реиндексации: фасад держит дескриптор
        файла графа и кэш ревизии, поэтому без пересоздания следующие операции
        отдавали бы факты прежней ревизии — тот самый дефект, ради которого
        заведён сторож.
        """
        try:
            self.close()
        except Exception:  # noqa: BLE001 — закрытие не должно мешать ответу
            pass
        # Имя разработки для поля `corpus` берётся из реестра и относится к
        # прежнему корпусу — после смены его надо посчитать заново.
        self._category_cache = None

    def set_project(self, config_path: str) -> dict:
        """Переключить сервер на другой корпус, возвратив состояние нового.

        Зачем. У клиента может быть ОДИН долгоживущий процесс сервера на все
        проекты (так делает DeepSeek Harness для профиля): тогда корпус
        выбирается по задаче, а не при запуске. Смена обязана быть видимой:
        в ответе идёт фактическая ревизия и режим нового корпуса — иначе
        агент работал бы по фактам прежнего и не знал об этом.
        """
        self._reset_facade()
        self.config_path = config_path
        self.cfg = None
        # Корпус выбран ЯВНО — это подтверждение, а не догадка по рабочему
        # каталогу процесса (B1). Именно после этого ответы перестают быть
        # «фактами о чужом проекте»: агент назвал проект сам.
        self.corpus_source = "use_project"
        self._category_cache = None
        self._note_corpus_mismatch()
        # Фасад создаётся ленивым свойством уже на новом конфиге; свежесть
        # считается принудительно, чтобы не отдать факты по старому кэшу.
        env = self.facade().op_freshness(force=True)
        status = self.facade().op_index_status()
        data = dict(status.get("result") or {})
        data["config"] = config_path
        data["freshness"] = env.get("result")
        return data

    def config(self) -> ProjectConfig:
        if self.cfg is None:
            if not self.config_path:
                raise ConfigError(
                    "корпус не выбран. Вызовите ragraf_use_project с именем разработки "
                    "(список даёт ragraf_catalog) или с путём к каталогу проекта."
                )
            self.cfg = load_config(self.config_path)
        return self.cfg

    def facade(self) -> Facade:
        if self._facade is None:
            # Агент передаётся в фасад, иначе вызовы не будут учтены
            # по агентам: именно этого не хватало раньше.
            #
            # Метка задачи (`RAGRAF_TASK`) читается ЗДЕСЬ (P0-4). До этой правки
            # её читал только CLI, а MCP-сервер создавал фасад без `task=`,
            # поэтому весь журнал обращений из HARNESS шёл как «(без задачи)» —
            # по нему нельзя было понять, на какой вопрос ушёл индекс.
            self._facade = Facade(
                self.config(), tool="mcp",
                agent=self.agent, agent_kind=self.agent_kind,
                task=self.task,
                token_label=self.access_label,
                watch=self.watch_enabled,
            )
        return self._facade

    def close(self) -> None:
        if self._facade is not None:
            self._facade.close()
            self._facade = None

    # ---------------- транспорт ----------------

    def send(self, message: dict) -> None:
        sys.stdout.write(json.dumps(message, ensure_ascii=False) + "\n")
        sys.stdout.flush()

    def log(self, text: str) -> None:
        """Диагностика идёт в stderr: stdout занят протоколом."""
        sys.stderr.write(f"[ragraf-mcp] {text}\n")
        sys.stderr.flush()

    def run(self) -> int:
        self.log(f"запуск, версия {__version__}, проект {self.config_path}")
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                request = json.loads(line)
            except json.JSONDecodeError as exc:
                self.send(self._error(None, -32700, f"не удалось разобрать JSON: {exc}"))
                continue
            response = self.handle(request)
            if response is not None:
                self.send(response)
        self.close()
        return 0

    # ---------------- обработка ----------------

    def handle(self, request: dict) -> dict | None:
        method = request.get("method")
        req_id = request.get("id")
        params = request.get("params") or {}

        if method in ("notifications/initialized", "initialized"):
            return None
        # Уведомления отмены: клиент сообщает, что запрос больше не нужен.
        # Обрабатывается ровно то, что можно обработать честно: фоновая
        # переиндексация, запущенная этим запросом, не начинается. Уже идущую
        # пересборку рвать нельзя — ревизия пишется в несколько таблиц, и
        # прерывание посередине оставило бы битый индекс (см. `_on_cancelled`).
        if method == "notifications/cancelled":
            return self._on_cancelled(params)
        if method == "notifications/progress":
            # Уведомления о ходе — от клиента к серверу их не бывает; молча
            # принимаем, чтобы не отвечать ошибкой на корректный поток.
            return None
        if method == "initialize":
            # Клиент представляется здесь — имя запоминается для телеметрии.
            # Фасад на этот момент ещё не создан, поэтому агент из clientInfo
            # успевает попасть в журнал с первого же вызова.
            self.note_client(params)
            self.note_roots(params)
            # След подключения: именно это обращение показывает панели, что
            # клиент ДОШЁЛ до RAGRAF, а не только записан в своих настройках.
            _note_contact(self.agent, "initialize",
                          client=str(((params.get("clientInfo") or {})
                                      .get("name")) or ""))
            return self._ok(req_id, self._initialize_result())
        if method == "ping":
            return self._ok(req_id, {})
        if method == "tools/list":
            _note_contact(self.agent, "tools/list")
            return self._ok(req_id, self._tools_list())
        if method == "tools/call":
            _note_contact(self.agent, "tools/call", str(params.get("name") or ""))
            return self._call_tool(req_id, params)
        if method == "resources/list":
            return self._ok(req_id, self._resources_list())
        if method == "resources/templates/list":
            return self._ok(req_id, self._resource_templates_list())
        if method == "resources/read":
            return self._read_resource(req_id, params)
        if method == "prompts/list":
            return self._ok(req_id, self._prompts_list())
        if method == "prompts/get":
            return self._get_prompt(req_id, params)
        return self._error(req_id, -32601, f"метод не поддерживается: {method}")

    # ---------------- initialize, корни, поверхность инструментов ----------------

    def _initialize_result(self) -> dict:
        """Ответ на `initialize`: версия протокола, возможности, отпечаток сборки.

        Три вещи здесь несут смысл, а не заполняют форму:

        * `instructions` — то, что клиент обязан показать агенту. Раньше поля не
          было вовсе, и правило «первым шагом вызови ensure» жило только в
          навыке и в тексте описания инструмента.
        * `serverInfo.build` — отпечаток СОДЕРЖИМОГО файла сервера. Измерено:
          в исходнике 30 инструментов, а клиент показывал 24, потому что живой
          процесс был старше правки кода. Сравнив отпечаток живого процесса с
          `python -c "from ragraf.mcp_server import build_fingerprint; ..."`,
          расхождение видно сразу, а не выясняется догадкой.
        * `serverInfo.tools` — сколько инструментов сервер отдал ФАКТИЧЕСКИ
          (после сверки состава с обработчиками), а не сколько их в списке.
        """
        listed = self._tools_list()
        return {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {
                # `listChanged: False` — честно: состав инструментов статичен,
                # уведомлений об изменении списка сервер не шлёт. Клиент узнаёт
                # о смене сборки по отпечатку, а не по обещанию, которого нет.
                "tools": {"listChanged": False},
                "resources": {"subscribe": False, "listChanged": False},
                "prompts": {"listChanged": False},
            },
            "serverInfo": {
                "name": SERVER_NAME,
                "version": __version__,
                "build": self.build,
                "tools": len(listed["tools"]),
            },
            "instructions": INSTRUCTIONS,
        }

    def note_roots(self, params: dict) -> None:
        """Запомнить корни проекта, если клиент их прислал.

        По протоколу корни приходят либо в `initialize` (поле `roots`), либо
        ответом на запрос `roots/list`, который сервер вправе сделать, только
        если клиент объявил возможность `roots`. HARNESS объявляет
        `capabilities: {}`, поэтому там корней не будет — но клиент, который их
        отдаёт, решает B1 сам: корень и есть проект задачи, и его надо
        предпочесть рабочему каталогу процесса.
        """
        raw = params.get("roots")
        if raw is None:
            return
        items = raw.get("roots") if isinstance(raw, dict) else raw
        if not isinstance(items, list):
            return
        roots: list[str] = []
        for item in items:
            if isinstance(item, dict):
                uri = item.get("uri") or item.get("path")
            else:
                uri = item
            if not isinstance(uri, str) or not uri.strip():
                continue
            path = uri.strip()
            if path.startswith("file://"):
                path = path[7:]
            # Windows-путь в URI приходит как /C:/dir — ведущий слэш отбрасывается,
            # иначе путь не существует ни для одного потребителя.
            if len(path) > 2 and path[0] == "/" and path[2] == ":":
                path = path[1:]
            roots.append(path)
        self.roots = roots
        self._note_corpus_mismatch()

    def _note_corpus_mismatch(self) -> None:
        """Сравнить выбранный корпус с корнями клиента и запомнить расхождение.

        Это и есть серверная часть B1: когда клиент называет проект задачи, а
        сервер работает с другим корпусом, подмена обязана быть видна в КАЖДОМ
        ответе. Молчаливая выдача фактов о чужом проекте — не неудобство:
        по таким фактам принимают решения о коде, которого перед агентом нет.
        """
        self.corpus_mismatch = []
        if not self.roots or not self.config_path:
            return
        current = ""
        try:
            current = str(self.config().root)
        except Exception:  # noqa: BLE001 — конфиг может не читаться; это не повод падать
            return
        if not current:
            return
        from pathlib import PurePath

        cur_norm = str(PurePath(current).as_posix()).rstrip("/").lower()
        for root in self.roots:
            try:
                root_norm = str(PurePath(root).as_posix()).rstrip("/").lower()
            except Exception:  # noqa: BLE001
                continue
            if not root_norm:
                continue
            if root_norm == cur_norm or root_norm.startswith(cur_norm + "/"):
                # Корень задачи внутри корпуса — это нормальный случай.
                continue
            self.corpus_mismatch.append(root)

    def _corpus_block(self) -> dict:
        """Компактное состояние корпуса для поля `corpus` каждого ответа."""
        data: dict[str, Any] = {
            "source": self.corpus_source,
            # Подтверждён — значит назван явно (`--project`) или выбран
            # операцией `use_project`; «auto» — угадан по рабочему каталогу
            # процесса и потому подлежит проверке агентом.
            "confirmed": self.corpus_source in ("arg", "use_project"),
        }
        if self.config_path:
            data["config"] = self.config_path
        try:
            from .watch import category_for

            cfg = self.config()
            data["root"] = str(cfg.root)
            # Имя разработки в реестре — не поле конфига: его знает реестр
            # (`watch.category_for`). Считается один раз на корпус: это чтение
            # реестра, а поле нужно в КАЖДОМ ответе.
            if self._category_cache is None:
                self._category_cache = category_for(cfg)
            data["category"] = self._category_cache
        except Exception as exc:  # noqa: BLE001 — корпус может быть не выбран
            data["error"] = f"{type(exc).__name__}"
        if self.roots:
            data["roots_from_client"] = list(self.roots)
        if self.corpus_mismatch:
            data["mismatch"] = list(self.corpus_mismatch)
        if not data["confirmed"]:
            data["how_to_change"] = (
                "вызовите ragraf_context (какой корпус выбран) и ragraf_use_project "
                "(имя разработки из ragraf_catalog или путь к каталогу проекта)"
            )
        return data

    def _tools_list(self) -> dict:
        """Список инструментов, сверенный с ФАКТИЧЕСКИМИ обработчиками.

        Зачем сверка. `TOOLS` — статический список описаний, `_handlers()` —
        словарь исполняемых обработчиков. Пока их состав совпадает, разницы не
        видно; при расхождении клиент получает инструмент, который отвечает
        «неизвестный инструмент», либо теряет работающий, не узнав об этом.
        Поэтому список строится по обработчикам: клиенту уходит только то, что
        действительно исполняется, а расхождение печатается в stderr.
        """
        handlers = set(self._handlers().keys())
        listed = [tool for tool in TOOLS if str(tool.get("name")) in handlers]
        orphan_handlers = sorted(handlers - {str(t.get("name")) for t in TOOLS})
        dropped = sorted({str(t.get("name")) for t in TOOLS} - handlers)
        if dropped or orphan_handlers or UNCLASSIFIED_TOOLS:
            self.log(
                "поверхность инструментов расходится с обработчиками: "
                f"без обработчика {dropped or '—'}, "
                f"без описания {orphan_handlers or '—'}, "
                f"без классификации {sorted(UNCLASSIFIED_TOOLS) or '—'}"
            )
        return {"tools": listed}

    # ---------------- ресурсы (MCP resources) ----------------

    def _resources_list(self) -> dict:
        """Ресурсы: состояние и реестр — без вызова инструмента и без его цены.

        Зачем. Операции `index_status` и `catalog` отвечают на вопрос «с чем я
        работаю», но стоят вызова инструмента: 3,4 КБ и 10,3 КБ контекста
        (замерено). Ресурс — это тот же ответ, который клиент читает ТОЛЬКО
        когда он ему нужен, и он не занимает место в списке инструментов.
        """
        return {
            "resources": [
                {
                    "uri": STATUS_URI,
                    # Имя ресурса без префикса `ragraf_`: имена ресурсов — не
                    # идентификаторы для клиента (адресует URI), а проверка
                    # чекера считает инструменты по образцу `"name": "ragraf_..."`.
                    # Одинаковый образец в двух разных местах делает счёт
                    # недостоверным — то есть правило перестало бы что-либо
                    # доказывать.
                    "name": "status",
                    "title": "Состояние индекса и корпуса",
                    "description": (
                        "Корпус, корень, ревизия, capability, свежесть. "
                        "Замена вызову ragraf_context без его стоимости."
                    ),
                    "mimeType": "application/json",
                },
                {
                    "uri": CATALOG_URI,
                    "name": "catalog",
                    "title": "Реестр разработок",
                    "description": (
                        "Какие проекты проиндексированы, их ревизии и совпадение "
                        "контрольных сумм."
                    ),
                    "mimeType": "application/json",
                },
                {
                    "uri": CORPUS_MAP_URI.format(name="current"),
                    "name": "corpus_map",
                    "title": "Карта текущего корпуса",
                    "description": (
                        "Модули и файлы корпуса с ключевыми символами в бюджете "
                        "токенов. Для другого корпуса — шаблон ragraf://corpus/{name}/map."
                    ),
                    "mimeType": "application/json",
                },
            ]
        }

    def _resource_templates_list(self) -> dict:
        """Шаблоны ресурсов: корпус задаётся именем разработки или путём."""
        return {
            "resourceTemplates": [
                {
                    "uriTemplate": CORPUS_MAP_URI,
                    "name": "corpus_map",
                    "title": "Карта корпуса по имени разработки",
                    "description": (
                        "Карта модулей корпуса. {name} — имя из ragraf_catalog "
                        "(например ragraf) или путь к каталогу проекта."
                    ),
                    "mimeType": "application/json",
                }
            ]
        }

    def _read_resource(self, req_id: Any, params: dict) -> dict:
        """Прочитать ресурс. Неизвестный URI — честная ошибка, а не пустой ответ."""
        uri = str(params.get("uri") or "").strip()
        if uri == STATUS_URI:
            payload = self._op_context({"probe": False})
            return self._ok(req_id, self._resource_contents(uri, payload))
        if uri == CATALOG_URI:
            payload = self.facade().op_catalog()
            return self._ok(req_id, self._resource_contents(uri, payload))
        prefix = CORPUS_MAP_URI_PREFIX
        if uri.startswith(prefix) and uri.endswith("/map"):
            name = uri[len(prefix):-len("/map")].strip("/")
            payload = self._op_corpus_map(name)
            if payload is None:
                return self._error(
                    req_id, -32602,
                    f"корпус не найден: «{name}». Список разработок даёт ресурс {CATALOG_URI}",
                )
            return self._ok(req_id, self._resource_contents(uri, payload))
        return self._error(
            req_id, -32602,
            f"ресурс не поддерживается: {uri}. Доступны {STATUS_URI}, {CATALOG_URI}, "
            f"{CORPUS_MAP_URI.format(name='имя')}",
        )

    def _resource_contents(self, uri: str, payload: dict) -> dict:
        """Ресурс отдаётся и текстом, и структурированно — как и ответ инструмента."""
        return {
            "contents": [
                {
                    "uri": uri,
                    "mimeType": "application/json",
                    "text": json.dumps(payload, ensure_ascii=False),
                }
            ]
        }

    def _op_corpus_map(self, name: str) -> dict | None:
        """Карта КОНКРЕТНОГО корпуса, не переключая сервер.

        Почему не переключением. `use_project` меняет состояние сервера: после
        него все последующие ответы были бы о другом проекте, а клиент читает
        ресурс ради справки. Поэтому для чужого корпуса создаётся ВРЕМЕННЫЙ
        фасад только для чтения (`watch=False`): он не переиндексирует чужой
        проект и не трогает текущий выбор.
        """
        from .config import load as load_config
        from .catalog import Catalog

        target = (name or "").strip()
        if not target or target == "current":
            return self.facade().op_repo_map(budget_tokens=2000)

        config_path: str | None = None
        try:
            catalog = Catalog()
        except Exception:  # noqa: BLE001 — реестр недоступен: остаётся путь
            catalog = None
        if catalog is not None:
            for category, entry in catalog.entries().items():
                if str(category).lower() == target.lower():
                    config_path = str(entry.get("config") or "")
                    break
            if config_path is None:
                try:
                    category = catalog.category_for_root(Path(target))
                except Exception:  # noqa: BLE001
                    category = None
                if category:
                    entry = catalog.get(category)
                    if entry is not None:
                        config_path = str(entry.config)
        if not config_path:
            candidate = Path(target) / ".ragraf" / "config.yaml"
            config_path = str(candidate) if candidate.is_file() else ""
        if not config_path or not Path(config_path).is_file():
            return None

        peer = Facade(load_config(config_path), tool="mcp", agent=self.agent,
                      agent_kind=self.agent_kind, token_label=self.access_label,
            task=os.environ.get("RAGRAF_TASK", "").strip(),
                      watch=False)
        try:
            data = peer.op_repo_map(budget_tokens=2000)
            data.setdefault("corpus", {})
            data["corpus"]["category"] = peer.cfg.category
            data["corpus"]["root"] = str(peer.cfg.root)
            data["corpus"]["source"] = "resource"
            return data
        finally:
            peer.close()

    # ---------------- подсказки (MCP prompts) ----------------

    def _prompts_list(self) -> dict:
        """Готовые запросы к агенту: проверка индекса и восстановление.

        Живут отдельно от правил проекта, потому что формулировка — часть
        контракта, а не пожелание: `/ragraf-check` требует начать с `ensure` и
        назвать ревизию, `/ragraf-repair` — не выдумывать факты, когда индекса нет.
        """
        return {
            "prompts": [
                {
                    "name": "ragraf-check",
                    "title": "Проверить индекс перед работой",
                    "description": (
                        "Сверить индекс с кодом, назвать корпус и ревизию, "
                        "честно сказать про stale."
                    ),
                    "arguments": [
                        {
                            "name": "project",
                            "description": "проект задачи, если он не совпадает с текущим корпусом",
                            "required": False,
                        }
                    ],
                },
                {
                    "name": "ragraf-repair",
                    "title": "Восстановить работу, когда фактов нет",
                    "description": (
                        "Что делать, если инструменты RAGRAF не отвечают или "
                        "корпус не выбран: порядок восстановления без выдумывания фактов."
                    ),
                    "arguments": [],
                },
            ]
        }

    def _get_prompt(self, req_id: Any, params: dict) -> dict:
        """Текст подсказки. Незнакомое имя — ошибка со списком доступных."""
        name = str(params.get("name") or "").strip()
        args = params.get("arguments") or {}
        if name == "ragraf-check":
            project = str(args.get("project") or "").strip()
            extra = ""
            if project:
                extra = (f"\nЕсли окажется, что выбран другой корпус, вызови "
                         f"ragraf_use_project с project={project!r}.")
            return self._ok(req_id, {
                "description": "Проверка индекса перед работой",
                "messages": [{
                    "role": "user",
                    "content": {
                        "type": "text",
                        "text": (
                            "Перед выводами о коде сделай ровно это:\n"
                            "1. Вызови ragraf_context — назови корпус, корень и ревизию, "
                            "и скажи, подтверждён ли корпус явно.\n"
                            "2. Вызови ragraf_ensure — приведи индекс в актуальное состояние.\n"
                            "3. Если в ответе stale=true, повтори ragraf_ensure и не делай "
                            "выводов по устаревшим фактам.\n"
                            "4. Дальше отвечай по фактам со ссылкой файл:строка; факт без "
                            "ссылки называй догадкой." + extra
                        ),
                    },
                }],
            })
        if name == "ragraf-repair":
            return self._ok(req_id, {
                "description": "Восстановление работы без выдумывания фактов",
                "messages": [{
                    "role": "user",
                    "content": {
                        "type": "text",
                        "text": (
                            "Если инструменты ragraf_* недоступны или отвечают отказом:\n"
                            "1. Вызови ragraf_catalog — какие корпуса вообще есть.\n"
                            "2. Если корпус не выбран, вызови ragraf_use_project с именем "
                            "разработки или путём к каталогу.\n"
                            "3. Если корпуса нет, вызови ragraf_ensure с root — он создаст "
                            "корпус, только если каталог похож на проект, и честно ответит "
                            "not_a_project с причиной.\n"
                            "4. Пока индекс не поднят, НЕ выдавай догадки за факты: скажи "
                            "прямо, что факты не подтверждены индексом."
                        ),
                    },
                }],
            })
        return self._error(
            req_id, -32602,
            f"подсказка не найдена: «{name}». Доступны ragraf-check, ragraf-repair",
        )

    def _on_cancelled(self, params: dict) -> None:
        """Обработать `notifications/cancelled`.

        Что можно отменить честно, а что нельзя. Отменяется ФОНОВАЯ
        переиндексация, ещё не начатая: она помечается и не запускается. Уже
        идущая пересборка НЕ прерывается: ревизия пишется в несколько таблиц
        (graph, coverage, rev_history, реестр), и обрыв посередине оставил бы
        индекс, которому нельзя верить, — то есть отмена навредила бы больше,
        чем помогла. Это ограничение названо прямо, а не спрятано.
        """
        request_id = params.get("requestId")
        self._cancelled_reindex = str(request_id) if request_id is not None else "*"
        self.log(f"запрошена отмена запроса {request_id}: фоновая переиндексация не начнётся")
        return None

    def _call_tool(self, req_id: Any, params: dict) -> dict:
        name = params.get("name")
        args = params.get("arguments") or {}

        # ФТ-15: никаких языков запросов к графу, в том числе через свободный аргумент.
        joined = " ".join(str(v) for v in args.values()
                          if isinstance(v, (str, int, float))).lower()
        for marker in ("match (", "return ", "cpgql", "gremlin", "create (", "merge ("):
            if marker in joined:
                return self._error(
                    req_id, -32602,
                    "язык запросов к графу не поддерживается: используйте фиксированные операции",
                )

        handler = self._handlers().get(name)
        if handler is None:
            return self._ok(
                req_id,
                {"isError": True, "content": [{"type": "text", "text": f"неизвестный инструмент: {name}"}]},
            )
        # Уведомления о ходе отправляются ТОЛЬКО по запросу клиента: у HARNESS
        # незапрошенное уведомление о ходе — ошибка («получено уведомление о
        # ходе для неизвестного токена»), а не подсказка. Замерено по коду
        # клиента 13.10.2026.
        progress_token = (params.get("_meta") or {}).get("progressToken")
        if progress_token is not None:
            self._notify_progress(progress_token, 0.0, f"начато: {name}")
        try:
            result = handler(args)
        except ConfigError as exc:
            # Корпус ещё не выбран (клиент запустил сервер без --project и не
            # вызывал use_project) либо его конфиг не читается. Это штатная
            # ветвь, а не сбой сервера: агент получает причину и подсказку,
            # а не тип исключения и не мёртвый процесс.
            self.log(f"корпус недоступен для {name}: {exc}")
            return self._ok(
                req_id,
                {"isError": True, "content": [{"type": "text", "text": f"{exc}"}]},
            )
        except Exception as exc:  # ошибка операции не должна ронять сервер
            self.log(f"ошибка {name}: {type(exc).__name__}: {exc}")
            if "--debug" in sys.argv:
                traceback.print_exc(file=sys.stderr)
            return self._ok(
                req_id,
                {
                    "isError": True,
                    "content": [{"type": "text", "text": f"ошибка операции {name}: {type(exc).__name__}"}],
                },
            )
        if progress_token is not None:
            self._notify_progress(progress_token, 1.0, f"готово: {name}")
        if isinstance(result, dict):
            # Корпус идёт в КАЖДЫЙ ответ: при источнике "auto" он не
            # подтверждён, и агент обязан это видеть, а не догадываться,
            # о каком проекте ему ответили (B1).
            result.setdefault("corpus", self._corpus_block())
            if args.get("brief"):
                result = self._brief_view(result)
        return self._ok(req_id, self._content(result))

    # ---------------- инструменты ----------------

    def _handlers(self) -> dict[str, Callable[[dict], dict]]:
        f = self.facade
        return {
            "ragraf_index_status": lambda a: f().op_index_status(),
            # Контекст корпуса: единственная операция, которая обязана отвечать
            # даже когда корпус не выбран вовсе (B1).
            "ragraf_context": lambda a: self._op_context(a),
            "ragraf_drift_report": lambda a: f().op_drift_report(
                since=a.get("since"), limit=a.get("limit", 500)
            ),
            "ragraf_search": lambda a: f().op_search(
                a.get("query", ""), limit=a.get("limit", 20),
                with_text=bool(a.get("with_text")),
                text_width=int(a.get("text_width", 160)),
                answer=bool(a.get("answer")),
            ),
            # Поиск «одним ответом» и структура корпуса (P1-1, P1-2).
            "ragraf_search_multi": lambda a: f().op_search_multi(
                a.get("queries") or [], limit=a.get("limit", 20),
                scope=a.get("scope"), regex=bool(a.get("regex")),
                answer=bool(a.get("answer")),
            ),
            "ragraf_duplicates": lambda a: f().op_duplicates(
                scope=a.get("scope"), limit=a.get("limit", 100)
            ),
            "ragraf_entrypoints": lambda a: f().op_entrypoints(
                path=a.get("path"), scope=a.get("scope"), limit=a.get("limit", 200)
            ),
            "ragraf_dead_code": lambda a: f().op_dead_code(
                scope=a.get("scope"), limit=a.get("limit", 200)
            ),
            "ragraf_diff": lambda a: f().op_diff(
                since=a.get("since"), limit=a.get("limit", 50)
            ),
            "ragraf_note": lambda a: f().op_note(
                a.get("text", ""), task=a.get("task", ""),
                tags=list(a.get("tags") or []), path=a.get("path", ""),
            ),
            "ragraf_notes": lambda a: f().op_notes(
                task=a.get("task"), query=a.get("query"), limit=a.get("limit", 50)
            ),
            "ragraf_task_stats": lambda a: f().op_task_stats(
                task=a.get("task"), fmt=a.get("format", "markdown"),
                limit=a.get("limit", 10),
            ),
            "ragraf_task": lambda a: f().op_task_set(
                a.get("name", ""), clear=bool(a.get("clear")),
            ),
            "ragraf_symbol": lambda a: f().op_symbol(
                a.get("name", ""), path=a.get("path"), limit=a.get("limit", 20),
                answer=bool(a.get("answer")),
            ),
            "ragraf_references": lambda a: f().op_references(
                a.get("fqn", ""), limit=a.get("limit", 100)
            ),
            "ragraf_callers": lambda a: f().op_callers(
                a.get("fqn", ""), depth=a.get("depth", 1),
                hard_only=bool(a.get("hard_only")), limit=a.get("limit", 100),
                answer=bool(a.get("answer")),
            ),
            "ragraf_callees": lambda a: f().op_callees(
                a.get("fqn", ""), depth=a.get("depth", 1),
                hard_only=bool(a.get("hard_only")), limit=a.get("limit", 100),
            ),
            "ragraf_impact": lambda a: f().op_impact(
                a.get("target", ""), mode=a.get("mode", "full"),
                depth=a.get("depth", 3), limit=a.get("limit", 200),
                answer=bool(a.get("answer")),
            ),
            "ragraf_slice": lambda a: f().op_slice(
                a.get("fqn", ""), direction=a.get("direction", "back"),
                limit=a.get("limit", 200),
            ),
            "ragraf_code": lambda a: f().op_code(
                fqn=a.get("fqn"), path=a.get("path"),
                style=a.get("style", "compact"), limit=a.get("limit", 5),
                entry_ids=a.get("entry_ids"),
                start=a.get("start"), end=a.get("end"),
                source=a.get("source", "auto"),
                include_containers=bool(a.get("include_containers")),
            ),
            "ragraf_repo_map": lambda a: f().op_repo_map(
                scope=a.get("scope"), budget_tokens=a.get("budget_tokens", 2000),
                detail=a.get("detail", "normal"),
            ),
            "ragraf_schema": lambda a: f().op_schema(
                table=a.get("table"), limit=a.get("limit", 200)
            ),
            "ragraf_table_usage": lambda a: f().op_table_usage(
                a.get("table", ""), limit=a.get("limit", 200), mode=a.get("mode", "all")
            ),
            # Трассировка решений о правах (U6): точки применения и поведение
            # по умолчанию. Дефект с расширением прав был невидим при обычном чтении
            # кода, потому что лежал в большой ветви switch.
            "ragraf_policy_trace": lambda a: f().op_policy_trace(
                a.get("symbol", ""), action=a.get("action"),
                depth=a.get("depth", 3), limit=a.get("limit", 300)
            ),
            "ragraf_routes": lambda a: f().op_routes(
                path=a.get("path"), limit=a.get("limit", 200)
            ),
            # Сверка плана записи со схемой (U1): вызов ДО сохранения закрывает
            # ошибки вроде «Unknown column», которые иначе видны только при запуске.
            # Классы оформления: где определён, где использован, что расходится.
            # 32 класса подсистемы agreements использовались в разметке внутри
            # PHP и не имели ни одного правила в стилях — теперь это видно.
            "ragraf_class_usage": lambda a: f().op_class_usage(
                name=a.get("name"), limit=a.get("limit", 200),
                mode=a.get("mode", "all")
            ),
            # Слой живых баз данных: структура, связи, примеры, сверки.
            # Источник — сама СУБД, а не миграции (для миграций есть ragraf_schema).
            "ragraf_db_schema": lambda a: f().op_db_schema(
                table=a.get("table"), database=a.get("database"),
                limit=a.get("limit", 200),
            ),
            "ragraf_db_table": lambda a: f().op_db_table(
                a.get("table", ""), database=a.get("database"),
            ),
            "ragraf_db_relations": lambda a: f().op_db_relations(a.get("table", "")),
            "ragraf_db_sample": lambda a: f().op_db_sample(
                a.get("table", ""), database=a.get("database"),
            ),
            "ragraf_db_drift": lambda a: f().op_db_drift(
                previous_rev=a.get("previous_rev"),
            ),
            "ragraf_db_cross": lambda a: f().op_db_cross(),
            "ragraf_write_check": lambda a: f().op_write_check(
                a.get("table", ""),
                list(a.get("columns") or []),
                operation=(a.get("operation") or "insert"),
                values=a.get("values"),
            ),
            "ragraf_usage": lambda a: self._op_usage(a),
            # Свежесть индекса и реестр разработок (сторож контрольных сумм).
            "ragraf_freshness": lambda a: f().op_freshness(force=bool(a.get("force"))),
            "ragraf_catalog": lambda a: f().op_catalog(),
            # Приведение корпуса в актуальное состояние: проверяет сумму и
            # пересобирает индекс при расхождении. Вызывается первым шагом.
            "ragraf_use_project": lambda a: self._op_use_project(a),
            "ragraf_ensure": lambda a: self._op_ensure(a),
            "ragraf_live_read": lambda a: f().op_live_read(
                a.get("path", ""), start=a.get("start"), end=a.get("end")
            ),
            "ragraf_drift_ack": lambda a: f().op_drift_ack(
                [int(x) for x in (a.get("entry_ids") or [])], note=a.get("note")
            ),
        }

    def _op_usage(self, args: dict) -> dict:
        """Статистика обращений — в общем конверте ответа.

        Зачем обёртка, а не прямой `usage_summary`. Сводка журнала — «сырой»
        словарь (`calls_total`, `by_agent`, `filter`): её читают CLI
        (`cli.py`), админ-панель (`admin_server.py`) и проверки, и менять её
        форму нельзя. Но ответ ИНСТРУМЕНТА обязан нести конверт контракта
        (`op`, `index_rev`, `capability`, `provenance`, `found`): клиент
        проверяет `outputSchema`, где `op` обязателен, и без него отбрасывает
        ответ ЦЕЛИКОМ. Замерено 13.10.2026 в HARNESS: агент вместо данных
        получал «Structured content does not match the tool's output schema:
        data must have required property 'op'», а шапка текстового блока
        печатала `op=None rev=None capability=None provenance=None found=None`.
        Источник данных остаётся один — `usage_summary`.

        `scope=session` отвечает на вопрос «видит ли журнал МОИ вызовы»: до
        этого проверить его было нечем (замер: 12 вызовов за ход не изменили
        сводку) — B7. Фильтры по агенту: агент может спросить «сколько работал
        я» или «сколько работали все агенты AutoClaw».

        Оговорка о смысле числа, чтобы его не прочитали как «число моих
        вызовов»: журнал считает ОПЕРАЦИИ фасада, а не вызовы инструментов.
        `ragraf_context` читает статус и потому виден как `index_status`;
        `ragraf_ensure` и `ragraf_usage` в журнал не пишутся вовсе. Поэтому
        `calls_in_session` не равен числу обращений агента — различие измерено
        и нарочно не «исправляется» подгонкой числа.
        """
        f = self.facade
        env = f().make_env("usage")
        env.result = f().usage_summary(
            since_session=False,
            agent=args.get("agent"),
            agent_kind=args.get("agent_kind"),
            scope=args.get("scope") or "all",
            session_id=args.get("session_id"),
        )
        env.found = True
        return env.to_dict()

    def _op_use_project(self, args: dict) -> dict:
        """Выбрать корпус по имени разработки в реестре или по каталогу проекта.

        Разрешение намеренно неброское: непонятный корпус — это честный отказ
        с подсказкой, а не выбор «похожего» проекта: агент, работающий не с тем
        корпусом, выдаёт чужие факты за свои.
        """
        from .catalog import Catalog
        from . import ensure as ensure_mod

        target = str(args.get("project") or "").strip()
        if not target:
            env = self.facade().make_env("use_project")
            env.found = False
            env.error = "не указан корпус: нужен аргумент project"
            return env.to_dict()

        config_path: str | None = None
        resolved: str | None = None

        # 1. Имя разработки в реестре. Регистр не важен: имя приходит от человека.
        try:
            catalog = Catalog()
            entries = catalog.entries()
        except Exception:  # noqa: BLE001 — реестр недоступен: пробуем путь
            catalog = None
            entries = {}

        for category, entry in entries.items():
            if str(category).lower() == target.lower():
                candidate = Path(entry.get("config", ""))
                if candidate.is_file():
                    config_path = str(candidate)
                    resolved = str(entry.get("root") or category)
                break

        # 2. Путь к каталогу проекта: корпус берётся из реестра по КОРНЮ, а для
        # чужого проекта с непустым `.ragraf` годится и конфиг рядом с корнем.
        if config_path is None:
            root = Path(target)
            if root.is_dir():
                category = None
                if catalog is not None:
                    try:
                        category = catalog.category_for_root(root)
                    except Exception:  # noqa: BLE001
                        category = None
                if category:
                    entry = catalog.get(category)
                    if entry is not None and Path(entry.config).is_file():
                        config_path = str(entry.config)
                        resolved = str(entry.root)
                if config_path is None:
                    outcome = ensure_mod.ensure(root, create=False, index=False)
                    if outcome.config and Path(outcome.config).is_file():
                        config_path = str(outcome.config)
                        resolved = str(outcome.root)

        if config_path is None:
            env = self.facade().make_env("use_project")
            env.found = False
            env.error = (
                f"корпус не найден: «{target}» нет в реестре и это не каталог проекта. "
                "Список разработок даёт операция catalog."
            )
            return env.to_dict()

        data = self.set_project(config_path)
        t0 = time.perf_counter()
        env = self.facade().make_env("use_project")
        env.result = data
        env.found = True
        if resolved:
            env.result["root"] = resolved
        # Операция меняет корпус сервера и тоже живёт вне фасада: без этой
        # записи нельзя было бы увидеть, каким проектом агент работал.
        try:
            self.facade().record_external_call(
                "use_project", env.to_dict(), (time.perf_counter() - t0) * 1000.0
            )
        except Exception:  # noqa: BLE001
            pass
        return env.to_dict()

    def _op_context(self, args: dict) -> dict:
        """Какой корпус выбран, на каком основании и насколько ему можно верить.

        Зачем отдельная операция (B1). Конфигурация MCP-клиента задаёт рабочий
        каталог дочернего процесса (`cwd`), поэтому корпус определяется по
        каталогу ПРОЦЕССА, а не по проекту задачи. Замерено в HARNESS: `cwd`
        указывал на RAGRAF, задача была о другом проекте, и факты о чужом
        репозитории приходили МОЛЧА. Ни один инструмент не отвечал на вопрос
        «о каком проекте мне сейчас отвечают» — теперь отвечает этот.

        Работает и без выбранного корпуса: это первое, что вызывают, когда
        непонятно, с чем работаешь, поэтому отказ здесь был бы вреден.
        """
        probe = bool(args.get("probe", True))
        payload: dict[str, Any] = {
            "op": "context",
            "index_rev": None,
            "capability": "unknown",
            "provenance": "index",
            "drift": {},
            "limits": [],
            "result": {},
            "stale_warnings": [],
            "found": False,
            "corpus": self._corpus_block(),
        }
        payload["corpus_source"] = self.corpus_source
        payload["corpus_confirmed"] = bool(payload["corpus"].get("confirmed"))
        payload["roots_from_client"] = list(self.roots)
        payload["corpus_mismatch"] = list(self.corpus_mismatch)
        # Отпечаток и число инструментов — здесь тоже: расхождение «в исходнике
        # 30, у клиента 24» объясняется устаревшим живым процессом, и увидеть
        # это можно, только имея отпечаток рядом с ответом.
        payload["build"] = self.build
        payload["tools"] = len(self._tools_list()["tools"])
        if not self.config_path:
            payload["note"] = (
                "корпус не выбран: список разработок даёт ragraf_catalog, "
                "выбор — ragraf_use_project"
            )
            return payload
        try:
            status = self.facade().op_index_status()
        except Exception as exc:  # noqa: BLE001 — контекст обязан ответить всегда
            payload["error"] = f"{type(exc).__name__}: {exc}"
            return payload
        res = status.get("result") or {}
        payload["found"] = True
        payload["index_rev"] = status.get("index_rev")
        payload["capability"] = status.get("capability")
        payload["drift"] = status.get("drift") or {}
        payload["limits"] = status.get("limits") or []
        payload["stale_warnings"] = list(status.get("stale_warnings") or [])
        payload["freshness"] = status.get("freshness")
        payload["result"] = {
            "project": res.get("project"),
            "root": res.get("root"),
            "rev": (res.get("rev") or {}).get("rev_id"),
            "coverage": res.get("coverage"),
            "vector_available": res.get("vector_available"),
            "embedding_model": res.get("embedding_model"),
        }
        if probe:
            try:
                fresh = self.facade().op_freshness(force=bool(args.get("force")))
                payload["freshness"] = fresh.get("result")
                payload["stale"] = bool((fresh.get("result") or {}).get("stale"))
            except Exception as exc:  # noqa: BLE001
                payload["freshness"] = {"checked": False, "stale": True,
                                        "warning": type(exc).__name__}
                payload["stale"] = True
        # Расхождение корней клиента с выбранным корпусом идёт в те же
        # предупреждения: агент читает их одним полем, а не тремя.
        for root in self.corpus_mismatch:
            payload["stale_warnings"].append({
                "path": root,
                "reason": "корень клиента не входит в выбранный корпус",
                "suggest": "ragraf_use_project",
            })
        return payload

    def _op_ensure(self, args: dict) -> dict:
        """Привести корпус в актуальное состояние.

        Своей реализации операции у фасада нет: фасад читает готовый индекс,
        а `ensure` может его изменить. Поэтому вызывается модуль `ensure`
        напрямую, а результат заворачивается в тот же конверт, что отдают
        операции фасада — иначе агент получал бы два разных формата ответа
        на одну и ту же задачу «актуален ли индекс».
        """
        from . import ensure as ensure_mod

        f = self.facade
        root = args.get("root")

        if not root:
            # Без аргумента проверяется СВОЙ корпус: сервер привязан к одному
            # конфигу, и это основной случай — «актуален ли индекс проекта,
            # с которым я работаю».
            outcome = ensure_mod.ensure(
                Path(self.config().root),
                create=False,
                index=not args.get("no_index"),
                force_reindex=bool(args.get("force")),
            )
        else:
            outcome = ensure_mod.ensure(
                Path(root),
                create=not args.get("no_create"),
                index=not args.get("no_index"),
                force_reindex=bool(args.get("force")),
            )

        data = outcome.to_dict()
        t0 = time.perf_counter()
        env = f().make_env("ensure")
        env.result = data
        env.found = data["status"] not in ("not_a_project", "error")
        if not env.found:
            env.extra["note"] = data["reason"]
        # После реиндексации фасад пересоздаётся: он держит дескриптор файла
        # графа и кэш ревизии, поэтому без пересоздания следующие операции
        # отдавали бы факты прежней ревизии — тот самый дефект, ради которого
        # заведён сторож.
        if data["status"] in ("reindexed", "created"):
            try:
                self._reset_facade()
                # Фасад создаётся заново ленивым свойством; свежесть берётся уже
                # у нового экземпляра, поэтому в ответе видна новая ревизия.
                env.freshness = self.facade().op_freshness(force=True).get("result")
            except Exception:  # noqa: BLE001 — пересоздание не должно ронять ответ
                pass
        # Журнал статистики: `ensure` — первый шаг работы агента по правилам,
        # поэтому он обязан быть виден в сводке. Фасадная обёртка его не
        # покрывает (операция живёт в сервере), запись идёт явно.
        try:
            self.facade().record_external_call(
                "ensure", env.to_dict(), (time.perf_counter() - t0) * 1000.0
            )
        except Exception:  # noqa: BLE001
            pass
        return env.to_dict()

    def _notify_progress(self, token: Any, progress: float, message: str) -> None:
        """Уведомление о ходе — ТОЛЬКО по запросу клиента.

        Клиент HARNESS не передаёт `progressToken` (замерено по его коду), а
        незапрошенное уведомление о ходе он превращает в ОШИБКУ «получено
        уведомление о ходе для неизвестного токена». Поэтому механизм
        поддержан, но молчит, пока его не попросят: уведомление без токена
        навредило бы, а не помогло.
        """
        try:
            self.send({
                "jsonrpc": "2.0",
                "method": "notifications/progress",
                "params": {"progressToken": token, "progress": progress,
                           "total": 1.0, "message": message},
            })
        except Exception:  # noqa: BLE001 — уведомление не должно ронять вызов
            pass

    def _brief_view(self, data: dict) -> dict:
        """Убрать служебную часть ответа, не трогая факты.

        Что именно убрано — перечислено в поле `brief_omitted`: «короткий
        ответ» не должен превращаться в «ответ, о котором нельзя сказать, что
        из него выбросили». Факты (ссылки `path:line`, найденные символы, строки
        схемы, шаги обхода) не удаляются ни при каком режиме: ради них операция
        и вызывается. Убирается книжка учёта — суммы контрольных сумм,
        подробности журнала расхождений, хвост предупреждений.

        Повод — замеренные размеры: `catalog` ≈ 10,3 КБ, `index_status` ≈ 3,4 КБ
        одним текстовым блоком, а клиент показывает модели ТОЛЬКО текстовые блоки.
        """
        out = dict(data)
        omitted: list[str] = []

        fresh = out.get("freshness")
        if isinstance(fresh, dict):
            keep = ("checked", "stale", "changed_since_index", "index_present",
                    "rev_id", "reindexed", "from_cache", "reindex_mode",
                    "reindex_rev")
            reduced = {k: fresh.get(k) for k in keep if k in fresh}
            dropped = sorted(k for k in fresh if k not in keep)
            if dropped:
                omitted.append("freshness." + ",freshness.".join(dropped))
            out["freshness"] = reduced

        warnings = out.get("stale_warnings")
        if isinstance(warnings, list) and len(warnings) > 3:
            out["stale_warnings"] = warnings[:3]
            out["stale_warnings_omitted"] = len(warnings) - 3
            omitted.append(f"stale_warnings[{3}:]")

        drift = out.get("drift")
        if isinstance(drift, dict):
            keep_drift = ("entries", "files", "harmless")
            reduced_drift = {k: drift.get(k) for k in keep_drift if k in drift}
            if len(reduced_drift) != len(drift):
                omitted.append("drift:подробности")
            out["drift"] = reduced_drift

        res = out.get("result")
        if isinstance(res, dict) and isinstance(res.get("entries"), list):
            # Реестр разработок: строки реестра — не факты о коде. В кратком
            # ответе достаточно состояния каждой разработки.
            res = dict(res)
            total = len(res["entries"])
            if total > 10:
                res["entries"] = res["entries"][:10]
                res["entries_omitted"] = total - 10
                omitted.append(f"result.entries[10:] из {total}")
            out["result"] = res

        corpus = out.get("corpus")
        if isinstance(corpus, dict):
            keep_corpus = ("category", "root", "source", "confirmed", "mismatch")
            out["corpus"] = {k: corpus.get(k) for k in keep_corpus if k in corpus}

        out["brief"] = True
        out["brief_omitted"] = omitted
        return out

    # ---------------- формат ответов ----------------

    def _content(self, data: dict) -> dict:
        """Ответ инструмента: машиночитаемый JSON + короткая сводка для чтения.

        Сводка ставится первой: агент видит ревизию, режим и предупреждения об
        устаревании ДО самих данных, а не после.
        """
        warnings = data.get("stale_warnings") or []
        parts = [
            f"op={data.get('op')} rev={data.get('index_rev')} "
            f"capability={data.get('capability')} provenance={data.get('provenance')} "
            f"found={data.get('found')}"
        ]
        # Корпус — сразу после конверта: при источнике "auto" он определён по
        # рабочему каталогу ПРОЦЕССА, и молчать об этом нельзя (B1).
        corpus = data.get("corpus") or {}
        if corpus:
            parts.append(
                f"КОРПУС: {corpus.get('category') or '?'} "
                f"({corpus.get('root') or '—'}) источник={corpus.get('source')} "
                f"подтверждён={corpus.get('confirmed')}"
            )
            if not corpus.get("confirmed", True):
                parts.append(
                    "ВНИМАНИЕ: корпус определён по рабочему каталогу процесса, а не "
                    "по проекту задачи. Если задача о другом проекте — вызовите "
                    "ragraf_context, затем ragraf_use_project"
                )
        drift = data.get("drift") or {}
        if drift.get("entries"):
            parts.append(
                f"ВНИМАНИЕ: журнал содержит {drift['entries']} записей о расхождениях "
                f"({drift['files']} файлов) — проверьте поля stale_warnings и при "
                "необходимости вызовите ragraf_live_read"
            )
        for w in warnings[:10]:
            parts.append(f"УСТАРЕЛО: {w.get('path')} ({w.get('reason')}) → {w.get('suggest')}")
        for lim in data.get("limits") or []:
            parts.append(f"ОГРАНИЧЕНИЕ: {lim}")
        if data.get("error"):
            parts.append(f"ОШИБКА: {data['error']}")
        if data.get("brief"):
            parts.append(
                "КРАТКО: выброшено "
                + (", ".join(data.get("brief_omitted") or []) or "ничего")
            )
        summary = "\n".join(parts)
        return {
            "content": [
                {"type": "text", "text": summary},
                {"type": "text", "text": json.dumps(data, ensure_ascii=False)},
            ],
            # Машинная копия того же ответа: клиент валидирует её по
            # `outputSchema` и сохраняет. НО модель видит только текстовые блоки
            # (клиент HARNESS склеивает блоки `text`), поэтому текстовые блоки
            # остаются обязательными и структурой не заменяются.
            "structuredContent": data,
        }

    @staticmethod
    def _ok(req_id: Any, result: dict) -> dict:
        return {"jsonrpc": "2.0", "id": req_id, "result": result}

    @staticmethod
    def _error(req_id: Any, code: int, message: str) -> dict:
        return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def _resolve_default_project() -> Path | None:
    """Найти конфиг корпуса, когда `--project` не задан.

    Зачем. Клиент может запускать сервер без аргументов — так удобнее
    подключать его к любому проекту одним блоком конфигурации. Тогда корпус
    определяется по рабочему каталогу процесса: сначала реестр разработок
    (корпус ищется по КОРНЮ проекта, а не по каталогу, в котором лежит конфиг),
    затем — сам каталог как корень корпуса.

    Второй ветвь нужна для ИНДЕКСИРУЕМОГО проекта: для него конфиг живёт не
    в конфиге RAGRAF, а рядом с кодом, и в реестр корпус попадает только после
    первого `ensure`/`init`. Если каталог не похож на проект, `ensure` вернёт
    `not_a_project`, и мы честно сообщаем об этом, а не выдумываем корпус.

    Ничего не создаётся и не индексируется: это только поиск конфигурации.
    Создание нового корпуса — отдельное решение владельца (команда `ensure`).
    """
    from .catalog import Catalog
    from . import ensure as ensure_mod

    cwd = Path.cwd()
    try:
        catalog = Catalog()
    except Exception:
        catalog = None

    category = None
    if catalog is not None:
        try:
            category = catalog.category_for_root(cwd)
        except Exception:
            category = None
    if category:
        entry = catalog.get(category)
        if entry is not None and entry.config:
            candidate = Path(entry.config)
            if candidate.is_file():
                return candidate

    ok, _reason = ensure_mod.looks_like_project(cwd)
    if not ok:
        return None

    outcome = ensure_mod.ensure(cwd, create=False, index=False)
    if outcome.config:
        candidate = Path(outcome.config)
        if candidate.is_file():
            return candidate
    return None


def main(argv: list[str] | None = None) -> int:
    import argparse

    # Диагностика (лог в stderr) должна быть UTF-8 до всего остального.
    force_utf8_stdio()

    parser = argparse.ArgumentParser(prog="ragraf-mcp", description="MCP-сервер RAGRAF (stdio)")
    parser.add_argument(
        "--project", "-p",
        # Значение можно передать переменной окружения: у клиента AutoClaw
        # (тип «Local program») нет поля рабочего каталога, а есть переменные
        # окружения — без этого корпус определялся по каталогу процесса и мог
        # не найтись (замер 15.10.2026).
        default=(os.environ.get("RAGRAF_PROJECT")
                 or os.environ.get("RAGRAF_CONFIG") or None),
        help="конфигурация корпуса; если не задана, берётся из RAGRAF_PROJECT, "
             "затем из реестра по рабочему каталогу, а если и там нет — "
             "выбирается операцией use_project",
    )
    # Имя агента: используется для статистики «какой агент использовал RAGRAF».
    # Не обязательно: если не задано, имя берётся из clientInfo в initialize,
    # а затем из переменной окружения (клиент может дать урезанное окружение).
    parser.add_argument(
        "--agent", default=os.environ.get("RAGRAF_AGENT", "unknown"),
        help="имя агента для статистики (например AutoClaw); если не задано, "
             "берётся из clientInfo запроса initialize или из RAGRAF_AGENT",
    )
    parser.add_argument(
        "--agent-kind", default=os.environ.get("RAGRAF_AGENT_KIND", "unknown"),
        help="семейство агента: autoclaw, external или internal; если не задано, "
             "определяется по имени",
    )
    parser.add_argument(
        "--access-label", default=os.environ.get("RAGRAF_ACCESS_LABEL", ""),
        help="метка выданного доступа, которым вызван сервер; попадает "
             "в статистику и отвечает на вопрос «кто и по какому праву работал»",
    )
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args(argv)

    project = args.project
    # Источник корпуса различается, потому что цена ошибки разная: `arg` —
    # владелец назвал корпус явно, `auto` — он угадан по рабочему каталогу
    # ПРОЦЕССА сервера, а клиент задаёт этот каталог своей конфигурацией
    # (в HARNESS профиле — `cwd: C:\AutoClaw\RAGRAF`). При `auto` корпус
    # неподтверждён, и это видно в каждом ответе (B1).
    source = "arg" if project else "none"
    if not project:
        found = _resolve_default_project()
        if found is not None:
            project = str(found)
            source = "auto"
        else:
            # Не выход с ошибкой: клиент может работать с НЕСКОЛЬКИМИ проектами
            # одним процессом, и корпус тогда выбирается операцией
            # `ragraf_use_project`. Сервер без корпуса отвечает на catalog,
            # context и use_project и честно отказывает остальным операциям,
            # называя причину, — вместо трассировки и мёртвого процесса.
            sys.stderr.write(
                "[ragraf-mcp] корпус не определён по рабочему каталогу: "
                "он будет выбран операцией ragraf_use_project (список — ragraf_catalog)\n"
            )

    if source == "auto":
        sys.stderr.write(
            f"[ragraf-mcp] корпус определён по рабочему каталогу процесса ({project}) "
            "и НЕ подтверждён: если задача о другом проекте, вызовите ragraf_context "
            "и ragraf_use_project\n"
        )

    return McpServer(project, agent=args.agent,
                     agent_kind=args.agent_kind,
                     access_label=args.access_label,
                     corpus_source=source).run()


if __name__ == "__main__":
    raise SystemExit(main())
