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
import time
from dataclasses import dataclass
from typing import Any

# Оценка стоимости одной строки кода в символах: используется для перевода
# «числа возвращённых фактов» в «объём, который не пришлось читать файлами».
# Значение 45 взято как среднее по двум корпусам (второй PHP-корпус и пилотный PHP-портал),
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

    @property
    def est_tokens(self) -> int:
        """Оценка токенов ответа (не измерение): 4 символа на токен."""
        return int(self.result_chars / CHARS_PER_UNIT)

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
                 agent_kind: str = "unknown", task: str = "") -> None:
        """Журнал использования.

        Разделяются ДВЕ разные вещи, которые раньше смешивались в поле `tool`:

          * `tool` — КАНАЛ вызова: mcp, cli, admin, dev, selftest. Отвечает
            на вопрос «откуда пришёл вызов».
          * `agent` — КТО именно вызвал: имя агента (например `AutoClaw`,
            `небольшой PHP-корпус`, `RdpAudit`). Отвечает на вопрос владельца
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
        # `/RAGRAF` в внешний проект: там каждый вызов инструмента — новый процесс,
        # поэтому «сессия RAGRAF» с задачей не совпадает.
        self.task = (task or "").strip()
        self._ensure_schema()
        self.session_id = self._start_session()

    def _ensure_schema(self) -> None:
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
        # 59 в myproject), и терять её ради новой колонки нельзя.
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
        """
        sid = f"s{int(time.time() * 1000)}-{id(self) % 100000:05d}"
        try:
            self.control.conn.execute(
                "INSERT INTO usage_session(session_id, started_at, tool, agent,"
                " agent_kind, task, note) VALUES(?,?,?,?,?,?,?)",
                (sid, time.time(), self.tool, self.agent, self.agent_kind,
                 self.task, ""),
            )
            self.control.conn.commit()
        except Exception:
            pass
        return sid

    def record(self, rec: CallRecord) -> None:
        """Записать вызов. Сбой записи не влияет на результат операции.

        Агент пишется в КАЖДЫЙ вызов, а не только в сессию. Причина:
        сессия — это запуск процесса, а за одним процессом MCP может стоять
        несколько агентов (если сервер поднят один на хост). Храня агента
        в обоих местах, можно отвечать и на вопрос «сколько сессий у агента»,
        и на «сколько вызовов от агента», а они могут расходиться.
        """
        try:
            self.control.conn.execute(
                """INSERT INTO usage_call(session_id, ts, op, rev_id, capability, duration_ms,
                       found, fact_count, facts_with_span, result_chars, est_tokens,
                       replaces, error, tool, agent, agent_kind, task,
                       arg_hash, arg_digest)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
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
                    rec.error,
                    rec.tool,
                    self.agent,
                    self.agent_kind,
                    self.task,
                    rec.arg_hash,
                    rec.arg_digest,
                ),
            )
            self.control.conn.commit()
        except Exception:
            pass

    # ---------------- сводка ----------------

    def summary(self, since_session: bool = False, agent: str | None = None,
                agent_kind: str | None = None, task: str | None = None) -> dict:
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
        if since_session:
            where = "WHERE session_id = ?"
            params = (self.session_id,)
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
                           SUM(CASE WHEN error IS NOT NULL THEN 1 ELSE 0 END) AS errors
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

        return {
            # sessions_total — сессии, попавшие под фильтр; sessions_total_all
            # — все сессии журнала. Разделение нужно потому, что при фильтре
            # по агенту числа различаются, и путать их нельзя.
            "sessions_total": sessions_filtered,
            "sessions_total_all": sessions,
            "calls_total": total,
            "calls_in_session": total if since_session else None,
            # Фильтр отдаётся наружу: потребитель сводки должен видеть,
            # по какому агенту она построена, а не догадываться.
            "filter": {
                "agent": agent,
                "agent_kind": agent_kind,
                "task": task,
                "since_session": since_session,
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
            "result_chars_total": chars,
            "result_chars_replacing_reading": replacing,
            "est_tokens_total": int(chars / CHARS_PER_UNIT),
            "est_tokens_replacing_reading": int(replacing / CHARS_PER_UNIT),
            "est_lines_not_read": int(replacing / CHARS_PER_LINE),
            "by_op": by_op,
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
