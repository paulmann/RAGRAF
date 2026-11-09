"""Сторож контрольных сумм: сверка «изменился ли код» и реиндексация при изменении.

Назначение
    Требование владельца: при новом обращении к AutoClaw RAGRAF должен САМ
    понять, были ли изменения после последней индексации, и если были —
    переиндексировать. Порядок именно такой: сначала сверка, потом ответ.

Почему это нужно
    Индекс, отставший от кода, отвечает фактами о прошлом состоянии. Агент не
    видит разницы между «такой функции нет» и «функция появилась после
    индексации», а решение принимает по этому ответу. Раньше расхождение
    обнаруживалось отдельной командой `drift`, то есть только если про неё
    вспомнили. Теперь проверка выполняется перед работой и её результат
    попадает в каждый ответ операции (поле `freshness`).

Как считается «изменился»
    1. Сканируется корпус: пути и sha256 файлов (`corpus.scan`).
    2. Считается `corpus_hash` — та же функция, что при индексации, поэтому
       сравнение корректно, а не «похоже»: сумма по файлам не зависит от того,
       индексировали ли их, и от порядка обхода.
    3. Сравнивается с суммой из записи разработки (реестр) и из ревизии.
       Отличается — код изменился после индексации.
    4. Сравнивается `config_hash`: если правили конфиг корпуса (маски файлов,
       модель эмбеддингов, разбор), старая ревизия несопоставима с новой, даже
       когда файлы кода те же.

Что НЕ делает
    Не индексирует без нужды: если суммы совпали, работа не выполняется.
    Не глушит ошибки: если реиндексация упала, ответ операции будет помечен
    «индекс устарел» и приведена причина — молчаливая выдача старых фактов
    запрещена (ФТ-28, I3).
"""

from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import corpus, util
from .catalog import Catalog, Entry
from .config import ProjectConfig

#: Имя переменной, включающей трассировку записи прогона в реестр.
#: Зачем: расхождение «ревизия в графе есть, в реестре нет» не воспроизводилось
#: по коду — его видно только в момент записи, из ДВУХ процессов сразу. Трассировка
#: по умолчанию молчит (stderr клиента MCP иначе забьётся), включается одной
#: переменной и пишет машинные строки JSON.
TRACE_ENV = "RAGRAF_TRACE_REGISTRY"

#: Имя переменной с путём файла, куда дополнительно пишется та же трассировка.
#: Нужна для параллельных процессов: stderr смешался бы, а файл — нет.
TRACE_FILE_ENV = "RAGRAF_TRACE_REGISTRY_FILE"


#: Ключ в `control.sqlite`: каталог состояния, который записал последнюю
#: ревизию корпуса. Им проверяется, что ревизию создаёт тот же каталог
#: состояния, который про неё знает: иначе запись уходит в чужой (например,
#: временный) реестр и исчезает вместе с ним (замер 06.11.2026: rev-1442/
#: 1443/1446/1448/1449 в графе есть, в реестре нет).
REGISTRY_OWNER_KEY = "registry_owner_dir"


def registry_owner(cfg: ProjectConfig) -> str:
    """Какой каталог состояния записал последнюю ревизию корпуса ("" — не записан)."""
    try:
        from . import store

        conn = store.connect(cfg.control_db, readonly=True)
        try:
            row = conn.execute(
                "SELECT value FROM meta WHERE key = ?", (REGISTRY_OWNER_KEY,)
            ).fetchone()
        finally:
            conn.close()
        return str(row[0]) if row else ""
    except Exception:  # noqa: BLE001 — нет журнала — значит владелец не записан
        return ""


