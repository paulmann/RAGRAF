"""Каталог разработок: отдельный учёт по каждому проекту с контрольными суммами.

Назначение
    RAGRAF индексирует несколько проектов (пилот revoap.com, полигон
    z.batteryservice.ru, дальше — любые другие). Требований два:

      1. Информация о разработках хранится ОТДЕЛЬНО друг от друга: у каждой
         разработки свои ревизии, свой индекс, своя телеметрия, свой журнал
         расхождений. Смешивать их нельзя — факт из одного проекта, выданный
         по запросу к другому, это ровно тот класс ошибок, против которого
         RAGRAF и создавался.

      2. По каждой разработке хранится КОНТРОЛЬНАЯ СУММА корпуса и конфигурации.
         При новом обращении сначала сверяется сумма: если код изменился после
         последней индексации, индекс устарел, и это надо обнаружить ДО того,
         как агент начнёт на него опираться.

Реализация разделения
    Физическое разделение уже обеспечено: у каждого корпуса свой каталог
    `<root>/.ragraf` (индекс, ревизии, телеметрия) — так решено в архитектуре.
    Этот модуль добавляет СВОДНЫЙ реестр: где какая разработка лежит, когда
    индексировалась, с какой контрольной суммой и с какими показателями.
    Без сводного реестра нельзя ответить на вопрос «а всё ли проиндексировано»,
    не обходя диски вручную.

Контрольная сумма
    Сумма корпуса — `corpus_hash` из `corpus.scan()`: отсортированный список
    (путь, sha256) всех файлов, попадающих в индекс. Она:
      * считается по ФАЙЛАМ КОРПУСА, а не по индексу, поэтому её не меняет
        сама индексация (это проверяется отдельным тестом);
      * не включает служебный каталог `.ragraf` (он в исключениях по умолчанию),
        иначе хэш менялся бы от собственной работы;
      * не зависит от порядка обхода и времени — только от содержимого.

Совместимость
    Реестр — обычный JSON рядом с проектом, без базы. Чтение переносимо между
    версиями: отсутствующие поля читаются как пустые, а не рвут разбор.
"""

from __future__ import annotations

import json
import shutil
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from . import util
from .config import ProjectConfig

# Версия формата реестра. Нужна, чтобы при чтении старого файла было понятно,
# какие поля появились позже и требуют досчёта.
CATALOG_VERSION = 1

# Ограничение на длину журнала прогонов в записи разработки: реестр должен
# оставаться читаемым, а полная история прогонов есть в `.ragraf/logs`.
MAX_RUNS = 20


@dataclass
class RunRecord:
    """Один прогон индексации: когда, какой ревизией, с какими показателями."""

    rev_id: str
    indexed_at: str
    corpus_hash: str
    config_hash: str
    file_count: int = 0
    node_count: int = 0
    edge_count: int = 0
    seconds: float = 0.0
    trigger: str = "manual"   # manual | guard | watch | cli


