"""Ревизии индекса: создание, запечатывание, ротация, история (ФТ-1, ФТ-32, ФТ-34).

Инвариант I1: внутри ревизии индекс иммутабелен. Ротация удаляет данные ревизии
из хранилищ, но НИКОГДА не удаляет rev_history и drift_archive.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from . import store, util
from .config import ProjectConfig

REV_RE = re.compile(r"^rev-(\d{4,})$")


@dataclass
class RevInfo:
    rev_id: str
    parent_rev: str | None
    created_at: str
    state: str
    capability: str
    joern_used: bool
    files: int
    nodes: int
    edges: int
    corpus_hash: str
    config_hash: str


class ControlStore:
    """Обёртка над control.sqlite (append-only для журнала расхождений).

    Про режим «только чтение». Обычно хранилище открывается на запись: в него
    пишут ревизии и телеметрия. Но бывают базы, которые читать можно, а писать
    нельзя: чужой каталог `.ragraf` без права записи, база на диске только для
    чтения. Раньше это делало НЕВОЗМОЖНЫМ чтение: конструктор падал, и панель
    не могла показать ни статистику, ни качество по такой разработке (измерено
    15.10.2026). Теперь при отказе записи хранилище открывается только для
    чтения, а `readonly` говорит вызывающему, что записывать нельзя: молча
    делать вид, что запись прошла, нельзя тем более.
    """

    def __init__(self, cfg: ProjectConfig) -> None:
        self.cfg = cfg
        self.readonly = False
        try:
            cfg.ragraf_dir.mkdir(parents=True, exist_ok=True)
            self.conn = store.connect(cfg.control_db)
            store.init_control(self.conn)
        except (OSError, sqlite3.OperationalError):
            # Запись невозможна — читаем то, что уже есть. Схему не создаём:
            # создавать её нечем, а читать существующую можно.
            self.conn = store.connect(cfg.control_db, readonly=True)
            self.readonly = True
        if not store.table_exists(self.conn, "rev_history"):  # pragma: no cover
            raise RuntimeError("не удалось инициализировать control.sqlite")

    # ---------------- ревизии ----------------

    def next_rev_id(self) -> str:
        row = self.conn.execute(
            "SELECT rev_id FROM rev_history ORDER BY rev_id DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return "rev-0001"
        m = REV_RE.match(str(row["rev_id"]))
        if not m:
            return "rev-0001"
        return f"rev-{int(m.group(1)) + 1:04d}"

    def current_rev(self) -> str | None:
        data = util.read_json(self.cfg.rev_file, default=None)
        if isinstance(data, dict):
            rev = data.get("rev_id")
            if isinstance(rev, str):
                return rev
        return None

    def latest_ready_rev(self) -> str | None:
        row = self.conn.execute(
            "SELECT rev_id FROM rev_history WHERE state = 'ready' ORDER BY rev_id DESC LIMIT 1"
        ).fetchone()
        return str(row["rev_id"]) if row else None

    def register_rev(
        self,
        rev_id: str,
        parent_rev: str | None,
        corpus_hash: str,
        config_hash: str,
        capability: str,
        joern_used: bool,
        project: str,
    ) -> None:
        self.conn.execute(
            """INSERT OR REPLACE INTO rev_history
               (rev_id, parent_rev, project, state, capability, created_at, corpus_hash,
                config_hash, files, nodes, edges, joern_used)
               VALUES (?, ?, ?, 'building', ?, ?, ?, ?, NULL, NULL, NULL, ?)""",
            (
                rev_id,
                parent_rev,
                project,
                capability,
                util.now_iso(),
                corpus_hash,
                config_hash,
                1 if joern_used else 0,
            ),
        )
        self.conn.commit()

    def seal_rev(
        self,
        rev_id: str,
        files: int,
        nodes: int,
        edges: int,
        capability: str,
        joern_used: bool,
        *,
        extractor_versions: dict[str, str] | None = None,
        cpg: dict | None = None,
        corpus_hash: str | None = None,
        config_hash: str | None = None,
    ) -> None:
        """Запечатать ревизию и записать rev.json — паспорт активной ревизии.

        Новые поля (`extractor_versions`, `cpg`, `corpus_hash`, `config_hash`)
        добавлены именованными и со значениями по умолчанию, поэтому прежние
        вызовы `seal_rev(rev_id, files, nodes, edges, capability, joern_used)`
        продолжают работать без правок. Пять исходных ключей rev.json
        (`rev_id`, `capability`, `joern_used`, `sealed_at`, `project`) не
        переименованы и не удалены: их читают `facade.rev_info` и
        `facade.op_index_status`.

        Зачем блок `cpg`. Измерено на rev-0069/rev-0070: у первой CPG была
        собрана (`coverage.cpg_used=1`, добавлено 1203 ребра вызовов), у второй
        — нет (`cpg_used=0`), но у ОБЕИХ ревизий были одинаковы `capability=full`,
        `joern_used=1`, `file_count`/`node_count`/`edge_count` и `config_hash`.
        Отличить сборку от переноса рёбер по rev.json было нельзя вообще:
        активная ревизия выглядела полноценной. Теперь режим CPG, факт сборки,
        число добавленных вызовов, донор переноса и признак понижения покрытия
        лежат в самом файле активной ревизии, а не только в graph.sqlite.
        """
        self.conn.execute(
            """UPDATE rev_history
               SET state = 'ready', sealed_at = ?, files = ?, nodes = ?, edges = ?,
                   capability = ?, joern_used = ?
               WHERE rev_id = ?""",
            (
                util.now_iso(),
                files,
                nodes,
                edges,
                capability,
                1 if joern_used else 0,
                rev_id,
            ),
        )
        # Нормализация блока CPG: пишутся ВСЕ пять ключей даже когда вызывающий
        # передал часть. Иначе читатель различал бы «нет данных» и «режим none»
        # через .get(), то есть молча — а это ровно тот дефект, который здесь
        # и закрывается.
        cpg_src = cpg or {}
        raw_calls = cpg_src.get("calls_added") or 0
        try:
            calls_added = int(raw_calls)
        except (TypeError, ValueError):
            calls_added = 0
        cpg_block = {
            # built — CPG собрана в этом прогоне; carried — не собиралась, но
            # рёбра вызовов взяты у донора; none — ни того, ни другого.
            "mode": str(cpg_src.get("mode") or "none"),
            "used": bool(cpg_src.get("used")),
            "calls_added": calls_added,
            "carried_from": cpg_src.get("carried_from"),
            "downgraded": bool(cpg_src.get("downgraded")),
        }
        # Активная ревизия ровно одна (архитектура §10).
        payload: dict = {
            "rev_id": rev_id,
            "capability": capability,
            "joern_used": joern_used,
            "sealed_at": util.now_iso(),
            "project": self.cfg.project,
            "extractor_versions": dict(extractor_versions or {}),
            "cpg": cpg_block,
        }
        # Суммы пишутся только когда вызывающий их передал: `None` здесь значит
        # «неизвестно», а не «пустая сумма», и подставлять пустую строку нельзя —
        # сторож свежести сравнивает суммы со своим расчётом.
        if corpus_hash is not None:
            payload["corpus_hash"] = corpus_hash
        if config_hash is not None:
            payload["config_hash"] = config_hash
        util.write_json(self.cfg.rev_file, payload)
        self.conn.commit()

    def fail_rev(self, rev_id: str, note: str) -> None:
        self.conn.execute(
            "UPDATE rev_history SET state = 'failed' WHERE rev_id = ?", (rev_id,)
        )
        self.conn.execute(
            "INSERT INTO event_log(ts, kind, payload) VALUES(?, 'rev_failed', ?)",
            (util.now_iso(), util.dumps({"rev_id": rev_id, "note": note})),
        )
        self.conn.commit()

    def history(self, limit: int = 50) -> list[RevInfo]:
        rows = self.conn.execute(
            """SELECT rev_id, parent_rev, created_at, state, capability, joern_used,
                      COALESCE(files,0) AS files, COALESCE(nodes,0) AS nodes,
                      COALESCE(edges,0) AS edges, corpus_hash, config_hash
               FROM rev_history ORDER BY rev_id DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        return [
            RevInfo(
                rev_id=str(r["rev_id"]),
                parent_rev=r["parent_rev"],
                created_at=str(r["created_at"]),
                state=str(r["state"]),
                capability=str(r["capability"]),
                joern_used=bool(r["joern_used"]),
                files=int(r["files"]),
                nodes=int(r["nodes"]),
                edges=int(r["edges"]),
                corpus_hash=str(r["corpus_hash"]),
                config_hash=str(r["config_hash"]),
            )
            for r in rows
        ]

    def rotate(self, retain: int) -> list[str]:
        """Удалить данные самых старых ревизий из хранилищ. История сохраняется."""
        ready = [r.rev_id for r in self.history(limit=10_000) if r.state == "ready"]
        keep = set(ready[:retain])
        removed: list[str] = []
        for rev_id in ready[retain:]:
            if rev_id in keep:  # pragma: no cover
                continue
            self._purge_rev_data(rev_id)
            self.conn.execute(
                "UPDATE rev_history SET state = 'rotated' WHERE rev_id = ?", (rev_id,)
            )
            removed.append(rev_id)
        self.conn.commit()
        return removed

    def _purge_rev_data(self, rev_id: str) -> None:
        """Удаление данных ревизии из графа и лексики. drift не трогается."""
        if self.cfg.graph_db.exists():
            conn = store.connect(self.cfg.graph_db)
            try:
                for table in (
                    "file", "node", "edge", "text_chunk", "cfg_edge", "dfg_edge",
                    "db_table", "db_column", "db_fk", "web_route", "invalidated",
                    "coverage", "stage_timing",
                ):
                    if store.table_exists(conn, table):
                        conn.execute(f"DELETE FROM {table} WHERE rev_id = ?", (rev_id,))
                conn.execute("DELETE FROM rev WHERE rev_id = ?", (rev_id,))
                conn.commit()
            finally:
                conn.close()
        if self.cfg.lexvec_db.exists():
            conn = store.connect(self.cfg.lexvec_db)
            try:
                # В vec0 нет колонки rev_id: идентификаторы собираем из vec_meta.
                try:
                    ids = [
                        str(r["chunk_id"])
                        for r in conn.execute(
                            "SELECT chunk_id FROM vec_meta WHERE rev_id = ?", (rev_id,)
                        ).fetchall()
                    ]
                except sqlite3.Error:
                    ids = []
                for table in ("ft_chunk", "chunk_map", "vec_meta", "chunk_text"):
                    if store.table_exists(conn, table):
                        conn.execute(f"DELETE FROM {table} WHERE rev_id = ?", (rev_id,))
                if store.table_exists(conn, "vec_chunk") and ids:
                    for start in range(0, len(ids), 400):
                        part = ids[start : start + 400]
                        marks = ",".join("?" for _ in part)
                        try:
                            conn.execute(
                                f"DELETE FROM vec_chunk WHERE chunk_id IN ({marks})", tuple(part)
                            )
                        except sqlite3.Error:
                            break
                conn.commit()
            finally:
                conn.close()

    # ---------------- события ----------------

    def log_event(self, kind: str, payload: dict) -> None:
        self.conn.execute(
            "INSERT INTO event_log(ts, kind, payload) VALUES(?, ?, ?)",
            (util.now_iso(), kind, util.dumps(payload)),
        )
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()


