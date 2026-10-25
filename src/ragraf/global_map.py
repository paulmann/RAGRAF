"""Глобальная карта метаданных: «что где лежит» без чтения содержимого.

Место в архитектуре
    Реестр разработок (`src/ragraf/catalog.py`) отвечает на вопрос «какие
    корпуса заведены и насколько свежи». Карта отвечает на другой вопрос:
    «какие файлы существуют в разрешённых корнях, где именно и когда изменены».
    Вместе они дают маршрутизатору (`src/ragraf/route.py`) основание выбрать
    локальный индекс вместо догадки.

Почему карта живёт вне `.ragraf`
    Контрольная сумма корпуса считается по файлам, которые пойдут в индекс
    (`src/ragraf/corpus.py:63-73`). Если бы карта попадала в тот же каталог и
    участвовала в сумме, сторож свежести пересобирал бы корпус бесконечно:
    каждый скан менял бы вход. Поэтому хранилище — `catalog/global_map.sqlite`,
    рядом с реестром и вне любого корпуса.

Что читается, а что нет
    Читаются только метаданные каталога: имя, размер, время правки, тип по
    расширению. Содержимое не открывается ни для одного файла — в схеме нет
    ни одной колонки под текст, хэш содержимого или фрагмент.

    Файл, подпадающий под `DEFAULT_SECRETS` (`src/ragraf/config.py:85`),
    не попадает в `file_meta` вовсе: для него пишется строка в `secret_place`
    (путь, правило, размер, время) — и только она. Это ровно та форма, которую
    разрешает правило проекта: имя, размер и правило, без значения.

Детерминизм
    Обход каталогов идёт по `sorted(имя)`, фингерпринт считается по
    отсортированным строкам `путь\\tразмер\\tвремя`. Два прогона на неизменённом
    дереве обязаны дать одно значение — иначе карта, как и сумма корпуса,
    начнёт дрожать.
"""

from __future__ import annotations

import mimetypes
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from . import store, util
from .config import DEFAULT_EXCLUDES, DEFAULT_SECRETS
from .sources import Source, SourceRegistry, probe_path as source_probe

SCHEMA_VERSION = "1"

# Пределы скана. Значения не «на всякий случай»: скан сетевого каталога без
# пределов уже приводил к таймаутам в этом проекте (38 675 файлов, 2,3 ГБ).
DEFAULT_LIMITS: dict[str, Any] = {
    "max_files": 200_000,
    "max_depth": 32,
    "max_seconds": 120.0,
    "max_errors": 20,
}

DDL = """
CREATE TABLE IF NOT EXISTS meta (
    k TEXT PRIMARY KEY,
    v TEXT
);
CREATE TABLE IF NOT EXISTS source (
    id INTEGER PRIMARY KEY,
    root TEXT UNIQUE NOT NULL,
    kind TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    priority INTEGER NOT NULL DEFAULT 0,
    added_at TEXT DEFAULT '',
    last_scan_at TEXT DEFAULT '',
    scan_status TEXT DEFAULT '',
    reason TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS file_meta (
    source_id INTEGER NOT NULL,
    rel_path TEXT NOT NULL,
    name TEXT NOT NULL,
    ext TEXT DEFAULT '',
    size INTEGER NOT NULL DEFAULT 0,
    mtime REAL NOT NULL DEFAULT 0,
    mime TEXT DEFAULT '',
    availability TEXT DEFAULT 'ok',
    first_seen TEXT DEFAULT '',
    last_seen TEXT DEFAULT '',
    PRIMARY KEY (source_id, rel_path)
);
CREATE INDEX IF NOT EXISTS idx_file_meta_name ON file_meta(name);
CREATE INDEX IF NOT EXISTS idx_file_meta_ext ON file_meta(ext);
CREATE TABLE IF NOT EXISTS secret_place (
    source_id INTEGER NOT NULL,
    rel_path TEXT NOT NULL,
    rule TEXT NOT NULL,
    size INTEGER NOT NULL DEFAULT 0,
    mtime REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (source_id, rel_path)
);
CREATE TABLE IF NOT EXISTS scan_run (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL,
    files_seen INTEGER NOT NULL DEFAULT 0,
    files_skipped INTEGER NOT NULL DEFAULT 0,
    files_truncated INTEGER NOT NULL DEFAULT 0,
    errors TEXT DEFAULT '',
    fingerprint TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_scan_run_source ON scan_run(source_id, id);
"""