@dataclass
class Entry:
    """Запись об одной разработке в реестре."""

    category: str              # человекочитаемое имя разработки (ключ учёта)
    root: str                  # абсолютный путь корпусу (POSIX-форма)
    config: str                # путь к конфигу корпуса (POSIX-форма)
    project: str = ""          # имя проекта из конфига
    indexed_at: str = ""       # время последней индексации (ISO)
    rev_id: str = ""           # последняя ревизия
    capability: str = ""       # full | degraded
    corpus_hash: str = ""      # контрольная сумма корпуса на момент индексации
    config_hash: str = ""      # контрольная сумма конфигурации
    file_count: int = 0
    node_count: int = 0
    edge_count: int = 0
    embedding_model: str = ""
    runs: list[dict[str, Any]] = field(default_factory=list)
    # Состояние разработки: `registered` — заведена и суммы зафиксированы, но
    # индекс не строился; `indexed` — есть ревизия. Нужно потому, что запись
    # «файлов 54 235, узлов 0» читается как сломанный индекс, хотя означает
    # ровно обратное: индекса ещё нет. Различие показывается в `catalog`.
    state: str = ""
    # Разрешена ли индексация этой разработки. Выключается галочкой в панели:
    # владелец может вести разработку в реестре, но не тратить место и время
    # на её индекс. Значение по умолчанию `True` — отсутствие поля в старом
    # файле реестра читается как «разрешено», а не как «запрещено».
    index_enabled: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "Entry":
        """Прочитать запись, устойчиво к отсутствующим полям старого формата.

        ВАЖНО про runs. В файле они лежат списком СЛОВАРЕЙ, а не объектов
        `RunRecord`: `to_dict` вызывает `asdict`, который рекурсивно превращает
        вложенные датаклассы в словари. Первая версия этого не учитывала, и код,
        читающий `run.seconds`, получал ошибку или ноль — из-за чего автоподбор
        порогов не видел ни одного замера и всегда возвращал базовые значения.
        Дефект найден проверкой tests/test_auto_thresholds.py.
        """
        known = {f for f in Entry.__dataclass_fields__}   # type: ignore[attr-defined]
        clean = {k: v for k, v in (data or {}).items() if k in known}
        clean.setdefault("category", "")
        clean.setdefault("root", "")
        clean.setdefault("config", "")

        raw_runs = clean.get("runs")
        if isinstance(raw_runs, list):
            fields = {f for f in RunRecord.__dataclass_fields__}  # type: ignore[attr-defined]
            restored: list[RunRecord] = []
            for item in raw_runs:
                if isinstance(item, RunRecord):
                    restored.append(item)
                    continue
                if isinstance(item, dict):
                    # Неизвестные поля отбрасываются: старый файл мог содержать
                    # поля, которых в текущем RunRecord уже нет.
                    subset = {k: v for k, v in item.items() if k in fields}
                    subset.setdefault("rev_id", "")
                    try:
                        restored.append(RunRecord(**subset))  # type: ignore[arg-type]
                    except TypeError:
                        continue
            clean["runs"] = restored
        return Entry(**clean)   # type: ignore[arg-type]