# --------------------------------------------------------------------------
# Графовое хранилище (запись)
# --------------------------------------------------------------------------


class GraphWriter:
    """Писатель в graph.sqlite. Одна ревизия за раз (архитектура §10)."""

    def __init__(self, cfg: ProjectConfig) -> None:
        self.cfg = cfg
        self.conn = store.connect(cfg.graph_db)
        store.init_graph(self.conn)

    def begin_rev(
        self,
        rev_id: str,
        parent_rev: str | None,
        corpus_hash: str,
        config_hash: str,
        extractor_versions: dict[str, str],
        capability: str,
        joern_used: bool,
    ) -> None:
        self.conn.execute("DELETE FROM rev WHERE rev_id = ?", (rev_id,))
        self.conn.execute(
            """INSERT INTO rev(rev_id, parent_rev, created_at, corpus_hash, config_hash,
                               extractor_versions, state, capability, joern_used)
               VALUES(?, ?, ?, ?, ?, ?, 'building', ?, ?)""",
            (
                rev_id,
                parent_rev,
                util.now_iso(),
                corpus_hash,
                config_hash,
                util.dumps(extractor_versions),
                capability,
                1 if joern_used else 0,
            ),
        )
        self.conn.commit()

    def add_files(self, rows: list[tuple]) -> int:
        return store.executemany_chunked(
            self.conn,
            """INSERT OR REPLACE INTO file(rev_id, path, lang, lines, sha256,
                                          parse_status, parse_note, encoding)
               VALUES(?, ?, ?, ?, ?, ?, ?, ?)""",
            rows,
            batch=self.cfg.index.batch_size,
        )

    def add_nodes(self, rows: list[tuple]) -> int:
        return store.executemany_chunked(
            self.conn,
            """INSERT OR REPLACE INTO node(rev_id, node_id, kind, name, fqn, path,
                                          start_line, end_line, signature, props, layer)
               VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            rows,
            batch=self.cfg.index.batch_size,
        )

    def add_edges(self, rows: list[tuple]) -> int:
        return store.executemany_chunked(
            self.conn,
            """INSERT OR REPLACE INTO edge(rev_id, edge_uid, src, dst, kind, provenance,
                                          evidence_path, evidence_line, confidence, sources, props)
               VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            rows,
            batch=self.cfg.index.batch_size,
        )

    def add_text_chunks(self, rows: list[tuple]) -> int:
        return store.executemany_chunked(
            self.conn,
            """INSERT OR REPLACE INTO text_chunk(rev_id, chunk_id, owner_node_id, path,
                                                 start_line, end_line, text_kind, raw)
               VALUES(?, ?, ?, ?, ?, ?, ?, ?)""",
            rows,
            batch=self.cfg.index.batch_size,
        )

    def add_routes(self, rows: list[tuple]) -> int:
        """Записать точки входа ревизии (P1-3).

        Таблица `web_route` существовала с релиза 1, но вставки в неё не было
        ни одной: операция `routes` всегда отвечала «не найдено», а
        `entrypoints` не знал ни одного маршрута. Пишет сюда индексатор, из
        того, что извлекли PHP/HTML/JS: `action=`, `src=`, `fetch(...)`,
        `Route::get(...)`, серверные включения.
        """
        return store.executemany_chunked(
            self.conn,
            """INSERT OR REPLACE INTO web_route(rev_id, route_id, node_id, route, method,
                                                 evidence_path, evidence_line, confidence)
               VALUES(?, ?, ?, ?, ?, ?, ?, ?)""",
            rows,
            batch=self.cfg.index.batch_size,
        )

    def count_edges(self, rev_id: str) -> int:
        """Фактическое число рёбер ревизии в таблице.

        Нужно для журнала: между подсчётом строк рёбер и запечатыванием
        ревизии работают стадии, которые ДОБАВЛЯЮТ рёбра (разрешение
        внешних имён и перенос графа свойств кода). Из-за этого число
        в журнале расходилось с содержимым индекса — на z расхождение
        было 12499 против 12956.

        Чтение из таблицы не может разойтись с содержимым: это и есть
        содержимое.
        """
        try:
            row = self.conn.execute(
                "SELECT COUNT(*) AS n FROM edge WHERE rev_id = ?", (rev_id,)
            ).fetchone()
            return int(row["n"]) if row else 0
        except sqlite3.Error:
            return 0

    def count_nodes(self, rev_id: str) -> int:
        """Фактическое число узлов ревизии в таблице (по тому же поводу)."""
        try:
            row = self.conn.execute(
                "SELECT COUNT(*) AS n FROM node WHERE rev_id = ?", (rev_id,)
            ).fetchone()
            return int(row["n"]) if row else 0
        except sqlite3.Error:
            return 0

    def set_coverage(self, rev_id: str, metric: str, value: float, note: str | None = None) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO coverage(rev_id, metric, value, note) VALUES(?, ?, ?, ?)",
            (rev_id, metric, value, note),
        )

    def add_stage_time(self, rev_id: str, stage: str, seconds: float, items: int | None, note: str | None = None) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO stage_timing(rev_id, stage, seconds, items, note)"
            " VALUES(?, ?, ?, ?, ?)",
            (rev_id, stage, seconds, items, note),
        )

    def finish_rev(
        self, rev_id: str, file_count: int, node_count: int, edge_count: int,
        capability: str, joern_used: bool,
    ) -> None:
        self.conn.execute(
            """UPDATE rev SET state = 'ready', file_count = ?, node_count = ?,
                              edge_count = ?, capability = ?, joern_used = ?
               WHERE rev_id = ?""",
            (
                file_count,
                node_count,
                edge_count,
                capability,
                1 if joern_used else 0,
                rev_id,
            ),
        )
        self.conn.commit()

    def counts(self, rev_id: str) -> dict[str, int]:
        out: dict[str, int] = {}
        for table in ("file", "node", "edge", "text_chunk"):
            row = self.conn.execute(
                f"SELECT COUNT(*) AS c FROM {table} WHERE rev_id = ?", (rev_id,)
            ).fetchone()
            out[table] = int(row["c"]) if row else 0
        return out

    def commit(self) -> None:
        self.conn.commit()

    def close(self) -> None:
        self.conn.commit()
        self.conn.close()
