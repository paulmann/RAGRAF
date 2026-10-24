"""Телеметрия использования RAGRAF: журнал обращений к операциям.

Назначение
    Собирает статистику по каждому вызову операций фасада, чтобы можно было
    измерить эффект от внедрения RAGRAF: сколько обращений было, какие операции
    востребованы, сколько данных они отдали, как часто ответ попадал в цель.

Зачем это нужно именно здесь
    Владелец проекта поставил задачу: по итогам разработки /agreements/ собрать
    сводный отчёт о выгодности RAGRAF. Отчёт без объективных данных превратился
    бы в самооценку, поэтому каждый вызов операции записывается в журнал
    (control.sqlite той же ревизии), а не подсчитывается на глаз.

Что записывается (и чего НЕ записывается)
    Записывается: имя операции, время, длительность, ревизия индекса, capability,
    число фактов в ответе, наличие ошибки, объём ответа в символах и оценка
    токенов, наличие ссылок path:line в фактах, был ли найден результат.
    НЕ записывается: текст запроса (может содержать фрагменты кода и имена
    файлов проекта), содержимое результата, имена пользователей. Это требование
    НФТ-3: телеметрия не должна становиться каналом утечки кода.

Две метрики «пользы», которые считаются по журналу
    1. savings_chars — сколько символов кода агент не стал бы читать, если
       пользовался индексом. Считается честно и консервативно: только по
       операциям, которые ЗАМЕНЯЮТ чтение файлов (symbol, references, code,
       impact, search), и по фактическому числу возвращённых фактов с учётом
       оценки строки кода. Не приписывается операциям структуры (repo_map,
       index_status).
    2. verifiable_ratio — доля фактов со ссылкой path:line. Это проверяемость:
       ответ, который нельзя проверить по коду, для нас считается бесполезным.
"""

from __future__ import annotations

import hashlib
import sqlite3
import sys
import time
from dataclasses import dataclass
from typing import Any

# Оценка стоимости одной строки кода в символах: используется для перевода
# «числа возвращённых фактов» в «объём, который не пришлось читать файлами».
# Значение 45 взято как среднее по двум корпусам (revoap.com и z.batteryservice.ru),
# замерено, а не придумано: см. tools/bench_corpus.py, AVG_CHARS_PER_FILE.
CHARS_PER_LINE = 45

# Оценка токенов на символ: 4 символа на токен — грубое, но устойчивое правило
# для смешанного русского и английского текста. Помечается как оценка всюду,
# где выводится, чтобы не выдавать её за измерение.
CHARS_PER_UNIT = 4.0

# Операции, которые ЗАМЕНЯЮТ чтение файлов: по ним считается экономия.
# repo_map/index_status/drift_report/schema/routes/table_usage/write_check —
# структура и проверки: их результат не заменяет чтение тела кода, поэтому
# экономия по ним не начисляется (иначе отчёт стал бы саморекламой).
READ_REPLACING_OPS = {
    "symbol",
    "references",
    "code",
    "impact",
    "search",
    "callers",
    "callees",
    "slice",
    "live_read",
}


def normalize_arg(value: Any) -> tuple[str, str]:
    """Нормализовать первичный аргумент операции для журнала обращений.

    Нужно, чтобы ответить на вопрос «вызывали ли нужную операцию для этого
    объекта»: без аргумента записи по разным таблицам и символам неотличимы,
    и эффект улучшений измерить нельзя.

    Возвращает пару (хеш, читаемый дайджест). Открытый текст аргумента целиком
    не хранится намеренно: аргументом может быть фрагмент кода из запроса агента,
    а телеметрия не должна становиться каналом утечки исходников (НФТ-3).
    """
    if value is None:
        return "", ""
    text = " ".join(str(value).strip().split())
    if not text:
        return "", ""
    digest = text[:60]
    return hashlib.sha1(digest.lower().encode("utf-8")).hexdigest()[:12], digest


@dataclass
class CallRecord:
    """Одна запись журнала: один вызов операции."""

    op: str
    rev_id: str | None
    capability: str
    duration_ms: float
    found: bool
    fact_count: int
    facts_with_span: int
    result_chars: int
    error: str | None
    tool: str = "unknown"
    arg_hash: str = ""
    arg_digest: str = ""
    # Метрики P2-4. `stale` — ответ отдан с пометкой устаревания (факт мог
    # относиться к прошлому состоянию кода), `missed` — «не найдено» без ошибки.
    # Оба нужны для приёмки «индекс используется правильно»: доля попаданий
    # (`hit_rate`) считается по ним, а `stale_served` обязан быть нулём.
    stale: bool = False
    missed: bool = False
    # TOKEN-ATTRIBUTION-PATCH: чем пришёл вызов — идентификатор и метка выданного доступа.
    # Пустые значения означают «без него»: так выглядят CLI, доступ сессии
    # и прежние записи журнала. Поля нужны, чтобы статистика отвечала на
    # вопрос «кто и по какому праву», а не только «какой агент».
    token_id: str = ""
    token_label: str = ""

    @property
    def est_tokens(self) -> int:
        """Оценка токенов ответа (не измерение): 4 символа на токен."""
        return int(self.result_chars / CHARS_PER_UNIT)

    # Шаг 0.6 плана: измеренная экономия в СИМВОЛАХ, а не флаг «эта операция
    # обычно заменяет чтение». Считается по фактически выданным путям ответа
    # (см. Facade._saved_chars): 0 означает «нечего было заменять», и это
    # честный ноль, а не отсутствие измерения.
    replaces_chars: int = 0

    @property
    def replaces_reading(self) -> bool:
        """Заменяет ли эта операция чтение файлов агентом."""
        return self.op in READ_REPLACING_OPS