def remember_registry_owner(cfg: ProjectConfig, catalog_path: str | Path) -> None:
    """Запомнить каталог состояния, в котором записан прогон этого корпуса."""
    try:
        from . import store

        value = util.to_posix(Path(catalog_path).parent)
        conn = store.connect(cfg.control_db)
        try:
            conn.execute(
                "INSERT INTO meta(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (REGISTRY_OWNER_KEY, value),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 — отметка вспомогательная
        sys.stderr.write(
            f"[ragraf-watch] владелец реестра не записан: {type(exc).__name__}: {exc}\n")


def foreign_state_note(cfg: ProjectConfig, catalog: Catalog) -> str:
    """Причина НЕ индексировать, если корпус принадлежит другому каталогу состояния.

    Почему это нужно. Каталог состояния можно отвести переменной
    `RAGRAF_CATALOG_DIR` — так делает прогон проверок (`tools/check_all.py`)
    и любая песочница. При этом `.ragraf` корпуса НЕ отводится: сторож с
    отведённым каталогом строит настоящую ревизию в настоящем графе, а запись
    о прогоне кладёт в копию реестра, которая умирает вместе с песочницей.
    Замер 06.11.2026: прогон набора проверок (рев. 1442, 1443, 1446, 1448,
    1449) оставил пять ревизий без записи в реестре; в `stage_timing` этих же
    ревизий прямо сказано «каталог состояния отведён переменной
    RAGRAF_CATALOG_DIR» (`indexer.py:170`) и «режим без CPG делает watch.py».

    Возвращает текст причины или пустую строку, если владелец совпадает
    (или ещё не записан — тогда корпус опознаётся в этом каталоге состояния).
    """
    owner = registry_owner(cfg)
    if not owner:
        return ""
    current = util.to_posix(Path(catalog.path).parent)
    if util.to_posix(owner) == current:
        return ""
    return ("индекс этого корпуса принадлежит другому каталогу состояния "
            f"({owner}), а этот процесс работает с {current}: автоматическая "
            "переиндексация не запущена. Иначе ревизия появилась бы в графе, "
            "а запись о прогоне ушла бы в чужой (временный) реестр и пропала. "
            "Явная индексация (`ragraf.py index` или `reindex`) закрепит корпус "
            "за текущим каталогом состояния; либо снимите RAGRAF_CATALOG_DIR")


def trace_registry(event: str, **fields: Any) -> None:
    """Записать событие трассировки записи в реестр (stderr и/или файл).

    Пишутся ровно те поля, по которым различаются гипотезы: `pid` (кто),
    `trigger` (какой путь), `rev_id` (что записано), каталог реестра (куда
    записано) и итог записи. Формат — одна строка JSON на событие, чтобы
    файл разбирался без догадок о разделителях.
    """
    if not os.environ.get(TRACE_ENV):
        return
    record = {"ts": util.now_iso(), "pid": os.getpid(), "event": event}
    record.update(fields)
    line = json.dumps(record, ensure_ascii=False, default=str)
    try:
        sys.stderr.write(f"[ragraf-trace] {line}\n")
        sys.stderr.flush()
    except Exception:  # noqa: BLE001 — трассировка не имеет права ронять прогон
        pass
    path = os.environ.get(TRACE_FILE_ENV, "").strip()
    if path:
        try:
            with open(path, "a", encoding="utf-8", newline="\n") as handle:
                handle.write(line + "\n")
        except OSError:
            pass

# Сколько секунд считать результат проверки годным, чтобы не сканировать корпус
# на каждый вызов операции. Скан 123 файлов занимает ~0.2 с, но при серии из
# десятков вызовов это заметно; 15 секунд — компромисс: агент делает правку,
# и следующая проверка её увидит.
FRESHNESS_TTL_SEC = 15.0


@dataclass
class Freshness:
    """Состояние соответствия индекса коду."""

    category: str
    checked: bool
    changed: bool
    indexed: bool
    corpus_hash: str
    expected_hash: str
    config_hash: str
    expected_config_hash: str
    rev_id: str
    files_now: int
    # Результат проверки структуры баз: отдельное поле, потому что
    # это НЕ часть сверки корпуса. Пусто — сбор баз выключен или
    # проверка не выполнялась.
    db_check: dict | None = None
    changed_files: int = 0
    added_files: int = 0
    removed_files: int = 0
    reindexed: bool = False
    reindex_rev: str = ""
    reindex_seconds: float = 0.0
    stale: bool = False
    note: str = ""
    # Почему активной считается не самая новая ревизия графа. Пусто,
    # когда самая новая и есть активная. Показывать номер ревизии без
    # данных и молчать о причине — то же, что выдавать недостроенное
    # за проиндексированное.
    revision_note: str = ""
    # Итог записи прогона в реестр. Поле существует потому, что запись
    # может НЕ состояться при живом графе: замер 06.11.2026 — ревизии
    # rev-1442/1443/1446/1448/1449 есть в графе, а в реестре их нет, и ответ
    # об этом молчал (при успешной реиндексации примечание выбрасывалось).
    # Теперь расхождение идёт в КОНВЕРТ ответа, а не только в лог.
    registry_note: str = ""
    # Индекс принадлежит другому каталогу состояния: свежесть здесь не проверяется.
    # Это НЕ «устарел»: неизвестно, отстал ли индекс, — известна только
    # невозможность сверки. Смешивать эти два ответа нельзя.
    foreign_state: str = ""
    from_cache: bool = False
    # Чем доказана свежесть: "fingerprint" — дешёвой сверкой по путям, размерам
    # и времени правки; пусто — полной сверкой контрольных сумм. Разница
    # существенна: отпечаток НЕ читает содержимое файлов.
    verified_by: str = ""
    # --- накопление правок за сессию ---
    # Считается по журналу расхождений в окне от последней индексации.
    # Нужно, чтобы отличить «одна мелкая правка» от «двадцать правок за час».
    changes_since_index: int = 0
    changes_threshold: int = 0
    full_threshold: int = 0
    threshold_reached: str = ""      # "", "fast", "full"
    reindex_mode: str = ""           # "fast", "full"
    last_index_age_min: int = 0
    interval_reached: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Компактное представление для конверта ответа (ФТ-16).

        Поля названы так, чтобы агент понимал их без пояснений: это контракт,
        а не внутренняя структура.
        """
        out: dict[str, Any] = {
            "category": self.category,
            "checked": self.checked,
            "changed_since_index": self.changed,
            "index_present": self.indexed,
            "rev_id": self.rev_id,
            "db_check": self.db_check,
            "corpus_hash_now": self.corpus_hash,
            "corpus_hash_indexed": self.expected_hash,
            "config_changed": bool(
                self.expected_config_hash and self.config_hash != self.expected_config_hash
            ),
            "reindexed": self.reindexed,
        }
        if self.reindex_rev:
            out["reindex_rev"] = self.reindex_rev
            out["reindex_seconds"] = round(self.reindex_seconds, 2)
        if self.reindex_mode:
            out["reindex_mode"] = self.reindex_mode
        if self.changed:
            out["diff"] = {
                "changed": self.changed_files,
                "added": self.added_files,
                "removed": self.removed_files,
                "files_now": self.files_now,
            }
        # Накопление показывается всегда, когда включено: агенту важно видеть,
        # насколько индекс отстал, даже если порог ещё не достигнут.
        if self.changes_threshold:
            out["accumulation"] = {
                "changes_since_index": self.changes_since_index,
                "fast_threshold": self.changes_threshold,
                "full_threshold": self.full_threshold,
                "last_index_age_min": self.last_index_age_min,
                "interval_minutes": self.interval_reached,
            }
        if self.threshold_reached:
            out["threshold_reached"] = self.threshold_reached
        # `stale` отдаётся ВСЕГДА, а не только когда он true. Раньше поля не
        # было в свежем ответе, и отличить «свежо» от «поля нет по другой
        # причине» можно было только по отсутствию ключа — машиной это не
        # проверяется, а проверка свежести обязана проверяться (приёмка §5 п.5).
        out["stale"] = bool(self.stale)
        if self.stale:
            out["warning"] = self.note or "индекс устарел: факты могут не соответствовать коду"
        if self.from_cache:
            out["from_cache"] = True
        if self.verified_by:
            out["verified_by"] = self.verified_by
        # Причина выбора активной ревизии. Печатается только когда самая
        # новая ревизия не завершена: тогда без неё ответ показывал бы
        # номер, за которым нет данных, и молчал бы о причине.
        if self.revision_note:
            out["revision_note"] = self.revision_note
        if self.registry_note:
            out["registry_note"] = self.registry_note
        if self.foreign_state:
            out["freshness_limitation"] = self.foreign_state
        return out


@dataclass(frozen=True)
class CorpusState:
    """Состояние входа, снятое ОДНИМ проходом по корпусу.

    Все четыре величины считаются из одного `corpus.scan(digest=True)`, поэтому
    они не могут разойтись между собой: сумма и отпечаток описывают ОДИН И ТОТ
    ЖЕ набор файлов. Раздельные вызовы такой гарантии не дают — файл, изменённый
    между двумя обходами, давал пару «сумма от старого входа, отпечаток от нового».

    `by_path` — карта путь→sha256: по ней видно, ЧТО именно изменилось, а не только
    факт расхождения.
    """

    corpus_hash: str
    config_hash: str
    files: int
    by_path: dict[str, str]
    fingerprint: str
    total_bytes: int = 0


# Кэш результатов на время процесса: ключ — каталог категории.
_CACHE: dict[str, tuple[float, Freshness]] = {}


def current_state(cfg: ProjectConfig) -> "CorpusState":
    """Одна сверка корпуса: контрольная сумма, карта файлов И отпечаток входа.

    Зачем одна функция на три величины. Раньше `ensure` считал сумму трижды за
    вызов (в `check`, в `run_reindex` и снова после успеха), а отпечаток брался
    отдельным обходом дерева. На корпусе 944 файла один обход с чтением — 1,20 с,
    без чтения — 0,18 с (замер 01.11.2026), и эта плата набиралась на каждом
    шаге агента, потому что сторож проверяет свежесть перед каждой операцией.

    Смысл не изменился: сумма по-прежнему считается по `corpus.scan`, то есть
    тем же сканером, что и у индексатора (инвариант §4.4 AGENTS.md).
    """
    scanned = corpus.scan(cfg, digest=True)
    return CorpusState(
        corpus_hash=scanned.corpus_hash,
        config_hash=cfg.config_hash,
        files=len(scanned.files),
        by_path={f.path: f.sha256 for f in scanned.files},
        fingerprint=corpus.fingerprint_of(scanned),
        total_bytes=scanned.total_bytes,
    )


def current_hashes(cfg: ProjectConfig) -> tuple[str, str, int, dict[str, str]]:
    """Суммы корпуса и конфигурации прямо сейчас.

    Возвращает (corpus_hash, config_hash, число файлов, карта путь→sha256).
    Карта нужна, чтобы показать, ЧТО именно изменилось, а не только факт.
    Остаётся ради совместимости (attest, admin, синхронизация реестра); новая
    работа идёт через `current_state` — тот же скан плюс отпечаток входа.
    """
    state = current_state(cfg)
    return state.corpus_hash, state.config_hash, state.files, state.by_path


def indexed_hashes(category: str, cfg: ProjectConfig) -> tuple[str, str, str]:
    """Суммы, которые были зафиксированы при последней индексации.

    Источники в порядке доверия:
      1. запись ревизии в графе (`rev.corpus_hash`) — то, что реально
         индексировалось этим индексом;
      2. запись в реестре разработок — на случай, если граф пересоздан.
    """
    corpus_hash = ""
    config_hash = ""
    rev_id = ""
    try:
        from . import store

        conn = store.connect(cfg.graph_db, readonly=True)
        # Сортировка по rev_id, а НЕ по created_at: метка времени хранится
        # с точностью до секунды, и при двух ревизиях в одну секунду порядок
        # становился неопределённым (дефект найден тестом сторожа: после
        # реиндексации бралась прежняя ревизия, и сумма «не совпадала»).
        # rev_id — монотонный номер с ведущими нулями, поэтому его сортировка
        # совпадает с порядком создания (так уже сделано в revisions.py).
        # Берётся ЗАВЕРШЁННАЯ ревизия (`state='ready'`). Незавершённая
        # индексом не является, даже если её номер больше: в ней нет
        # данных, а сумма корпуса записана на момент начала сборки.
        # Замер 28.10.2026 (задание И42) на корпусе revoap.com: активной
        # считалась rev-0040 в состоянии `building` с 0 файлов, тогда как
        # готовой была rev-0039 с 2 722 файлами. Сторож сравнивал сумму
        # корпуса с числом недостроенной ревизии, и ответ «свежо/отстал»
        # ничего не говорил о том, что реально проиндексировано.
        row = conn.execute(
            "SELECT rev_id, corpus_hash, config_hash FROM rev"
            " WHERE state = 'ready' ORDER BY rev_id DESC LIMIT 1"
        ).fetchone()
        if row is None:
            # Ни одной завершённой ревизии: корпус в первой сборке.
            # Тогда берётся последняя по номеру, а причина выбора видна
            # в ответе через `revision_note`.
            row = conn.execute(
                "SELECT rev_id, corpus_hash, config_hash FROM rev"
                " ORDER BY rev_id DESC LIMIT 1"
            ).fetchone()
        if row is not None:
            rev_id = str(row["rev_id"])
            corpus_hash = str(row["corpus_hash"] or "")
            config_hash = str(row["config_hash"] or "")
        conn.close()
    except Exception:
        pass

    if not corpus_hash:
        entry = Catalog().get(category)
        if entry is not None:
            corpus_hash = entry.corpus_hash
            config_hash = entry.config_hash
            rev_id = rev_id or entry.rev_id

    return corpus_hash, config_hash, rev_id


def unfinished_revision(cfg: ProjectConfig, active_rev: str) -> str:
    """Почему активной считается не самая новая ревизия графа.

    Пустая строка — самая новая ревизия и есть активная. Иначе
    возвращается причина: незавершённая ревизия (`state='building'`)
    индексом не является, и молчать о том, что номер ревизии относится
    к недостроенной, значит выдавать её за проиндексированную.
    """
    try:
        from . import store

        conn = store.connect(cfg.graph_db, readonly=True)
        row = conn.execute(
            "SELECT rev_id, state, file_count FROM rev"
            " ORDER BY rev_id DESC LIMIT 1"
        ).fetchone()
        conn.close()
    except Exception:
        return ""
    if row is None:
        return ""
    newest = str(row["rev_id"])
    if newest == active_rev:
        return ""
    return (
        f"последняя ревизия {newest} не завершена (state={row['state']}, "
        f"файлов {int(row['file_count'] or 0)}): активной считается "
        f"{active_rev or 'ни одна'}"
    )

def measure_accumulation(cfg: ProjectConfig, rev_id: str) -> tuple[int, int]:
    """Сколько правок накопилось от последней индексации и сколько минут прошло.

    Считается по журналу расхождений: каждое расхождение — это изменение файла,
    зафиксированное после ревизии. Это точнее счётчика в памяти процесса:
    агент может запускаться заново, а журнал остаётся.

    Возвращает пару (число записей, минут с момента индексации).
    """
    entries = 0
    age_min = 0
    try:
        from . import store

        conn = store.connect(cfg.graph_db, readonly=True)
        row = conn.execute(
            "SELECT created_at FROM rev WHERE rev_id = ? LIMIT 1", (rev_id,)
        ).fetchone()
        conn.close()
        if row is not None and row["created_at"]:
            age_min = _age_minutes(str(row["created_at"]))
    except Exception:
        age_min = 0

    try:
        from .revisions import ControlStore

        control = ControlStore(cfg)
        try:
            entries = int(
                control.conn.execute(
                    "SELECT COUNT(*) AS c FROM drift_entry WHERE index_rev = ?", (rev_id,)
                ).fetchone()["c"]
            )
        finally:
            control.close()
    except Exception:
        entries = 0
    return entries, age_min


def _age_minutes(created_at: str) -> int:
    """Сколько минут прошло с момента индексации.

    Метка времени в ревизии хранится с часовым поясом. Если разобрать её
    не удалось, возвращается 0: неизвестный возраст не должен выглядеть
    как «только что проиндексировано» и запускать лишнюю работу.
    """
    from datetime import datetime

    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%d %H:%M:%S"):
        try:
            stamp = datetime.strptime(created_at, fmt)
            now = datetime.now(stamp.tzinfo) if stamp.tzinfo else datetime.now()
            return max(0, int((now - stamp).total_seconds() // 60))
        except ValueError:
            continue
    return 0


def choose_mode(
    cfg: ProjectConfig, rev_id: str, *, changed: bool
) -> tuple[str, dict[str, Any]]:
    """Выбрать глубину реиндексации: fast | full | none.

    Почему два уровня, а не один. Замерено на этом корпусе (124 файла):
      * индексация без CPG (AST, схема, лексика)   — 5,6 с;
      * индексация с CPG (граф свойств кода)       — 20,8 с.
    Разница в 3,7 раза. При работе агента, который правит файлы десятки раз
    за сессию, полная пересборка на каждое изменение тратит время на то, что
    не менялось. Поэтому:

      * при числе правок ниже порога — быстрый режим: AST-граф и схема
        обновляются, поиск и проверки схемы остаются достоверными;
      * при достижении порога полного пересбора — полный: пересобираются и
        межпроцедурные связи (CPG);
      * при превышении временного предела — полный пересбор, даже если правок
        было мало: за длительное время структура могла уйти.

    Возвращает (режим, подробности). Режим `none` — если правок нет.
    """
    changes, age_min = measure_accumulation(cfg, rev_id)
    # Пороги берутся из автоподбора (пункт 1.3.5): они зависят от ФАКТИЧЕСКОЙ
    # стоимости полного пересбора этого корпуса, а не только от конфига.
    # На корпусе в 124 файла автоподбор возвращает те же 5/20/15, на корпусе
    # в 10 раз тяжелее — примерно 15/60/45, и это правильнее константы.
    from .config import auto_thresholds

    fast_at, full_at, interval_minutes, auto_info = auto_thresholds(cfg)
    interval = max(1, int(interval_minutes))

    info: dict[str, Any] = {
        "changes_since_index": changes,
        "fast_threshold": fast_at,
        "full_threshold": full_at,
        "last_index_age_min": age_min,
        "interval_minutes": False,
        "thresholds_auto": bool(auto_info.get("applied")),
        "thresholds_basis": auto_info.get("reason") or "базовые значения конфигурации",
    }

    if not changed and changes == 0:
        return "none", info

    if changes >= full_at:
        info["reason"] = f"накоплено правок {changes} (порог полного пересбора {full_at})"
        return "full", info
    if age_min >= interval:
        info["interval_minutes"] = True
        reason = f"индекс старше {age_min} мин (предел {interval})"
        # ПЕРЕСБОР ПО ИНТЕРВАЛУ НЕ ДОЛЖЕН БЛОКИРОВАТЬ ОТВЕТ.
        #
        # Замер 06.11.2026 (жалоба HARNESS «MCP недоступен всю сессию»):
        # полный прогон этого корпуса идёт 558 с, быстрый — 129 с, а бюджет
        # одного прогона — 120 с. Прогон не мог завершиться НИ РАЗУ: бюджет
        # исчерпывался, ревизия не закреплялась, в истории копились записи
        # `building` с нулями, и следующее обращение снова видело «старше
        # предела» — то есть каждый вызов агента заново тратил минуты.
        # Поэтому если измеренная стоимость пересбора больше бюджета, сторож
        # НЕ запускает его внутри обращения: он отвечает по действующей ревизии
        # с честной пометкой «устарел» и названной причиной — обновление
        # остаётся отдельным осознанным действием (`reindex`).
        measured = float(auto_info.get("measured_seconds") or 0.0)
        budget = float(getattr(cfg.index, "budget_seconds", 0.0) or 0.0)
        if measured and budget and measured > budget:
            info["reason"] = (
                f"{reason}: пересбор стоит ~{measured:.0f} с при бюджете "
                f"{budget:.0f} с — обновление только явное (`reindex`)"
            )
            info["skip_reason"] = "cost_exceeds_budget"
            return "none", info
        info["reason"] = reason
        return "full", info
    info["reason"] = (
        f"накоплено правок {changes} (порог быстрой переиндексации {fast_at})"
        if changes >= fast_at else "обнаружены изменения в корпусе"
    )
    return "fast", info


def diff_corpus(
    expected: dict[str, str], actual: dict[str, str]
) -> tuple[int, int, int]:
    """(изменилось, добавилось, удалилось) между двумя картами путь→sha256."""
    changed = added = removed = 0
    for path, sha in actual.items():
        old = expected.get(path)
        if old is None:
            added += 1
        elif old != sha:
            changed += 1
    for path in expected:
        if path not in actual:
            removed += 1
    return changed, added, removed


def check(
    cfg: ProjectConfig,
    category: str | None = None,
    *,
    auto_reindex: bool = True,
    force: bool = False,
    use_cache: bool = True,
    budget_seconds: float | None = None,
    with_vectors: bool = True,
) -> Freshness:
    """Сверить индекс с кодом и при необходимости переиндексировать.

    Порядок действий важен: сначала сверка сумм (дешёвая операция: чтение
    файлов корпуса), и только при расхождении — индексация. Обратный порядок
    означал бы индексацию на каждом обращении, что бессмысленно дорого.
    """
    cat = category or category_for(cfg)
    now = time.time()

    if use_cache and not force:
        cached = _CACHE.get(cat)
        if cached is not None and (now - cached[0]) < FRESHNESS_TTL_SEC:
            fresh = cached[1]
            fresh.from_cache = True
            return fresh

    # Дешёвый путь (шаг 0.2 плана). Измерено на корпусе RAGRAF 23.10.2026:
    # полная сверка `current_hashes` — 1111 мс на 713 файлах (каждый файл
    # читается и хешируется), отпечаток входа `stat_fingerprint` — 173 мс
    # (только путь, размер и mtime). При совпадении отпечатка содержимое
    # доказуемо то же с точностью до правки, сохранившей и размер, и время;
    # `quick_check` именно поэтому возвращает None, когда доказать не удалось,
    # и тогда ниже идёт прежний полный путь.
    #
    # Почему при включённом сборе структуры БД быстрый путь НЕ применяется:
    # `check` в этом случае дополнительно сверяет структуру живой базы
    # (`db_structure_changed`), а отпечаток файлов о ней ничего не говорит.
    # Пропустить эту сверку значило бы выдать «свежо» без проверки — то есть
    # ровно тот дефект, ради которого сторож и заведён.
    _db_enabled = bool(getattr(getattr(cfg, "db", None), "enabled", False))
    if not force and not _db_enabled:
        quick = quick_check(cfg, cat)
        if quick is not None:
            _CACHE[cat] = (now, quick)
            return quick

    state = current_state(cfg)
    corpus_hash, config_hash, files_now, actual = (state.corpus_hash, state.config_hash,
                                                   state.files, state.by_path)
    expected_hash, expected_config, rev_id = indexed_hashes(cat, cfg)
    # Новая ревизия могла начать собираться и не завершиться: тогда
    # активной считается прежняя, и причина названа явно.
    _rev_note = unfinished_revision(cfg, rev_id)

    fresh = Freshness(
        category=cat,
        checked=True,
        changed=False,
        indexed=bool(expected_hash),
        corpus_hash=corpus_hash,
        expected_hash=expected_hash,
        config_hash=config_hash,
        expected_config_hash=expected_config,
        rev_id=rev_id,
        files_now=files_now,
        revision_note=_rev_note,
    )

    if not expected_hash:
        # Индекса нет вовсе: это не «изменился», это «не проиндексировано».
        fresh.changed = True
        fresh.stale = True
        fresh.note = "индекс не создан: факты недоступны, требуется индексация"
    else:
        index_changed = expected_config and config_hash != expected_config
        if corpus_hash == expected_hash and not index_changed:
            # Отпечаток ЛЕЧИТСЯ здесь, и это не косметика. `write_fingerprint`
            # вызывался только после индексации, а отпечаток отпечатка — уже
            # ПОСЛЕ правки; любое касание файла (перезапись без изменения
            # содержимого, утилита чистки, распаковка архива) расходило его с
            # сохранённым НАВСЕГДА: `quick_check` больше никогда не подтверждал
            # свежесть, и каждый вызов операции читал весь корпус.
            # Замер 01.11.2026 на этом корпусе: две правки в день делали
            # дешёвый путь мёртвым, и `ensure` шёл 2,2 с вместо 0,2 с.
            if state.fingerprint:
                write_fingerprint(cfg, rev_id, fingerprint=state.fingerprint,
                                  files=state.files, total_bytes=state.total_bytes)
            # Код не менялся. Проверяем структуру баз — но ТОЛЬКО если сбор
            # включён: при выключенном сборе это лишний запуск клиента СУБД на
            # каждой проверке, а при недоступной базе корпус кода не устаревает.
            try:
                from . import db_index as db_index_mod

                verdict = db_index_mod.db_structure_changed(cfg)
                fresh.db_check = verdict
                if verdict.get("checked") and verdict.get("changed"):
                    fresh.changed = True
                    fresh.stale = True
                    fresh.note = ("структура баз изменилась: " +
                                  str(verdict.get("reason") or ""))
                elif verdict.get("error"):
                    # Это НЕ устаревание: корпус кода в порядке, недоступна база.
                    fresh.db_check = {**verdict, "harmless": True}
            except Exception as exc:
                fresh.db_check = {"checked": False, "changed": False,
                                  "reason": f"{type(exc).__name__}: {exc}"}
            _CACHE[cat] = (now, fresh)
            return fresh

        fresh.changed = True
        # Что именно изменилось — по карте файлов из ревизии, если её удалось
        # прочитать. Иначе остаётся констатация факта расхождения.
        try:
            from . import store

            conn = store.connect(cfg.graph_db, readonly=True)
            rows = conn.execute(
                "SELECT path, sha256 FROM file WHERE rev_id = ?", (rev_id,)
            ).fetchall()
            conn.close()
            expected_map = {str(r["path"]): str(r["sha256"]) for r in rows}
            if expected_map:
                c, a, r = diff_corpus(expected_map, actual)
                fresh.changed_files, fresh.added_files, fresh.removed_files = c, a, r
        except Exception:
            pass

    if not auto_reindex:
        fresh.stale = True
        if index_changed_reason(fresh):
            fresh.note = index_changed_reason(fresh)
        # Накопление сообщается даже без реиндексации: агент должен видеть,
        # насколько индекс отстал, и решить сам.
        _fill_accumulation(cfg, rev_id, fresh, changed=True)
        _CACHE[cat] = (now, fresh)
        return fresh

    # Индексация может быть запрещена владельцем (галочка в панели). Проверка
    # стоит здесь, ПЕРЕД выбором глубины и запуском индексатора: иначе запрет
    # ничего не значил бы — сторож пересобирал бы разработку при каждом
    # обращении, и выключенная галочка не давала бы никакого эффекта.
    try:
        from . import catalog as catalog_mod

        _entry = catalog_mod.Catalog().get(cat)
        if _entry is not None and not bool(_entry.index_enabled):
            fresh.changed = True
            fresh.stale = True
            fresh.note = ("индексация этой разработки запрещена в панели: "
                          "разрешите её, чтобы обновлять индекс")
            _fill_accumulation(cfg, rev_id, fresh, changed=True)
            _CACHE[cat] = (now, fresh)
            return fresh
    except Exception:
        # Реестр недоступен — это не причина отказывать в проверке свежести.
        pass

    # Выбор глубины: быстрая или полная. Замерено: 5,6 с против 20,8 с.
    mode, mode_info = choose_mode(cfg, rev_id, changed=True)
    if mode == "none":
        # Расхождение есть, но пересбор не нужен или невыполним в бюджете.
        # Причина берётся из выбора режима: «меньше порога» и «не влезает в
        # бюджет» — разные ответы, и смешивать их нельзя.
        fresh.changed = True
        fresh.stale = True
        if mode_info.get("skip_reason") == "cost_exceeds_budget":
            fresh.note = ("факты могут не соответствовать коду: "
                          + str(mode_info.get("reason") or ""))
        else:
            fresh.note = ("изменения есть, но их меньше порога переиндексации: "
                          "факты могут не соответствовать коду")
        _fill_accumulation(cfg, rev_id, fresh, changed=True, info=mode_info)
        _CACHE[cat] = (now, fresh)
        return fresh

    # ЧУЖОЙ КАТАЛОГ СОСТОЯНИЯ: не пересобирать и не врать про «устарел».
    # Индекс может быть свежим — в этом окружении его просто нельзя ПРОВЕРИТЬ:
    # в копии реестра лежит чужая сумма. Называется ограничение, а не «stale»:
    # «stale» утверждало бы, что факты не соответствуют коду, чего здесь не знать.
    foreign = foreign_state_note(cfg, Catalog())
    if foreign:
        fresh.changed = True
        fresh.stale = False
        fresh.foreign_state = foreign
        fresh.note = foreign
        trace_registry("guard_foreign_state", category=cat, root=str(cfg.root))
        _fill_accumulation(cfg, rev_id, fresh, changed=True)
        _CACHE[cat] = (now, fresh)
        return fresh

    recorded: dict[str, Any] = {}
    ok, rev, seconds, note = run_reindex(cfg, cat, trigger="guard", mode=mode,
                                     budget_seconds=budget_seconds,
                                     state=state, recorded=recorded,
                                     with_vectors=with_vectors)
    fresh.reindexed = ok
    fresh.reindex_rev = rev
    fresh.reindex_seconds = seconds
    fresh.reindex_mode = mode
    if ok:
        # ИТОГ ЗАПИСИ В РЕЕСТР ИДЁТ В ОТВЕТ, А НЕ ТОЛЬКО В ЛОГ.
        # Замер 06.11.2026: пять ревизий (rev-1442/1443/1446/1448/1449) были
        # закреплены в графе, а записи о прогоне в реестре не было. Ответ при
        # этом говорил «reindexed, stale=false», потому что примечание
        # прогона выбрасывалось именно в успешной ветке. Теперь оно доходит
        # до конверта полем `registry_note`.
        if note:
            fresh.registry_note = note
        # После успешной индексации расхождения нет: суммы ПЕРЕСЧИТАНЫ там же,
        # где записаны в реестр (третьего обхода корпуса больше нет).
        new_hash = str(recorded.get("corpus_hash") or corpus_hash)
        new_config = str(recorded.get("config_hash") or config_hash)
        new_files = int(recorded.get("files") or files_now)
        fresh.corpus_hash = new_hash
        fresh.expected_hash = new_hash
        fresh.config_hash = new_config
        fresh.expected_config_hash = new_config
        fresh.files_now = new_files
        fresh.changed = False
        fresh.stale = False
        fresh.rev_id = rev
        # Счётчик накопления обнулён: правки вошли в новую ревизию.
        # Пороги передаются в ответ и после реиндексации — иначе агент
        # не видит, при каком числе правок сработает следующий пересбор.
        fresh.changes_since_index = 0
        fresh.changes_threshold = max(1, int(cfg.index.reindex_after_changes))
        fresh.full_threshold = max(2, int(cfg.index.full_reindex_after_changes))
        fresh.threshold_reached = ""
        fresh.last_index_age_min = 0
    else:
        fresh.stale = True
        fresh.note = note or "реиндексация не удалась: факты могут не соответствовать коду"

    _CACHE[cat] = (now, fresh)
    return fresh


def _fill_accumulation(
    cfg: ProjectConfig, rev_id: str, fresh: Freshness,
    *, changed: bool, info: dict[str, Any] | None = None
) -> None:
    """Заполнить поля накопления правок в результате проверки.

    Вынесено отдельной функцией: одни и те же сведения нужны в четырёх
    ветвях работы сторожа (нет индекса, изменения без реиндексации,
    ниже порога, после реиндексации), и дублировать их нельзя — расхождение
    в числах между ветвями читалось бы как неисправность.
    """
    if not cfg.index.change_counter:
        return
    try:
        if info is None:
            _, info = choose_mode(cfg, rev_id, changed=changed)
        fresh.changes_since_index = int(info.get("changes_since_index") or 0)
        fresh.changes_threshold = int(info.get("fast_threshold") or 0)
        fresh.full_threshold = int(info.get("full_threshold") or 0)
        fresh.last_index_age_min = int(info.get("last_index_age_min") or 0)
        fresh.interval_reached = bool(info.get("interval_minutes"))
        changes = fresh.changes_since_index
        if changes >= fresh.full_threshold > 0:
            fresh.threshold_reached = "full"
        elif changes >= fresh.changes_threshold > 0:
            fresh.threshold_reached = "fast"
    except Exception:
        # Неудача подсчёта не должна ломать проверку: поля просто останутся пустыми.
        pass


def index_changed_reason(fresh: Freshness) -> str:
    """Человекочитаемая причина, если расхождение вызвано конфигурацией."""
    if fresh.expected_config_hash and fresh.config_hash != fresh.expected_config_hash:
        return ("конфигурация корпуса изменилась: состав файлов или разбор отличаются "
                "от проиндексированного")
    return ""


def run_reindex(
    cfg: ProjectConfig, category: str, *, trigger: str = "guard", mode: str = "full",
    budget_seconds: float | None = None,
    state: "CorpusState | None" = None,
    recorded: dict[str, Any] | None = None,
    with_vectors: bool = True,
) -> tuple[bool, str, float, str]:
    """Выполнить индексацию и записать результат в реестр.

    Аргумент mode определяет глубину:
      * "full" — с построением CPG (медленно, но с межпроцедурными связями);
      * "fast" — без CPG (только AST, схема, лексика): в 3,7 раза быстрее,
        что замерено на этом корпусе (5,6 с против 20,8 с).

    `state` — состояние входа, УЖЕ снятое вызывающим (сторожем). Если оно передано,
    контрольная сумма в реестр пишется по нему, БЕЗ повторного чтения корпуса.
    Обязательное условие честности: перед записью заново снимается ДЕШЁВЫЙ
    отпечаток (`путь/размер/время`), и если файлы менялись во время прогона,
    сумма пересчитывается полностью. Инвариант «записанная сумма соответствует
    содержимому» остаётся, а третий полный обход в обычном случае исчезает.

    `recorded` — необязательный словарь, куда кладутся записанные величины
    (`corpus_hash`, `config_hash`, `files`, `fingerprint`, `rescan`): вызывающий
    берёт оттуда результат вместо нового обхода дерева.

    Возвращает (успех, ревизия, секунды, примечание). Ошибки не поднимаются
    наружу: вызывающий код должен получить состояние «устарело с причиной»,
    а не исключение — иначе обращение агента упадёт вместо того, чтобы выдать
    факты с честной пометкой.

    Блокировка реестра обязательна: два одновременных обращения к одному
    корпусу иначе запустят две индексации и перезапишут показатели.
    """
    catalog = Catalog()
    if not catalog.acquire_corpus_lock(category):
        return False, "", 0.0, "индексация уже выполняется другим процессом"

    started = time.time()
    trace_registry("reindex_start", category=category, trigger=trigger, mode=mode,
                   catalog_path=str(catalog.path), root=str(cfg.root))
    # Ревизия не создаётся в чужом каталоге состояния — см. `foreign_state_note`.
    foreign = foreign_state_note(cfg, catalog)
    if foreign:
        trace_registry("reindex_foreign_state", category=category, trigger=trigger,
                       mode=mode, catalog_path=str(catalog.path), owner=registry_owner(cfg))
        # Замок снимается ЗДЕСЬ: выход идёт до `try`, и его `finally` не выполнится.
        # Забыть это — значит оставить замок корпуса висеть до пятнадцати минут.
        catalog.release_corpus_lock(category)
        return False, "", 0.0, foreign
    # Режим Joern задаётся на время прогона и возвращается назад: конфигурация
    # читается из файла, и её нельзя менять навсегда из кода.
    joern_before = cfg.index.joern_enabled
    try:
        from .indexer import Indexer

        # Бюджет: явный из ensure, иначе из конфига корпуса (run_reindex
        # вызывается и сторожем, где явного бюджета нет).
        effective_budget = (
            budget_seconds if budget_seconds is not None
            else getattr(cfg.index, "budget_seconds", None)
        )

        def _index(use_mode: str):
            """Один прогон индексатора в заданном режиме."""
            cfg.index.joern_enabled = False if use_mode == "fast" else joern_before
            indexer = Indexer(cfg, mode=use_mode,
                              heartbeat=lambda: catalog.touch_corpus_lock(category))
            try:
                return indexer.run(budget_seconds=effective_budget,
                                    with_vectors=with_vectors)
            finally:
                indexer.close()

        report = _index(mode)
        # Бюджет исчерпан НА ПОЛНОМ прогоне — это не «ничего не вышло», а
        # «выбранная глубина в бюджет не влезает». Замер 01.11.2026 на этом
        # корпусе: полный прогон 208 с при бюджете 120 с, то есть ЛЮБАЯ правка
        # старше получаса не могла попасть в индекс вообще: сторож запускал
        # обречённый полный прогон при каждом обращении, прогон обрывался, и так
        # по кругу (4 брошенные ревизии за час). Поэтому один ограниченный шаг
        # вниз по глубине: быстрый прогон того же корпуса укладывается в бюджет
        # и обновляет лексику, AST и схему; связи CPG остаются от последнего
        # полного — это названо в ответе, а не спрятано.
        downgraded = ""
        if mode == "full" and not getattr(report, "ok", False) \
                and getattr(report, "budget_exceeded", False):
            report_fast = _index("fast")
            if getattr(report_fast, "ok", False):
                report = report_fast
                downgraded = ("полный прогон не уложился в бюджет "
                              f"({time.time() - started:.0f} с): выполнен быстрый, "
                              "связи CPG остались от последнего полного")
                mode = "fast"
            else:
                report = report_fast or report
        seconds = time.time() - started

        rev_id = ""
        note = ""
        files = nodes = edges = 0
        capability = ""
        try:
            from . import store

            conn = store.connect(cfg.graph_db, readonly=True)
            # В реестр идут числа ЗАПЕЧАТАННОЙ ревизии: у прерванного прогона
            # числа нулевые или неполные, и запись в реестр делала бы его лживым.
            active, skipped = store.active_revision(conn)
            if active is not None:
                rev_id = str(active.get("rev_id"))
                files = int(active.get("file_count") or 0)
                nodes = int(active.get("node_count") or 0)
                edges = int(active.get("edge_count") or 0)
                capability = str(active.get("capability") or "")
            if skipped:
                note = store.describe_skipped(skipped)
            conn.close()
        except Exception as exc:
            note = f"ревизия записана, но счётчики не прочитаны: {type(exc).__name__}"

        # ПРОГОН, КОТОРЫЙ НЕ ЗАКРЕПИЛ РЕВИЗИЮ, НЕ ЗАПИСЫВАЕТСЯ В РЕЕСТР.
        # Измеренный дефект 01.11.2026: прерванный по бюджету прогон записывался
        # как успешный, и реестр начинал утверждать, что текущий корпус лежит
        # в индексе ревизии, которой он не касается: сумма в реестре — новая,
        # файлы в графе — старые. Следом `ensure` отвечал «индекс актуален»,
        # хотя факты отставали. Это тот самый класс «сторож врёт», ради которого
        # он и заведён.
        sealed = bool(getattr(report, "ok", False)) and not getattr(
            report, "budget_exceeded", False)
        sealed_rev = str(getattr(report, "rev_id", "") or "")
        if not sealed or (sealed_rev and rev_id and sealed_rev != rev_id):
            reason = ("прогон прерван до закрепления ревизии"
                      if getattr(report, "budget_exceeded", False)
                      else "прогон завершился без закреплённой ревизии")
            stage = str(getattr(report, "budget_stage", "") or "")
            detail = (f"бюджет времени исчерпан на стадии «{stage}»" if stage else reason)
            if downgraded:
                detail = f"{downgraded}; {detail}"
            if recorded is not None:
                recorded.clear()
                recorded.update({"rescan": True, "recorded": False})
            trace_registry("reindex_not_sealed", category=category, trigger=trigger,
                           mode=mode, rev_id=rev_id, detail=detail)
            invalidate_cache(category)
            return False, "", seconds, (
                f"{detail}: ревизия не закреплена, в реестр ничего не записано, "
                "прежний индекс остался рабочим")

        # Контрольная сумма фиксируется ПОСЛЕ индексации, но считается по файлам
        # корпуса, поэтому совпадает с суммой до индексации (проверяется тестом).
        # Берётся из УЖЕ СДЕЛАННОГО скана, если таковой передан и вход за время
        # прогона не изменился: дешёвый отпечаток после прогона доказывает это
        # за 0,18 с вместо 1,20 с на корпусе 944 файла (замер 01.11.2026).
        now_fp, now_files, now_bytes = corpus.stat_fingerprint(cfg)
        rescan = False
        if state is not None and state.fingerprint and now_fp == state.fingerprint:
            corpus_hash = state.corpus_hash
            config_hash = state.config_hash
            file_total = state.files
            fingerprint = state.fingerprint
        else:
            # Файлы менялись во время прогона (или скана не было): считать заново.
            # Это редкий случай, и он обязан быть дорогим, а не неточным.
            rescan = True
            fresh_state = current_state(cfg)
            corpus_hash = fresh_state.corpus_hash
            config_hash = fresh_state.config_hash
            file_total = fresh_state.files
            fingerprint = fresh_state.fingerprint
        # ОТКАЗ ЗАПИСИ В РЕЕСТР ДОЛЖЕН БЫТЬ СЛЫШЕН. Замер 06.11.2026:
        # ревизии rev-1442/1443/1446 появились в графе, а в реестре остался
        # прежний номер — прогон считался успешным, и расхождение возникало
        # молча. Теперь ошибка записи попадает в примечание ответа: агент
        # видит «реестр не обновлён», а не старую ревизию за текущую.
        registry_note = ""
        try:
            catalog.record_run(
                category,
                rev_id=rev_id,
                corpus_hash=corpus_hash,
                config_hash=config_hash,
                file_count=files,
                node_count=nodes,
                edge_count=edges,
                seconds=seconds,
                capability=capability,
                trigger=f"{trigger}:{mode}",
            )
            trace_registry("reindex_recorded", category=category,
                           trigger=f"{trigger}:{mode}", mode=mode, rev_id=rev_id,
                           catalog_path=str(catalog.path))
            remember_registry_owner(cfg, catalog.path)
        except Exception as exc:  # noqa: BLE001 — прогон уже состоялся
            registry_note = (f"реестр не обновлён ({type(exc).__name__}): "
                             "номер ревизии в реестре отстаёт от графа")
            trace_registry("reindex_record_failed", category=category,
                           trigger=f"{trigger}:{mode}", mode=mode, rev_id=rev_id,
                           error=f"{type(exc).__name__}: {exc}",
                           catalog_path=str(catalog.path))
        # Отпечаток входа обновляется И ЗДЕСЬ. Это важнее, чем кажется: путь
        # сторожа (`run_reindex`) пишет реестр сам, минуя `record_index_run`, и
        # без этой строки отпечаток после переиндексации по расхождению остался
        # бы от ПРЕЖНЕЙ ревизии. Тогда дешёвая сверка никогда не подтверждала бы
        # свежесть, и каждый ответ снова читал бы весь корпус — то есть гарантия
        # «проверка перед каждым вызовом» превратилась бы в полную сверку на
        # каждом вызове. Измерено на живом корпусе 13.10.2026.
        try:
            write_fingerprint(cfg, rev_id, fingerprint=fingerprint,
                              files=file_total, total_bytes=now_bytes)
        except Exception as exc:  # noqa: BLE001 — отпечаток вспомогательный
            sys.stderr.write(
                f"[ragraf-watch] отпечаток не записан (guard): {type(exc).__name__}: {exc}\n"
            )
        if recorded is not None:
            recorded.update({
                "corpus_hash": corpus_hash,
                "config_hash": config_hash,
                "files": file_total,
                "fingerprint": fingerprint,
                "rescan": rescan,
            })
        # Кэш проверки относился к прежней ревизии.
        invalidate_cache(category)
        if registry_note:
            note = f"{note}; {registry_note}" if note else registry_note
        return True, rev_id, seconds, note
    except Exception as exc:
        return False, "", time.time() - started, f"{type(exc).__name__}: {exc}"
    finally:
        # Режим Joern восстанавливается даже при ошибке: иначе один сбойный
        # прогон отключил бы построение CPG для всего процесса.
        cfg.index.joern_enabled = joern_before
        catalog.release_corpus_lock(category)


def category_for(cfg: ProjectConfig) -> str:
    """Определить категорию разработки для корпуса.

    Сначала ищется в реестре по корню (разработка уже известна), иначе берётся
    имя проекта из конфига. Автосоздание записи при этом не выполняется:
    регистрация — отдельное явное действие, чтобы реестр не наполнялся мусором
    от случайных запусков.
    """
    found = Catalog().category_for_root(cfg.root)
    if found:
        return found
    return cfg.project


def invalidate_cache(category: str | None = None) -> None:
    """Сбросить кэш проверки — после правки кода или ручной индексации."""
    if category is None:
        _CACHE.clear()
    else:
        _CACHE.pop(category, None)


# Имя файла отпечатка входа. Лежит рядом с rev.json в каталоге индекса:
# отпечаток относится к РЕВИЗИИ (что именно было проиндексировано), а не к
# конфигу корпуса, и переживает перезапуск процесса.
FINGERPRINT_NAME = "watch_fingerprint.json"


def fingerprint_file(cfg: ProjectConfig) -> Path:
    """Путь файла отпечатка входа для этого корпуса."""
    return cfg.index_dir / FINGERPRINT_NAME


def write_fingerprint(cfg: ProjectConfig, rev_id: str, *,
                      fingerprint: str | None = None,
                      files: int | None = None,
                      total_bytes: int | None = None) -> str:
    """Запомнить отпечаток входа, соответствующий проиндексированной ревизии.

    Вызывается из ДВУХ мест, и оба обязательны: `record_index_run` (все пути
    индексации: ensure, CLI, админка) и `run_reindex` (сторож). Без этого
    дешёвая проверка не могла бы отличить «вход не менялся» от «сравнивать не
    с чем», и сторож был бы вынужден перечитывать корпус на каждом вызове.

    Готовый отпечаток можно передать аргументами: вызывающий обычно уже снял его
    тем же сканом, который дал контрольную сумму, и второй обход дерева только
    за ним — чистая плата (0,18 с на 942 файлах, замер 01.11.2026).

    Ошибка записи не роняет операцию: следующий вызов просто пойдёт полной
    сверкой — это дороже, но не неправильно.
    """
    if fingerprint is None or files is None or total_bytes is None:
        fingerprint, files, total_bytes = corpus.stat_fingerprint(cfg)
    payload = {
        "rev_id": rev_id,
        "fingerprint": fingerprint,
        "files": files,
        "bytes": total_bytes,
        "written_at": time.time(),
    }
    try:
        util.write_json(fingerprint_file(cfg), payload)
    except OSError as exc:
        sys.stderr.write(f"[ragraf-watch] отпечаток входа не записан: {exc}\n")
    return fingerprint


def read_fingerprint(cfg: ProjectConfig) -> dict | None:
    """Прочитать отпечаток входа. None — сравнивать не с чем."""
    try:
        data = util.read_json(fingerprint_file(cfg), default=None)
    except OSError:
        return None
    return data if isinstance(data, dict) else None


def quick_check(cfg: ProjectConfig, category: str | None = None) -> Freshness | None:
    """Дешёвая сверка «вход не менялся» — по отпечатку, без чтения файлов.

    Возвращает `Freshness` ТОЛЬКО когда удалось доказать, что вход не менялся с
    последней индексации. `None` означает «доказать не удалось» (отпечатка нет
    или он разошёлся) — тогда вызывающий обязан сделать полную сверку `check()`.

    Чего эта функция НЕ делает и не должна: она никогда не отвечает «свежо» по
    догадке. Отпечаток — это пути, размеры и время правки; файл, изменённый с
    сохранением размера и времени, различит только полная сверка. Ошибаться в
    эту сторону безопасно (лишняя полная сверка), в обратную — запрещено:
    это была бы молчаливая выдача фактов о прошлом состоянии кода (ФТ-28).
    """
    saved = read_fingerprint(cfg)
    if not saved or not saved.get("fingerprint"):
        return None
    try:
        fingerprint, files, _ = corpus.stat_fingerprint(cfg)
    except Exception as exc:  # noqa: BLE001 — дешёвый путь не имеет права падать
        sys.stderr.write(f"[ragraf-watch] отпечаток не посчитан: {type(exc).__name__}\n")
        return None
    if fingerprint != saved.get("fingerprint"):
        return None

    cat = category or category_for(cfg)
    expected_hash, expected_config, rev_id = indexed_hashes(cat, cfg)
    if not expected_hash:
        # Индекса нет: отпечаток не делает его существующим.
        return None
    if expected_config and cfg.config_hash != expected_config:
        # Конфиг корпуса правили — прежняя ревизия несопоставима с новой, и
        # объяснить это обязан полный путь (он же назовёт причину в ответе).
        return None

    fresh = Freshness(
        category=cat,
        checked=True,
        changed=False,
        indexed=True,
        # Суммы НЕ считались: поле остаётся пустым, а не подставляется из
        # ревизии. Подставить сюда indexed-хэш значило бы утверждать, что
        # текущий корпус даёт именно этот хэш, — этого мы не проверяли.
        corpus_hash="",
        expected_hash=expected_hash,
        config_hash=cfg.config_hash,
        expected_config_hash=expected_config,
        rev_id=rev_id or str(saved.get("rev_id") or ""),
        files_now=files,
    )
    fresh.verified_by = "fingerprint"
    fresh.note = "вход не менялся (пути, размеры и время правки совпали с индексацией)"
    return fresh


def _sealed_counts(cfg: ProjectConfig) -> tuple[int, int, int, str] | None:
    """Числа и режим ЗАПЕЧАТАННОЙ ревизии — то, что реально лежит в графе.

    ЗАЧЕМ ОТДЕЛЬНАЯ ФУНКЦИЯ. Отчёт индексатора считает записи ДО схлопывания
    повторов, а таблицы хранят то, что в них легло. Замер 06.11.2026 на
    корпусе `default-workspace`: отчёт назвал 4 595 узлов и 125 828 рёбер, а в
    ревизии `rev-0013` в таблицах лежало 2 233 и 34 306 (проверено запросом
    `count(*)` по `node`/`edge`). Реестр в таком виде утверждает о разработке
    то, чего в её индексе нет, и "расхождение реестра с записью ревизии" из
    README — не оговорка, а этот дефект.
    """
    try:
        from . import store

        conn = store.connect(cfg.graph_db, readonly=True)
        try:
            active, _skipped = store.active_revision(conn)
        finally:
            conn.close()
    except Exception:  # noqa: BLE001 — числа не повод падать записи реестра
        return None
    if active is None:
        return None
    return (
        int(active.get("file_count") or 0),
        int(active.get("node_count") or 0),
        int(active.get("edge_count") or 0),
        str(active.get("capability") or ""),
    )


def record_index_run(cfg: ProjectConfig, report, *, trigger: str = "manual") -> None:
    """Записать результат прогона индексации в реестр разработок.

    ЕДИНСТВЕННАЯ точка записи прогона в реестр: любой путь, который запускает
    индексатор, обязан позвать её. Иначе реестр — единственное место, откуда
    панель и операция `catalog` берут состояние разработки, — расходится с
    фактическим графом: показывает пустую ревизию и нули.

    Измеренный дефект, из-за которого функция появилась: `ensure` заводил корпус
    (запись с триггером `register`) и результат прогона не записывал, поэтому
    реестр показывал `rev_id=""` и `node_count=0` при 1855 узлах в индексе.

    Контрольная сумма берётся из СКАНИРОВАНИЯ корпуса, а не из отчёта
    индексатора: сумма не должна зависеть от того, как прошла индексация, иначе
    проверка «менялся ли код» теряет смысл. Пустая ревизия не записывается:
    такой прогон затёр бы ранее записанное состояние разработки.
    """
    rev_id = str(getattr(report, "rev_id", "") or "")
    if not rev_id:
        # Молчаливый выход здесь и был причиной «ревизия есть, записи нет»:
        # прогон без закреплённой ревизии ничего в реестр не пишет, и это
        # правильно, но должно быть СЛЫШНО в трассировке.
        trace_registry("record_skipped_empty_rev", trigger=trigger,
                       root=str(getattr(cfg, "root", "")))
        return

    from .catalog import Catalog

    catalog = Catalog()
    category = category_for(cfg)
    trace_registry("record_start", category=category, trigger=trigger, rev_id=rev_id,
                   catalog_path=str(catalog.path), root=str(cfg.root))
    corpus_hash, config_hash, files_now, _ = current_hashes(cfg)
    # Разработка могла быть не заведена в реестр: запись прогона должна её
    # создать, иначе результат будет потерян.
    if catalog.get(category) is None:
        catalog.register(category, cfg, cfg.config_path or "")
    sealed = _sealed_counts(cfg)
    if sealed is not None:
        files, nodes, edges, capability = sealed
    else:
        files = int(getattr(report, "files", 0) or files_now)
        nodes = int(getattr(report, "nodes", 0) or 0)
        edges = int(getattr(report, "edges", 0) or 0)
        capability = str(getattr(report, "capability", "") or "")
    catalog.record_run(
        category,
        rev_id=rev_id,
        # Прерванный прогон (report.ok = False) не становится состоянием
        # разработки: ревизия не закреплена, действует прежняя.
        completed=bool(getattr(report, "ok", True)),
        corpus_hash=corpus_hash,
        config_hash=config_hash,
        file_count=files,
        node_count=nodes,
        edge_count=edges,
        seconds=float(getattr(report, "seconds", 0.0) or 0.0),
        capability=capability,
        trigger=trigger,
    )
    trace_registry("record_done", category=category, trigger=trigger, rev_id=rev_id,
                   catalog_path=str(catalog.path))
    remember_registry_owner(cfg, catalog.path)
    # Отпечаток входа пишется ЗДЕСЬ, потому что это единственная точка, через
    # которую проходят ВСЕ пути индексации. Иначе дешёвая проверка свежести
    # сравнивала бы текущее состояние не с чем и всегда уходила бы в полную
    # сверку — то есть выигрыша не было бы вовсе.
    try:
        write_fingerprint(cfg, rev_id)
    except Exception as exc:  # noqa: BLE001 — отпечаток вспомогательный
        sys.stderr.write(f"[ragraf-watch] отпечаток не записан: {type(exc).__name__}: {exc}\n")
    # Кэш проверки относится к прежней ревизии: после прогона он неверен.
    invalidate_cache(category)


def register_project(
    cfg: ProjectConfig, config_path: str | Path, category: str | None = None
) -> Entry:
    """Зарегистрировать разработку и СРАЗУ зафиксировать её контрольные суммы.

    Суммы фиксируются без индексации: это позволяет отличить «разработка
    заведена в реестр» от «разработка проиндексирована» (у первой будет
    `rev_id` пустой, у второй — заполненный).
    """
    catalog = Catalog()
    cat = category or cfg.project
    entry = catalog.register(cat, cfg, config_path)
    corpus_hash, config_hash, files_now, _ = current_hashes(cfg)
    if not entry.corpus_hash:
        # Фиксируем только при первом заведении: иначе повторная регистрация
        # затёрла бы сумму проиндексированного состояния.
        catalog.record_run(
            cat,
            rev_id=entry.rev_id,
            corpus_hash=corpus_hash,
            config_hash=config_hash,
            file_count=files_now,
            trigger="register",
        )
    return catalog.get(cat) or entry
