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

import contextlib
import json
import os
import re
import secrets
import shutil
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from . import paths
from . import registry_facts
from . import util
from .config import ProjectConfig

#: Сколько секунд без обновления признака живости (`ts` в файле замка) считается
#: «держатель молчит». Живой прогон обновляет `ts` на каждой стадии индексора
#: (`Indexer._stage` → `Catalog.touch_corpus_lock`), поэтому порог в 15 минут
#: переживается только действительно зависшим процессом.
LOCK_HEARTBEAT_SEC = 900.0

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
    # `ready` — прогон построил и закрепил ревизию; `aborted` — прогон
    # прерван (бюджет времени, падение стадии), ревизия не закреплена.
    # Поле добавлено 28.10.2026: без него прерванный прогон в истории
    # выглядел как обычный, отличаясь только нулями.
    state: str = "ready"


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


class _CorpusLock:
    """Контекст замка корпуса: вход в `with` берёт, выход снимает.

    Отдельный объект, а не `@contextlib.contextmanager` на методе: так в
    `enter` видно, ВЗЯТ ли замок, и вызывающий код обязан это проверить —
    иначе «не взяли» превратилось бы в «проиндексировали без защиты».
    """

    def __init__(self, catalog: "Catalog", category: str,
                 timeout_sec: float, poll: float) -> None:
        self.catalog = catalog
        self.category = category
        self.timeout_sec = timeout_sec
        self.poll = poll
        self.acquired = False

    def __enter__(self) -> bool:
        self.acquired = self.catalog.acquire_corpus_lock(
            self.category, timeout_sec=self.timeout_sec, poll=self.poll)
        return self.acquired

    def __exit__(self, exc_type, exc, tb) -> bool:
        if self.acquired:
            self.catalog.release_corpus_lock(self.category)
        return False


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
        # Реестр — файл состояния: путь выбирает `paths` (см. этап 7).
        self.path = Path(path) if path else paths.state_file("catalog.json")
        self.lock_path = self.path.with_suffix(".lock")
        self._data: dict[str, Any] | None = None
        # Какие записи ЭТОТ экземпляр менял: только они идут в файл поверх
        # свежего содержимого (см. `_save_unlocked`).
        self._dirty: set[str] = set()
        # Держит ли ЭТОТ экземпляр замок реестра: нужно, чтобы `save()` не
        # пытался взять его повторно (второй захват того же файла тем же
        # процессом ждал бы самого себя все 30 секунд).
        self._holds_lock = False
        # Метки взятых замков (путь → token). Нужны, чтобы СНЯТЬ и обновить
        # только свой замок: без метки снятие чужого замка стало бы способом
        # обойти защиту, а не освободить ресурс.
        self._tokens: dict[str, str] = {}

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
        """Записать реестр и, если состав изменился, отметить это в журнале.

        `updated_at` двигается при КАЖДОМ сохранении, в том числе когда
        индексируется другой корпус и состав реестра не меняется. Поэтому дата
        обновления реестра — подвижный факт: документ, называющий её, стареет
        без единой правки своего текста (измерено 28.10.2026, ПРОМТ-И33).

        Журнал (`catalog/registry_history.jsonl`) пополняется ТОЛЬКО сменой
        состава — числа корпусов и проиндексированных. По нему проверка
        отличает «документ отстал» (такое состояние было) от «документ врёт»
        (такого состояния не было). Файл лежит вне корпуса индексации
        (`**/catalog/**` исключён в config/ragraf.yaml), поэтому его рост не
        сдвигает контрольную сумму корпуса и не зацикливает сторожа.
        Запись реестра берёт СВОЙ короткий замок. Без него две параллельные
        индексации разных корпусов читали один файл, дополняли его каждая по
        своему и вторая затирала запись первой (потерянная запись прогона).
        Замок берётся на время `read → write`, а не на всю индексацию.
        """
        held_before = self._holds_lock
        retries = 0
        if not held_before:
            # ЗАМОК — НЕ ПОВОД МОЛЧА ПОТЕРЯТЬ ЗАПИСЬ. Раньше здесь стояло
            # `return`: если замок не удавалось взять за 30 с, запись прогона
            # просто не происходила, и реестр тихо отставал от графа (замер
            # 06.11.2026: ревизии rev-1442 и rev-1443 в графе есть, в реестре
            # нет). Теперь несколько попыток, и если не вышло — исключение:
            # вызывающий код обязан сказать «реестр не обновлён», а не выдать
            # старый ревизию за текущую.
            for _attempt in range(3):
                retries = _attempt
                if self.acquire_lock(timeout_sec=10.0):
                    break
            else:
                self._trace("save_lock_failed", dirty=sorted(self._dirty),
                            attempts=3)
                raise RuntimeError(
                    "реестр занят другим процессом: запись прогона не выполнена")
        if retries:
            self._trace("save_lock_waited", dirty=sorted(self._dirty),
                        attempts=retries + 1)
        written = sorted(self._dirty)
        try:
            self._save_unlocked()
            self._trace("save_ok", dirty=written, lock_held=held_before)
        finally:
            if not held_before:
                self.release_lock()

    def _trace(self, event: str, **fields: Any) -> None:
        """Трассировка записи реестра: та же переменная, что у сторожа.

        Живёт здесь, потому что потеря записи происходит именно в момент
        `read → merge → write`, а не в вызывающей функции: без отметки "что
        писалось и поверх чего" гипотезы («не тот путь», «не тот файл»,
        "потерянная запись") неразличимы.
        """
        try:
            from .watch import trace_registry

            trace_registry(event, catalog_path=str(self.path),
                           entries=len(self.entries()), **fields)
        except Exception:  # noqa: BLE001 — трассировка вспомогательная
            pass

    def _save_unlocked(self) -> None:
        data = self.load()
        data["version"] = CATALOG_VERSION
        # СВЕЖЕЕ ЧТЕНИЕ ПОД ЗАМКОМ: пока шла индексация, другой процесс мог
        # записать свою разработку. Своё — из памяти, чужое — с диска, иначе
        # запись соседа будет молча затёрта копией, прочитанной до его работы.
        on_disk = util.read_json(self.path, default=None)
        if isinstance(on_disk, dict) and isinstance(on_disk.get("entries"), dict):
            merged = dict(on_disk["entries"])
            for category in self._dirty:
                mine = data["entries"].get(category)
                if mine is not None:
                    merged[category] = mine
            data["entries"] = merged
        else:
            # Файл не прочитался (или прочитался не тем, чем надо), и запись
            # идёт в память-снимок. Тогда чужие разработки теряются молча —
            # это тот же класс дефекта, что «потерянная запись», и он обязан
            # быть виден: раньше эта ветка была беззвучной.
            self._trace("save_merge_skipped", dirty=sorted(self._dirty),
                        on_disk=type(on_disk).__name__)
        data["updated_at"] = util.now_iso()
        registry_facts.record_state(
            self.path,
            registry_facts.registry_state(data.get("entries") or {}),
            data["updated_at"],
        )
        util.write_json(self.path, data, indent=2)
        self._trace("save_written", dirty=sorted(self._dirty),
                    entries_written=len(data.get("entries") or {}),
                    entries_on_disk=(len(on_disk.get("entries") or {})
                                     if isinstance(on_disk, dict) else None))
        self._dirty.clear()

    def acquire_lock(self, timeout_sec: float = 120.0, poll: float = 0.2) -> bool:
        """Взять блокировку ЗАПИСИ РЕЕСТРА (файл `catalog.json`).

        Это не то же самое, что индексация. Замер 06.11.2026 показал, чем плохо
        одно замка на всё: сервер HARNESS завис на разборе одного файла и держал
        `catalog.lock` часами, а вместе с ним ждали все остальные корпуса —
        любая операция получала таймаут. Теперь индекс блокируется ПО КОРПУСУ
        (`acquire_corpus_lock`), а этот замок — короткий, на запись реестра,
        и без него две параллельные индексации потеряли бы записи друг друга.

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
        if self._holds_lock:
            return True
        got = self._acquire_path(self.lock_path, timeout_sec, poll,
                                 category="<registry>")
        self._holds_lock = got
        return got

    def release_lock(self) -> None:
        self._release_path(self.lock_path)
        self._holds_lock = False

    # ---------------- замок по корпусу ----------------
    def corpus_lock_path(self, category: str) -> Path:
        """Файл замка индексации КОНКРЕТНОГО корпуса.

        Имя корпуса становится именем файла, поэтому всё, что не буква, цифра,
        точка, дефис и подчёркивание, заменяется на `_`: имя вида
        `13-vpn-199.195.197.95` допустимо и без замены, а произвольное — нет.
        """
        safe = re.sub(r"[^0-9A-Za-z._-]+", "_", str(category or "corpus"))[:80]
        return self.lock_path.parent / "locks" / f"{safe or 'corpus'}.lock"

    def corpus_lock(self, category: str, timeout_sec: float = 120.0,
                    poll: float = 0.2):
        """Контекст: замок индексации корпуса с гарантированным снятием.

        Зачем контекст, а не пара вызовов. Замок нужен НА КАЖДОМ пути,
        запускающем индексатор (AGENTS.md, инвариант 11), а таких путей четыре:
        `index` и `reindex` из CLI, `ensure` и кнопка в панели. Проверка
        06.11.2026: два одновременных `index` одного корпуса шли ПАРАЛЛЕЛЬНО и
        оба взяли `rev-0001` — то есть писали одну ревизию вдвоём; спасало
        только совпадение содержимого. Пара «взял/снял» в четырёх местах
        разошлась бы при первой же правке, поэтому здесь один контекст.

        Пример: `with Catalog().corpus_lock(cat) as got: if not got: ...`.
        """
        return _CorpusLock(self, category, timeout_sec, poll)

    def acquire_corpus_lock(self, category: str, timeout_sec: float = 120.0,
                            poll: float = 0.2) -> bool:
        """Взять замок индексации этого корпуса. False — занят дольше срока.

        Другой корпус при этом НЕ блокируется: именно этого не хватало, когда
        один тяжёлый корпус останавливал работу всех остальных.
        """
        return self._acquire_path(self.corpus_lock_path(category), timeout_sec, poll,
                                  category=category)

    def release_corpus_lock(self, category: str) -> None:
        self._release_path(self.corpus_lock_path(category))

    def _acquire_path(self, path: Path, timeout_sec: float, poll: float,
                      *, category: str = "") -> bool:
        """Занять файл-замок: атомарное создание, решение по ЖИВОСТИ держателя.

        Что было не так (замерено 06.11.2026, два дефекта сразу):

          1. **Проверка и запись — два шага.** `path.exists()` → `write_json`
             не атомарны: два процесса одновременно видели «файла нет», оба
             записывали свой и ОБА считали замок взятым. Защита замком в этом
             случае была только на бумаге.
          2. **Возраст старше 15 минут считался смертью владельца.** Полный
             прогон этого корпуса идёт 700 с, а зависший HARNESS держал замок
             8,3 ч; в обоих случаях второй процесс ЗАХВАТЫВАЛ живой замок и
             начинал вторую индексацию того же корпуса.

        Теперь: создание через `O_CREAT|O_EXCL` (ядерная атомарность), а
        захват чужого замка — только когда держатель МЁРТВ или его признак
        живости (`ts`) старше `LOCK_HEARTBEAT_SEC`. Возраст сам по себе ничего
        не решает: живой прогон обновляет `ts` на каждой стадии.
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.time() + timeout_sec
        while time.time() < deadline:
            try:
                handle = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                info = _read_lock(path)
                verdict, why = lock_verdict(info, path)
                if verdict == "alive":
                    time.sleep(poll)
                    continue
                # ЗАХВАТ: снимаем только тот замок, который сейчас же и прочли.
                # Перечитанная запись с другим pid или меткой означает, что
                # замок уже перешёл другому — тогда его не трогаем.
                if not _remove_if_same(path, info):
                    time.sleep(poll)
                    continue
                self._trace("lock_taken_over", path=str(path), verdict=verdict,
                            reason=why, holder=info.get("pid"))
                continue
            else:
                token = secrets.token_hex(8)
                payload = {"pid": _pid(), "ts": time.time(), "token": token,
                           "category": category or "", "started": time.time()}
                try:
                    with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as fh:
                        json.dump(payload, fh, ensure_ascii=False)
                except Exception:
                    _remove_if_same(path, payload)
                    time.sleep(poll)
                    continue
                self._tokens[str(path)] = token
                return True
        return False

    def _release_path(self, path: Path) -> None:
        """Снять СВОЙ замок. Чужой не снимается никогда.

        Свой — это либо тот, что взял ЭТОТ экземпляр (метка совпадает), либо тот,
        чей pid — текущий процесс: замок мог взять другой экземпляр `Catalog`
        того же процесса (так делают CLI и панель).
        """
        token = self._tokens.pop(str(path), "")
        info = _read_lock(path)
        if not info:
            return
        mine = bool(token and info.get("token") == token) or \
            int(info.get("pid") or 0) == _pid()
        if not mine:
            return
        try:
            path.unlink()
        except Exception:
            pass

    def touch_lock(self, path: Path) -> bool:
        """Обновить признак живости СВОЕГО замка. False — замок уже не наш."""
        token = self._tokens.get(str(path))
        info = _read_lock(path)
        if not info:
            return False
        if not (bool(token and info.get("token") == token)
                or int(info.get("pid") or 0) == _pid()):
            return False
        payload = dict(info)
        payload["ts"] = time.time()
        try:
            util.write_json(path, payload)
        except Exception:
            return False
        return True

    def touch_corpus_lock(self, category: str) -> bool:
        """Признак живости замка индексации корпуса (зовётся из индексатора)."""
        return self.touch_lock(self.corpus_lock_path(category))

    def lock_report(self) -> list[dict[str, Any]]:
        """Состояние всех файлов-замков: кто держит, жив ли, сколько молчит.

        Нужно периодической проверке: брошенные замки нельзя искать глазами в
        каталоге, потому что имя файла — это имя корпуса, а не вердикт.
        """
        base = self.lock_path.parent
        items: list[dict[str, Any]] = []
        for path in sorted(list(base.glob("*.lock")) + list((base / "locks").glob("*.lock"))):
            info = _read_lock(path)
            verdict, why = lock_verdict(info, path)
            heartbeat = float(info.get("ts") or 0.0)
            items.append({
                "path": str(path),
                "file": path.name,
                "kind": "registry" if path.parent == base else "corpus",
                "category": str(info.get("category") or path.stem),
                "pid": int(info.get("pid") or 0),
                "alive": _pid_alive(int(info.get("pid") or 0)),
                "idle_seconds": round(time.time() - heartbeat, 1) if heartbeat else None,
                "verdict": verdict,
                "reason": why,
            })
        return items

    def clear_abandoned_locks(self, *, force: bool = False,
                              older_than: float | None = None) -> list[dict[str, Any]]:
        """Снять БРОШЕННЫЕ замки. Возвращает снятые с причиной.

        Правило без `--force`: снимается только замок, чей держатель МЁРТВ, или
        замок, который не читается вовсе (битый файл ничего не защищает).
        Живой, но молчащий дольше `older_than` — это отдельное решение
        человека: `--force` и причина в журнале, а не молчание.
        """
        removed: list[dict[str, Any]] = []
        for item in self.lock_report():
            path = Path(item["path"])
            verdict = item["verdict"]
            idle = item["idle_seconds"]
            take = verdict in ("dead", "unknown")
            if not take and force:
                limit = older_than if older_than is not None else 0.0
                take = idle is None or idle >= limit
            if not take:
                continue
            info = _read_lock(path)
            if not _remove_if_same(path, info):
                continue
            item = dict(item)
            item["removed"] = True
            item["forced"] = bool(force and verdict == "silent")
            removed.append(item)
            self._trace("lock_cleared", path=str(path), verdict=verdict,
                        forced=bool(item["forced"]))
        return removed

    # ---------------- операции с записями ----------------

    def entries(self) -> dict[str, dict[str, Any]]:
        return dict(self.load().get("entries") or {})

    def get(self, category: str) -> Entry | None:
        raw = self.entries().get(category)
        return Entry.from_dict(raw) if isinstance(raw, dict) else None

    def put(self, entry: Entry) -> None:
        data = self.load()
        data["entries"][entry.category] = entry.to_dict()
        self._dirty.add(entry.category)
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
        completed: bool = True,
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
        # индекс, хотя индекса там нет вовсе (`.ragraf`
        # не существует). Правило P0-1 «пустая ревизия не записывается»
        # относится и к этому пути.
        rev_built = bool(str(rev_id or "").strip())
        if rev_built and completed:
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
        elif rev_built:
            # Прерванный прогон: ревизия НЕ закреплена, действующей остаётся
            # прежняя. Замер 28.10.2026 (batterypassport.ru): прогон упёрся в
            # бюджет времени на стадии «databases», `index_status` показывал
            # прежнюю ревизию с 842 файлами, а `catalog` — новую с нулями,
            # потому что запись прогона переписывала ревизию и размеры.
            entry.runs.insert(0, asdict(RunRecord(
                rev_id=rev_id,
                indexed_at=util.now_iso(),
                corpus_hash=corpus_hash,
                config_hash=config_hash,
                file_count=file_count,
                node_count=node_count,
                edge_count=edge_count,
                seconds=round(seconds, 3),
                trigger=trigger,
                state="aborted",
            )))
            entry.runs = entry.runs[:MAX_RUNS]
            # Ревизия, суммы и время индексации НЕ переписываются: прерванный
            # прогон ничего не построил, и объявлять его состоянием разработки
            # значило бы выдать прошлое за настоящее.
            entry.state = "aborted" if entry.rev_id else "registered"
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
        # ПОДТВЕРЖДЕНИЕ ЗАПИСИ ЧТЕНИЕМ С ДИСКА.
        #
        # Замер 06.11.2026: ревизии rev-1442/1443/1446/1448/1449 были
        # закреплены в графе, а в реестре оставался прежний номер: запись
        # уходила в ЧУЖОЙ каталог состояния (песочница с RAGRAF_CATALOG_DIR)
        # и исчезала вместе с ним, а вызывающий код считал запись успешной.
        # Сверка с собственным снимком в памяти этого не ловит: она всегда
        # сходится. Поэтому перечитывается ФАЙЛ, и только из него берётся
        # ответ «записано/не записано».
        if rev_built and completed:
            confirmed = ""
            try:
                fresh = Catalog(self.path).get(category)
                confirmed = str(getattr(fresh, "rev_id", "") or "") if fresh else ""
            except Exception as exc:  # noqa: BLE001 — непрочитанный файл и есть отказ
                confirmed = f"<не прочитан: {type(exc).__name__}>"
            self._trace("record_verify", category=category, rev_id=rev_id,
                        confirmed=confirmed)
            if confirmed != str(rev_id):
                raise RuntimeError(
                    "запись не подтвердилась чтением: в реестре "
                    f"{confirmed or 'нет записи'}, ожидался {rev_id}")
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


