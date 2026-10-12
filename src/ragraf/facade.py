"""Фасад: 17 операций (ФТ-14..ФТ-23) и единый конверт ответа (ФТ-16).

Конверт обязателен для каждого ответа:
    op, index_rev, capability, drift, provenance, limits, result, stale_warnings

Принципы (архитектура §7):
  * агенту НЕ доступен язык запросов к графу — только фиксированные операции (ФТ-15);
  * каждый факт несёт path:line (ФТ-17);
  * impact разделяет доказанные и эвристические связи (ФТ-18);
  * slice отдаёт структуру без тел функций (ФТ-19), тела — только операция code;
  * при отсутствии релевантного результата отдаётся явный «не найдено» + live_read (ФТ-21).
"""

from __future__ import annotations

import json as _json
import re
import sqlite3
import time
from dataclasses import dataclass, field
from typing import Any

from . import store, util
from .config import ProjectConfig
from .embeddings import Embedder
from .lexvec import LexVecStore
from .revisions import ControlStore
from .telemetry import CHARS_PER_LINE, CHARS_PER_UNIT, CallRecord, Telemetry, normalize_arg


def watch_hashes(cfg: ProjectConfig) -> tuple[str, str, int, dict[str, str]]:
    """Суммы корпуса и конфигурации (обёртка для операций фасада).

    Вынесено отдельной функцией, чтобы фасад не тянул приватные детали модуля
    сторожа: контракт — четыре значения, и он стабилен.
    """
    from .watch import current_hashes

    return current_hashes(cfg)

DEFINED_LIMITS = ["статический анализ; динамика (значения во время выполнения) не покрыта"]