class Catalog:
    """Сводный реестр разработок. Один файл JSON, блокировка — файловая.

    Блокировка нужна, потому что сторож может быть вызван одновременно двумя
    агентами. Без неё две индексации одного корпуса пойдут параллельно и
    испортят показатели (счётчики и ревизии перезапишут друг друга).
    """

    def __init__(self, path: Path | None = None) -> None:
        # По умолчанию реестр лежит рядом с RAGRAF, а не в конкретном проекте:
        # он описывает НЕСКОЛЬКО разработок, и привязка к одной была бы против
        # самого требования о раздельном учёте.
        self.path = path or (Path(__file__).resolve().parents[2] / "catalog" / "catalog.json")
        self.lock_path = self.path.with_suffix(".lock")
        self._data: dict[str, Any] | None = None

    # ---------------- низкий уровень ----------------

    def load(self) -> dict[str, Any]:
        if self._data is None:
            raw = util.read_json(self.path, default=None)
            if not isinstance(raw, dict):
                raw = {"version": CATALOG_VERSION, "entries": {}}
            raw.setdefault("version", CATALOG_VERSION)
            raw.setdefault("entries", {})
            if not isinstance(raw["entries"], dict):
                raw["entries"] = {}
            self._data = raw
        return self._data

    def save(self) -> None:
        data = self.load()
        data["version"] = CATALOG_VERSION
        data["updated_at"] = util.now_iso()
        util.write_json(self.path, data, indent=2)

    def acquire_lock(self, timeout_sec: float = 120.0, poll: float = 0.2) -> bool:
        """Взять блокировку на индексацию. False — не удалось за отведённое время.

        Блокировка содержит pid и время. Проверок ДВЕ, и обе нужны:

          1. возраст (15 минут) — страховка на случай, когда pid не проверить;
          2. живость процесса — точная проверка, которая ловит случай, реально
             стоивший времени: прогон убивают (прерванная задача, закрытая
             консоль), а замок остаётся и до пятнадцати минут говорит «индексация
             уже выполняется другим процессом». Измерено на этом проекте: после
             прерванного прогона тесты `test_cpg_carry.py` и `test_ensure.py`
             падали четырьмя и одним провалом, причём не по своей вине —
             мешал мусорный замок от мёртвого процесса (pid существовал, но
             процесс уже завершился).

        Проверка живости безобидна, если платформа не умеет опрашивать процесс:
        исключение гасится, и решение остаётся за возрастом.
        """
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.time() + timeout_sec
        stale_after = 900.0   # 15 минут: индексация дольше считается зависшей
        while time.time() < deadline:
            if self.lock_path.exists():
                held_pid = 0
                try:
                    info = json.loads(self.lock_path.read_text(encoding="utf-8"))
                    started = float(info.get("ts", 0))
                    held_pid = int(info.get("pid", 0) or 0)
                except Exception:
                    started = 0.0
                fresh = bool(started) and (time.time() - started) < stale_after
                if fresh and held_pid and _pid_alive(held_pid):
                    time.sleep(poll)
                    continue
                # Замок мёртв по возрасту или по живности процесса — перезаписываем.
            try:
                util.write_json(self.lock_path, {"ts": time.time(), "pid": _pid()})
                return True
            except Exception:
                time.sleep(poll)
        return False

    def release_lock(self) -> None:
        try:
            if self.lock_path.exists():
                self.lock_path.unlink()
        except Exception:
            pass

    # ---------------- операции с записями ----------------

    def entries(self) -> dict[str, dict[str, Any]]:
        return dict(self.load().get("entries") or {})

    def get(self, category: str) -> Entry | None:
        raw = self.entries().get(category)
        return Entry.from_dict(raw) if isinstance(raw, dict) else None

    def put(self, entry: Entry) -> None:
        data = self.load()
        data["entries"][entry.category] = entry.to_dict()
        self.save()

    def register(self, category: str, cfg: ProjectConfig, config_path: str | Path) -> Entry:
        """Зарегистрировать (или обновить сведения о) разработку.

        Регистрация НЕ индексирует: она лишь заводит запись. Индексация —
        отдельное действие, потому что «где лежит» и «проиндексировано ли» —
        разные факты, и смешивать их значит терять, что именно произошло.
        """
        entry = self.get(category) or Entry(category=category, root="", config="")
        entry.root = util.to_posix(cfg.root)
        entry.config = util.to_posix(Path(config_path).resolve())
        entry.project = cfg.project
        entry.embedding_model = cfg.embedding.model
        self.put(entry)
        return entry

    def record_run(
        self,
        category: str,
        *,
        rev_id: str,
        corpus_hash: str,
        config_hash: str,
        file_count: int = 0,
        node_count: int = 0,
        edge_count: int = 0,
        seconds: float = 0.0,
        capability: str = "",
        trigger: str = "manual",
    ) -> Entry:
        """Записать результат прогона индексации в реестр.

        Контрольная сумма берётся из результата СКАНИРОВАНИЯ КОРПУСА (до или
        после индексации — она не зависит от неё), а не из индекса. Это важно:
        если бы сумма считалась по содержимому индекса, она менялась бы от
        самой индексации, и проверка «были ли изменения» потеряла бы смысл.
        """
        entry = self.get(category) or Entry(category=category, root="", config="")
        # В историю прогонов (runs) попадает ТОЛЬКО прогон, построивший ревизию.
        #
        # Что было не так. Регистрация разработки (`watch.register_project`)
        # фиксирует суммы корпуса ДО индексации и зовёт эту же точку с пустым
        # rev_id. Из-за этого в `runs` оказывалась запись «прогон, ревизия
        # пустая, узлов 0», а в реестре — разработка AutoClaw с 54 235 файлами
        # и нулём узлов (измерено 14.10.2026). Читается это как сломанный
        # индекс, хотя индекса там нет вовсе (`C:\AutoClaw\.ragraf`
        # не существует). Правило P0-1 «пустая ревизия не записывается»
        # относится и к этому пути.
        rev_built = bool(str(rev_id or "").strip())
        if rev_built:
            entry.rev_id = rev_id
            entry.capability = capability or entry.capability
            entry.corpus_hash = corpus_hash
            entry.config_hash = config_hash
            entry.file_count = file_count
            entry.node_count = node_count
            entry.edge_count = edge_count
            entry.indexed_at = util.now_iso()
            entry.runs.insert(0, asdict(RunRecord(
                rev_id=rev_id,
                indexed_at=entry.indexed_at,
                corpus_hash=corpus_hash,
                config_hash=config_hash,
                file_count=file_count,
                node_count=node_count,
                edge_count=edge_count,
                seconds=round(seconds, 3),
                trigger=trigger,
            )))
            entry.runs = entry.runs[:MAX_RUNS]
            entry.state = "indexed"
        else:
            # Регистрация: заполняются ТОЛЬКО пустые поля.
            #
            # Почему не «просто записать». Уже проиндексированная разработка
            # после повторной регистрации теряла ревизию (проверка поймала:
            # rev-0001 превращался в пустую строку), а её сумма корпуса
            # подменялась свежей — то есть устаревший индекс объявлялся
            # актуальным. Запись о построенном индексе принадлежит прогону,
            # а не регистрации.
            if not entry.corpus_hash:
                entry.corpus_hash = corpus_hash
            if not entry.config_hash:
                entry.config_hash = config_hash
            if not entry.file_count:
                entry.file_count = file_count
            if not entry.indexed_at:
                entry.indexed_at = util.now_iso()
            entry.state = "indexed" if entry.rev_id else "registered"
        self.put(entry)
        return entry

    def category_for_root(self, root: str | Path) -> str | None:
        """Найти разработку по корню корпуса. Нужно для автоопределения.

        Сравнение идёт по нормализованному пути без учёта регистра: на Windows
        один и тот же каталог пишется по-разному (короткий путь, разный регистр).
        """
        target = _norm(root)
        for name, raw in self.entries().items():
            entry = Entry.from_dict(raw)
            if _norm(entry.root) == target:
                return name
        return None

    def summary(self) -> dict[str, Any]:
        """Сводка по всем разработкам: сколько, когда индексировались, состояние."""
        out = []
        for name, raw in sorted(self.entries().items()):
            entry = Entry.from_dict(raw)
            out.append({
                "category": entry.category,
                "project": entry.project,
                "root": entry.root,
                "config": entry.config,
                "rev_id": entry.rev_id,
                "indexed_at": entry.indexed_at,
                "capability": entry.capability,
                "corpus_hash": entry.corpus_hash,
                "config_hash": entry.config_hash,
                "files": entry.file_count,
                "nodes": entry.node_count,
                "edges": entry.edge_count,
                "embedding_model": entry.embedding_model,
                "runs": len(entry.runs),
                "state": entry.state or ("indexed" if entry.rev_id else "registered"),
                "index_enabled": bool(entry.index_enabled),
            })
        return {"version": CATALOG_VERSION, "count": len(out), "entries": out}

    def set_index_enabled(self, category: str, enabled: bool) -> Entry:
        """Разрешить или запретить индексацию разработки.

        Запрет не удаляет данные: он только снимает разрешение ИНДЕКСИРОВАТЬ.
        Очистка индекса — отдельное действие (`clear_index`), потому что
        «больше не индексировать» и «стереть то, что уже посчитано» — разные
        решения, и связывать их молча нельзя: владелец выключил галочку, а у
        него пропали данные.
        """
        entry = self.get(category)
        if entry is None:
            raise KeyError(category)
        entry.index_enabled = bool(enabled)
        self.put(entry)
        return entry

    def clear_index(self, category: str) -> dict[str, Any]:
        """Удалить посчитанный индекс разработки. Реестр и история остаются.

        Удаляется каталог `.ragraf` корпуса целиком: индекс, векторный слой,
        журнал и телеметрия лежат в нём, и частичное удаление оставило бы
        разработку в состоянии «индекс есть, но неполный», которое панель
        показывала бы как сломанное. Реестр (`catalog/catalog.json`) лежит
        ВНЕ корпуса и не трогается: запись о разработке сохраняется, иначе
        её нечем было бы включить обратно.

        Возвращаются освобождённые байты и перечень удалённого, чтобы
        владелец видел, что именно исчезло, а не верил на слово.
        """
        entry = self.get(category)
        if entry is None:
            raise KeyError(category)
        root = Path(entry.root)
        ragraf_dir = root / ".ragraf"
        report: dict[str, Any] = {
            "category": category, "root": util.to_posix(root),
            "removed": False, "bytes": 0, "items": [],
        }
        # Предохранитель: удаляется только каталог с именем `.ragraf` и только
        # внутри корня разработки. Проверка нужна на случай повреждённой записи
        # реестра: цена ошибки — чужие данные.
        if ragraf_dir.name != ".ragraf" or not str(ragraf_dir).startswith(str(root)):
            raise ValueError("отказ: путь индекса вне корня разработки")
        if ragraf_dir.exists():
            freed = 0
            for path in sorted(ragraf_dir.rglob("*")):
                try:
                    if path.is_file():
                        freed += path.stat().st_size
                except OSError:
                    pass
            report["bytes"] = freed
            report["items"] = ["index", "lexvec", "control", "logs", "revisions"]
            try:
                shutil.rmtree(ragraf_dir)
                report["removed"] = True
            except OSError as exc:
                # Отказ удаления — это находка, а не успех. Измерено: под
                # файловой песочницей каталог чужой разработки не удаляется,
                # и прежняя редакция сообщала «removed: true», потому что
                # проверяла существование каталога ДО попытки. Владелец видел
                # «удалено», а индекс оставался на месте.
                report["removed"] = not ragraf_dir.exists()
                report["error"] = f"{type(exc).__name__}: {exc}"
            if not report["removed"]:
                # Запись НЕ переводится в «индекса нет»: данные на месте, и
                # снять ревизию значило бы соврать о состоянии.
                return report
        # Запись остаётся, но переходит в состояние «заведена, индекса нет»:
        # иначе панель показывала бы ревизию, данных которой больше нет.
        fresh = self.get(category)
        if fresh is not None:
            fresh.rev_id = ""
            fresh.capability = ""
            fresh.node_count = 0
            fresh.edge_count = 0
            fresh.state = "registered"
            self.put(fresh)
        return report