def _read_lock(path: Path) -> dict[str, Any]:
    """Прочесть файл замка. Пустой словарь — файла нет или он битый."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(raw, dict):
        return {}
    try:
        out = {
            "pid": int(raw.get("pid") or 0),
            "ts": float(raw.get("ts") or 0.0),
            "token": str(raw.get("token") or ""),
            "category": str(raw.get("category") or ""),
        }
        if raw.get("started"):
            out["started"] = float(raw.get("started"))
    except (TypeError, ValueError):
        return {}
    return out


def lock_verdict(info: dict[str, Any], path: Path) -> tuple[str, str]:
    """Решение о чужом замке: `alive` | `silent` | `dead` | `unknown`.

    Три состояния, а не два, потому что «живой» и «работающий» — разное.
    Живой, но молчащий дольше `LOCK_HEARTBEAT_SEC` — это зависший процесс: его
    замок ЗАХВАТЫВАЕТСЯ (иначе один зависший корпус простоит вечно), но факт
    захвата пишется в трассировку, а периодическая проверка называет его отдельно.
    """
    if not path.exists():
        return "dead", "файла нет"
    if not info or not info.get("pid"):
        # Битый или чужой формат: такой замок ничего не защищает, но и не
        # доказывает, что владелец жив.
        return "unknown", "файл не читается или в нём нет pid"
    pid = int(info.get("pid") or 0)
    if not _pid_alive(pid):
        return "dead", f"процесс {pid} не жив"
    heartbeat = float(info.get("ts") or 0.0)
    idle = time.time() - heartbeat if heartbeat else None
    if idle is not None and idle >= LOCK_HEARTBEAT_SEC:
        return "silent", (f"процесс {pid} жив, но признак живости не обновлялся "
                           f"{int(idle)} с (порог {int(LOCK_HEARTBEAT_SEC)} с)")
    return "alive", f"держит процесс {pid}, молчит {0 if idle is None else int(idle)} с"


def _remove_if_same(path: Path, info: dict[str, Any]) -> bool:
    """Снять замок, только если на диске всё ещё ТА ЖЕ запись.

    Между чтением и удалением замок мог перейти другому процессу; без сверки
    удалялся бы уже чужой замок, и два прогона пошли бы параллельно.
    """
    current = _read_lock(path)
    if info and current != info:
        return False
    if not info and not current:
        # Файл есть, но не читается (битый или чужого формата): защищать ему
        # нечего, поэтому он и снимается — иначе такая «защита» запрещала бы
        # уборку сама собой.
        if not path.exists():
            return False
    try:
        path.unlink()
        return True
    except FileNotFoundError:
        return True
    except Exception:
        return False


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