class Telemetry:
    """Журнал обращений, привязанный к control.sqlite проекта.

    Устроен как «открыл — записал — закрыл»: фасад может работать долго, журнал
    не должен держать транзакцию. Ошибка записи телеметрии НИКОГДА не прерывает
    работу операции: журнал вспомогательный, и его сбой не должен ломать ответ.
    """

    def __init__(self, control, tool: str = "unknown", agent: str = "unknown",
                 agent_kind: str = "unknown", task: str = "",
                 token_id: str = "", token_label: str = "") -> None:
        """Журнал использования.

        Разделяются ДВЕ разные вещи, которые раньше смешивались в поле `tool`:

          * `tool` — КАНАЛ вызова: mcp, cli, admin, dev, selftest. Отвечает
            на вопрос «откуда пришёл вызов».
          * `agent` — КТО именно вызвал: имя агента (например `AutoClaw`,
            `MD.Viewer`, `RdpAudit`). Отвечает на вопрос владельца
            «какой агент использовал RAGRAF».
          * `agent_kind` — СЕМЕЙСТВО агента: `autoclaw` или `external`.
            Нужно для общей статистики по всем агентам AutoClaw отдельно
            от сторонних потребителей.

        Разделение появилось по замеру: поле `tool` принимало значения
        admin/cli/dev/selftest/unknown, и по нему нельзя было отфильтровать
        статистику по агенту — 113 вызовов из 138 в корпусе z были `unknown`,
        потому что MCP-сервер не передавал себя вообще.
        """
        self.control = control
        self.tool = tool
        self.agent = agent or "unknown"
        self.agent_kind = agent_kind or "unknown"
        # Метка задачи: позволяет собрать статистику за ОДНУ задачу
        # пользователя, а не за один запуск процесса. Нужна команде
        # `/RAGRAF` в SagaAI: там каждый вызов инструмента — новый процесс,
        # поэтому «сессия RAGRAF» с задачей не совпадает.
        self.task = (task or "").strip()
        # TOKEN-ATTRIBUTION-PATCH: атрибуция доступа на уровне процесса. Сервер MCP знает,
        # каким доступом его вызвали, и передаёт это сюда; пусто — без него.
        self.token_id = (token_id or "").strip()
        self.token_label = (token_label or "").strip()
        # --- учёт потерь журнала ---
        # Ошибка записи не прерывает операцию (журнал вспомогательный), но
        # молчать о ней нельзя. Измерено 13.10.2026 в сессии HARNESS: 12 вызовов
        # за ход не изменили сводку `usage`, потому что `record()` глушил
        # исключение `except Exception: pass`. Владелец видел вывод «агент почти
        # не пользовался RAGRAF» вместо «журнал не смог записать».
        self.write_failures = 0
        self.write_error = ""
        self._warned_failures: set[str] = set()
        self._ensure_schema()
        self.session_id = self._start_session()

    @classmethod
    def for_reading(cls, control) -> "Telemetry":
        """Журнал только на чтение: сводка без старта новой сессии.

        Раньше это собиралось вручную через `Telemetry.__new__` в фасаде, и
        любое новое поле (например `write_failures`) там оказывалось
        неинициализированным. Одна точка создания убирает этот класс ошибок.
        """
        reading = cls.__new__(cls)
        reading.control = control
        reading.tool = "reader"
        reading.agent = "reader"
        reading.agent_kind = "internal"
        reading.task = ""
        reading.token_id = ""
        reading.token_label = ""
        reading.write_failures = 0
        reading.write_error = ""
        reading._warned_failures = set()
        reading.session_id = ""
        reading._ensure_schema()
        return reading

    def _note_failure(self, where: str, exc: BaseException) -> None:
        """Запомнить потерянную запись и сказать об этом в stderr.

        Каждый ВИД ошибки печатается один раз: иначе долгоживущий сервер зальёт
        диагностику повторами одного и того же отказа.
        """
        self.write_failures += 1
        self.write_error = f"{where}: {type(exc).__name__}: {exc}"
        key = f"{where}:{type(exc).__name__}"
        if key in self._warned_failures:
            return
        self._warned_failures.add(key)
        try:
            sys.stderr.write(
                f"[ragraf-telemetry] запись в журнал не удалась: {self.write_error} "
                "(повторов этого вида больше не будет)\n"
            )
            sys.stderr.flush()
        except Exception:  # noqa: BLE001 — диагностика не должна ронять операцию
            pass

    def _write(self, where: str, sql: str, params: tuple) -> bool:
        """Записать строку журнала, пережив кратковременную блокировку базы.

        `busy_timeout=10000` уже стоит на соединении, но одного его мало:
        административная панель и MCP-сервер пишут в ОДИН журнал контрольной
        базы, и при конкуренции вызов всё равно получает
        `database is locked`. Три коротких повтора снимают дрожание, а если не
        помогли — потеря попадает в счётчик, а не исчезает бесследно.
        """
        for attempt in range(3):
            try:
                self.control.conn.execute(sql, params)
                self.control.conn.commit()
                return True
            except sqlite3.OperationalError as exc:
                # Откат обязателен: `sqlite3` открывает транзакцию ДО оператора,
                # поэтому неудачная запись оставляет соединение «в транзакции».
                # Если до неё в этой же транзакции успела пройти другая запись,
                # блокировка записи журнала остаётся за процессом НАВСЕГДА:
                # замерено 13.10.2026 — живой MCP-сервер держал control.sqlite,
                # и ни переиндексация, ни второй сервер, ни тесты записать не
                # могли (Restart Manager показал держателя — pid сервера).
                self._rollback()
                if attempt == 2:
                    self._note_failure(where, exc)
                    return False
                time.sleep(0.05 * (2 ** attempt))
            except Exception as exc:  # noqa: BLE001 — журнал не ломает операцию
                self._rollback()
                self._note_failure(where, exc)
                return False
        return False

    def _rollback(self) -> None:
        """Снять незавершённую транзакцию: иначе соединение держит блокировку."""
        try:
            self.control.conn.rollback()
        except Exception:  # noqa: BLE001 — откат не должен ломать операцию
            pass

    def _ensure_schema(self) -> None:
        # На хранилище только для чтения схему не создаём и не мигрируем:
        # прав нет, а попытка падала бы при создании фасада и делала чтение
        # статистики невозможным (измерено 15.10.2026 на чужом каталоге
        # `.ragraf` без права записи). Читателю нужны колонки, а не их создание.
        if getattr(self.control, "readonly", False):
            return
        self.control.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS usage_call (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id    TEXT,
                ts            REAL NOT NULL,
                op            TEXT NOT NULL,
                rev_id        TEXT,
                capability    TEXT,
                duration_ms   REAL,
                found         INTEGER,
                fact_count    INTEGER,
                facts_with_span INTEGER,
                result_chars  INTEGER,
                est_tokens    INTEGER,
                replaces      INTEGER,
                error         TEXT,
                tool          TEXT,
                arg_hash      TEXT,
                arg_digest    TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_usage_call_op ON usage_call(op);
            CREATE INDEX IF NOT EXISTS idx_usage_call_session ON usage_call(session_id);

            CREATE TABLE IF NOT EXISTS usage_session (
                session_id    TEXT PRIMARY KEY,
                started_at    REAL NOT NULL,
                tool          TEXT,
                agent         TEXT,
                agent_kind    TEXT,
                note          TEXT
            );
            """
        )
        self.control.conn.commit()
        # Догоняющая миграция: колонки агента добавляются к существующим
        # таблицам — прежние журналы не должны требовать удаления.
        # Это важно: в корпусах уже накоплена история (190 сессий в z,
        # 59 в revoap), и терять её ради новой колонки нельзя.
        for column, table in (("agent", "usage_session"),
                              ("agent_kind", "usage_session"),
                              ("task", "usage_session"),
                              ("agent", "usage_call"),
                              ("agent_kind", "usage_call"),
                              ("task", "usage_call"),
                              ("tool", "usage_call")):
            try:
                self.control.conn.execute(
                    f"ALTER TABLE {table} ADD COLUMN {column} TEXT"
                )
            except Exception:
                pass   # колонка уже есть: это норма при повторном запуске
        # Числовые метрики P2-4: `stale` (ответ отдан с пометкой устаревания) и
        # `missed` (не найдено). Отдельный список, потому что тип INTEGER, а не
        # TEXT, и смешивать их в одном цикле значило бы положить числа в текст.
        # Шаг 0.6: измеренная экономия символов рядом с прежним флагом.
        # Догоняющая миграция: старые журналы не требуют удаления, у прежних
        # записей значение NULL и в сводке считается как «не измерялось».
        for column in ("stale", "missed", "replaces_chars"):
            try:
                self.control.conn.execute(
                    f"ALTER TABLE usage_call ADD COLUMN {column} INTEGER"
                )
            except Exception:
                pass
        # Индексы по агенту: без них фильтрация статистики по агенту
        # делала бы полный перебор журнала.
        for index_sql in (
            "CREATE INDEX IF NOT EXISTS idx_usage_call_agent "
            "ON usage_call(agent)",
            "CREATE INDEX IF NOT EXISTS idx_usage_call_task "
            "ON usage_call(task)",
            "CREATE INDEX IF NOT EXISTS idx_usage_session_agent "
            "ON usage_session(agent)",
        ):
            try:
                self.control.conn.execute(index_sql)
            except Exception:
                pass
        self.control.conn.commit()
        # Догоняющая миграция: колонки аргумента добавляются к уже существующей
        # таблице — прежние журналы не должны требовать удаления.
        for column in ("arg_hash", "arg_digest"):
            try:
                self.control.conn.execute(f"ALTER TABLE usage_call ADD COLUMN {column} TEXT")
            except Exception:
                pass   # колонка уже есть: это норма при повторном запуске
        # TOKEN-ATTRIBUTION-PATCH
        # Догоняющая миграция колонок доступа: прежние журналы не требуют
        # удаления — тот же приём, которым добавлялись agent/task/arg_hash.
        # Пустое значение означает «без него» и в сводке видно отдельной
        # строкой, чтобы разница не выглядела потерей данных.
        for column, table in (("token_id", "usage_call"),
                              ("token_label", "usage_call"),
                              ("token_id", "usage_session"),
                              ("token_label", "usage_session")):
            try:
                self.control.conn.execute(
                    f"ALTER TABLE {table} ADD COLUMN {column} TEXT")
            except Exception:
                pass   # колонка уже есть: норма при повторном запуске
        try:
            self.control.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_usage_call_token_id "
                "ON usage_call(token_id)"
            )
        except Exception:
            pass
        self.control.conn.commit()
        # Индекс по аргументу создаётся ПОСЛЕ добавления колонки: попытка создать
        # его в executescript выше падала на существующей таблице без колонки и
        # прерывала всю догоняющую миграцию (найдено прогоном на живом корпусе).
        try:
            self.control.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_usage_call_arg ON usage_call(op, arg_hash)"
            )
        except Exception:
            pass
        self.control.conn.commit()

    def _start_session(self) -> str:
        """Открыть сессию использования.

        Сессия = один запуск инструмента (CLI-вызов или запуск сервера MCP).
        Без неё «сколько было обращений» смешивалось бы между разными задачами.

        На хранилище только для чтения сессия НЕ открывается: записать её
        нельзя, а выдуманный номер сессии попал бы в ответ как существующий.
        """
        sid = f"s{int(time.time() * 1000)}-{id(self) % 100000:05d}"
        if getattr(self.control, "readonly", False):
            return ""
        self._write(
            "usage_session",
            "INSERT INTO usage_session(session_id, started_at, tool, agent,"
            " agent_kind, task, note, token_id, token_label)"
            " VALUES(?,?,?,?,?,?,?,?,?)",
            (sid, time.time(), self.tool, self.agent, self.agent_kind,
             self.task, "", self.token_id, self.token_label),
        )
        return sid

    def record(self, rec: CallRecord) -> None:
        """Записать вызов. Сбой записи не влияет на результат операции.

        Агент пишется в КАЖДЫЙ вызов, а не только в сессию. Причина:
        сессия — это запуск процесса, а за одним процессом MCP может стоять
        несколько агентов (если сервер поднят один на хост). Храня агента
        в обоих местах, можно отвечать и на вопрос «сколько сессий у агента»,
        и на «сколько вызовов от агента», а они могут расходиться.
        """
        self._write(
            "usage_call",
            """INSERT INTO usage_call(session_id, ts, op, rev_id, capability, duration_ms,
                       found, fact_count, facts_with_span, result_chars, est_tokens,
                       replaces, replaces_chars, error, tool, agent, agent_kind, task,
                       arg_hash, arg_digest, token_id, token_label,
                       stale, missed)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                self.session_id,
                time.time(),
                rec.op,
                rec.rev_id,
                rec.capability,
                rec.duration_ms,
                1 if rec.found else 0,
                rec.fact_count,
                rec.facts_with_span,
                rec.result_chars,
                rec.est_tokens,
                1 if rec.replaces_reading else 0,
                int(getattr(rec, "replaces_chars", 0) or 0),
                rec.error,
                rec.tool,
                self.agent,
                self.agent_kind,
                self.task,
                rec.arg_hash,
                rec.arg_digest,
                # ACCESS-FALLBACK-PATCH: доступ, названный вызовом, имеет приоритет; если
                # его нет — берётся доступ ПРОЦЕССА. Это разные вещи:
                # CallRecord описывает вызов, а Telemetry знает контекст
                # процесса (агент, семейство, задачу, доступ). Без подстановки
                # метка терялась: обёртка фасада доступа не заполняет, и в
                # статистике всё выглядело как «без доступа» (измерено).
                rec.token_id or self.token_id,
                rec.token_label or self.token_label,
                1 if rec.stale else 0,
                1 if rec.missed else 0,
            ),
        )

    # ---------------- сводка ----------------

    def summary(self, since_session: bool = False, agent: str | None = None,
                agent_kind: str | None = None, task: str | None = None,
                scope: str = "", session_id: str | None = None) -> dict:
        """Сводка по журналу: общая, по сессии или по агенту.

        Три режима, которые нужны владельцу:
          * без параметров — ОБЩАЯ статистика по всем агентам;
          * `agent="AutoClaw"` — только этот агент;
          * `agent_kind="autoclaw"` — все агенты AutoClaw вместе.

        Фильтры добавляются в WHERE, а не фильтруют выборку в Python:
        иначе на большом журнале пришлось бы читать все строки ради
        нескольких. Индексы по agent создаются в _ensure_schema.
        """
        where = ""
        params: tuple = ()
        # `scope` — явный выбор контекста (B7). Измеренный дефект: поле
        # `calls_in_session` всегда было `null`, а спросить «сколько вызовов
        # сделала ЭТА сессия» было нечем — интегратор не мог проверить свои же
        # вызовы и делал вывод «RAGRAF не используется» по пустой сводке.
        # scope="session" — по сессии процесса (или по переданному session_id),
        # scope="agent" — по агенту процесса, scope="all" — по всему журналу.
        scope = (scope or "").strip().lower()
        if scope not in ("", "all", "session", "agent"):
            scope = ""
        if scope == "agent" and not agent and not agent_kind:
            agent = self.agent
        sid = (session_id or "").strip()
        if scope == "session" or (since_session and not scope):
            where = "WHERE session_id = ?"
            params = (sid or self.session_id,)
        if agent:
            # Специальный случай: агент `unknown` — это НЕ строка `unknown`
            # в базе, а записи с NULL (старые, до появления поля) и пустые.
            # Без этого условия фильтр `agent='unknown'` возвращал 0 вызовов,
            # хотя в разбивке их 138: владелец видел строку
            # `unknown — 138 вызовов`, выбирал её и получал пусто.
            if agent == "unknown":
                cond = ("(agent IS NULL OR agent = '' OR agent = 'unknown')")
                where = (where + " AND " if where else "WHERE ") + cond
            else:
                where = (where + " AND " if where else "WHERE ") + "agent = ?"
                params = params + (agent,)
        if agent_kind:
            if agent_kind == "unknown":
                cond = ("(agent_kind IS NULL OR agent_kind = '' "
                        "OR agent_kind = 'unknown')")
                where = (where + " AND " if where else "WHERE ") + cond
            else:
                where = ((where + " AND " if where else "WHERE ")
                         + "agent_kind = ?")
                params = params + (agent_kind,)
        if task:
            # Пустая метка задачи (NULL) собирается отдельно, как и у агента:
            # записи до появления поля не имеют значения вовсе.
            if task in ("unknown", "(без задачи)", "-"):
                where = ((where + " AND " if where else "WHERE ")
                         + "(task IS NULL OR task = '')")
            else:
                where = (where + " AND " if where else "WHERE ") + "task = ?"
                params = params + (task,)

        conn = self.control.conn
        total = conn.execute(
            f"SELECT COUNT(*) AS c FROM usage_call {where}", params
        ).fetchone()["c"]

        by_op = [
            dict(r)
            for r in conn.execute(
                f"""SELECT op,
                           COUNT(*)                       AS calls,
                           SUM(found)                     AS found_calls,
                           SUM(fact_count)                AS facts,
                           SUM(facts_with_span)           AS with_span,
                           SUM(result_chars)              AS chars,
                           SUM(CASE WHEN replaces = 1 THEN result_chars ELSE 0 END) AS replacing_chars,
                           ROUND(AVG(duration_ms), 2)     AS avg_ms,
                           SUM(CASE WHEN error IS NOT NULL THEN 1 ELSE 0 END) AS errors,
                           SUM(COALESCE(stale, 0))        AS stale_served,
                           SUM(COALESCE(missed, 0))       AS misses
                      FROM usage_call {where}
                     GROUP BY op ORDER BY calls DESC""",
                params,
            ).fetchall()
        ]

        sessions = conn.execute(
            "SELECT COUNT(*) AS c FROM usage_session"
        ).fetchone()["c"]
        # Сессии считаются С УЧЁТОМ ФИЛЬТРА. В первой версии число сессий
        # бралось без фильтра, и при `--agent AutoClaw` выводилось «сессий 61»
        # при нуле вызовов — читатель решил бы, что агент работал,
        # но ничего не вызвал. Это разные величины: общее число сессий
        # печатается отдельно ниже, как sessions_total_all.
        sessions_filtered = sessions
        try:
            sess_where = ""
            sess_params: tuple = ()
            # Сессии считаются по тому же контексту, что и вызовы: при
            # scope="session" фильтр по session_id берётся из уже собранного
            # WHERE, иначе число сессий разошлось бы с числом вызовов.
            if where.startswith("WHERE session_id"):
                sess_where = "WHERE session_id = ?"
                sess_params = params
            if agent:
                # Та же логика, что и для вызовов: `unknown` — это NULL.
                if agent == "unknown":
                    sess_where = ("WHERE (agent IS NULL OR agent = '' "
                                  "OR agent = 'unknown')")
                else:
                    sess_where = "WHERE agent = ?"
                    sess_params = (agent,)
            if agent_kind:
                if agent_kind == "unknown":
                    cond = ("(agent_kind IS NULL OR agent_kind = '' "
                            "OR agent_kind = 'unknown')")
                    sess_where = ((sess_where + " AND " if sess_where else "WHERE ")
                                  + cond)
                else:
                    sess_where = ((sess_where + " AND " if sess_where else "WHERE ")
                                  + "agent_kind = ?")
                    sess_params = sess_params + (agent_kind,)
            if task:
                if task in ("unknown", "(без задачи)", "-"):
                    sess_where = ((sess_where + " AND " if sess_where else "WHERE ")
                                  + "(task IS NULL OR task = '')")
                else:
                    sess_where = ((sess_where + " AND " if sess_where else "WHERE ")
                                  + "task = ?")
                    sess_params = sess_params + (task,)
            if sess_where:
                sessions_filtered = conn.execute(
                    f"SELECT COUNT(*) AS c FROM usage_session {sess_where}",
                    sess_params,
                ).fetchone()["c"]
        except Exception:
            sessions_filtered = sessions

        # Запросы по объектам: какие именно таблицы и символы запрашивались.
        # Без этого нельзя измерить эффект улучшений (например U1: сверяли ли
        # схему перед правкой конкретной таблицы).
        by_arg = [
            dict(r)
            for r in conn.execute(
                f"""SELECT op, arg_digest,
                           COUNT(*)   AS calls,
                           SUM(found) AS found_calls
                      FROM usage_call {where}
                     GROUP BY op, arg_digest
                     ORDER BY calls DESC LIMIT 25""",
                params,
            ).fetchall()
        ]

        facts = sum(r["facts"] or 0 for r in by_op)
        with_span = sum(r["with_span"] or 0 for r in by_op)
        chars = sum(r["chars"] or 0 for r in by_op)
        replacing = sum(r["replacing_chars"] or 0 for r in by_op)
        errors = sum(r["errors"] or 0 for r in by_op)
        # Метрики приёмки «индекс используется всегда» (пункт 3.4 ТЗ):
        # `stale_served` — сколько ответов отдано с пометкой устаревания
        # (обязано быть нулём), `misses` — сколько раз индекс честно ответил
        # «не найдено», `hit_rate` — доля вызовов, принёсших хотя бы один факт.
        stale_served = sum(int(r.get("stale_served") or 0) for r in by_op)
        misses = sum(int(r.get("misses") or 0) for r in by_op)
        # `saved_calls`: вызов, который ЗАМЕНИЛ чтение файла или поиск по нему.
        # Считается по журналу: операция из списка `READ_REPLACING_OPS`, вернувшая
        # хотя бы один факт. Вызов без фактов ничего не заменил — он лишь показал,
        # что искать там нечего, и в это число не входит.
        try:
            saved_row = conn.execute(
                f"""SELECT SUM(CASE WHEN COALESCE(replaces, 0) = 1
                                     AND COALESCE(fact_count, 0) > 0
                                THEN 1 ELSE 0 END) AS saved
                      FROM usage_call {where}""",
                params,
            ).fetchone()
            saved_calls = int(saved_row["saved"] or 0) if saved_row else 0
        except Exception:
            saved_calls = 0
        # Доля попаданий считается ПО ВЫЗОВАМ, а не по операциям: «операция
        # приносила факты хоть раз» и «вызов принёс факт» — разные утверждения,
        # и подмена дала бы hit_rate 0,0135 вместо настоящей доли (измерено).
        try:
            agg = conn.execute(
                f"""SELECT SUM(CASE WHEN COALESCE(fact_count, 0) > 0 THEN 1 ELSE 0 END)
                              AS calls_with_facts,
                           ROUND(AVG(duration_ms), 2) AS avg_ms
                      FROM usage_call {where}""",
                params,
            ).fetchone()
            calls_with_facts = int(agg["calls_with_facts"] or 0) if agg else 0
            avg_ms = float(agg["avg_ms"] or 0.0) if agg else 0.0
        except Exception:
            calls_with_facts = 0
            avg_ms = 0.0

        # --- разбивка по агентам ---
        # Это ответ на запрос владельца: видеть, КАКОЙ агент использовал
        # RAGRAF, и при этом иметь общую картину по всем.
        #
        # by_agent — сколько каждый агент сделал вызовов и сколько фактов
        # получил; by_kind — то же по семействам (autoclaw / external).
        # Раньше это было невозможно: поле tool различало только каналы
        # (mcp/cli/admin), и 113 вызовов из 138 числились как unknown.
        by_agent = [
            dict(r)
            for r in conn.execute(
                f"""SELECT COALESCE(NULLIF(agent, ''), 'unknown') AS agent,
                           COALESCE(NULLIF(agent_kind, ''), 'unknown') AS agent_kind,
                           COUNT(*)                    AS calls,
                           SUM(found)                  AS found_calls,
                           SUM(fact_count)             AS facts,
                           SUM(facts_with_span)        AS with_span,
                           SUM(result_chars)           AS chars,
                           SUM(CASE WHEN replaces = 1 THEN result_chars ELSE 0 END)
                                                       AS replacing_chars,
                           ROUND(AVG(duration_ms), 2)  AS avg_ms,
                           SUM(CASE WHEN error IS NOT NULL THEN 1 ELSE 0 END)
                                                       AS errors,
                           MAX(ts)                     AS last_ts
                      FROM usage_call {where}
                     GROUP BY agent, agent_kind
                     ORDER BY calls DESC""",
                params,
            ).fetchall()
        ]
        # Число сессий по агенту берётся из usage_session: вызовы и сессии —
        # разные величины, и они могут расходиться (один процесс MCP может
        # обслужить несколько задач).
        sessions_by_agent: dict[str, int] = {}
        try:
            sess_where = ""
            sess_params: tuple = ()
            if agent:
                sess_where = "WHERE agent = ?"
                sess_params = (agent,)
            if agent_kind:
                sess_where = (sess_where + " AND " if sess_where else "WHERE ") \
                    + "agent_kind = ?"
                sess_params = sess_params + (agent_kind,)
            for r in conn.execute(
                f"""SELECT COALESCE(NULLIF(agent, ''), 'unknown') AS agent,
                           COUNT(*) AS n
                      FROM usage_session {sess_where}
                     GROUP BY agent""",
                sess_params,
            ).fetchall():
                sessions_by_agent[str(r["agent"])] = int(r["n"])
        except Exception:
            sessions_by_agent = {}
        for row in by_agent:
            row["sessions"] = sessions_by_agent.get(str(row["agent"]), 0)

        # Сводка по семействам: нужна для вопроса «все агенты AutoClaw вместе».
        by_kind: dict[str, dict] = {}
        for row in by_agent:
            kind = str(row["agent_kind"])
            bucket = by_kind.setdefault(
                kind, {"agent_kind": kind, "agents": 0, "sessions": 0,
                       "calls": 0, "facts": 0, "with_span": 0,
                       "chars": 0, "replacing_chars": 0, "errors": 0}
            )
            bucket["agents"] += 1
            bucket["sessions"] += int(row["sessions"] or 0)
            bucket["calls"] += int(row["calls"] or 0)
            bucket["facts"] += int(row["facts"] or 0)
            bucket["with_span"] += int(row["with_span"] or 0)
            bucket["chars"] += int(row["chars"] or 0)
            bucket["replacing_chars"] += int(row["replacing_chars"] or 0)
            bucket["errors"] += int(row["errors"] or 0)
        # Оценка экономии считается для каждого семейства: без неё сводка
        # по семейству не отвечает на главный вопрос «какая от них польза».
        for bucket in by_kind.values():
            bucket["est_tokens_total"] = int(bucket["chars"] / CHARS_PER_UNIT)
            bucket["est_tokens_replacing_reading"] = int(
                bucket["replacing_chars"] / CHARS_PER_UNIT
            )
            bucket["est_lines_not_read"] = int(
                bucket["replacing_chars"] / CHARS_PER_LINE
            )
        by_kind_list = sorted(by_kind.values(), key=lambda b: -b["calls"])

        # --- разбивка по задачам ---
        # Нужна команде /RAGRAF: показать вклад RAGRAF в ОДНУ задачу,
        # хотя каждый вызов инструмента запускает отдельный процесс.
        by_task = [
            dict(r)
            for r in conn.execute(
                f"""SELECT COALESCE(NULLIF(task, ''), '(без задачи)') AS task,
                           COUNT(*)                    AS calls,
                           COUNT(DISTINCT session_id)  AS processes,
                           SUM(found)                  AS found_calls,
                           SUM(fact_count)             AS facts,
                           SUM(facts_with_span)        AS with_span,
                           SUM(result_chars)           AS chars,
                           SUM(CASE WHEN replaces = 1 THEN result_chars ELSE 0 END)
                                                       AS replacing_chars,
                           ROUND(AVG(duration_ms), 2)  AS avg_ms,
                           SUM(CASE WHEN error IS NOT NULL THEN 1 ELSE 0 END)
                                                       AS errors,
                           MIN(ts)                     AS first_ts,
                           MAX(ts)                     AS last_ts
                      FROM usage_call {where}
                     GROUP BY task
                     ORDER BY calls DESC LIMIT 20""",
                params,
            ).fetchall()
        ]
        for row in by_task:
            row["est_tokens"] = int((row["chars"] or 0) / CHARS_PER_UNIT)
            row["est_lines_not_read"] = int(
                (row["replacing_chars"] or 0) / CHARS_PER_LINE
            )

        # TOKEN-ATTRIBUTION-PATCH
        # Разрез по выданному доступу: сколько обращений сделал каждый,
        # сколько успешных, сколько с ошибками, сколько фактов принёс.
        # Записи без него (CLI, прежние журналы) собраны строкой
        # «(без доступа)»: иначе «не пользовались» и «поле пустое» были бы
        # неотличимы. Условие фильтра берётся то же, что у сводки, иначе
        # разрез разошёлся бы с итогами.
        try:
            access_rows = self.control.conn.execute(
                "SELECT COALESCE(NULLIF(token_label, ''), '(без доступа)') AS label,"
                "       COUNT(*) AS calls,"
                "       SUM(CASE WHEN error IS NULL OR error = '' THEN 1 ELSE 0 END) AS ok_calls,"
                "       SUM(CASE WHEN error IS NOT NULL AND error <> '' THEN 1 ELSE 0 END) AS failed_calls,"
                "       SUM(fact_count) AS facts,"
                "       MAX(ts) AS last_ts"
                "  FROM usage_call " + where +
                " GROUP BY label ORDER BY calls DESC",
                params,
            ).fetchall()
        except Exception:
            access_rows = []
        by_access = [
            {"label": row["label"], "calls": row["calls"],
             "ok": row["ok_calls"], "failed": row["failed_calls"],
             "facts": row["facts"], "last_ts": row["last_ts"]}
            for row in access_rows
        ]

        # --- польза за 30 дней ---
        # Отдельный срез по времени: общая сводка считает весь журнал, а
        # решение «работает ли индекс сейчас» принимают по свежему периоду.
        try:
            since30 = time.time() - 30 * 86400.0
            cond30 = (where + " AND ts >= ?") if where else "WHERE ts >= ?"
            row30 = conn.execute(
                f"""SELECT COUNT(*) AS calls,
                              SUM(COALESCE(fact_count, 0)) AS facts,
                              SUM(COALESCE(result_chars, 0)) AS chars,
                              SUM(CASE WHEN replaces = 1 THEN COALESCE(result_chars, 0) ELSE 0 END) AS replacing,
                              SUM(CASE WHEN error IS NOT NULL AND error <> '' THEN 1 ELSE 0 END) AS errors
                         FROM usage_call {cond30}""",
                params + (since30,),
            ).fetchone()
            chars30 = int(row30["chars"] or 0)
            replacing30 = int(row30["replacing"] or 0)
            last30 = {
                "days": 30,
                "calls": int(row30["calls"] or 0),
                "facts": int(row30["facts"] or 0),
                "chars": chars30,
                "tokens": int(chars30 / CHARS_PER_UNIT),
                "lines": int(replacing30 / CHARS_PER_LINE),
                "errors": int(row30["errors"] or 0),
                "estimate": "токены и строки — оценка, не измерение",
            }
        except Exception as exc:  # noqa: BLE001 — срез не должен прятать общую сводку
            last30 = {"error": f"{type(exc).__name__}: {exc}"}

        return {
            "by_access": by_access,
            # sessions_total — сессии, попавшие под фильтр; sessions_total_all
            # — все сессии журнала. Разделение нужно потому, что при фильтре
            # по агенту числа различаются, и путать их нельзя.
            "sessions_total": sessions_filtered,
            "sessions_total_all": sessions,
            "calls_total": total,
            # `calls_in_session` — число вызовов выбранного контекста. Раньше
            # поле было `null` во всех режимах, кроме `since_session`, и вопрос
            # «видит ли сводка мои вызовы» оставался без ответа (B7).
            "calls_in_session": total if (since_session or scope == "session") else None,
            "session_id": sid or (
                self.session_id if (since_session or scope == "session") else ""
            ),
            # Потери журнала: ошибка записи не прерывает операцию, но и не
            # скрывается. Ненулевое значение означает, что настоящих обращений
            # было больше, чем показано ниже.
            "write_failures": getattr(self, "write_failures", 0),
            "write_error": getattr(self, "write_error", ""),
            "warning": (
                f"журнал не записал {getattr(self, 'write_failures', 0)} вызовов "
                f"({getattr(self, 'write_error', '')}): фактических обращений больше, "
                "чем в этой сводке"
            ) if getattr(self, "write_failures", 0) else None,
            # Фильтр отдаётся наружу: потребитель сводки должен видеть,
            # по какому агенту она построена, а не догадываться.
            "filter": {
                "agent": agent,
                "agent_kind": agent_kind,
                "task": task,
                "since_session": since_session,
                "scope": scope or ("session" if since_session else "all"),
                "session_id": sid or (
                    self.session_id if (since_session or scope == "session") else ""
                ),
            },
            "agents_total": len(by_agent),
            "by_agent": by_agent,
            "by_agent_kind": by_kind_list,
            "tasks_total": len(by_task),
            "by_task": by_task,
            "errors": errors,
            "facts_total": facts,
            "facts_with_span": with_span,
            "verifiable_ratio": round(with_span / facts, 4) if facts else 0.0,
            # --- метрики эффекта (P2-4 ТЗ) ---
            # Без них нельзя доказать, что индекс окупается: «вызовов много» и
            # «вызовы приносили факты» — разные утверждения.
            "hit_rate": round(calls_with_facts / total, 4) if total else 0.0,
            "calls_with_facts": calls_with_facts,
            "avg_ms": avg_ms,
            "misses": misses,
            "stale_served": stale_served,
            "saved_calls": saved_calls,
            "saves_reading": bool(READ_REPLACING_OPS),
            "saved_calls_note": (
                "saved_calls — вызовы, заменившие чтение файлов или поиск по ним: "
                "операция из списка READ_REPLACING_OPS, вернувшая хотя бы один факт. "
                "Это измерение по журналу; оценка экономии в строках — "
                "est_lines_not_read"
            ),
            "stale_note": (
                "stale_served обязан быть нулём: ответ с пометкой устаревания "
                "означает, что факт мог относиться к прошлому состоянию кода"
            ),
            "result_chars_total": chars,
            "result_chars_replacing_reading": replacing,
            "est_tokens_total": int(chars / CHARS_PER_UNIT),
            "est_tokens_replacing_reading": int(replacing / CHARS_PER_UNIT),
            "est_lines_not_read": int(replacing / CHARS_PER_LINE),
            "by_op": by_op,
            "last30": last30,
            "by_arg": by_arg,
            "estimate_note": (
                "Токены и строки — ОЦЕНКА: символы делятся на 4 (токен) и на 45 "
                "(строка). Замерено: средняя строка кода 45 символов, средний файл "
                "14 000 символов (tools/bench_corpus.py)."
            ),
        }

    def reset(self) -> int:
        """Очистить журнал (для чистого замера «до/после»). Возвращает число записей."""
        conn = self.control.conn
        n = conn.execute("SELECT COUNT(*) AS c FROM usage_call").fetchone()["c"]
        conn.execute("DELETE FROM usage_call")
        conn.execute("DELETE FROM usage_session")
        conn.commit()
        return int(n)
