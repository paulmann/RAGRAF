"""Реестр источников режима «Я это ты» — разрешённые корни для карты метаданных.

Зачем отдельный файл
    Настройки RAGRAF плоские (`catalog/settings.json`) и описывают поведение
    индексатора, а не список чужих каталогов. Список корней — это состояние
    доступа, а не настройка: его нужно видеть целиком, проверять и удалять
    по одному. Поэтому он живёт отдельным файлом рядом с реестром разработок —
    там же, где `catalog/db_access.json` (`src/ragraf/db_access.py:130`), и так
    же вне корпуса: пути не попадают ни в индекс, ни в поисковую выдачу.

Что здесь есть и чего нет
    * есть: добавление, удаление, включение/выключение, приоритет, вид корня;
    * нет: чтения содержимого, обхода дерева, индексации. Обход — в
      `global_map.py`, и он тоже читает только метаданные.

Почему путь нормализуется
    Один и тот же каталог приходит как `C:\\AutoClaw`, `c:/autoclaw` и
    `C:\\AutoClaw\\`. Без приведения к одной форме повторное добавление
    создавало бы вторую запись, а карта — второй источник с теми же файлами.
    Сравнение идёт по `os.path.normcase` от полного пути, хранение — POSIX.

Опасные корни
    Корень диска, домашний каталог, системный каталог Windows и любой путь
    внутри `.ragraf` отклоняются: первый превращает карту в скан всего диска,
    второй — в скан личных файлов, третий — в скан самой системы, четвёртый
    замыкает индекс на себя. Отказ называет причину, а не молчит.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from . import util

REPO = Path(__file__).resolve().parents[2]
CATALOG_DIR = REPO / "catalog"
SOURCES_NAME = "sources.json"
LOCK_NAME = "sources.lock"
MAX_SOURCES = 64
KINDS = ("project", "folder", "share")


class SourcesError(ValueError):
    """Ошибка реестра источников: неверный корень, дубль, лимит."""


@dataclass
class Source:
    """Один разрешённый корень.

    `root` хранится в POSIX-форме (как `Entry.root` в реестре разработок) —
    иначе один и тот же путь на Windows сравнивался бы с самим собой по-разному.
    """

    id: int
    root: str
    kind: str = "folder"
    enabled: bool = True
    priority: int = 0
    added_at: str = ""
    note: str = ""
    # Сетевой корень: включается только явным решением. Иначе один
    # недоступный хост превращает скан в ожидание сетевого таймаута.
    network: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "Source":
        return Source(
            id=int(data.get("id") or 0),
            root=str(data.get("root") or ""),
            kind=str(data.get("kind") or "folder"),
            enabled=bool(data.get("enabled", True)),
            priority=int(data.get("priority") or 0),
            added_at=str(data.get("added_at") or ""),
            note=str(data.get("note") or ""),
            network=bool(data.get("network", False)),
        )


class _FileLock:
    """Небольшая блокировка записи реестра.

    Реестр правится редко (добавление корня), но двумя путями сразу: CLI и
    панель. Потерять одну запись из-за гонки чтение-правка-запись — это молча
    исчезнувший источник, поэтому запись идёт под замком.

    Замок держится на `O_CREAT|O_EXCL`; «мёртвый» замок старше `stale_sec`
    снимается: иначе прерванный процесс запретил бы добавление навсегда.
    """

    def __init__(self, path: Path, *, timeout_sec: float = 10.0,
                 stale_sec: float = 60.0) -> None:
        self.path = Path(path)
        self.timeout_sec = float(timeout_sec)
        self.stale_sec = float(stale_sec)
        self._held = False

    def __enter__(self) -> "_FileLock":
        deadline = time.monotonic() + self.timeout_sec
        self.path.parent.mkdir(parents=True, exist_ok=True)
        while True:
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                try:
                    os.write(fd, json.dumps(
                        {"ts": time.time(), "pid": os.getpid()}
                    ).encode("utf-8"))
                finally:
                    os.close(fd)
                self._held = True
                return self
            except FileExistsError:
                try:
                    age = time.time() - self.path.stat().st_mtime
                except OSError:
                    age = 0.0
                if age > self.stale_sec:
                    try:
                        self.path.unlink()
                    except OSError:
                        pass
                    continue
                if time.monotonic() > deadline:
                    raise SourcesError(
                        "реестр источников занят другим процессом: "
                        f"{self.path.name} не освободился за {self.timeout_sec:.0f} с"
                    )
                time.sleep(0.1)
            except OSError as exc:
                raise SourcesError(
                    f"не удалось взять блокировку реестра: {type(exc).__name__}: {exc}"
                ) from exc

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._held:
            try:
                self.path.unlink()
            except OSError:
                pass
            self._held = False


def _is_unc(path: str) -> bool:
    text = str(path or "").strip()
    return text.startswith("\\\\") or text.startswith("//")


def probe_path(path: str, timeout: float = 4.0) -> bool:
    """Доступен ли путь. Для сетевого — отдельным процессом с таймаутом.

    Почему процесс, а не поток: `os.scandir` на мёртвом UNC-хосте блокируется
    внутри ОС, и поток из пула невозможно прервать — `ThreadPoolExecutor`
    дождётся его при выходе. Дочерний процесс убивается по таймауту.
    """
    text = str(path or "")
    if not _is_unc(text):
        try:
            return Path(text).is_dir()
        except OSError:
            return False
    try:
        proc = subprocess.run(
            [sys.executable, "-c",
             "import os,sys; next(iter(os.scandir(sys.argv[1])), None)", text],
            capture_output=True, timeout=timeout,
        )
        return proc.returncode == 0
    except Exception:  # noqa: BLE001 — таймаут, нет процесса, отказ доступа
        return False


class SourceRegistry:
    """Файл `catalog/sources.json`: чтение, проверка, правка."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path else (CATALOG_DIR / SOURCES_NAME)
        self.lock_path = self.path.with_name(LOCK_NAME)

    # ---------------- чтение ----------------

    def load(self) -> dict[str, Any]:
        raw = util.read_json(self.path, default=None)
        if not isinstance(raw, dict):
            return {"version": 1, "sources": []}
        rows = raw.get("sources")
        if not isinstance(rows, list):
            rows = []
        clean: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            try:
                clean.append(Source.from_dict(row).to_dict())
            except (TypeError, ValueError):
                continue
        return {"version": 1, "sources": clean}

    def sources(self, *, enabled_only: bool = False) -> list[Source]:
        rows = [Source.from_dict(r) for r in self.load()["sources"]]
        if enabled_only:
            rows = [s for s in rows if s.enabled]
        # Порядок детерминирован: приоритет по убыванию, затем путь.
        # Иначе `sources list` и карта дрожали бы от порядка вставки.
        return sorted(rows, key=lambda s: (-s.priority, s.root))

    def get(self, ident: int | str) -> Source | None:
        """Найти источник по id или по корню (в любой форме записи)."""
        wanted = str(ident)
        for src in self.sources():
            if wanted.isdigit() and src.id == int(wanted):
                return src
            if src.root == wanted:
                return src
            try:
                if os.path.normcase(str(Path(src.root))) == os.path.normcase(
                    str(Path(wanted))
                ):
                    return src
            except (OSError, ValueError):
                continue
        return None

    # ---------------- запись ----------------

    def _save_locked(self, rows: list[Source]) -> None:
        payload = {
            "version": 1,
            # Сортировка по id: файл не зависит от порядка правок.
            "sources": [s.to_dict() for s in sorted(rows, key=lambda s: s.id)],
        }
        util.write_json(self.path, payload)

    def add(self, root: str | Path, *, kind: str | None = None,
            priority: int = 0, note: str = "", network: bool = False) -> Source:
        raw = str(root or "").strip()
        # URL-коннекторы (ssh://, s3://, webdav://, http://) не реализованы.
        # Отказ обязан быть явным: иначе путь превращается в «не существует»,
        # и владелец ищет проблему в правах вместо отсутствующего коннектора.
        if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", raw):
            raise SourcesError(
                "URL-коннекторы (ssh/s3/webdav/http) не реализованы: "
                "смонтируйте ресурс как путь или используйте UNC с --network"
            )
        unc = _is_unc(raw)
        norm = self.normalize_root(root)
        unc = unc or _is_unc(norm)
        if unc and not network:
            raise SourcesError(
                "сетевой путь требует явного согласия: добавьте --network "
                "(в панели — флажок «сетевой путь»)"
            )
        if unc:
            if not probe_path(norm):
                raise SourcesError(
                    f"сетевой корень недоступен сейчас: {norm}; "
                    "проверьте хост, права и повторите"
                )
            kind = kind or "share"
        else:
            allowed, why = self.check_root(norm)
            if not allowed:
                raise SourcesError(why)
        if kind is not None and kind not in KINDS:
            raise SourcesError(
                f"неизвестный вид источника: {kind!r}; допустимо: {', '.join(KINDS)}"
            )
        priority = int(priority)
        if not -100 <= priority <= 100:
            raise SourcesError("приоритет источника должен быть в диапазоне -100..100")

        with _FileLock(self.lock_path):
            rows = self.sources()
            for existing in rows:
                if os.path.normcase(existing.root) == os.path.normcase(norm):
                    raise SourcesError(
                        f"источник уже добавлен: id={existing.id}, {existing.root}"
                    )
            if len(rows) >= MAX_SOURCES:
                raise SourcesError(
                    f"достигнут предел источников ({MAX_SOURCES}); "
                    "удалите неиспользуемые"
                )
            next_id = max((s.id for s in rows), default=0) + 1
            src = Source(
                id=next_id,
                root=norm,
                kind=kind or self.infer_kind(norm),
                enabled=True,
                priority=priority,
                added_at=util.now_iso(),
                note=str(note or ""),
                network=bool(network or unc),
            )
            rows.append(src)
            self._save_locked(rows)
            return src

    def remove(self, ident: int | str) -> Source:
        with _FileLock(self.lock_path):
            rows = self.sources()
            target = self.get(ident)
            if target is None:
                raise SourcesError(f"источник не найден: {ident}")
            kept = [s for s in rows if s.id != target.id]
            self._save_locked(kept)
            return target

    def set_enabled(self, ident: int | str, enabled: bool) -> Source:
        with _FileLock(self.lock_path):
            rows = self.sources()
            target = self.get(ident)
            if target is None:
                raise SourcesError(f"источник не найден: {ident}")
            for src in rows:
                if src.id == target.id:
                    src.enabled = bool(enabled)
            self._save_locked(rows)
            return self.get(target.id) or target

    def set_priority(self, ident: int | str, priority: int) -> Source:
        priority = int(priority)
        if not -100 <= priority <= 100:
            raise SourcesError("приоритет источника должен быть в диапазоне -100..100")
        with _FileLock(self.lock_path):
            rows = self.sources()
            target = self.get(ident)
            if target is None:
                raise SourcesError(f"источник не найден: {ident}")
            for src in rows:
                if src.id == target.id:
                    src.priority = priority
            self._save_locked(rows)
            return self.get(target.id) or target

    # ---------------- проверки ----------------

    @staticmethod
    def normalize_root(root: str | Path) -> str:
        """Привести корень к полному пути в POSIX-форме.

        `resolve(strict=False)` не требует существования: проверка отдельная,
        чтобы отказ называл причину, а не «файл не найден» из библиотеки.
        """
        raw = str(root or "").strip()
        if not raw:
            raise SourcesError("пустой путь к источнику")
        if _is_unc(raw):
            # Сетевой путь не проходит через realpath: на мёртвом хосте он
            # блокируется внутри ОС ещё до проверки доступности.
            return util.to_posix(raw).rstrip("/")
        path = Path(raw).expanduser()
        try:
            full = Path(os.path.realpath(path))
        except OSError:
            full = path.absolute()
        return util.to_posix(full)

    @staticmethod
    def check_root(root: str) -> tuple[bool, str]:
        """Проверить, что корень допустим. Возвращает (можно, причина)."""
        path = Path(root)
        if not path.exists():
            return False, f"путь не существует: {root}"
        if not path.is_dir():
            return False, f"это не каталог: {root}"

        parts = [p for p in Path(root).parts if p not in ("\\", "/")]
        drive, tail = os.path.splitdrive(str(path))
        if not tail.strip("\\/") and drive:
            return False, f"корень диска не индексируется: {root}"

        try:
            home = Path(os.path.realpath(Path.home()))
            if os.path.normcase(str(path)) == os.path.normcase(str(home)):
                return False, "домашний каталог целиком не индексируется: выберите подкаталог"
        except (OSError, RuntimeError):
            pass

        lowered = [p.lower() for p in parts]
        system_root = os.environ.get("SystemRoot") or "C:\\Windows"
        try:
            sys_norm = os.path.normcase(str(Path(os.path.realpath(system_root))))
            path_norm = os.path.normcase(str(path))
            if path_norm == sys_norm or path_norm.startswith(sys_norm + os.sep):
                return False, "системный каталог Windows не индексируется"
        except (OSError, RuntimeError):
            # Не смогли определить системный каталог — проверяем по имени сегмента.
            if "windows" in lowered:
                return False, "системный каталог Windows не индексируется"

        if ".ragraf" in [p.lower() for p in parts]:
            return False, "каталог .ragraf не может быть источником: индекс не читает сам себя"

        return True, ""

    @staticmethod
    def infer_kind(root: str) -> str:
        """Вид корня: сетевой путь, проект с маркерами или обычный каталог."""
        text = util.to_posix(root)
        if text.startswith("//"):
            return "share"
        try:
            from .ensure import looks_like_project

            ok, _why = looks_like_project(Path(root))
            return "project" if ok else "folder"
        except Exception:  # noqa: BLE001 — вид корня не имеет права мешать добавлению
            return "folder"
