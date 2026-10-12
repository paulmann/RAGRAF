"""MCP-сервер RAGRAF: операции фасада как инструменты агента (ФТ-22).

Протокол: JSON-RPC 2.0 поверх stdio (MCP, версия 2024-11-05).
Реализованы методы: initialize, notifications/initialized, tools/list, tools/call, ping.

Принципиальные ограничения (архитектура §7, ФТ-15):
  * наружу выставляются ТОЛЬКО фиксированные операции фасада;
  * отсутствует инструмент «выполнить запрос к графу» — языков запросов нет;
  * каждый ответ сохраняет конверт с провенансом и пометками устаревших фактов.

Запуск (пример конфигурации MCP-клиента):
    python tools/ragraf-mcp.py --project config/myproject.yaml
"""

from __future__ import annotations

import json
import sys
import traceback
from typing import Any, Callable

from . import __version__
from .config import ProjectConfig, load as load_config
from pathlib import Path

from .facade import Facade

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "ragraf"

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
            },
            "required": ["query"],
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
        "description": "Кто вызывает символ. hard_only=true — только доказанные связи.",
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
            "Нужна для оценки эффекта от использования индекса."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent": {
                    "type": "string",
                    "description": (
                        "только этот агент, например AutoClaw, внешний проект "
                        "или небольшой PHP-корпус (без параметра — все агенты)"
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
]


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

    def __init__(self, config_path: str, agent: str = "unknown",
                 agent_kind: str = "unknown") -> None:
        self.config_path = config_path
        self.cfg: ProjectConfig | None = None
        self._facade: Facade | None = None
        # Имя из параметров запуска: считается приоритетным, потому что
        # задано владельцем явно и не зависит от клиента.
        self.agent_arg = (agent or "").strip() or "unknown"
        self.agent_kind_arg = (agent_kind or "").strip() or "unknown"
        # Имя, полученное от клиента в initialize.
        self.agent_client = "unknown"
        self.agent_kind_client = "unknown"

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

    def config(self) -> ProjectConfig:
        if self.cfg is None:
            self.cfg = load_config(self.config_path)
        return self.cfg

    def facade(self) -> Facade:
        if self._facade is None:
            # Агент передаётся в фасад, иначе вызовы не будут учтены
            # по агентам: именно этого не хватало раньше.
            self._facade = Facade(
                self.config(), tool="mcp",
                agent=self.agent, agent_kind=self.agent_kind,
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
        if method == "initialize":
            # Клиент представляется здесь — имя запоминается для телеметрии.
            # Фасад на этот момент ещ¸ не создан, поэтому агент из clientInfo
            # успевает попасть в журнал с первого же вызова.
            self.note_client(params)
            return self._ok(
                req_id,
                {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": SERVER_NAME, "version": __version__},
                },
            )
        if method == "ping":
            return self._ok(req_id, {})
        if method == "tools/list":
            return self._ok(req_id, {"tools": TOOLS})
        if method == "tools/call":
            return self._call_tool(req_id, params)
        return self._error(req_id, -32601, f"метод не поддерживается: {method}")

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
        try:
            result = handler(args)
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
        return self._ok(req_id, self._content(result))

    # ---------------- инструменты ----------------

    def _handlers(self) -> dict[str, Callable[[dict], dict]]:
        f = self.facade
        return {
            "ragraf_index_status": lambda a: f().op_index_status(),
            "ragraf_drift_report": lambda a: f().op_drift_report(
                since=a.get("since"), limit=a.get("limit", 500)
            ),
            "ragraf_search": lambda a: f().op_search(
                a.get("query", ""), limit=a.get("limit", 20),
                with_text=bool(a.get("with_text")),
                text_width=int(a.get("text_width", 160)),
            ),
            "ragraf_symbol": lambda a: f().op_symbol(
                a.get("name", ""), path=a.get("path"), limit=a.get("limit", 20)
            ),
            "ragraf_references": lambda a: f().op_references(
                a.get("fqn", ""), limit=a.get("limit", 100)
            ),
            "ragraf_callers": lambda a: f().op_callers(
                a.get("fqn", ""), depth=a.get("depth", 1),
                hard_only=bool(a.get("hard_only")), limit=a.get("limit", 100),
            ),
            "ragraf_callees": lambda a: f().op_callees(
                a.get("fqn", ""), depth=a.get("depth", 1),
                hard_only=bool(a.get("hard_only")), limit=a.get("limit", 100),
            ),
            "ragraf_impact": lambda a: f().op_impact(
                a.get("target", ""), mode=a.get("mode", "full"),
                depth=a.get("depth", 3), limit=a.get("limit", 200),
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
            "ragraf_write_check": lambda a: f().op_write_check(
                a.get("table", ""),
                list(a.get("columns") or []),
                operation=(a.get("operation") or "insert"),
            ),
            "ragraf_usage": lambda a: f().usage_summary(
                since_session=False,
                # Фильтры по агенту: агент может спросить «сколько работал я»
                # или «сколько работали все агенты AutoClaw».
                agent=a.get("agent"),
                agent_kind=a.get("agent_kind"),
            ),
            # Свежесть индекса и реестр разработок (сторож контрольных сумм).
            "ragraf_freshness": lambda a: f().op_freshness(force=bool(a.get("force"))),
            "ragraf_catalog": lambda a: f().op_catalog(),
            # Приведение корпуса в актуальное состояние: проверяет сумму и
            # пересобирает индекс при расхождении. Вызывается первым шагом.
            "ragraf_ensure": lambda a: self._op_ensure(a),
            "ragraf_live_read": lambda a: f().op_live_read(
                a.get("path", ""), start=a.get("start"), end=a.get("end")
            ),
            "ragraf_drift_ack": lambda a: f().op_drift_ack(
                [int(x) for x in (a.get("entry_ids") or [])], note=a.get("note")
            ),
        }

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
                self.close()
                # Фасад создаётся заново ленивым свойством; свежесть берётся уже
                # у нового экземпляра, поэтому в ответе видна новая ревизия.
                env.freshness = self.facade().op_freshness(force=True).get("result")
            except Exception:  # noqa: BLE001 — пересоздание не должно ронять ответ
                pass
        return env.to_dict()

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
        summary = "\n".join(parts)
        return {
            "content": [
                {"type": "text", "text": summary},
                {"type": "text", "text": json.dumps(data, ensure_ascii=False)},
            ]
        }

    @staticmethod
    def _ok(req_id: Any, result: dict) -> dict:
        return {"jsonrpc": "2.0", "id": req_id, "result": result}

    @staticmethod
    def _error(req_id: Any, code: int, message: str) -> dict:
        return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="ragraf-mcp", description="MCP-сервер RAGRAF (stdio)")
    parser.add_argument("--project", "-p", required=True, help="конфигурация корпуса")
    # Имя агента: используется для статистики «какой агент использовал RAGRAF».
    # Не обязательно: если не задано, имя берётся из clientInfo в initialize.
    parser.add_argument(
        "--agent", default="unknown",
        help="имя агента для статистики (например AutoClaw); если не задано, "
             "берётся из clientInfo запроса initialize",
    )
    parser.add_argument(
        "--agent-kind", default="unknown",
        help="семейство агента: autoclaw, external или internal; если не задано, "
             "определяется по имени",
    )
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args(argv)
    return McpServer(args.project, agent=args.agent,
                     agent_kind=args.agent_kind).run()


if __name__ == "__main__":
    raise SystemExit(main())