def _snippet_head(raw: str, width: int) -> str:
    """Фрагмент чанка: служебный заголовок плюс первая строка кода, в границах width.

    Формат текста чанка (замерено на rev-0031): строка 0 — заголовок вида
    «function indexed_hashes (src/ragraf/watch.py:151)», строки с 1 — сам код,
    соответствующий `chunk_map.start_line`. Заголовок один давал агенту адрес и вид
    символа, но не содержание: `def f(...)` без тела не отвечает на вопрос
    «тот ли это фрагмент». Заголовок плюс первая строка кода отвечает, и это
    остаётся дешёвым (две строки вместо всего чанка на 1 672 символа).

    Обрезка — по границе слова в пределах первой строки кода: идентификатор,
    разрезанный посередине (`set_te`), агент читает как другое имя.
    """
    lines = raw.splitlines()
    if not lines:
        return ""
    head = lines[0].strip()
    body = next((ln for ln in lines[1:] if ln.strip()), "")
    out = f"{head} — {body.strip()}" if body else head
    if len(out) > width:
        cut = out.rfind(" ", 0, width)
        out = out[: cut if cut > width // 2 else width] + "…"
    return out


def _payload_weight(entry: dict) -> int:
    """Фактическая длина полезной нагрузки ОДНОЙ записи repo_map, в символах.

    Считается тем же сериализатором, которым ответ уходит наружу (UTF-8, без
    экранирования не-ASCII): «символов» на диске и «символов» в ответе — разные
    величины (замер: 4 360 803 байта = 3 389 418 символов, ×1,29 из-за
    кириллицы), и бюджет обязан считаться во вторых, потому что именно они
    попадают в контекст.
    """
    import json as _json

    return len(_json.dumps(entry, ensure_ascii=False))


def _payload_weight_list(entries: list[dict]) -> int:
    """Длина списка записей ровно в том виде, в каком он уходит в ответ.

    Отличие от суммы весов записей — разделители: между записями есть `,`,
    и на 78 файлах это +77 символов. Замер rev-0032: бюджет 6 400 при заявленных
    6 389 и фактических 6 417 — именно из-за непосчитанных разделителей.
    Бюджет, который «почти» соблюдён, не худший случай: агент считает себя
    в пределах лимита, когда уже нет. Поэтому считается целый список.
    """
    import json as _json

    if not entries:
        return 0
    return len(_json.dumps(entries, ensure_ascii=False))


def _drop_nested(spans: list[tuple[str, int, int, str | None]]) -> list[tuple[str, int, int, str | None]]:
    """Убрать спаны, ЦЕЛИКОМ вложенные в другой спан (без частичных пересечений).

    Замер rev-0031 на `src/ragraf/facade.py`: 61 спан, из них 50 вложенных —
    51 метод внутри класса-контейнера `Facade`. Сумма строк со вложенными —
    6 482 при файле в 2 264 строки: 51 метод посчитан дважды (как часть класса
    и сам по себе), и именно это давало Σtext = 180 507 при файле 100 354 (×1,80).

    Границы важны: у `to_dict` и `close` конец СПАДАЕТ с концом контейнера
    (`to_dict` 208-227 внутри `Envelope` 194-227; `close` 2549-2552 внутри
    `Facade` 299-2552), поэтому строгое «конец меньше» их пропускал — дубль
    оставался в выдаче. Здесь вложенность считается нестрого (<=), но только
    при РАЗНЫХ границах: два одинаковых спана — не вложенность, а повтор,
    и лишний из них убирается отдельно по порядку следования.
    """
    keep: list[bool] = [True] * len(spans)
    for i, a in enumerate(spans):
        for j, b in enumerate(spans):
            if i == j or not keep[j]:
                continue
            if a[0] != b[0]:
                continue
            same = a[1] == b[1] and a[2] == b[2]
            if same:
                if j < i:
                    keep[j] = False
            elif a[1] <= b[1] and b[2] <= a[2]:
                keep[j] = False
    return [s for s, k in zip(spans, keep) if k]

LANG_OF_EXT = {".php": "php", ".md": "markdown", ".sql": "sql"}

# Кэш признака DEFAULT по (ревизия, таблица) и корень корпуса для чтения миграций.
_DEFAULT_CACHE: dict[str, set[str]] = {}
_CURRENT_ROOT: str | None = None


def _parse_array_shapes(docblock: str) -> dict[str, dict]:
    """Разобрать psalm-описания массивов из docblock'а: `array{key:type, key?:type}`.

    Возвращает {имя_параметра: {ключи}} — например для
    `@param array{action:string, user_id?:int} $data` будет
    {'data': {'action': {'type': 'string', 'required': True},
              'user_id': {'type': 'int', 'required': False}}}.

    Знак `?` после имени ключа означает необязательность — именно это отличает
    контракт от простого перечисления. Разбор нужен потому, что сигнатура
    `array $data` для агента бесполезна: она не говорит, какие ключи обязательны.
    """
    import re as _re

    out: dict[str, dict] = {}
    # Один @param на строку; массив может переноситься, поэтому берём многострочно.
    for m in _re.finditer(
        r"@param\s+array\s*\{(?P<body>[^}]*)\}\s*\$(?P<name>[A-Za-z_][A-Za-z0-9_]*)",
        docblock,
        _re.DOTALL,
    ):
        keys: dict[str, dict] = {}
        # Из многострочного docblock в текст попадают ведущие символы комментария
        # ("* from:?string"): без очистки они становятся частью имени ключа.
        raw_body = m.group("body")
        raw_body = raw_body.replace("*", " ")
        body = " ".join(raw_body.split())
        for part in body.split(","):
            part = part.strip()
            if not part or ":" not in part:
                continue
            key, _, typ = part.partition(":")
            key = key.strip()
            optional = key.endswith("?")
            key = key.rstrip("?")
            if not key:
                continue
            keys[key] = {"type": typ.strip() or "mixed", "required": not optional}
        if keys:
            out[m.group("name")] = keys
    return out


def summarize_result(data: dict | None) -> tuple[int, int, int]:
    """Сводка по результату операции: (фактов, фактов со строкой, символов).

    Факт — элемент результата, к которому может относиться ссылка на код.
    Фактом со строкой считается тот, у которого есть и путь, и номер строки:
    именно такие ответы агент может проверить по коду, а непроверяемые для нас
    бесполезны (ФТ-17).

    Символы считаются по сериализованному результату: это объём, который агент
    получил из индекса вместо чтения файлов.
    """
    if not data:
        return 0, 0, 0

    try:
        chars = len(_json.dumps(data.get("result"), ensure_ascii=False))
    except Exception:
        chars = 0

    # Разбор результата: либо список фактов, либо словарь с несколькими списками
    # (у impact, table_usage, repo_map факты лежат под разными ключами).
    items: list[dict] = []
    res = data.get("result")
    if isinstance(res, list):
        items = [x for x in res if isinstance(x, dict)]
    elif isinstance(res, dict):
        for key in ("references", "sql_literals", "affected_files", "stated", "derived",
                    "files", "symbols", "columns", "steps", "nodes", "edges",
                    "items", "hits", "matches", "direct", "indirect"):
            val = res.get(key)
            if isinstance(val, list):
                items.extend(x for x in val if isinstance(x, dict))
            elif isinstance(val, dict):
                items.append(val)

    spans = 0
    for item in items:
        path = item.get("path") or item.get("evidence_path") or item.get("file")
        line = item.get("line", item.get("start_line", item.get("evidence_line")))
        if path and line is not None:
            spans += 1
    return len(items), spans, chars


@dataclass
class Envelope:
    op: str
    index_rev: str | None
    capability: str
    drift: dict
    provenance: str
    limits: list[str] = field(default_factory=lambda: list(DEFINED_LIMITS))
    result: Any = None
    stale_warnings: list[dict] = field(default_factory=list)
    found: bool = True
    error: str | None = None
    extra: dict = field(default_factory=dict)
    freshness: dict | None = None

    def to_dict(self) -> dict:
        out: dict[str, Any] = {
            "op": self.op,
            "index_rev": self.index_rev,
            "capability": self.capability,
            "drift": self.drift,
            "provenance": self.provenance,
            "limits": self.limits,
            "result": self.result,
            "stale_warnings": self.stale_warnings,
            "found": self.found,
        }
        if self.error:
            out["error"] = self.error
        # Свежесть идёт рядом с ревизией: агент должен одновременно видеть,
        # на какую ревизию опирается ответ и совпадает ли она с кодом сейчас.
        if self.freshness is not None:
            out["freshness"] = self.freshness
        out.update(self.extra)
        return out


def _column_has_default(rev: str, table: str, column: str) -> bool:
    """Есть ли у колонки значение по умолчанию (по тексту SQL-миграций корпуса).

    Признак нужен, чтобы не объявлять обязательными колонки, которым СУБД сама
    подставляет значение (`Deleted DEFAULT 0`, `Status DEFAULT 'draft'`) — иначе
    проверка выдавала бы ложные «обязательные», и агент перестал бы ей доверять.

    Источник — файлы миграций корпуса, а не граф: SQL-текст в графе отдельным
    видом чанков не хранится (в нём только comment и docblock — замерено),
    а подключение к базе запрещено: индексация работает без СУБД.

    Разбор кэшируется по (ревизия, таблица): список колонок одной таблицы
    проверяется за один проход чтения файла.
    """
    key = f"{rev}|{table.lower()}"
    if key in _DEFAULT_CACHE:
        return column.lower() in _DEFAULT_CACHE[key]

    import re as _re
    from pathlib import Path as _Path

    # Карта колонок с DEFAULT по таблице: собирается один раз на таблицу.
    defaults: set[str] = set()
    autoincrement: set[str] = set()
    try:
        cfg_root = _CURRENT_ROOT
        if cfg_root:
            for path in sorted(_Path(cfg_root).glob("migrations/*.sql")):
                text = path.read_text(encoding="utf-8", errors="replace")
                m = _re.search(
                    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[`\"]?"
                    + _re.escape(table) + r"[`\"]?\s*\(",
                    text, _re.IGNORECASE,
                )
                if not m:
                    continue
                # Тело таблицы — до парной закрывающей скобки.
                depth, i = 0, m.end() - 1
                while i < len(text):
                    if text[i] == "(":
                        depth += 1
                    elif text[i] == ")":
                        depth -= 1
                        if depth == 0:
                            break
                    i += 1
                body = text[m.end():i]
                # Признак AUTO_INCREMENT: в разборе миграций колонка с ним
                # не получает ключ PRI (ключ задаётся отдельной строкой
                # `PRIMARY KEY (ID)`), поэтому проверяется текст строки.
                for line in body.splitlines():
                    cm = _re.match(r"\s*[`\"]?([A-Za-z_][A-Za-z0-9_]*)[`\"]?\s", line)
                    if not cm:
                        continue
                    col_name = cm.group(1).lower()
                    if _re.search(r"\bAUTO_INCREMENT\b", line, _re.IGNORECASE):
                        autoincrement.add(col_name)
                    if _re.search(r"\bDEFAULT\b", line, _re.IGNORECASE):
                        defaults.add(col_name)
    except Exception:
        defaults = set()

    # Колонки с AUTO_INCREMENT тоже считаются «заполняемыми СУБД»: их значение
    # генерирует база, и требовать их от агента нельзя (иначе проверка давала бы
    # ложное «обязательная колонка ID не задана» — найдено прогоном).
    _DEFAULT_CACHE[key] = defaults | autoincrement
    return column.lower() in _DEFAULT_CACHE[key]


class Facade:
    """Все операции чтения. Индекс не изменяется (ФТ-24)."""

    def __init__(self, cfg: ProjectConfig, tool: str = "unknown",
                 telemetry: bool = True, watch: bool = True,
                 agent: str = "unknown", agent_kind: str = "unknown",
                 task: str = "") -> None:
        """Фасад операций чтения.

        Параметры `tool` и `agent` РАЗНЫЕ и не заменяют друг друга:
          * `tool` — канал вызова: mcp, cli, admin, dev, selftest;
          * `agent` — имя агента, который работает с RAGRAF
            (например `AutoClaw`, `небольшой PHP-корпус`, `RdpAudit`);
          * `agent_kind` — семейство: `autoclaw`, `external` или `internal`.

        Разделение нужно для статистики: владелец хочет видеть как каждого
        агента отдельно, так и все агенты AutoClaw вместе. Раньше поле `tool`
        несло обе роли сразу, и отфильтровать по агенту было невозможно.
        """
        self.cfg = cfg
        self.tool = tool
        self.agent = agent
        self.agent_kind = agent_kind
        # Метка задачи: нужна, чтобы собрать вклад RAGRAF в одну задачу
        # пользователя (см. команду `/RAGRAF` в внешний проект).
        self.task = task
        # Корень корпуса нужен разбору DEFAULT в миграциях — запоминается на модуль.
        global _CURRENT_ROOT
        _CURRENT_ROOT = str(cfg.root)
        self.control = ControlStore(cfg)
        self.graph = store.connect(cfg.graph_db, readonly=True)
        self.lexvec = LexVecStore(cfg)
        # Операции чтения НЕ скачивают модель: только кэш (иначе обычный запрос агента
        # неожиданно тянет сотни мегабайт из сети).
        self.embedder = Embedder(cfg, allow_download=False)
        self._rev_cache: dict[str, Any] | None = None
        self._drift_cache: dict | None = None
        self._freshness: dict | None = None
        # Кэш центральности графа на ревизию: считается один раз, а не на
        # каждый вызов карты репозитория.
        self._graph_cache: dict[str, Any] | None = None
        self.category = ""
        self.watch_enabled = watch

        # Сторож контрольных сумм: перед работой сверяется, совпадает ли индекс
        # с кодом, и при расхождении выполняется реиндексация. Требование
        # владельца: агент не должен получать факты о прошлом состоянии кода,
        # не зная об этом. Проверка кэшируется на несколько секунд, поэтому
        # серия вызовов не сканирует корпус каждый раз.
        if self.watch_enabled:
            self._run_guard()

        # Телеметрия: журнал обращений для замера эффекта от внедрения.
        # Отключается парметром telemetry=False для модельных прогонов тестов,
        # чтобы проверки не засоряли статистику.
        self.telemetry = (
            Telemetry(self.control, tool=tool, agent=agent,
                      agent_kind=agent_kind, task=task)
            if telemetry else None
        )
        if self.telemetry is not None:
            self._install_telemetry()

    def _centrality(self, rev: str) -> dict[str, Any]:
        """Центральность узлов по файлам (PageRank на разрежённой матрице).

        Зачем: архитектура (ARCHITECTURE.md, §7, операция `repo_map`) требует
        ранжировать файлы «по связности и по „центральности“ в графе», а код
        сортировал их только по числу символов. Это разные величины: файл с
        многими мелкими символами обгонял файл, на который опирается вся
        остальная кодовая база.

        Считается лениво и кэшируется на ревизию: граф и результат не меняются
        в пределах ревизии (инвариант ФТ-24), поэтому второй вызов бесплатен.
        Любая ошибка расчёта не роняет операцию: карта репозитория не должна
        становиться недоступной из-за отсутствия torch. В этом случае
        возвращается пустая карта оценок и причина — вызывающая сторона обязана
        сказать об этом в `limits`, а не выдать понижение за норму.
        """
        cached = self._graph_cache
        if cached is not None and cached.get("rev") == rev:
            return cached
        result: dict[str, Any] = {
            "rev": rev, "scores": {}, "scores_ordered": [], "status": None, "error": ""
        }
        try:
            from . import graph_algo

            arrays = graph_algo.load_graph(self.graph, rev)
            scores, compute = graph_algo.pagerank(arrays, self.cfg)
            ordered = graph_algo.centrality_by_file(arrays, scores)
            result["scores"] = dict(ordered)
            result["scores_ordered"] = ordered
            result["status"] = compute.to_dict()
            result["nodes"] = arrays.n
            result["edges"] = arrays.m
        except Exception as exc:  # noqa: BLE001 — карта не должна падать из-за графа
            result["error"] = f"{type(exc).__name__}: {exc}"
        self._graph_cache = result
        return result

    def graph_compute_state(self) -> dict[str, Any]:
        """Состояние графового вычислителя — для `index_status`, не для правки.

        Показывает, где считаются графовые алгоритмы и почему (карта, порог
        окупаемости, отсутствие torch), плюс время и объём графа. Без этого
        настройка `graph_gpu` оставалась бы значением без наблюдаемого эффекта.
        """
        rev = self.current_rev()
        if not rev:
            return {}
        data = self._centrality(rev)
        out: dict[str, Any] = {
            "nodes": data.get("nodes", 0),
            "edges": data.get("edges", 0),
            "files_ranked": len(data.get("scores") or {}),
        }
        if data.get("status"):
            out.update(data["status"])
        if data.get("error"):
            out["error"] = data["error"]
        return out

    def _install_telemetry(self) -> None:
        """Обернуть ВСЕ операции фасада записью в журнал.

        Сделано обёрткой, а не правкой 17 методов: так нельзя забыть добавить
        запись в новую операцию. Обёртка ничего не меняет в результате и при
        любой своей ошибке возвращает исходный ответ как есть.
        """
        for name in list(vars(type(self))):
            if not name.startswith("op_") or name.startswith("op__"):
                continue
            method = getattr(self, name)
            if not callable(method):
                continue
            setattr(self, name, self._wrap_op(name, method))

    def _wrap_op(self, name: str, method):
        """Обёртка одной операции: замер времени и запись в журнал."""

        def wrapped(*args, **kwargs):
            t0 = time.perf_counter()
            error: str | None = None
            try:
                data = method(*args, **kwargs)
            except Exception as exc:   # ошибка операции пишется в журнал и прокидывается
                error = f"{type(exc).__name__}"
                self._record(name, 0.0, {}, error, args, kwargs)
                raise
            self._record(name, (time.perf_counter() - t0) * 1000.0, data, None, args, kwargs)
            return data

        return wrapped

    def _record(self, name: str, duration_ms: float, data: dict, error: str | None,
                args: tuple = (), kwargs: dict | None = None) -> None:
        """Записать одну операцию в журнал (никогда не ломает ответ).

        Первичный аргумент сохраняется в виде хеша и короткого дайджеста:
        без него нельзя отличить запрос схемы одной таблицы от другой, а значит
        нельзя измерить эффект улучшений отчёта.
        """
        if self.telemetry is None:
            return
        try:
            facts, spans, chars = summarize_result(data)
            # Первичный аргумент — самая содержательная часть вызова: имя
            # символа, таблицы, поисковый запрос. Для операций без аргумента
            # (index_status, repo_map) он пуст — это норма.
            primary = ""
            if args:
                first = args[0]
                if isinstance(first, (str, int)):
                    primary = str(first)
            arg_hash, arg_digest = normalize_arg(primary)
            self.telemetry.record(
                CallRecord(
                    op=name[3:] if name.startswith("op_") else name,
                    rev_id=data.get("index_rev") if data else self.current_rev(),
                    capability=str(data.get("capability") or self.capability()) if data else self.capability(),
                    duration_ms=round(duration_ms, 3),
                    found=bool(data.get("found")) if data else False,
                    fact_count=facts,
                    facts_with_span=spans,
                    result_chars=chars,
                    error=error,
                    tool=self.tool,
                    arg_hash=arg_hash,
                    arg_digest=arg_digest,
                )
            )
        except Exception:
            pass

    def usage_summary(self, since_session: bool = False,
                      agent: str | None = None,
                      agent_kind: str | None = None,
                      task: str | None = None) -> dict:
        """Сводка использования (операция 18: статистика эффекта от внедрения).

        Три режима, заданных владельцем:
          * без параметров — общая по всем агентам;
          * `agent="AutoClaw"` — только этот агент;
          * `agent_kind="autoclaw"` — все агенты AutoClaw вместе.

        ВАЖНО: чтение сводки НЕ зависит от флага telemetry. Раньше здесь
        стояла проверка `if self.telemetry is None`, и любой вызов с
        telemetry=False (например диагностика или проверка) получал ответ
        «телеметрия отключена» — то есть нельзя было ПРОЧИТАТЬ статистику,
        не записав новую сессию. Это разные действия, и смешивать их нельзя:
        проверка статистики не должна её менять.

        Поэтому сводка читается прямо из журнала через ControlStore.
        """
        # Сводка читается независимо от того, пишется ли текущая сессия.
        if self.telemetry is not None:
            return self.telemetry.summary(
                since_session=since_session, agent=agent,
                agent_kind=agent_kind, task=task,
            )
        # Телеметрия выключена: сводка всё равно читается из журнала —
        # проверка статистики не должна её менять.
        #
        # Раньше здесь создавался «читатель» без вызова _ensure_schema, и это
        # давало падение `no such column: task` на корпусе, где за последнюю
        # миграцию ни один вызов не писал телеметрию: схема журнала создаётся
        # и дополняется только в Telemetry.__init__, а сводка сразу
        # обращается к колонкам agent/agent_kind/task. Замер: на корпусе
        # пилотный PHP-портал usage через фасад с telemetry=False падал,
        # хотя с telemetry=True тот же вызов работал — то есть ответ
        # зависел от служебного флага, а не от данных.
        #
        # Поэтому схема и догоняющая миграция выполняются явно (только они,
        # без старта сессии): чтение статистики не должно добавлять запись
        # в тот журнал, который она же и показывает.
        from .telemetry import Telemetry

        reader = Telemetry.__new__(Telemetry)
        reader.control = self.control
        reader.tool = "reader"
        reader.agent = "reader"
        reader.agent_kind = "internal"
        reader.session_id = ""
        reader._ensure_schema()
        return reader.summary(
            since_session=since_session, agent=agent,
            agent_kind=agent_kind, task=task,
        )

    # ---------------- сторож и реестр ----------------

    def op_freshness(self, force: bool = False) -> dict:
        """20. Свежесть индекса: совпадает ли контрольная сумма корпуса с кодом.

        Смысл операции — дать агенту явный ответ, можно ли доверять фактам
        прямо сейчас. При force=true проверка идёт минуя кэш: это нужно сразу
        после правки кода, чтобы убедиться, что реиндексация сработала.
        """
        env = self.make_env("freshness")
        try:
            from . import watch as watch_mod

            fresh = watch_mod.check(self.cfg, auto_reindex=True, force=force)
            self._freshness = fresh.to_dict()
            env.result = fresh.to_dict()
            # Свежесть подмешивается в сам ответ: операция обязана сообщать
            # состояние индекса так же, как и любая другая — чтобы наличие
            # поля freshness было единообразным во всех ответах.
            env.freshness = self._freshness
            env.found = True
        except Exception as exc:
            env.found = False
            env.error = f"{type(exc).__name__}: {exc}"
        return env.to_dict()

    def op_catalog(self) -> dict:
        """21. Реестр разработок: что индексировано, когда, с какими суммами.

        Ответ на вопрос «разделены ли разработки»: у каждой записи свой корень,
        своя ревизия, своя контрольная сумма. Физически разработки разделены
        каталогами `.ragraf`, реестр лишь даёт сводную картину.
        """
        from .catalog import Catalog, Entry

        env = self.make_env("catalog")
        catalog = Catalog()
        summary = catalog.summary()
        # По каждой разработке дополнительно сверяется сумма: это отвечает,
        # актуален ли её индекс, без запуска индексации.
        for row in summary["entries"]:
            row["hash_matches"] = None
            entry = Entry.from_dict(row)
            if not entry.root or not entry.config:
                continue
            try:
                from pathlib import Path as _Path

                from .config import load as _load

                cfg_path = _Path(entry.config)
                if not cfg_path.exists():
                    row["check_error"] = "конфиг не найден"
                    continue
                peer = _load(cfg_path)
                now_hash, now_config, files_now, _ = watch_hashes(peer)
                row["hash_matches"] = bool(entry.corpus_hash and now_hash == entry.corpus_hash)
                row["files_now"] = files_now
                row["config_matches"] = bool(
                    not entry.config_hash or now_config == entry.config_hash
                )
            except Exception as exc:
                row["check_error"] = type(exc).__name__
        env.result = summary
        # Реестр тоже сообщает состояние своего индекса в том же поле — контракт
        # ответа единый для всех операций.
        env.freshness = self._freshness
        env.found = bool(summary["entries"])
        if not env.found:
            env.extra["suggest"] = ["index"]
            env.extra["note"] = "реестр пуст: ни одна разработка не зарегистрирована"
        return env.to_dict()

    def _run_guard(self) -> None:
        """Сверить контрольные суммы и при необходимости переиндексировать.

        Ошибка сторожа НЕ ломает работу: если проверка или реиндексация не
        удалась, ответ помечается «индекс устарел» с причиной, и агент сам
        решает, доверять ли фактам. Молчаливая выдача старых фактов запрещена
        (ФТ-28, I3): именно она порождает выводы о коде, которого уже нет.
        """
        try:
            from . import watch as watch_mod

            fresh = watch_mod.check(self.cfg, auto_reindex=True)
            self.category = fresh.category
            self._freshness = fresh.to_dict()
            if fresh.reindexed:
                # Индекс перестроен — соединение переоткрывается, иначе фасад
                # продолжит читать прежние файлы базы и отдаст старые факты.
                try:
                    self.graph.close()
                except Exception:
                    pass
                self.graph = store.connect(self.cfg.graph_db, readonly=True)
                self._rev_cache = None
                self._drift_cache = None
        except Exception as exc:
            self._freshness = {
                "checked": False,
                "stale": True,
                "warning": f"проверка свежести не выполнена: {type(exc).__name__}",
            }

    # ---------------- конверт ----------------

    def rev_info(self) -> dict:
        if self._rev_cache is None:
            self._rev_cache = util.read_json(self.cfg.rev_file, default=None) or {}
        return self._rev_cache

    def current_rev(self) -> str | None:
        rev = self.rev_info().get("rev_id")
        return str(rev) if rev else None

    def capability(self) -> str:
        info = self.rev_info()
        cap = info.get("capability")
        return str(cap) if cap else "degraded"

    def capability_joern_used(self) -> bool:
        """Использовался ли граф свойств кода при индексации этой ревизии.

        Нужно, чтобы объяснить отказ операции, не называя режим наугад: у
        неполного режима (`partial`) и у полного отказа (`degraded`) причины
        разные, и текст сообщения обязан их различать.
        """
        return bool(self.rev_info().get("joern_used"))

    def drift_summary(self) -> dict:
        if self._drift_cache is not None:
            return self._drift_cache
        rev = self.current_rev()
        if not rev:
            self._drift_cache = {"files": 0, "symbols": 0, "entries": 0}
            return self._drift_cache
        rows = self.control.conn.execute(
            """SELECT path, symbols_added, symbols_removed, symbols_changed
               FROM drift_entry WHERE index_rev = ?""",
            (rev,),
        ).fetchall()
        paths = {str(r["path"]) for r in rows}
        symbols = 0
        for r in rows:
            for col in ("symbols_added", "symbols_removed", "symbols_changed"):
                val = r[col]
                if val:
                    try:
                        symbols += len(util.loads(val))
                    except Exception:
                        pass
        self._drift_cache = {"files": len(paths), "symbols": symbols, "entries": len(rows)}
        return self._drift_cache

    def stale_paths(self) -> dict[str, dict]:
        rev = self.current_rev()
        if not rev:
            return {}
        rows = self.control.conn.execute(
            """SELECT path, kind, ts, id FROM drift_entry
               WHERE index_rev = ? ORDER BY id DESC""",
            (rev,),
        ).fetchall()
        out: dict[str, dict] = {}
        for r in rows:
            p = str(r["path"])
            if p in out:
                continue
            out[p] = {"path": p, "reason": f"{r['kind']} после {rev}", "since": r["ts"],
                      "entry_id": int(r["id"]), "suggest": "live_read"}
        return out

    def make_env(self, op: str, provenance: str = "index") -> Envelope:
        return Envelope(
            op=op,
            index_rev=self.current_rev(),
            capability=self.capability(),
            drift=self.drift_summary(),
            provenance=provenance,
            freshness=self._freshness,
        )

    def attach_stale(self, env: Envelope, paths: list[str]) -> Envelope:
        """Пометка stale для затронутых путей (ФТ-28). Никогда не молчаливая (I3)."""
        stale = self.stale_paths()
        for p in paths:
            hit = stale.get(util.to_posix(p))
            if hit and hit not in env.stale_warnings:
                env.stale_warnings.append(hit)
        return env

    # ---------------- операции ----------------

    def op_index_status(self) -> dict:
        """13. Состояние ревизии, покрытие, деградация, размер журнала."""
        env = self.make_env("index_status")
        rev = self.current_rev()
        if not rev:
            env.found = False
            env.error = "индекс не создан: выполните ragraf index"
            return env.to_dict()
        cov = self._coverage(rev)
        row = self.graph.execute(
            "SELECT rev_id, parent_rev, created_at, state, capability, joern_used,"
            " file_count, node_count, edge_count, corpus_hash, config_hash, extractor_versions"
            " FROM rev WHERE rev_id = ?",
            (rev,),
        ).fetchone()
        stages = [
            {"stage": r["stage"], "seconds": r["seconds"], "items": r["items"], "note": r["note"]}
            for r in self.graph.execute(
                "SELECT stage, seconds, items, note FROM stage_timing WHERE rev_id = ?"
                " ORDER BY rowid",
                (rev,),
            ).fetchall()
        ]
        lex_counts = self.lexvec.counts(rev)
        env.result = {
            "rev": dict(row) if row else None,
            "coverage": cov,
            "stages": stages,
            "lexvec": lex_counts,
            "vector_available": self.lexvec.vec_ok,
            "embedding_model": self.cfg.embedding.model,
            "drift": self.drift_summary(),
            "project": self.cfg.project,
            "root": util.to_posix(self.cfg.root),
            # Графовый вычислитель: где считаются алгоритмы и почему. Настройка
            # `graph_gpu` без этого поля была бы значением без наблюдаемого
            # эффекта — а так видно, сработала карта, сработал порог или помешал
            # отсутствующий torch.
            "graph_compute": self.graph_compute_state(),
            # U7: показатели качества индекса. Зачем: три дефекта на полигоне
            # (нулевые оценки векторов, чанк в 11 331 токен, потеря docblock)
            # выглядели как «пустой результат», а не как ошибка, и обнаруживались
            # только случайно. Здесь они видны сразу.
            "quality": self._quality(rev),
        }
        env.extra["counts"] = {
            "files": int(row["file_count"]) if row else 0,
            "nodes": int(row["node_count"]) if row else 0,
            "edges": int(row["edge_count"]) if row else 0,
        }
        return env.to_dict()

    def _quality(self, rev: str, samples: int = 64) -> dict:
        """Показатели качества индекса — то, что отличает «пусто» от «сломано».

        Что считается:
          * vectors: нормы векторов (p50/max) и признак нормированности. Если
            нормы далеки от единицы — модель не нормирует векторы, и формула
            перевода расстояния в оценку обязана это учитывать (дефект №1);
          * chunks: размеры чанков, число превысивших лимит и число файлов
            без символов — признаки размытой выдачи (дефект №3);
          * signatures: сколько функций разобрано, сколько сигнатур обрезано и
            сколько имеет нетипизированные параметры (U3, дефект №8);
          * docblocks: сколько docblock'ов попало в индекс — без них контракт
            параметров недостижим (U2).

        Все значения замеряются по графу и лексическому хранилищу.
        """
        out: dict = {}

        # --- векторы ---
        try:
            import math
            import struct

            lv = self.lexvec
            vec_ok = bool(lv.vec_ok)
            norms: list[float] = []
            if vec_ok:
                rows = lv.conn.execute(
                    "SELECT embedding FROM vec_chunk LIMIT ?", (samples,)
                ).fetchall()
                for r in rows:
                    blob = r["embedding"]
                    if isinstance(blob, memoryview):
                        blob = blob.tobytes()
                    n = len(blob) // 4
                    if n <= 0:
                        continue
                    vals = struct.unpack(f"<{n}f", blob[: n * 4])
                    norms.append(math.sqrt(sum(v * v for v in vals)))
            norms.sort()
            p50 = norms[len(norms) // 2] if norms else None
            out["vectors"] = {
                "available": vec_ok,
                "sampled": len(norms),
                "norm_p50": round(p50, 4) if p50 is not None else None,
                "norm_max": round(norms[-1], 4) if norms else None,
                # Нормы около 1 → векторы нормированы, косинус считается упрощённо.
                "normalized": bool(norms) and p50 is not None and abs(p50 - 1.0) < 0.05,
                "note": ("нормы далеки от 1: перевод расстояния в оценку обязан "
                         "учитывать нормы"
                         if p50 is not None and abs(p50 - 1.0) >= 0.05 else ""),
            }
        except Exception as exc:
            out["vectors"] = {"available": False, "error": type(exc).__name__}

        # --- чанки ---
        # ВАЖНО: chunk_map и ft_chunk лежат в ЛЕКСИЧЕСКОЙ базе, а не в графе.
        # Первая версия запрашивала chunk_map через self.graph — запрос падал
        # с OperationalError, и блок чанков возвращал только «error».
        # В интерфейсе это выглядело как «нет данных» вместо показателя,
        # то есть дефект был невидим (найдено при снятии снимка панели).
        #
        # ВТОРОЕ УТОЧНЕНИЕ про единицу измерения. Сначала размер брался как
        # LENGTH(ft_chunk.code_tokens), и это давало чанки «на 44 тысячи токенов»
        # при лимите 8 000. Причина: code_tokens — ТОКЕНИЗИРОВАННАЯ форма текста
        # для поиска FTS5, она в несколько раз длиннее исходника. Честная мера —
        # колонка chunk_map.tokens: это оценка размера самого чанка, и именно
        # она сравнивается с лимитом при чанкинге.
        try:
            row = self.lexvec.conn.execute(
                """SELECT COUNT(*) AS total,
                          COALESCE(MAX(tokens), 0) AS max_tok,
                          SUM(CASE WHEN tokens > 8000 THEN 1 ELSE 0 END) AS over,
                          COALESCE(AVG(tokens), 0) AS avg_tok
                     FROM chunk_map WHERE rev_id = ?""",
                (rev,),
            ).fetchone()
            file_only = self.lexvec.conn.execute(
                """SELECT COUNT(DISTINCT path) AS c FROM chunk_map
                    WHERE rev_id = ? AND source_kind = 'file'""",
                (rev,),
            ).fetchone()
            out["chunks"] = {
                "total": int(row["total"] or 0) if row else 0,
                "max_tokens": int(row["max_tok"] or 0) if row else 0,
                "avg_tokens": int(row["avg_tok"] or 0) if row else 0,
                "over_budget": int(row["over"] or 0) if row else 0,
                "files_without_symbols": int(file_only["c"] or 0) if file_only else 0,
                "budget_tokens": 8000,
                "measure": "chunk_map.tokens: оценка размера чанка при чанкинге",
            }
        except Exception as exc:
            out["chunks"] = {"error": type(exc).__name__}

        # --- сигнатуры и docblock'и ---
        try:
            sym_total = self.graph.execute(
                "SELECT COUNT(*) AS c FROM node WHERE rev_id = ?"
                " AND kind IN ('function','method')",
                (rev,),
            ).fetchone()["c"]
            trunc = self.graph.execute(
                """SELECT COUNT(*) AS c FROM node WHERE rev_id = ?
                    AND kind IN ('function','method')
                    AND props LIKE '%' || char(34) || 'signature_truncated' || char(34) || ': true%'""",
                (rev,),
            ).fetchone()["c"]
            untyped = self.graph.execute(
                """SELECT COUNT(*) AS c FROM node WHERE rev_id = ?
                    AND kind IN ('function','method')
                    AND props LIKE '%' || char(34) || 'type' || char(34) || ': null%'""",
                (rev,),
            ).fetchone()["c"]
            doc_total = self.graph.execute(
                "SELECT COUNT(*) AS c FROM text_chunk WHERE rev_id = ? AND text_kind = 'docblock'",
                (rev,),
            ).fetchone()["c"]
            out["signatures"] = {
                "functions": int(sym_total or 0),
                "truncated": int(trunc or 0),
                "with_untyped_params": int(untyped or 0),
                "docblocks": int(doc_total or 0),
                "note": ("есть обрезанные сигнатуры: текстовая форма теряет параметры, "
                         "используйте signature_params" if trunc else ""),
            }
        except Exception as exc:
            out["signatures"] = {"error": type(exc).__name__}

        # Сводка проблем: клиент должен видеть состояние одним взглядом.
        problems: list[str] = []
        v = out.get("vectors") or {}
        c = out.get("chunks") or {}
        s = out.get("signatures") or {}
        # Ненормированные векторы — НЕ проблема: перевод расстояния в оценку в
        # lexvec уже учитывает нормы (q_norm, c_norm). Первая версия этой
        # проверки помечала такие векторы как неисправность и показывала
        # «здоровье: нет» на обоих рабочих корпусах, где оценки поиска
        # при этом верные (замер: разброс 0.0576, максимум 0.6627).
        # Такое предупреждение хуже отсутствия: администратор начинает искать
        # несуществующую поломку.
        if not v.get("available"):
            problems.append("векторный поиск недоступен: расширение vec0 не загружено")
        if (c.get("over_budget") or 0) > 0:
            problems.append(f"чанков сверх лимита: {c['over_budget']}")
        if (s.get("truncated") or 0) > 0:
            problems.append(f"обрезанных сигнатур: {s['truncated']}")
        if c.get("error"):
            problems.append(f"размеры чанков не прочитаны: {c['error']}")
        if v.get("error"):
            problems.append(f"нормы векторов не прочитаны: {v['error']}")
        out["problems"] = problems
        out["healthy"] = not problems
        # Ненормированность сообщается отдельным полем как СПРАВКА: это не
        # дефект, но полезно знать, что формула обязана учитывать нормы.
        if v.get("available") and v.get("normalized") is False:
            out["notes"] = [
                "векторы не нормированы (норма p50 "
                f"{v.get('norm_p50')}): оценка близости считается через нормы "
                "запроса и чанка, что и делает lexvec"
            ]
        return out

    def op_symbol(self, name: str, path: str | None = None, limit: int = 20) -> dict:
        """2. Символ: вид, файл, строки, сигнатура."""
        env = self.make_env("symbol")
        rev = self.current_rev()
        if not rev:
            return self._no_index(env)
        rows = self._symbol_rows(rev, name, path, limit)
        if not rows:
            env.found = False
            env.extra["suggest"] = ["search", "live_read"]
            env.result = []
            return env.to_dict()
        env.result = rows
        # Контракт параметров из docblock'а (U2). Без этого вызывать функцию,
        # принимающую массив, приходится наугад: сигнатура "array $data" не
        # сообщает состав ключей, а docblock с psalm-описанием
        # `array{action:string, ...}` лежит в индексе рядом.
        # Дефект №8 (TypeError на z_agr_acl_audit) был именно из-за этого.
        self._attach_docblocks(rev, rows)
        self.attach_stale(env, [r["path"] for r in rows])
        return env.to_dict()

    def _attach_docblocks(self, rev: str, rows: list[dict]) -> None:
        """Добавить к символам их docblock и разобранный контракт параметров.

        Docblock в индексе привязан к узлу ФАЙЛА (owner kind = file), поэтому
        раньше он не был достижим по имени функции. Здесь привязка делается по
        позиции: берётся последний docblock в том же файле, который заканчивается
        не позже начала символа (тот же приём, что уже применяется к комментариям).

        Разбор `array{...}` покрывает psalm-формат @param — основной способ
        описания массивов в этом проекте. Если описания нет, добавляется поле
        `params_hint` с прямым указанием, что состав массива в индексе отсутствует:
        молчаливая неполнота недопустима.
        """
        import re as _re

        for row in rows:
            path = str(row.get("path") or "")
            start = int(row.get("start_line") or 0)
            if not path or start <= 0:
                continue
            try:
                blocks = self.graph.execute(
                    """SELECT raw, start_line, end_line FROM text_chunk
                        WHERE rev_id = ? AND path = ? AND text_kind = 'docblock'
                          AND end_line < ?
                        ORDER BY end_line DESC LIMIT 1""",
                    (rev, path, start),
                ).fetchall()
            except Exception:
                continue
            if not blocks:
                continue
            raw = str(blocks[0]["raw"])
            row["doc"] = raw.strip()[:1200]
            shape = _parse_array_shapes(raw)
            if shape:
                row["array_shape"] = shape
            else:
                row["params_hint"] = (
                    "состав параметров-массивов в индексе не описан: проверьте docblock "
                    "или тело функции перед вызовом"
                )

    def op_references(self, fqn: str, limit: int = 100) -> dict:
        """3. Все места использования символа."""
        env = self.make_env("references")
        rev = self.current_rev()
        if not rev:
            return self._no_index(env)
        node_ids = [r["node_id"] for r in self._symbol_rows(rev, fqn, None, 5)]
        if not node_ids:
            env.found = False
            env.result = []
            env.extra["suggest"] = ["symbol", "search"]
            return env.to_dict()
        marks = ",".join("?" for _ in node_ids)
        rows = self.graph.execute(
            f"""SELECT src, kind, provenance, evidence_path, evidence_line, confidence, dst
                FROM edge WHERE rev_id = ? AND dst IN ({marks})
                  AND kind IN ('calls','calls_heuristic','instantiates','extends','implements',
                               'references','overrides')
                ORDER BY evidence_path, evidence_line LIMIT ?""",
            (rev, *node_ids, limit),
        ).fetchall()
        out = []
        for r in rows:
            owner = self.graph.execute(
                "SELECT node_id, kind, name, fqn, path, start_line, end_line FROM node"
                " WHERE rev_id = ? AND node_id = ?",
                (rev, r["src"]),
            ).fetchone()
            out.append(
                {
                    "from": dict(owner) if owner else {"node_id": r["src"]},
                    "kind": r["kind"],
                    "provenance": r["provenance"],
                    "evidence": f"{r['evidence_path']}:{r['evidence_line']}",
                    "confidence": r["confidence"],
                }
            )
        if not out:
            env.found = False
            env.extra["suggest"] = ["search", "live_read"]
        env.result = out
        self.attach_stale(env, [str(r["evidence_path"]) for r in rows if r["evidence_path"]])
        return env.to_dict()

    def op_callers(self, fqn: str, depth: int = 1, hard_only: bool = False, limit: int = 200) -> dict:
        """4. Кто вызывает."""
        return self._graph_walk(fqn, depth, hard_only, "callers", limit)

    def op_callees(self, fqn: str, depth: int = 1, hard_only: bool = False, limit: int = 200) -> dict:
        """5. Кого вызывает."""
        return self._graph_walk(fqn, depth, hard_only, "callees", limit)

    def op_impact(self, target: str, mode: str = "full", depth: int = 3, limit: int = 500) -> dict:
        """6. Что затронет изменение. Разделение доказанных и эвристических связей (ФТ-18)."""
        env = self.make_env("impact")
        rev = self.current_rev()
        if not rev:
            return self._no_index(env)

        # Цель может быть таблицей БД, файлом или символом.
        table_target = None
        if target.startswith("table:") or target.lower() in self._table_names(rev):
            table_target = target.split(":", 1)[-1].lower()

        if table_target:
            return self._impact_table(env, rev, table_target, mode)

        rows = self._symbol_rows(rev, target, None, 5)
        if not rows:
            env.found = False
            env.error = f"символ не найден: {target}"
            env.extra["suggest"] = ["symbol", "search", "live_read"]
            return env.to_dict()

        roots = [r["node_id"] for r in rows]
        affected = self._bfs(rev, roots, depth, reverse=True, limit=limit)

        hard: list[dict] = []
        soft: list[dict] = []
        files: dict[str, dict] = {}
        for item in affected:
            if item["provenance"] == "stated":
                hard.append(item)
            else:
                soft.append(item)
            p = item.get("path")
            if p:
                slot = files.setdefault(p, {"path": p, "stated": 0, "derived": 0})
                slot[item["provenance"]] += 1

        env.result = {
            "target": rows[0]["fqn"],
            "mode": mode,
            "affected_files": sorted(files.values(), key=lambda x: (-x["stated"], x["path"])),
            "stated": hard,
            "derived": soft,
            "affected_count": {"stated": len(hard), "derived": len(soft)},
        }
        if mode == "hard":
            env.result["derived"] = []
        self.attach_stale(env, [f["path"] for f in env.result["affected_files"]])
        return env.to_dict()

    def op_slice(self, fqn: str, direction: str = "back", limit: int = 200) -> dict:
        """7. Структурный срез: узлы+рёбра+строки, БЕЗ тел функций (ФТ-19)."""
        env = self.make_env("slice")
        rev = self.current_rev()
        if not rev:
            return self._no_index(env)
        rows = self._symbol_rows(rev, fqn, None, 3)
        if not rows:
            env.found = False
            env.error = f"символ не найден: {fqn}"
            return env.to_dict()
        if self.capability() != "full":
            # Сообщение называет ФАКТИЧЕСКИЙ режим, а не «degraded» по умолчанию.
            # Прежде текст был зашит, и ревизия в режиме `partial` (свойства кода
            # не построены, но подтверждённые вызовы в графе есть) получала
            # сообщение «режим degraded» — отчёт расходился со своими данными.
            # Порядок причин: сначала признак использования CPG, потом режим.
            cap = self.capability()
            env.found = False
            env.error = (
                f"срез недоступен: ревизия в режиме {cap}: полные свойства кода "
                "(PDG/DDG) не построены"
            )
            env.extra["capability"] = cap
            env.extra["capability_reason"] = (
                "Joern не использован при индексации"
                if not self.capability_joern_used()
                else f"экспорт графа свойств кода не удался (режим {cap})"
            )
            return env.to_dict()

        roots = [r["node_id"] for r in rows]
        reverse = direction == "back"
        steps = self._bfs(rev, roots, 4, reverse=reverse, limit=limit)
        env.result = {
            "target": rows[0]["fqn"],
            "direction": direction,
            "nodes": steps,
            "node_count": len(steps),
            "note": "тела символов не включены: используйте операцию code",
        }
        self.attach_stale(env, [s["path"] for s in steps if s.get("path")])
        return env.to_dict()

    def op_code(self, fqn: str | None = None, path: str | None = None,
                style: str = "compact", limit: int = 5,
                entry_ids: str | None = None, start: int | None = None,
                end: int | None = None, source: str = "auto",
                include_containers: bool = False) -> dict:
        """8. Тела символов — только по явному запросу.

        Изменения по итогам замеров rev-0030 (каждое закрывает измеренный дефект):

        * `entry_ids` — адрес второго шага. Хит поиска отдаёт `chunk_id` и
          `node_id`, но раньше их НЕЛЬЗЯ было использовать: вызов с одним
          из них отвечал «нужен параметр fqn или path», и агент шёл искать
          файл заново. Теперь идентификатор хита разрешается в спаны.

        * `start`/`end` — окно внутри найденных спанов. Раньше эти параметры
          принимались, но игнорировались: `code --path X --start 151 --end 190`
          отвечал тем же, что без диапазона.

        * Двойной счёт. Замер: `code --path src/ragraf/facade.py` отдавал
          Σtext = 180 507 при файле в 100 354 символа (×1,80): класс-контейнер
          `Facade 243-2264` шёл целиком И все его 51 метод отдельно. Теперь
          спаны, вложенные в другой спан, не выдаются — вместо них идёт
          счётчик `nested_dropped`. Флаг `include_containers=True` возвращает
          прежнее поведение тому, кому нужен файл целиком одним спаном.

        * Молчаливый обрез. Лимит применялся к запросу (`LIMIT ?`), и о том,
          что спанов больше, ответ молчал. Теперь в нём `truncated` и
          `spans_total`.

        * Источник текста. По умолчанию текст берётся ИЗ ИНДЕКСА (таблица
          чанков), а не с диска: тогда чтение не зависит от того, менялся ли
          файл после индексации. Если текста в индексе нет, берётся диск, и это
          видно в поле `text_source` каждого спана: `index` либо `disk`.
        """
        env = self.make_env("code", provenance="live")
        rev = self.current_rev()
        if not rev:
            return self._no_index(env)
        spans: list[tuple[str, int, int, str | None]] = []
        unresolved: list[str] = []
        via_node = 0
        via_chunk = 0

        if entry_ids:
            import re as _re

            raws = [x for x in _re.split(r"[,\s]+", str(entry_ids)) if x]
            spans, unresolved, via_node, via_chunk = self._resolve_entry_ids(rev, raws)
        elif fqn:
            for r in self._symbol_rows(rev, fqn, None, limit):
                spans.append((str(r["path"]), int(r["start_line"]), int(r["end_line"]), r["fqn"]))
        elif path:
            rows = self.graph.execute(
                """SELECT path, start_line, end_line, fqn FROM node
                   WHERE rev_id = ? AND path = ? AND kind IN ('function','method','class')
                   ORDER BY start_line, end_line""",
                (rev, util.to_posix(path)),
            ).fetchall()
            all_spans = [
                (str(r["path"]), int(r["start_line"]), int(r["end_line"]), r["fqn"])
                for r in rows
            ]
            spans = _drop_nested(all_spans) if not include_containers else all_spans
            dropped_nested = len(all_spans) - len(spans)
            env.extra["spans_total"] = len(all_spans)
            env.extra["nested_dropped"] = dropped_nested
            if include_containers:
                env.limits.append(
                    "include_containers=true: вложенные спаны выданы, но контейнер "
                    "сокращён до своей шапки (строки до первого дочернего спана), "
                    "чтобы один и тот же код не приехал дважды"
                )
            elif dropped_nested:
                env.limits.append(
                    f"вложенные спаны не выданы: {dropped_nested} из {len(all_spans)} "
                    "(класс-контейнер и его методы давали текст дважды); "
                    "запросить их можно через include_containers=true"
                )
            if len(spans) > limit:
                env.extra["truncated"] = True
                env.extra["spans_returned"] = limit
                env.limits.append(
                    f"спанов в файле больше лимита: показано {limit} из {len(spans)}; "
                    "увеличьте limit или задайте окно start/end"
                )
                spans = spans[:limit]
            # Нумерация после отсечения: индексы нужны для адресации повторного
            # вызова, и они должны соответствовать тому, что реально выдано.
            env.extra["spans"] = [
                {"index": i, "path": p, "start_line": s, "end_line": e, "fqn": f}
                for i, (p, s, e, f) in enumerate(spans)
            ]
        else:
            env.found = False
            env.error = "нужен параметр fqn, path или entry_ids"
            return env.to_dict()

        if entry_ids and not spans:
            env.found = False
            env.error = "идентификаторы не разрешились в спаны"
            env.extra["unresolved"] = unresolved
            env.extra["suggest"] = ["search", "symbol"]
            return env.to_dict()
        if entry_ids:
            env.extra["resolved"] = {
                "by_node_id": via_node,
                "by_chunk_id": via_chunk,
                "unresolved": unresolved,
            }

        # Текст спанов. Берём из индекса; там, где его нет, — с диска.
        # Вложенность: спан, внутри которого есть ДРУГИЕ выданные спаны, отдаётся
        # только своей «шапкой» — строками до первого дочернего спана. Иначе
        # класс-контейнер повторил бы все свои методы, и один и тот же код приехал
        # бы дважды (замер: Σtext 180 507 при файле 100 354).
        from_index: dict[int, tuple[int, str]] = {}
        if source != "disk":
            for i, (rel, span_start, span_end, _name) in enumerate(spans):
                children = [
                    s for j, (_p2, s, _e2, _f2) in enumerate(spans)
                    if j != i and s > span_start and spans[j][0] == rel
                ]
                cut = min(children) - 1 if children else span_end
                if cut > span_end:
                    cut = span_end
                got = self.lexvec.text_for_span(rev, rel, span_start, cut)
                if got is not None:
                    from_index[i] = got

        out = []
        stale = self.stale_paths()
        for idx, (rel, span_start, span_end, name) in enumerate(spans):
            lo = span_start if start is None else max(span_start, int(start))
            hi = span_end if end is None else min(span_end, int(end))
            if hi < lo:
                out.append({
                    "path": rel,
                    "symbol": name,
                    "start_line": span_start,
                    "end_line": span_end,
                    "error": "запрошенное окно вне спана",
                })
                continue
            text = None
            text_source = "disk"
            covered = 0
            got = from_index.get(idx)
            if got is not None:
                chunk_start, chunk_text = got
                lines = chunk_text.splitlines()
                off_lo = lo - chunk_start
                off_hi = hi - chunk_start + 1
                if 0 <= off_lo < len(lines):
                    seg = lines[off_lo:max(off_hi, off_lo + 1)]
                    if seg:
                        text = "\n".join(seg)
                        text_source = "index"
            if text is None:
                abs_path = self.cfg.root / rel
                if not abs_path.exists():
                    out.append({"path": rel, "error": "файл отсутствует в рабочем дереве"})
                    continue
                lines = abs_path.read_text(encoding="utf-8", errors="replace").splitlines()
                seg = lines[max(0, lo - 1) : hi]
                text = "\n".join(seg)
                covered = len(seg)
            else:
                covered = len(text.splitlines())
            wanted = max(0, hi - lo + 1)
            if style == "signatures":
                text = text.splitlines()[0] if text else ""
                covered = min(covered, 1)
            changed = rel in stale
            item = {
                "index": idx,
                "path": rel,
                "symbol": name,
                "start_line": span_start,
                "end_line": span_end,
                "window": {"start": lo, "end": hi},
                "changed_since_index": changed,
                "text_source": text_source,
                "text": text,
            }
            # Пробелы в индексе — не ошибка, но и не то же самое, что полный текст:
            # модульный код между символами чанком не является (замер rev-0029: чанки
            # покрывают 65 311 из 86 177 строк). Помечаем явно, чтобы агент знал,
            # что видит не весь файл, а только его индексированные куски.
            if covered < wanted:
                item["gaps"] = {
                    "lines_in_index": covered,
                    "lines_requested": wanted,
                    "note": "в индексе нет чанков для части строк (модульный код между символами)",
                }
                env.extra.setdefault("gaps", 0)
                env.extra["gaps"] = int(env.extra["gaps"]) + 1
            out.append(item)
        env.result = out
        # Провенанс — по фактическому источнику текста, а не по способу вызова.
        # Раньше `code` безусловно объявлял `live`, даже когда читал файл,
        # а не индекс; теперь значение соответствует тому, откуда взято
        # содержимое: index (из индекса), live (всё с диска), mixed (и то и другое).
        real = [o for o in out if o.get("text_source")]
        if not real:
            env.provenance = "live"
        elif all(o["text_source"] == "index" for o in real) and not any(
            o.get("changed_since_index") for o in real
        ):
            env.provenance = "index"
        elif all(o["text_source"] == "disk" for o in real):
            env.provenance = "live"
        else:
            env.provenance = "mixed"
        if any(o.get("text_source") == "disk" for o in real):
            env.limits.append(
                "часть текста взята с диска: в индексе нет сохранённого текста этих чанков"
            )
        self.attach_stale(env, [o["path"] for o in out if o.get("path")])
        return env.to_dict()

    def op_repo_map(self, scope: str | None = None, budget_tokens: int = 2000,
                    detail: str = "normal") -> dict:
        """9. Карта репозитория: модули → файлы → ключевые символы, в бюджете токенов."""
        env = self.make_env("repo_map")
        rev = self.current_rev()
        if not rev:
            return self._no_index(env)
        rows = self.graph.execute(
            """SELECT path, COUNT(*) AS symbols FROM node
               WHERE rev_id = ? AND kind IN ('class','function','method')
               GROUP BY path ORDER BY symbols DESC""",
            (rev,),
        ).fetchall()

        # Ранжирование. Архитектура требует центральности в графе; число
        # символов — запасной вариант, когда графовый расчёт недоступен.
        # Разница не косметическая: центральный файл мог иметь мало символов,
        # но от него зависит остальной код, и именно его нужно показать первым.
        central = self._centrality(rev)
        scores: dict[str, float] = central.get("scores") or {}
        if scores:
            rows = sorted(
                rows, key=lambda r: (-scores.get(str(r["path"]), 0.0), str(r["path"]))
            )
            ranking = "centrality"
        else:
            ranking = "symbols"
            if central.get("error"):
                env.limits.append(
                    "центральность в графе не посчитана, ранг по числу символов: "
                    + str(central["error"])
                )
        files: list[dict] = []
        budget_chars = max(400, int(budget_tokens * 3.2))
        used = 0
        # Замер rev-0030: `chars_used` считался по СИНТЕТИЧЕСКОЙ строке
        # «путь (N симв.): имена[строки]», которой в ответе нет — в JSON уходит
        # структурный список `top`. При бюджете 2000 токенов заявлено 6 304
        # символа (якобы 98% бюджета), а фактический результат — 30 772 символа,
        # в 4,9 раза больше заявленного, и самый тяжёлый ответ корпуса
        # (24 655 симв./вызов). Агент, доверившись заявленному числу, считал
        # ответ уложившимся в бюджет, хотя он был вдвое больше запрошенного.
        #
        # Теперь считаем фактическую полезную нагрузку: сумму сериализованной
        # записи файла. Заявленное число обязано быть не меньше фактического,
        # иначе это не бюджет, а метка.
        for r in rows:
            p = str(r["path"])
            if scope and not p.startswith(util.to_posix(scope)):
                continue
            top = self.graph.execute(
                """SELECT name, kind, start_line, fqn FROM node
                   WHERE rev_id = ? AND path = ? AND kind IN ('class','function','method')
                   ORDER BY kind, start_line LIMIT ?""",
                (rev, p, 4 if detail == "normal" else 8),
            ).fetchall()
            entry = {
                "path": p,
                "line": 1,   # файл начинается со строки 1: ссылка всегда локализуема
                "symbols": int(r["symbols"]),
                "top": [dict(t) for t in top],
            }
            weight = _payload_weight_list(files + [entry])
            if files and weight > budget_chars:
                break
            used = weight
            files.append(entry)
        env.result = {
            "scope": scope or "весь корпус",
            "files": files,
            "files_included": len(files),
            "files_total": len(rows),
            "chars_used": used,
            "budget_chars": budget_chars,
            "budget_tokens": budget_tokens,
            "budget_note": (
                "chars_used — фактический объём полезной нагрузки (тот же формат, "
                "в котором уходит список файлов); объём ответа больше на его оболочку"
            ),
            # Чем ранжировали и почему — часть контракта: «порядок файлов» без
            # указания способа нельзя ни повторить, ни опровергнуть.
            "ranking": ranking,
            "ranking_note": (
                "по центральности в графе (PageRank на разрежённой матрице)"
                if ranking == "centrality"
                else "по числу символов: графовая центральность недоступна"
            ),
        }
        if scores:
            env.result["top_centrality"] = [
                {"path": p, "score": round(v, 8)} for p, v in central["scores_ordered"][:10]
            ]
            env.result["graph_compute"] = central.get("status")
        env.extra["budget_ok"] = used <= budget_chars
        return env.to_dict()

    def op_search(self, query: str, limit: int = 20, kinds: list[str] | None = None,
                  with_text: bool = False, text_width: int = 160) -> dict:
        """1. Гибридный поиск: лексика + вектор (RRF), каждый хит с провенансом.

        `with_text=True` добавляет к каждому хиту поле `snippet` — начало сырого
        текста чанка ИЗ ИНДЕКСА. Замер rev-0030: без этого поля за текстом
        приходилось идти вторым вызовом (`code`/`live_read`), то есть 20 хитов
        стоили 20 чтений файлов. Текст берётся одним пакетным запросом к
        хранилищу чанков (lexvec.chunk_texts), а не циклом по хитам.

        Текста может не быть (прежняя ревизия, чужой корпус): тогда поле не
        появляется вовсе, а число таких хитов названо в `limits` — отсутствие
        текста видно, а не замаскировано пустой строкой.
        """
        env = self.make_env("search")
        rev = self.current_rev()
        if not rev:
            return self._no_index(env)
        vector = None
        if self.embedder.available():
            try:
                vector = self.embedder.embed_one(query)
            except Exception:
                vector = None
        hits = self.lexvec.search_hybrid(rev, query, vector, limit=limit)
        if kinds:
            hits = [h for h in hits if h.source_kind in kinds]
        out = []
        stale = self.stale_paths()
        for h in hits:
            item = {
                "chunk_id": h.chunk_id,
                "path": h.path,
                "node_id": h.node_id,
                "start_line": h.start_line,
                "end_line": h.end_line,
                "source_kind": h.source_kind,
                "score": h.score,
                "why": h.why,
            }
            # Роль документа отдаётся только там, где она есть. Поле не
            # добавляется пустым: агент должен видеть признак, а не догадываться,
            # что пустая строка означает «не документ».
            if getattr(h, "doc_role", ""):
                from .docrole import ROLE_TITLES

                item["doc_role"] = h.doc_role
                item["doc_role_title"] = ROLE_TITLES.get(h.doc_role, h.doc_role)
            if h.path in stale:
                item["stale"] = True
            out.append(item)
        env.result = out
        env.found = bool(out)
        if with_text and out:
            texts = self.lexvec.chunk_texts(rev, [str(o["chunk_id"]) for o in out])
            missing = 0
            for item in out:
                raw = texts.get(str(item["chunk_id"]))
                if raw is None:
                    missing += 1
                    continue
                item["snippet"] = _snippet_head(raw, text_width)
            env.extra["text_from_index"] = True
            env.extra["snippet_width"] = int(text_width)
            if missing:
                env.limits.append(
                    f"сниппет недоступен для {missing} из {len(out)} хитов: "
                    "в индексе нет сохранённого текста этих чанков"
                )
        if not out:
            env.extra["suggest"] = ["repo_map", "symbol", "live_read"]
        env.extra["vector_used"] = bool(vector)
        env.extra["vector_available"] = self.lexvec.vec_ok
        self.attach_stale(env, [h.path for h in hits])
        return env.to_dict()

    def op_live_read(self, path: str, start: int | None = None, end: int | None = None,
                     context: int = 5) -> dict:
        """16. Чтение актуального файла с выделением отличий от индекса (ФТ-31)."""
        env = self.make_env("live_read", provenance="live")
        rev = self.current_rev()
        # Пустой путь раньше превращался в корень корпуса: `root / ""` — это
        # каталог, он существует, а `read_bytes()` на каталоге давал
        # PermissionError. Наружу уходила «ошибка выполнения» без объяснения,
        # хотя причина — отсутствующий адрес. Проверка пути и того, что это
        # файл, ставится до чтения.
        if not (path or "").strip():
            env.found = False
            env.error = "не указан путь файла"
            env.extra["suggest"] = ["repo_map", "search"]
            return env.to_dict()
        abs_path = self.cfg.root / util.to_posix(path)
        try:
            resolved = abs_path.resolve()
            resolved.relative_to(self.cfg.root.resolve())
        except (ValueError, OSError):
            env.found = False
            env.error = f"путь вне корпуса: {path}"
            return env.to_dict()
        if not resolved.exists():
            env.found = False
            env.error = f"файл не найден: {path}"
            return env.to_dict()
        if resolved.is_dir():
            # Каталог — не файл: говорим об этом прямо, а не отдаём отказ
            # доступа, который читается как проблема прав.
            env.found = False
            env.error = f"это каталог, а не файл: {path}"
            env.extra["suggest"] = ["repo_map"]
            return env.to_dict()

        data = resolved.read_bytes()
        sha_now = util.sha256_bytes(data)
        sha_index = None
        if rev:
            r = self.graph.execute(
                "SELECT sha256, lines FROM file WHERE rev_id = ? AND path = ?",
                (rev, util.to_posix(path)),
            ).fetchone()
            sha_index = str(r["sha256"]) if r else None

        text = data.decode("utf-8", errors="replace")
        lines = text.splitlines()
        if start is None:
            start = 1
        if end is None:
            end = min(len(lines), start + 400)
        seg = lines[max(0, start - 1) : end]
        env.result = {
            "path": util.to_posix(path),
            "requested": {"start": start, "end": end},
            "total_lines": len(lines),
            "text": "\n".join(seg),
            "sha256_now": sha_now,
            "sha256_index": sha_index,
            "changed_since_index": sha_index is not None and sha_now != sha_index,
        }
        if sha_index is None:
            env.extra["in_index"] = False
            env.limits.append("файл отсутствует в индексе: вывод опирается только на текущее содержимое")
        else:
            env.extra["in_index"] = True
        if env.result["changed_since_index"]:
            env.stale_warnings.append(
                {"path": util.to_posix(path), "reason": f"файл изменён после {rev}",
                 "since": self.drift_summary().get("last_ts"), "suggest": "live_read"}
            )
            env.provenance = "mixed"
        return env.to_dict()

    def op_drift_report(self, since: int | None = None, limit: int = 500) -> dict:
        """14. Журнал расхождений."""
        env = self.make_env("drift_report")
        rev = self.current_rev()
        sql = "SELECT id, index_rev, ts, path, kind, old_hash, new_hash, symbols_added," \
              " symbols_removed, symbols_changed, edges_invalidated, detected_by, commit_sha," \
              " ack, note FROM drift_entry"
        params: list[Any] = []
        where = []
        if rev:
            where.append("index_rev = ?")
            params.append(rev)
        if since is not None:
            where.append("id > ?")
            params.append(since)
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        rows = self.control.conn.execute(sql, tuple(params)).fetchall()
        out = []
        for r in rows:
            item = dict(r)
            for col in ("symbols_added", "symbols_removed", "symbols_changed"):
                if item.get(col):
                    try:
                        item[col] = util.loads(item[col])
                    except Exception:
                        item[col] = []
            item["ack"] = util.loads(item["ack"]) if item.get("ack") else None
            out.append(item)
        env.result = out
        env.found = bool(out)
        env.extra["summary"] = self.drift_summary()
        if not out:
            env.extra["suggest"] = ["index_status"]
            env.limits.append("журнал пуст: индекс соответствует рабочему дереву")
        return env.to_dict()

    def op_schema(self, table: str | None = None, limit: int = 200) -> dict:
        """10. Схема БД из графа (M2)."""
        env = self.make_env("schema")
        rev = self.current_rev()
        if not rev:
            return self._no_index(env)
        if not store.table_exists(self.graph, "db_table"):
            env.found = False
            env.error = "слой схемы БД не построен (этап M2)"
            return env.to_dict()
        params: list[Any] = [rev]
        sql = "SELECT table_id, table_name, engine, row_estimate, source FROM db_table WHERE rev_id = ?"
        if table:
            sql += " AND table_name = ?"
            params.append(table)
        sql += " LIMIT ?"
        params.append(limit)
        rows = self.graph.execute(sql, tuple(params)).fetchall()
        out = []
        for r in rows:
            cols = self.graph.execute(
                "SELECT column_name, data_type, is_nullable, column_key FROM db_column"
                " WHERE rev_id = ? AND table_id = ? ORDER BY ordinal",
                (rev, r["table_id"]),
            ).fetchall()
            out.append({**dict(r), "columns": [dict(c) for c in cols]})
        env.result = out
        env.found = bool(out)
        return env.to_dict()

    def op_table_usage(self, table: str, limit: int = 200, mode: str = "all") -> dict:
        """11. Кто читает и кто пишет таблицу (U4, U5).

        Что изменилось по итогам исследования GAPS_FOR_LLM:
          * U4: в каждом факте есть `write` и `access` (select/insert/update/
            delete/ddl). Раньше признак вычислялся при извлечении и выбрасывался,
            поэтому на вопрос «где именно идёт вставка» ответить было нечем;
          * U5: таблицы, которых НЕТ в схеме из миграций, выносятся отдельным
            списком `unresolved_tables`. Причина: регулярка имени таблицы
            срабатывает на конструкциях языка (CREATE TABLE, <table> в разметке),
            и в выдаче появлялись фантомные имена вида `value`, `hash`, `ugroup`,
            смешанные с подтверждёнными таблицами. Доверие к операции от этого
            падало, а различие «подтверждено схемой» и «похоже на таблицу» важно.

        Параметр mode: all — всё, read — только читающие обращения,
        write — только изменяющие.
        """
        env = self.make_env("table_usage")
        rev = self.current_rev()
        if not rev:
            return self._no_index(env)
        if mode not in ("all", "read", "write"):
            env.found = False
            env.error = "неизвестный режим: ожидается all, read или write"
            return env.to_dict()

        # Схема из миграций: по ней решается, подтверждена таблица или нет.
        known = self._known_tables(rev)

        dst = f"@table:{table.lower()}"
        rows = self.graph.execute(
            """SELECT src, kind, provenance, evidence_path, evidence_line, confidence, props
               FROM edge WHERE rev_id = ? AND dst = ? LIMIT ?""",
            (rev, dst, limit),
        ).fetchall()
        out = []
        for r in rows:
            owner = self.graph.execute(
                "SELECT name, fqn, kind, path, start_line FROM node WHERE rev_id = ? AND node_id = ?",
                (rev, r["src"]),
            ).fetchone()
            edge_props: dict = {}
            try:
                edge_props = util.loads(r["props"]) if r["props"] else {}
            except Exception:
                edge_props = {}
            access = str(edge_props.get("access") or "")
            is_write = bool(edge_props.get("write"))
            # Фильтр по виду доступа, если он запрошен.
            if mode == "read" and is_write:
                continue
            if mode == "write" and not is_write:
                continue
            evidence = f"{r['evidence_path']}:{r['evidence_line']}"
            # Плоские path/line: агент должен получать ссылку в том же виде, что и
            # в других операциях. Без этого проверяемость ответа падала до нуля
            # (найдено замером на полигоне: 29 фактов, ни одного со ссылкой).
            item = {
                "path": r["evidence_path"],
                "line": r["evidence_line"],
                "symbol": dict(owner) if owner else None,
                "kind": r["kind"],
                "provenance": r["provenance"],
                "evidence": evidence,
            }
            # Поля доступа добавляются только при наличии данных: пустая строка
            # читалась бы как «в индексе нет признака», хотя он есть.
            if access:
                item["write"] = is_write
                item["access"] = access
            out.append(item)

        # Список таблиц, на которые есть обращения, но которых нет в схеме.
        unresolved = [] if table.lower() in known else [
            {
                "name": table,
                "reason": "обращения в коде есть, но в схеме из миграций таблица не найдена",
                "hint": "возможная опечатка в SQL, обращение к системной таблице или неполнота разбора миграций",
            }
        ]

        env.result = {
            "table": table,
            "table_in_schema": table.lower() in known,
            "references": out,
            "read_count": sum(1 for x in out if not x.get("write")),
            "write_count": sum(1 for x in out if x.get("write")),
            "unresolved_tables": unresolved,
        }
        env.found = bool(out)
        if not env.found:
            env.extra["suggest"] = ["schema", "write_check", "search"]
            env.extra["note"] = (
                "обращений к таблице в коде не найдено; проверьте имя через schema"
                if table.lower() in known
                else "таблицы нет в схеме из миграций — проверьте имя и написание"
            )
        self.attach_stale(env, [str(x["path"]) for x in out if x["path"]])
        return env.to_dict()

    def _known_tables(self, rev: str) -> set[str]:
        """Набор таблиц из схемы (миграции) в нижнем регистре.

        Нужен, чтобы отличать подтверждённые таблицы от фантомных имён
        (U5): регулярка имени таблицы срабатывает и на конструкциях языка.
        """
        try:
            rows = self.graph.execute(
                "SELECT table_name FROM db_table WHERE rev_id = ?", (rev,)
            ).fetchall()
            return {str(r["table_name"]).lower() for r in rows}
        except Exception:
            return set()

    def op_policy_trace(self, symbol: str, action: str | None = None,
                        depth: int = 3, limit: int = 300) -> dict:
        """22. Трассировка решений о правах (U6).

        Зачем операция. Самый дорогой дефект на полигоне (№9 в отчёте по
        разработке) нашёлся только проверкой под реальной ролью: роль
        `employee` получала права `approve`, `mark_paid`, `sign`, `archive`,
        потому что ветвь `switch` возвращала `true` всем, кому выдан журнал.
        Никакой замер навигации такое не показывает, а глазами в большом
        `switch` это теряется.

        Что делает операция: по символу, принимающему решение о правах,
        собирает все места, где возвращается разрешение, вместе с условиями,
        и все места, где вызывается проверка этого символа.

        Что возвращает:
          * allows: ветви, возвращающие разрешение, — файл, строка, текст строки,
            номер ветви `case` (если определён);
          * denies: ветви явного отказа;
          * default_behavior: что происходит, если ни одно условие не совпало.
            Значение `deny` означает fail-closed — это правильно; `allow` —
            признак дефекта, потому что неизвестное действие получает право;
          * callers: кто вызывает проверку — это точки, где решение применяется.

        Границы: операция статическая. Она показывает ветви в коде, а не
        фактический результат для конкретного пользователя: значения ролей
        берутся из базы, которой у индекса нет.
        """
        env = self.make_env("policy_trace")
        rev = self.current_rev()
        if not rev:
            return self._no_index(env)

        rows = self._symbol_rows(rev, symbol, None, 3)
        if not rows:
            env.found = False
            env.error = f"символ не найден: {symbol}"
            env.extra["suggest"] = ["symbol", "search"]
            return env.to_dict()

        target = rows[0]
        path = str(target.get("path") or "")
        start = int(target.get("start_line") or 0)
        end = int(target.get("end_line") or 0)

        allows: list[dict] = []
        denies: list[dict] = []
        resolved_read = False

        # Разбор тела идёт по актуальному файлу: решение о правах важно видеть
        # в текущем виде, а не в версии на момент индексации.
        try:
            abs_path = self.cfg.root / path
            text = abs_path.read_text(encoding="utf-8", errors="replace")
            resolved_read = True
            lines = text.splitlines()
            # Ветвь `case` отслеживается, чтобы в выдаче было видно,
            # к какому действию относится решение.
            current_case = ""
            default_line = 0
            default_allow = False

            for i in range(max(0, start - 1), min(len(lines), end)):
                line = lines[i]
                line_no = i + 1
                stripped = line.strip()

                # Номер ветви: `case 'create':` или `case "update":`.
                cm = re.match(r"case\s+['\"]([A-Za-z_][A-Za-z0-9_]*)['\"]\s*:", stripped)
                if cm:
                    current_case = cm.group(1)
                    continue
                if re.match(r"default\s*:", stripped):
                    current_case = "default"
                    default_line = line_no
                    continue

                if action and current_case and action.lower() not in current_case.lower():
                    # Если запрошено конкретное действие, ветви других действий
                    # пропускаются: цель — не вывалить весь switch.
                    continue

                ret = re.search(r"\breturn\s+(true|false)\s*;", stripped)
                if ret or re.search(r"return\s+\$?\w*(?:->|\[).*;|return\s+\$\w+\s*;", stripped):
                    # Решение: явное true/false либо возврат вычисленного значения.
                    value = ret.group(1) if ret else "computed"
                    item = {
                        "path": path,
                        "line": line_no,
                        "case": current_case or None,
                        "returns": value,
                        "text": stripped[:160],
                    }
                    if value == "false":
                        denies.append(item)
                    else:
                        allows.append(item)
                    if current_case == "default":
                        default_allow = value in ("true", "computed")
        except Exception as exc:
            env.limits.append(f"тело символа прочитать не удалось: {type(exc).__name__}")

        # Кто вызывает проверку: это точки применения решения.
        callers: list[dict] = []
        try:
            steps = self._bfs(rev, [str(target.get("node_id"))], depth, reverse=True,
                              limit=limit)
            for s in steps:
                # Ключ шага — edge_kind, а не relation. Ошибка в имени ключа
                # привела к тому, что вызывающие не находились, хотя рёбра
                # в графе есть (найдено проверкой .staging/check_callers.py).
                if s.get("edge_kind") in ("calls", "calls_heuristic"):
                    callers.append({
                        "path": s.get("path"),
                        "line": s.get("start_line"),
                        "symbol": s.get("name") or s.get("fqn"),
                        "provenance": s.get("provenance"),
                        "edge_kind": s.get("edge_kind"),
                    })
        except Exception as exc:
            env.limits.append(f"обход вызовов не выполнен: {type(exc).__name__}")

        # Поведение по умолчанию: если ни одно условие не совпало, остаётся
        # последний `return false` или ветвь default.
        if default_line:
            default_behavior = "allow" if default_allow else "deny"
        else:
            default_behavior = "deny" if denies else ("allow" if allows else "unknown")

        env.result = {
            "symbol": symbol,
            "path": path,
            "lines": {"start": start, "end": end},
            "action_filter": action,
            "allows": allows,
            "denies": denies,
            "default_behavior": default_behavior,
            "callers": callers,
            "body_read": resolved_read,
        }
        env.found = bool(allows or denies)

        # Самый важный вывод операции: признак fail-closed. Если функция разрешает
        # всё, что не описано явно, это дефект, а не решение.
        if default_behavior == "allow":
            env.extra["warning"] = (
                "проверка разрешает действие, если оно не описано явно: "
                "неизвестное действие получает право"
            )
            env.stale_warnings.append({
                "path": path,
                "reason": "fail-open: неназванное действие разрешено",
                "suggest": "проверить ветви глазами",
            })
        env.extra["fail_closed"] = default_behavior == "deny"
        if not env.found:
            env.extra["suggest"] = ["code", "search"]
            env.extra["note"] = "в теле символа не найдено решений о правах"
        self.attach_stale(env, [path])
        return env.to_dict()

    def op_class_usage(self, name: str | None = None, limit: int = 200,
                       mode: str = "all") -> dict:
        """23. Классы оформления: где определён, где использован, что расходится.

        Зачем операция. Класс оформления — самый частый объект, с которым
        сверяются при правке интерфейса: «есть ли стиль», «где ещё используется»,
        «не останется ли кнопка без оформления». Без этой операции агент ищет
        классы текстом по файлам и пропускает расхождения.

        Три режима:
          * `all` (по умолчанию) — сводка по одному классу или список проблемных;
          * `missing` — классы, используемые в разметке, но НЕ описанные
            в стилях. Это реальный класс дефектов: страница работает и
            выглядит «как-то» за счёт оформления браузера по умолчанию,
            поэтому ошибка не проявляется при запуске;
          * `unused` — классы, описанные в стилях, но нигде не используемые.

        Параметр `name` задаёт конкретный класс (без ведущей точки). Если он
        не задан — возвращается список по режиму.
        """
        env = self.make_env("class_usage")
        rev = self.current_rev()
        if not rev:
            return self._no_index(env)
        if mode not in ("all", "missing", "unused"):
            env.found = False
            env.error = "неизвестный режим: ожидается all, missing или unused"
            return env.to_dict()

        clean_name = (name or "").lstrip(".")

        if clean_name:
            rows = self.graph.execute(
                """SELECT e.kind, e.evidence_path AS path, e.evidence_line AS line,
                          e.provenance, e.props
                     FROM edge e JOIN node n
                       ON n.rev_id = e.rev_id AND n.node_id = e.dst
                    WHERE e.rev_id = ? AND n.kind = 'css_class' AND n.name = ?
                      AND e.kind IN ('defines_class','uses_class')
                    ORDER BY e.kind, e.evidence_path, e.evidence_line LIMIT ?""",
                (rev, clean_name, limit),
            ).fetchall()
            defines = [dict(r) for r in rows if r["kind"] == "defines_class"]
            uses = [dict(r) for r in rows if r["kind"] == "uses_class"]
            env.result = {
                "class": clean_name,
                "defined": [
                    {"path": r["path"], "line": r["line"],
                     "selector": (util.loads(r["props"]) or {}).get("selector")
                                 if r["props"] else None}
                    for r in defines
                ],
                "used": [{"path": r["path"], "line": r["line"]} for r in uses],
                "defined_count": len(defines),
                "used_count": len(uses),
                "status": ("только используется: правила в стилях нет"
                           if uses and not defines else
                           "только описан: в разметке не найден"
                           if defines and not uses else
                           "описан и используется" if defines and uses else
                           "не найден ни в стилях, ни в разметке"),
            }
            env.found = bool(defines or uses)
            if not env.found:
                env.extra["suggest"] = ["search", "class_usage"]
                env.limits.append(
                    "класс не найден: если он определён вне проиндексированных файлов, "
                    "добавьте путь в include конфигурации корпуса"
                )
            self.attach_stale(env, [r["path"] for r in rows if r["path"]])
            return env.to_dict()

        # Сводный режим: список классов по состоянию.
        having = "HAVING defs = 0 AND uses > 0" if mode == "missing" else \
                 "HAVING defs > 0 AND uses = 0" if mode == "unused" else \
                 "HAVING defs = 0 AND uses > 0"
        rows = self.graph.execute(
            f"""SELECT n.name,
                       SUM(CASE WHEN e.kind = 'defines_class' THEN 1 ELSE 0 END) AS defs,
                       SUM(CASE WHEN e.kind = 'uses_class' THEN 1 ELSE 0 END) AS uses
                  FROM node n JOIN edge e
                    ON e.rev_id = n.rev_id AND e.dst = n.node_id
                 WHERE n.rev_id = ? AND n.kind = 'css_class'
                   AND e.kind IN ('defines_class','uses_class')
                 GROUP BY n.node_id {having}
                 ORDER BY uses DESC, n.name LIMIT ?""",
            (rev, limit),
        ).fetchall()
        out = []
        for r in rows:
            first = self.graph.execute(
                """SELECT e.evidence_path AS path, e.evidence_line AS line
                     FROM edge e JOIN node n ON n.rev_id = e.rev_id AND n.node_id = e.dst
                    WHERE e.rev_id = ? AND n.name = ?
                      AND e.kind = ? LIMIT 1""",
                (rev, r["name"],
                 "uses_class" if mode != "unused" else "defines_class"),
            ).fetchone()
            out.append({
                "name": r["name"],
                "defined_count": int(r["defs"] or 0),
                "used_count": int(r["uses"] or 0),
                "first_at": (f"{first['path']}:{first['line']}" if first else None),
            })
        env.result = {
            "mode": mode,
            "classes": out,
            "count": len(out),
            "note": ("классы используются в разметке, но правил в стилях нет: "
                     "страница отрисуется оформлением браузера по умолчанию"
                     if mode == "missing" else
                     "классы описаны, но нигде не используются" if mode == "unused"
                     else "классы без правил в стилях"),
        }
        env.found = bool(out)
        if not out:
            env.limits.append(
                "расхождений нет" if mode == "missing"
                else "неиспользуемых классов нет"
            )
        return env.to_dict()

    def op_write_check(self, table: str, columns: list[str] | None = None,
                       operation: str = "insert") -> dict:
        """18. Сверка плана записи со схемой ДО выполнения (U1).

        Зачем операция. Дефект №7 на полигоне: код писал в колонки AuthorUserID
        и UpdatedBy, которых в таблицах журналов нет (там InitiatorID, а UpdatedBy
        есть только у одной таблицы). Ошибка драйвера 42S22 всплыла только при
        первом сохранении — то есть после написания кода и запуска.

        Схема в индексе есть (стадия schema разбирает миграции), но операции
        «сверь мой список колонок» не было: каждый раз её делали глазами.
        Эта операция закрывает разрыв и отвечает ДО запуска.

        Что возвращает:
          ok               — все колонки найдены и обязательные (NOT NULL без
                             значения по умолчанию) при вставке заданы;
          unknown_columns  — колонки, которых нет, с ближайшим похожим именем
                             (подсказка правится одним сравнением);
          insert_required  — колонки NOT NULL без DEFAULT, которые надо задать;
          primary_key      — ключевые колонки (для UPDATE нужны в условии).

        Границы: операция НЕ подключается к базе. Источник — схема из миграций,
        то есть индексация остаётся работоспособной без СУБД.
        """
        env = self.make_env("write_check")
        rev = self.current_rev()
        if not rev:
            return self._no_index(env)

        operation = (operation or "insert").lower()
        if operation not in ("insert", "update", "delete"):
            env.found = False
            env.error = "неизвестная операция: ожидается insert, update или delete"
            return env.to_dict()

        # Ищем таблицу без учёта регистра: в миграциях и в коде регистр различается.
        trow = self.graph.execute(
            "SELECT table_id, table_name, source FROM db_table"
            " WHERE rev_id = ? AND lower(table_name) = lower(?) LIMIT 1",
            (rev, table),
        ).fetchone()
        if trow is None:
            env.found = False
            # Подсказка с похожими именами: без неё агент не поймёт, опечатка это
            # или таблица вообще не разобрана из миграций.
            near = [
                str(r["table_name"])
                for r in self.graph.execute(
                    "SELECT table_name FROM db_table WHERE rev_id = ? ORDER BY table_name",
                    (rev,),
                ).fetchall()
            ]
            liked = util.closest_names(table, near, limit=5)
            env.extra["suggest"] = ["schema"]
            env.extra["similar_tables"] = liked
            env.result = {
                "table": table,
                "table_found": False,
                "unknown_columns": [],
                "insert_required": [],
                "primary_key": [],
                "checked_columns": [],
                "note": "таблица не найдена в схеме из миграций",
            }
            return env.to_dict()

        cols: list[dict] = []
        # Наличие DEFAULT проверяется по тексту миграции: в db_column этого
        # признака нет. Запрос идёт по text_chunk того же файла — разбор миграций
        # уже хранит определения таблиц как текст.
        for r in self.graph.execute(
            "SELECT column_name, data_type, is_nullable, column_key, ordinal"
            " FROM db_column WHERE rev_id = ? AND table_id = ? ORDER BY ordinal",
            (rev, str(trow["table_id"])),
        ).fetchall():
            cols.append(
                {
                    "name": str(r["column_name"]),
                    "type": r["data_type"],
                    "nullable": bool(r["is_nullable"]),
                    "key": r["column_key"],
                    "has_default": _column_has_default(
                        rev, str(trow["table_name"]), str(r["column_name"])
                    ),
                }
            )

        existing = {c["name"].lower(): c["name"] for c in cols}
        given = [str(c) for c in (columns or [])]

        unknown: list[dict] = []
        for name in given:
            if name.lower() not in existing:
                unknown.append({
                    "name": name,
                    "nearest": util.closest_names(name, list(existing.values()), limit=2),
                })

        # Обязательные при вставке: NOT NULL и без значения по умолчанию.
        # AUTO_INCREMENT в разборе миграций не выделяется отдельным признаком,
        # поэтому исключаются PRIMARY-колонки (их значение даёт СУБД) и колонки
        # с явным DEFAULT — иначе проверка выдавала бы ложные «обязательные»,
        # и агент перестал бы ей доверять. Замерено на DocPayments: без этого
        # правила в обязательные попадали ID и Deleted (у обоих есть DEFAULT).
        required = []
        for c in cols:
            if c["nullable"] or (c["key"] or "") == "PRI":
                continue
            if c.get("has_default"):
                continue
            required.append(c["name"])
        required_missing = [
            n for n in required if n.lower() not in {g.lower() for g in given}
        ]
        primary = [c["name"] for c in cols if (c["key"] or "") == "PRI"]

        env.result = {
            "table": str(trow["table_name"]),
            "table_found": True,
            "operation": operation,
            "checked_columns": given,
            "unknown_columns": unknown,
            "insert_required": required_missing if operation == "insert" else [],
            "all_required_columns": required,
            "primary_key": primary,
            "column_count": len(cols),
            "source": str(trow["source"] or "migration"),
        }
        # Результат считаем найденным только при отсутствии проблем: агент должен
        # видеть «проверка не пройдена» как отрицательный ответ, а не как данные.
        env.found = bool(unknown) or bool(required_missing) if given else True
        if unknown:
            env.extra["problem"] = (
                "колонки отсутствуют в таблице: "
                + ", ".join(str(u["name"]) for u in unknown)
            )
        elif required_missing and operation == "insert":
            env.extra["problem"] = (
                "обязательные колонки не заданы: " + ", ".join(required_missing)
            )
        return env.to_dict()

    def op_routes(self, path: str | None = None, limit: int = 200) -> dict:
        """12. Маршруты и точки входа (M2)."""
        env = self.make_env("routes")
        rev = self.current_rev()
        if not rev:
            return self._no_index(env)
        if not store.table_exists(self.graph, "web_route"):
            env.found = False
            env.error = "слой маршрутов не построен (этап M2)"
            return env.to_dict()
        params: list[Any] = [rev]
        sql = "SELECT route, method, node_id, evidence_path, evidence_line FROM web_route WHERE rev_id = ?"
        if path:
            sql += " AND route LIKE ?"
            params.append(f"%{path}%")
        sql += " LIMIT ?"
        params.append(limit)
        rows = self.graph.execute(sql, tuple(params)).fetchall()
        env.result = [dict(r) for r in rows]
        env.found = bool(rows)
        return env.to_dict()

    def op_drift_ack(self, entry_ids: list[int], note: str | None = None, by_whom: str = "agent") -> dict:
        """15. Признание дельт: дописывание ack, БЕЗ удаления записи (ФТ-36)."""
        env = self.make_env("drift_ack")
        done = []
        for eid in entry_ids:
            row = self.control.conn.execute(
                "SELECT id FROM drift_entry WHERE id = ?", (eid,)
            ).fetchone()
            if row is None:
                continue
            payload = util.dumps({"by": by_whom, "ts": util.now_iso(), "note": note})
            self.control.conn.execute(
                "UPDATE drift_entry SET ack = ? WHERE id = ?", (payload, eid)
            )
            self.control.conn.execute(
                "INSERT INTO ack(drift_entry_id, by_whom, ts, note) VALUES(?, ?, ?, ?)",
                (eid, by_whom, util.now_iso(), note),
            )
            done.append(eid)
        self.control.conn.commit()
        env.result = {"acked": done, "requested": entry_ids}
        env.extra["note"] = "записи журнала не удаляются: ack только дописывается"
        return env.to_dict()

    def op_calibrate(self, kind: str = "confidence", samples: dict | None = None) -> dict:
        """17. Калибровка порогов (ФТ-11 ТЗ)."""
        env = self.make_env("calibrate")
        if kind == "confidence":
            params = samples or self.cfg.raw.get("confidence", {})
            note = "стартовые значения из архитектуры §5.2"
        else:
            params = samples or {}
            note = "пользовательская калибровка"
        n = int((samples or {}).get("sample_size", 0)) if isinstance(samples, dict) else 0
        self.control.conn.execute(
            "INSERT INTO calibration(created_at, kind, params, sample_size, note) VALUES(?,?,?,?,?)",
            (util.now_iso(), kind, util.dumps(params), n, note),
        )
        self.control.conn.commit()
        env.result = {"kind": kind, "params": params, "sample_size": n, "note": note}
        env.limits.append(
            "пороги требуют калибровки на ~20 размеченных примерах: в литературе разброс в 3 раза"
        )
        return env.to_dict()

    # ---------------- внутреннее ----------------

    def _resolve_entry_ids(self, rev: str, raws: list[str]) -> tuple[
        list[tuple[str, int, int, str | None]], list[str], int, int
    ]:
        """Разрешить идентификаторы хита поиска (node_id и/или chunk_id) в спаны.

        Зачем. Хит `search` отдаёт `node_id` и `chunk_id`, но ДО этой правки их
        нельзя было передать никуда: `code --entry-ids <node_id>` отвечал
        «нужен параметр fqn или path», и агент шёл искать файл заново — то есть
        двухшаговая схема была двухшаговой только формально.

        Порядок разрешения и его границы:
        * сначала `node_id` — по таблице узлов (даёт точный путь и границы);
        * затем `chunk_id` — по таблице чанков (есть только при `source_kind`
          символ/файл; у чанков без узла путь есть, а имени нет);
        * что не разрешилось — возвращается списком, а не выбрасывается молча:
          агент должен видеть, что именно не найдено.
        """
        spans: list[tuple[str, int, int, str | None]] = []
        unresolved: list[str] = []
        via_node = 0
        via_chunk = 0
        seen: set[tuple[str, int, int]] = set()
        for raw in raws:
            ident = str(raw).strip()
            if not ident:
                continue
            # 1) node_id из графа (даёт точный путь, границы и имя)
            row = self.graph.execute(
                """SELECT path, start_line, end_line, COALESCE(fqn, name) AS label
                   FROM node WHERE rev_id = ? AND node_id = ?""",
                (rev, ident),
            ).fetchone()
            if row is not None and row["path"] is not None:
                key = (str(row["path"]), int(row["start_line"]), int(row["end_line"]))
                if key not in seen:
                    seen.add(key)
                    spans.append((key[0], key[1], key[2], row["label"]))
                via_node += 1
                continue
            # 2) chunk_id из хранилища чанков. Колонки chunk_id НЕТ в таблице node
            # (DDL store.py:122-131: rev_id, node_id, kind, name, fqn, path, ...),
            # а прежняя редакция делала по ней запрос к графу — сервер отвечал
            # OperationalError «no such column», то есть адресация по chunk_id
            # падала вместо разрешения. Такой идентификатор ищется там, где он
            # действительно есть: в chunk_map и chunk_text.
            cm = self.lexvec.conn.execute(
                """SELECT path, start_line, end_line FROM chunk_map
                   WHERE rev_id = ? AND chunk_id = ?""",
                (rev, ident),
            ).fetchone()
            if cm is not None and cm["path"] is not None:
                cstart = int(cm["start_line"] or 1)
                cend = int(cm["end_line"] or 0)
                if cend >= cstart:
                    key = (str(cm["path"]), cstart, cend)
                    if key not in seen:
                        seen.add(key)
                        spans.append((key[0], key[1], key[2], None))
                via_chunk += 1
                continue
            # 3) node_id, но без пути (например внешний узел) — не адресуемо
            unresolved.append(ident)
        return spans, unresolved, via_node, via_chunk

    def _no_index(self, env: Envelope) -> dict:
        env.found = False
        env.error = "индекс не создан: выполните ragraf index --project <config>"
        return env.to_dict()

    def _coverage(self, rev: str) -> dict:
        rows = self.graph.execute(
            "SELECT metric, value, note FROM coverage WHERE rev_id = ?", (rev,)
        ).fetchall()
        return {str(r["metric"]): r["value"] for r in rows}

    def _table_names(self, rev: str) -> set[str]:
        if not store.table_exists(self.graph, "db_table"):
            return set()
        try:
            rows = self.graph.execute(
                "SELECT table_name FROM db_table WHERE rev_id = ?", (rev,)
            ).fetchall()
            return {str(r["table_name"]).lower() for r in rows}
        except sqlite3.Error:
            return set()

    def _symbol_rows(self, rev: str, name: str, path: str | None, limit: int) -> list[dict]:
        """Поиск символа: сначала точное fqn, затем по имени, затем по вхождению."""
        exact = self.graph.execute(
            """SELECT node_id, kind, name, fqn, path, start_line, end_line, signature, props
               FROM node WHERE rev_id = ? AND (fqn = ? OR (name = ? AND kind <> 'file'))
               ORDER BY COALESCE(path,''), start_line LIMIT ?""",
            (rev, name, name, limit),
        ).fetchall()
        if not exact and "::" in name:
            cls, meth = name.split("::", 1)
            exact = self.graph.execute(
                """SELECT node_id, kind, name, fqn, path, start_line, end_line, signature, props
                   FROM node WHERE rev_id = ? AND fqn LIKE ? AND name = ?
                   ORDER BY start_line LIMIT ?""",
                (rev, f"%{cls}%", meth, limit),
            ).fetchall()
        # Поиск по имени класса без метода: "Config" → сам класс
        if not exact and "::" not in name:
            exact = self.graph.execute(
                """SELECT node_id, kind, name, fqn, path, start_line, end_line, signature, props
                   FROM node WHERE rev_id = ? AND name = ?
                     AND kind IN ('class','interface','trait','enum','property','constant')
                   ORDER BY start_line LIMIT ?""",
                (rev, name, limit),
            ).fetchall()
        if not exact and path:
            exact = self.graph.execute(
                """SELECT node_id, kind, name, fqn, path, start_line, end_line, signature, props
                   FROM node WHERE rev_id = ? AND path = ? AND name = ? LIMIT ?""",
                (rev, util.to_posix(path), name, limit),
            ).fetchall()
        if not exact:
            exact = self.graph.execute(
                """SELECT node_id, kind, name, fqn, path, start_line, end_line, signature, props
                   FROM node WHERE rev_id = ? AND fqn LIKE ?
                   ORDER BY LENGTH(COALESCE(fqn,'')) LIMIT ?""",
                (rev, f"%{name}%", limit),
            ).fetchall()
        out = []
        for r in exact:
            item = dict(r)
            try:
                item["props"] = util.loads(item["props"]) if item["props"] else {}
            except Exception:
                item["props"] = {}
            # U3: машинный список параметров поднимается из props на верхний уровень
            # ответа: агенту нужны типы и порядок аргументов, а не вложенность.
            params = (item["props"] or {}).get("signature_params")
            if params is not None:
                item["signature_params"] = params
                item["signature_truncated"] = bool(
                    (item["props"] or {}).get("signature_truncated")
                )
            elif item.get("kind") in ("function", "method"):
                # Функция без параметров и без признака обрезки — это норма,
                # но об этом надо сказать явно, чтобы отсутствие поля не читалось
                # как «индекс не смог разобрать».
                item["signature_params"] = []
                item["signature_truncated"] = False
            out.append(item)
        return out

    def _graph_walk(self, fqn: str, depth: int, hard_only: bool, op: str, limit: int) -> dict:
        env = self.make_env(op)
        rev = self.current_rev()
        if not rev:
            return self._no_index(env)
        rows = self._symbol_rows(rev, fqn, None, 3)
        if not rows:
            env.found = False
            env.error = f"символ не найден: {fqn}"
            env.extra["suggest"] = ["symbol", "search"]
            return env.to_dict()
        reverse = op == "callers"
        steps = self._bfs(rev, [rows[0]["node_id"]], max(1, depth), reverse=reverse, limit=limit)
        # первый уровень — сама цель, исключаем её из результата
        steps = [s for s in steps if s["node_id"] != rows[0]["node_id"]]
        if hard_only:
            steps = [s for s in steps if s["provenance"] == "stated"]
        env.result = {
            "target": rows[0]["fqn"],
            "direction": "кто вызывает" if reverse else "кого вызывает",
            "depth": depth,
            "items": steps,
            "count": len(steps),
            "stated": sum(1 for s in steps if s["provenance"] == "stated"),
            "derived": sum(1 for s in steps if s["provenance"] == "derived"),
        }
        env.found = bool(steps)
        if not env.found:
            env.extra["suggest"] = ["search", "live_read"]
            env.limits.append("связи не найдены: возможен динамический вызов вне статического анализа")
        self.attach_stale(env, [s["path"] for s in steps if s.get("path")])
        return env.to_dict()

    def _bfs(self, rev: str, roots: list[str], depth: int, reverse: bool, limit: int) -> list[dict]:
        """Обход графа в ширину. Каждый шаг несёт доказательство и достоверность."""
        seen: dict[str, dict] = {}
        frontier = list(roots)
        root_set = set(roots)
        # Направление обхода:
        #   reverse=True  — ищем ВХОДЯЩИЕ связи (кто вызывает / откуда приходят данные);
        #                   фильтр по dst, сосед = src.
        #   reverse=False — ищем ИСХОДЯЩИЕ связи (кого вызывает); фильтр по src, сосед = dst.
        # Раньше было наоборот: callers и impact возвращали вызываемых вместо вызывающих.
        rel = "dst" if reverse else "src"          # колонка, по которой ищем известный узел
        join = "src" if reverse else "dst"         # колонка, откуда берём соседа
        for level in range(depth):
            if not frontier:
                break
            next_frontier: list[str] = []
            CH = 200
            for i in range(0, len(frontier), CH):
                part = frontier[i : i + CH]
                marks = ",".join("?" for _ in part)
                sql = f"""SELECT src, dst, kind, provenance, evidence_path, evidence_line, confidence
                          FROM edge WHERE rev_id = ? AND {rel} IN ({marks})
                          AND kind IN ('calls','calls_heuristic','instantiates','extends',
                                       'implements','overrides','queries','imports',
                                       'imports_heuristic','handles','renders','tested_by',
                                       'documented_by')"""
                rows = self.graph.execute(sql, (rev, *part)).fetchall()
                for r in rows:
                    other = str(r[join])
                    if other in root_set or other in seen:
                        continue
                    node = self.graph.execute(
                        """SELECT node_id, kind, name, fqn, path, start_line, end_line
                           FROM node WHERE rev_id = ? AND node_id = ?""",
                        (rev, other),
                    ).fetchone()
                    item = {
                        "node_id": other,
                        "level": level + 1,
                        "edge_kind": r["kind"],
                        "provenance": r["provenance"],
                        "confidence": r["confidence"],
                        "evidence": f"{r['evidence_path']}:{r['evidence_line']}",
                        "via": str(r["dst"]) if reverse else str(r["src"]),
                    }
                    if node:
                        item.update(
                            {
                                "kind": node["kind"],
                                "name": node["name"],
                                "fqn": node["fqn"],
                                "path": node["path"],
                                "start_line": node["start_line"],
                                "end_line": node["end_line"],
                            }
                        )
                    else:
                        item["kind"] = "external"
                        item["fqn"] = other
                    seen[other] = item
                    next_frontier.append(other)
                    if len(seen) >= limit:
                        return list(seen.values())
            frontier = next_frontier
        return list(seen.values())

    def _impact_table(self, env: Envelope, rev: str, table: str, mode: str) -> dict:
        dst = f"@table:{table}"
        rows = self.graph.execute(
            """SELECT src, kind, provenance, evidence_path, evidence_line FROM edge
               WHERE rev_id = ? AND dst = ?""",
            (rev, dst),
        ).fetchall()
        roots = [str(r["src"]) for r in rows]
        steps = self._bfs(rev, roots, 2, reverse=True, limit=300) if roots else []
        stated = [s for s in steps if s["provenance"] == "stated"]
        derived = [s for s in steps if s["provenance"] == "derived"]
        direct = [
            {
                "symbol": str(r["src"]),
                "kind": r["kind"],
                "provenance": r["provenance"],
                "evidence": f"{r['evidence_path']}:{r['evidence_line']}",
            }
            for r in rows
        ]
        env.result = {
            "target": f"table:{table}",
            "mode": mode,
            "direct_readers_writers": direct,
            "indirect": stated + ([] if mode == "hard" else derived),
            "affected_count": {"direct": len(direct), "indirect": len(steps)},
        }
        env.found = bool(direct or steps)
        self.attach_stale(env, [d["evidence"].split(":")[0] for d in direct if d.get("evidence")])
        return env.to_dict()

    def close(self) -> None:
        self.graph.close()
        self.lexvec.close()
        self.control.close()