@dataclass
class FileRow:
    rel_path: str
    name: str
    ext: str
    size: int
    mtime: float
    mime: str = ""


@dataclass
class SecretRow:
    rel_path: str
    rule: str
    size: int
    mtime: float


@dataclass
class ScanResult:
    source_id: int
    root: str
    status: str = "ok"                 # ok | unavailable | error | truncated
    files: int = 0
    secrets: int = 0
    skipped: int = 0
    truncated: bool = False
    fingerprint: str = ""
    errors: list[str] = field(default_factory=list)
    error_count: int = 0
    seconds: float = 0.0
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "root": self.root,
            "status": self.status,
            "files": self.files,
            "secrets": self.secrets,
            "skipped": self.skipped,
            "truncated": self.truncated,
            "fingerprint": self.fingerprint,
            "errors": list(self.errors),
            "error_count": self.error_count,
            "seconds": round(self.seconds, 3),
            "reason": self.reason,
        }


class GlobalMap:
    """Хранилище карты и скан разрешённых источников."""

    def __init__(self, path: str | Path | None = None) -> None:
        from .sources import CATALOG_DIR

        self.path = Path(path) if path else (CATALOG_DIR / "global_map.sqlite")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = store.connect(self.path)
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        self.conn.executescript(DDL)
        self.conn.execute(
            "INSERT OR REPLACE INTO meta(k, v) VALUES('schema', ?)", (SCHEMA_VERSION,)
        )
        self.conn.commit()

    def close(self) -> None:
        try:
            self.conn.close()
        except Exception:  # noqa: BLE001 — закрытие не имеет права падать
            pass

    # ---------------- синхронизация с реестром ----------------

    def sync_sources(self, registry: SourceRegistry | None = None) -> int:
        """Перенести реестр источников в таблицу `source`.

        Реестр — источник истины по составу и правам, карта — по результатам
        скана. Поэтому поле `enabled`/`priority`/`kind` всегда берётся из
        реестра, а `last_scan_at` остаётся прежним.
        """
        registry = registry or SourceRegistry()
        wanted = registry.load()["sources"]
        known = {
            int(row["id"]): row
            for row in self.conn.execute("SELECT id, root FROM source").fetchall()
        }
        seen: set[int] = set()
        for row in wanted:
            src = Source.from_dict(row)
            seen.add(src.id)
            if src.id in known:
                self.conn.execute(
                    "UPDATE source SET root=?, kind=?, enabled=?, priority=?, added_at=?"
                    " WHERE id=?",
                    (src.root, src.kind, 1 if src.enabled else 0, src.priority,
                     src.added_at, src.id),
                )
            else:
                self.conn.execute(
                    "INSERT INTO source(id, root, kind, enabled, priority, added_at)"
                    " VALUES(?,?,?,?,?,?)",
                    (src.id, src.root, src.kind, 1 if src.enabled else 0,
                     src.priority, src.added_at),
                )
        # Удалённые из реестра источники убираются из карты целиком: держать
        # метаданные каталога, доступ к которому отозван, — это хранение
        # сведений без разрешения. Удаление идёт в одной транзакции с
        # обновлением, без вложенного `with conn` (он коммитил бы середину).
        for sid in known:
            if sid not in seen:
                self.conn.execute("DELETE FROM file_meta WHERE source_id=?", (sid,))
                self.conn.execute("DELETE FROM secret_place WHERE source_id=?", (sid,))
                self.conn.execute("DELETE FROM scan_run WHERE source_id=?", (sid,))
                # Сама запись источника тоже уходит: иначе `map status`
                # показывал бы корень, доступ к которому уже отозван.
                self.conn.execute("DELETE FROM source WHERE id=?", (sid,))
        self.conn.commit()
        return len(seen)

    # ---------------- скан ----------------

    def scan(self, source: Source, *, limits: dict[str, Any] | None = None) -> ScanResult:
        """Просканировать один источник и заменить его строки в карте."""
        lim = dict(DEFAULT_LIMITS)
        if limits:
            lim.update(limits)
        started = time.monotonic()
        started_iso = util.now_iso()
        result = ScanResult(source_id=source.id, root=source.root)

        root = Path(source.root)
        if source.network and not source_probe(source.root):
            result.status = "unavailable"
            result.reason = "сетевой корень недоступен: хост или права"
            self._save_scan(source, result, started_iso, started, [], [])
            return result
        if not root.exists() or not root.is_dir():
            result.status = "unavailable"
            result.reason = "каталог недоступен: не существует или не является каталогом"
            self._save_scan(source, result, started_iso, started, [], [])
            return result

        files: list[FileRow] = []
        secrets: list[SecretRow] = []
        errors: list[str] = []
        error_count = 0
        truncated = False
        stop_reason = ""

        def note_error(message: str) -> None:
            nonlocal error_count
            error_count += 1
            if len(errors) < int(lim["max_errors"]):
                errors.append(message)

        def note_truncated(reason: str) -> None:
            nonlocal truncated, stop_reason
            if not truncated:
                truncated = True
                stop_reason = reason

        def walk(dir_path: Path, rel_dir: str, depth: int) -> Iterator[FileRow]:
            if depth > int(lim["max_depth"]):
                note_truncated(f"глубина больше {lim['max_depth']} уровней")
                return
            if time.monotonic() - started > float(lim["max_seconds"]):
                note_truncated(f"скан дольше {lim['max_seconds']} с")
                return
            try:
                entries = sorted(os.scandir(dir_path), key=lambda e: e.name)
            except OSError as exc:
                note_error(f"{rel_dir or '.'}: {type(exc).__name__}: {exc.strerror or exc}")
                return
            subdirs: list[tuple[Path, str]] = []
            for entry in entries:
                rel = f"{rel_dir}/{entry.name}" if rel_dir else entry.name
                rel = util.to_posix(rel)
                try:
                    is_dir = entry.is_dir(follow_symlinks=False)
                except OSError as exc:
                    note_error(f"{rel}: {type(exc).__name__}")
                    result.skipped += 1
                    continue
                if is_dir:
                    if entry.is_symlink():
                        result.skipped += 1
                        continue
                    if util.prune_dir(rel, DEFAULT_EXCLUDES):
                        result.skipped += 1
                        continue
                    subdirs.append((Path(entry.path), rel))
                    continue
                if not entry.is_file(follow_symlinks=False):
                    result.skipped += 1
                    continue
                if util.matches_any(rel, DEFAULT_EXCLUDES):
                    result.skipped += 1
                    continue
                try:
                    st = entry.stat(follow_symlinks=False)
                except OSError as exc:
                    note_error(f"{rel}: {type(exc).__name__}")
                    result.skipped += 1
                    continue
                hit_secret = util.matches_any(rel, DEFAULT_SECRETS)
                if hit_secret:
                    # ВАЖНО: содержимое не читается. В таблицу мест попадает
                    # только путь, правило, размер и время — этого достаточно,
                    # чтобы предупредить о небезопасном расположении.
                    secrets.append(SecretRow(rel_path=rel, rule=hit_secret,
                                             size=int(st.st_size), mtime=float(st.st_mtime)))
                    continue
                ext = Path(entry.name).suffix.lower()
                mime = mimetypes.guess_type(entry.name)[0] or ""
                if result.files >= int(lim["max_files"]):
                    note_truncated(f"файлов больше {lim['max_files']}")
                    return
                files.append(FileRow(rel_path=rel, name=entry.name, ext=ext,
                                     size=int(st.st_size), mtime=float(st.st_mtime),
                                     mime=mime))
                result.files += 1
                if result.files % 5000 == 0 and time.monotonic() - started > float(lim["max_seconds"]):
                    note_truncated(f"скан дольше {lim['max_seconds']} с")
                    return
            for path, rel in subdirs:
                if truncated:
                    return
                yield from walk(path, rel, depth + 1)

        # Скан идёт в список, затем пишется одной транзакцией: прерванный
        # процесс не оставит карту наполовину обновлённой.
        for _row in walk(root, "", 0):
            pass

        result.secrets = len(secrets)
        result.truncated = truncated
        result.error_count = error_count
        result.errors = errors
        result.reason = stop_reason
        if truncated:
            result.status = "truncated"
        elif error_count and not files:
            result.status = "error"
            result.reason = "ни одного файла не прочитано"
        else:
            result.status = "ok"
        result.fingerprint = self.fingerprint(files)
        self._save_scan(source, result, started_iso, started, files, secrets)
        result.seconds = time.monotonic() - started
        return result

    def scan_all(self, *, limits: dict[str, Any] | None = None) -> list[ScanResult]:
        registry = SourceRegistry()
        self.sync_sources(registry)
        out: list[ScanResult] = []
        for src in registry.sources(enabled_only=True):
            out.append(self.scan(src, limits=limits))
        return out

    @staticmethod
    def fingerprint(files: list[FileRow]) -> str:
        """Отпечаток карты: путь, размер и время, отсортированные по пути."""
        payload = "\n".join(
            f"{f.rel_path}\t{f.size}\t{int(f.mtime * 1_000_000)}"
            for f in sorted(files, key=lambda f: f.rel_path)
        )
        return util.sha256_text(payload)

    def _save_scan(self, source: Source, result: ScanResult, started_iso: str,
                   started: float, files: list[FileRow],
                   secrets: list[SecretRow]) -> None:
        now = util.now_iso()
        try:
            with self.conn:
                old = {
                    str(row["rel_path"]): str(row["first_seen"] or now)
                    for row in self.conn.execute(
                        "SELECT rel_path, first_seen FROM file_meta WHERE source_id=?",
                        (source.id,),
                    ).fetchall()
                }
                self.conn.execute("DELETE FROM file_meta WHERE source_id=?", (source.id,))
                self.conn.execute("DELETE FROM secret_place WHERE source_id=?", (source.id,))
                self.conn.executemany(
                    "INSERT OR REPLACE INTO file_meta(source_id, rel_path, name, ext,"
                    " size, mtime, mime, availability, first_seen, last_seen)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?)",
                    [
                        (source.id, f.rel_path, f.name, f.ext, f.size, f.mtime,
                         f.mime, "ok", old.get(f.rel_path, now), now)
                        for f in files
                    ],
                )
                self.conn.executemany(
                    "INSERT OR REPLACE INTO secret_place(source_id, rel_path, rule,"
                    " size, mtime) VALUES(?,?,?,?,?)",
                    [(source.id, s.rel_path, s.rule, s.size, s.mtime) for s in secrets],
                )
                self.conn.execute(
                    "UPDATE source SET enabled=?, last_scan_at=?, scan_status=?, reason=?"
                    " WHERE id=?",
                    (1 if source.enabled else 0, now, result.status, result.reason,
                     source.id),
                )
                self.conn.execute(
                    "INSERT INTO scan_run(source_id, started_at, finished_at, files_seen,"
                    " files_skipped, files_truncated, errors, fingerprint)"
                    " VALUES(?,?,?,?,?,?,?,?)",
                    (source.id, started_iso, now, result.files, result.skipped,
                     1 if result.truncated else 0,
                     "; ".join(result.errors)[:4000], result.fingerprint),
                )
        except Exception as exc:  # noqa: BLE001 — скан обязан вернуть причину, а не упасть
            result.status = "error"
            result.reason = f"не удалось записать карту: {type(exc).__name__}: {exc}"

    # ---------------- чтение ----------------

    def purge(self, source_id: int) -> None:
        """Удалить строки источника из карты. Файлы на диске не трогаются."""
        with self.conn:
            self.conn.execute("DELETE FROM file_meta WHERE source_id=?", (source_id,))
            self.conn.execute("DELETE FROM secret_place WHERE source_id=?", (source_id,))
            self.conn.execute("DELETE FROM scan_run WHERE source_id=?", (source_id,))

    def sources(self) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT s.*, "
            " (SELECT COUNT(*) FROM file_meta f WHERE f.source_id=s.id) AS files,"
            " (SELECT COUNT(*) FROM secret_place p WHERE p.source_id=s.id) AS secrets,"
            " (SELECT fingerprint FROM scan_run r WHERE r.source_id=s.id"
            "  ORDER BY r.id DESC LIMIT 1) AS fingerprint"
            " FROM source s ORDER BY s.priority DESC, s.root"
        ).fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["enabled"] = bool(item.get("enabled"))
            out.append(item)
        return out

    def secret_places(self, source_id: int | None = None) -> list[dict[str, Any]]:
        sql = ("SELECT p.source_id, p.rel_path, p.rule, p.size, p.mtime, s.root"
               " FROM secret_place p JOIN source s ON s.id = p.source_id")
        args: tuple[Any, ...] = ()
        if source_id is not None:
            sql += " WHERE p.source_id=?"
            args = (int(source_id),)
        sql += " ORDER BY p.rel_path"
        return [dict(r) for r in self.conn.execute(sql, args).fetchall()]

    def file_count(self, source_id: int | None = None) -> int:
        if source_id is None:
            row = self.conn.execute("SELECT COUNT(*) AS c FROM file_meta").fetchone()
        else:
            row = self.conn.execute(
                "SELECT COUNT(*) AS c FROM file_meta WHERE source_id=?", (int(source_id),)
            ).fetchone()
        return int(row["c"] if row else 0)

    def find(self, tokens: list[str], *, limit: int = 50,
             source_ids: list[int] | None = None) -> list[dict[str, Any]]:
        """Найти файлы по подстрокам в пути/имени. Порядок детерминирован.

        Это не поиск по содержимому: совпадение считается по имени и пути,
        поэтому запрос «AMD» находит каталог и файлы с этим именем, а не
        документы, где слово встречается в тексте.
        """
        clean = [t.strip().lower() for t in tokens if t and t.strip()]
        if not clean:
            return []
        where: list[str] = []
        args: list[Any] = []
        for token in clean:
            # Ищем и в относительном пути, и в корне источника: запрос по
            # имени разработки («AMD») обязан находить её файлы, даже если
            # само имя встречается только в корне. Замерено: до правки
            # `map find AMD` давал 0 при 249 файлах в карте.
            where.append("(lower(f.rel_path) LIKE ? OR lower(s.root) LIKE ?)")
            args.append(f"%{token}%")
            args.append(f"%{token}%")
        sql = ("SELECT f.source_id, f.rel_path, f.name, f.ext, f.size, f.mtime,"
               " f.mime, s.root, s.priority, s.kind"
               " FROM file_meta f JOIN source s ON s.id = f.source_id"
               " WHERE s.enabled=1 AND " + " AND ".join(where))
        if source_ids:
            sql += " AND f.source_id IN (" + ",".join("?" for _ in source_ids) + ")"
            args.extend(int(s) for s in source_ids)
        sql += " ORDER BY f.rel_path LIMIT ?"
        args.append(max(1, int(limit)))
        return [dict(r) for r in self.conn.execute(sql, args).fetchall()]

    def status(self) -> dict[str, Any]:
        rows = self.sources()
        return {
            "storage": util.to_posix(self.path),
            "sources": rows,
            "count": len(rows),
            "enabled": sum(1 for r in rows if r["enabled"]),
            "files": self.file_count(),
            "secrets": self.conn.execute(
                "SELECT COUNT(*) AS c FROM secret_place"
            ).fetchone()["c"],
            "schema": SCHEMA_VERSION,
        }