def _norm(path: str | Path) -> str:
    """Нормализовать путь для сравнения: POSIX-форма, без хвостового слеша."""
    text = util.to_posix(path).rstrip("/").lower()
    # Приведение к длинной форме пути: TEMP и профиль пользователя на Windows
    # отдаются короткими 8.3-путями, и без этого один каталог не находится.
    try:
        import os

        text = os.path.realpath(util.to_posix(path)).replace("\\", "/").rstrip("/").lower()
    except Exception:
        pass
    return text


def _pid() -> int:
    try:
        import os

        return os.getpid()
    except Exception:
        return 0


def _pid_alive(pid: int) -> bool:
    """Жив ли процесс с таким номером.

    Зачем: замок реестра хранит pid владельца, и проверка живости отличает
    брошенный замок от рабочего. Измеренный случай: убитый прогон оставил замок,
    и до истечения возраста замка любой другой запуск получал отказ «индексация
    уже выполняется другим процессом» — падали даже проверки, не связанные с
    причиной.

    Поведение подобрано так, чтобы НЕ отказать по ошибке:
      * Windows: `OpenProcess` с правом опроса (0x1000) возвращает дескриптор для
        живого процесса; ошибка означает «не опросить или мёртв»; если опрос
        невозможен по правам — считаем процесс живым (осторожное решение);
      * на других платформах: `os.kill(pid, 0)` — стандартный способ проверки;
      * при любой иной ошибке отвечаем `True`: ложное «мёртв» позволило бы
        двум индексациям пойти одновременно и перезаписать показатели.
    """
    if not pid or pid <= 0:
        return False
    if pid == _pid():
        return True
    try:
        import os
        import sys

        if sys.platform == "win32":
            import ctypes

            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
            if handle:
                kernel32.CloseHandle(handle)
                return True
            # Дескриптор не получен: либо процесс завершился, либо прав не хватило.
            # Ошибка доступа (5) — почти наверняка живой чужой процесс.
            return int(ctypes.get_last_error() or 0) == 5
        os.kill(int(pid), 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except Exception:
        return True
