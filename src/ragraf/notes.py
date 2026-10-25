"""Заметки агента: короткое знание, которое переживает сессию (P2-3).

Зачем отдельное хранилище, а не граф или лексика. Заметка — это ЗНАНИЕ АГЕНТА,
а не факт о коде: она не подтверждена разбором и не должна попадать в поиск по
коду так, будто это ребро графа. Поэтому таблица живёт в CONTROL-хранилище
корпуса (там же журнал обращений и ревизии), а не в графе.

Что это решает. Найденное в одной сессии — «в этой базе часть колонок
генерируемые, писать в них нельзя», «этот каталог копия, рабочий вон тот» —
следующая сессия переоткрывает заново, потому что знание осталось в переписке.
Заметка кладёт его рядом с индексом и отдаёт по задаче.

Границы доверия:
  * заметка НЕ проверяется индексом: чтение (`notes`) возвращает её с полем
    `source: "note"`, автором и временем, чтобы её нельзя было спутать с фактом;
  * хранилище недоступно для записи → операция отвечает ошибкой, а не молчит:
    потеря заметки не должна выглядеть как «заметка есть, но не нашлась»;
  * дубликаты не схлопываются молча — их видно по списку (одна и та же мысль,
    записанная дважды, остаётся двумя записями с разным временем).
"""

from __future__ import annotations

import time
import uuid
from typing import Any

#: Ограничения на размер, чтобы таблица не превратилась в свалку: текст заметки
#: — короткое утверждение, а не документ (по ТЗ — «короткие записи»).
MAX_TEXT = 4000
MAX_TAGS = 10
MAX_TAG_LEN = 40


class NotesStore:
    """Заметки в control-хранилище: append-only список с фильтрами."""

    def __init__(self, control) -> None:
        """`control` — ControlStore (или любой объект с `conn` и `cfg`)."""
        self.control = control
        self.conn = control.conn
        self._ensure_schema()

    # ---------------- схема ----------------

    def _ensure_schema(self) -> None:
        """Создать таблицу заметок, если её нет.

        Догоняющая миграция: журналы прежних корпусов не требуют удаления —
        таблица просто добавляется рядом. Индексы по задаче и времени нужны
        потому, что чтение идёт «сначала свежие» и «по задаче».
        """
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS note (
                note_id   TEXT PRIMARY KEY,
                ts        REAL NOT NULL,
                task      TEXT,
                agent     TEXT,
                rev_id    TEXT,
                path      TEXT,
                tags      TEXT,
                text      TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_note_task ON note(task);
            CREATE INDEX IF NOT EXISTS idx_note_ts ON note(ts DESC);
            """
        )
        self.conn.commit()

    # ---------------- запись ----------------

    def add(self, text: str, *, task: str = "", tags: list[str] | None = None,
            path: str = "", agent: str = "", rev_id: str = "") -> dict[str, Any]:
        """Записать заметку. Возврат — запись в том же виде, что отдаёт чтение."""
        body = (text or "").strip()
        if not body:
            raise ValueError("текст заметки пуст")
        if len(body) > MAX_TEXT:
            body = body[:MAX_TEXT]
        clean_tags = []
        for tag in tags or []:
            value = str(tag).strip()[:MAX_TAG_LEN]
            if value and value not in clean_tags:
                clean_tags.append(value)
            if len(clean_tags) >= MAX_TAGS:
                break
        note_id = uuid.uuid4().hex[:16]
        ts = time.time()
        self.conn.execute(
            "INSERT INTO note(note_id, ts, task, agent, rev_id, path, tags, text)"
            " VALUES(?,?,?,?,?,?,?,?)",
            (note_id, ts, (task or "").strip(), agent or "", rev_id or "",
             path or "", ",".join(clean_tags), body),
        )
        self.conn.commit()
        return {
            "note_id": note_id,
            "ts": ts,
            "task": (task or "").strip(),
            "agent": agent or "",
            "rev_id": rev_id or "",
            "path": path or "",
            "tags": clean_tags,
            "text": body,
            "source": "note",
        }

    # ---------------- чтение ----------------

    def list(self, *, task: str | None = None, query: str | None = None,
             limit: int = 50) -> list[dict[str, Any]]:
        """Заметки: свежие первыми, при равенстве времени — по идентификатору.

        Порядок детерминирован (НФТ-5): сортировка по `ts DESC, note_id DESC`
        не зависит от порядка вставки строк в файл.
        """
        sql = ("SELECT note_id, ts, task, agent, rev_id, path, tags, text"
               " FROM note")
        where: list[str] = []
        params: list[Any] = []
        if task:
            where.append("task = ?")
            params.append(task.strip())
        if query:
            # Подстрока, а не FTS: заметок десятки, и точное совпадение здесь
            # важнее ранжирования — «не нашёл» не должно зависеть от токенизации.
            where.append("text LIKE ?")
            params.append(f"%{query.strip()}%")
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY ts DESC, note_id DESC LIMIT ?"
        params.append(max(1, int(limit or 50)))
        out: list[dict[str, Any]] = []
        for row in self.conn.execute(sql, tuple(params)).fetchall():
            record = dict(row)
            tags = str(record.get("tags") or "")
            record["tags"] = [t for t in tags.split(",") if t]
            record["source"] = "note"
            out.append(record)
        return out

    def count(self, *, task: str | None = None) -> int:
        """Сколько заметок всего (и по задаче, если она названа)."""
        if task:
            row = self.conn.execute(
                "SELECT COUNT(*) AS n FROM note WHERE task = ?",
                (task.strip(),)).fetchone()
        else:
            row = self.conn.execute("SELECT COUNT(*) AS n FROM note").fetchone()
        try:
            return int(row["n"] or 0)
        except Exception:
            return int(row[0] or 0)
