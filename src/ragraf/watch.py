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

import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import corpus, util
from .catalog import Catalog, Entry
from .config import ProjectConfig

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
        return out


# Кэш результатов на время процесса: ключ — каталог категории.
_CACHE: dict[str, tuple[float, Freshness]] = {}


def current_hashes(cfg: ProjectConfig) -> tuple[str, str, int, dict[str, str]]:
    """Суммы корпуса и конфигурации прямо сейчас.

    Возвращает (corpus_hash, config_hash, число файлов, карта путь→sha256).
    Карта нужна, чтобы показать, ЧТО именно изменилось, а не только факт.
    """
    scan = corpus.scan(cfg)
    return scan.corpus_hash, cfg.config_hash, len(scan.files), {f.path: f.sha256 for f in scan.files}


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
        row = conn.execute(
            "SELECT rev_id, corpus_hash, config_hash FROM rev ORDER BY rev_id DESC LIMIT 1"
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
        info["reason"] = f"индекс старше {age_min} мин (предел {interval})"
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

    corpus_hash, config_hash, files_now, actual = current_hashes(cfg)
    expected_hash, expected_config, rev_id = indexed_hashes(cat, cfg)

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
    )

    if not expected_hash:
        # Индекса нет вовсе: это не «изменился», это «не проиндексировано».
        fresh.changed = True
        fresh.stale = True
        fresh.note = "индекс не создан: факты недоступны, требуется индексация"
    else:
        index_changed = expected_config and config_hash != expected_config
        if corpus_hash == expected_hash and not index_changed:
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
        # Расхождение есть, но накопление ниже порогов: пересбор не нужен,
        # достаточно честно пометить состояние.
        fresh.changed = True
        fresh.stale = True
        fresh.note = ("изменения есть, но их меньше порога переиндексации: "
                      "факты могут не соответствовать коду")
        _fill_accumulation(cfg, rev_id, fresh, changed=True, info=mode_info)
        _CACHE[cat] = (now, fresh)
        return fresh

    ok, rev, seconds, note = run_reindex(cfg, cat, trigger="guard", mode=mode,
                                     budget_seconds=budget_seconds)
    fresh.reindexed = ok
    fresh.reindex_rev = rev
    fresh.reindex_seconds = seconds
    fresh.reindex_mode = mode
    if ok:
        # После успешной индексации расхождения нет: суммы перечитаны.
        new_hash, new_config, new_files, _ = current_hashes(cfg)
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
) -> tuple[bool, str, float, str]:
    """Выполнить индексацию и записать результат в реестр.

    Аргумент mode определяет глубину:
      * "full" — с построением CPG (медленно, но с межпроцедурными связями);
      * "fast" — без CPG (только AST, схема, лексика): в 3,7 раза быстрее,
        что замерено на этом корпусе (5,6 с против 20,8 с).

    Возвращает (успех, ревизия, секунды, примечание). Ошибки не поднимаются
    наружу: вызывающий код должен получить состояние «устарело с причиной»,
    а не исключение — иначе обращение агента упадёт вместо того, чтобы выдать
    факты с честной пометкой.

    Блокировка реестра обязательна: два одновременных обращения к одному
    корпусу иначе запустят две индексации и перезапишут показатели.
    """
    catalog = Catalog()
    if not catalog.acquire_lock():
        return False, "", 0.0, "индексация уже выполняется другим процессом"

    started = time.time()
    # Режим Joern задаётся на время прогона и возвращается назад: конфигурация
    # читается из файла, и её нельзя менять навсегда из кода.
    joern_before = cfg.index.joern_enabled
    try:
        if mode == "fast":
            cfg.index.joern_enabled = False
        from .indexer import Indexer

        indexer = Indexer(cfg)
        try:
            # Бюджет: явный из ensure, иначе из конфига корпуса (run_reindex
            # вызывается и сторожем, где явного бюджета нет).
            effective_budget = (
                budget_seconds if budget_seconds is not None
                else getattr(cfg.index, "budget_seconds", None)
            )
            report = indexer.run(budget_seconds=effective_budget)
        finally:
            indexer.close()
        seconds = time.time() - started

        rev_id = ""
        note = ""
        files = nodes = edges = 0
        capability = ""
        try:
            from . import store

            conn = store.connect(cfg.graph_db, readonly=True)
            row = conn.execute(
                "SELECT rev_id, file_count, node_count, edge_count, capability"
                " FROM rev ORDER BY rev_id DESC LIMIT 1"
            ).fetchone()
            if row is not None:
                rev_id = str(row["rev_id"])
                files = int(row["file_count"] or 0)
                nodes = int(row["node_count"] or 0)
                edges = int(row["edge_count"] or 0)
                capability = str(row["capability"] or "")
            conn.close()
        except Exception as exc:
            note = f"ревизия записана, но счётчики не прочитаны: {type(exc).__name__}"

        # Контрольная сумма фиксируется ПОСЛЕ индексации, но считается по файлам
        # корпуса, поэтому совпадает с суммой до индексации (проверяется тестом).
        corpus_hash, config_hash, _, _ = current_hashes(cfg)
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
        # Отпечаток входа обновляется И ЗДЕСЬ. Это важнее, чем кажется: путь
        # сторожа (`run_reindex`) пишет реестр сам, минуя `record_index_run`, и
        # без этой строки отпечаток после переиндексации по расхождению остался
        # бы от ПРЕЖНЕЙ ревизии. Тогда дешёвая сверка никогда не подтверждала бы
        # свежесть, и каждый ответ снова читал бы весь корпус — то есть гарантия
        # «проверка перед каждым вызовом» превратилась бы в полную сверку на
        # каждом вызове. Измерено на живом корпусе 13.10.2026.
        try:
            write_fingerprint(cfg, rev_id)
        except Exception as exc:  # noqa: BLE001 — отпечаток вспомогательный
            sys.stderr.write(
                f"[ragraf-watch] отпечаток не записан (guard): {type(exc).__name__}: {exc}\n"
            )
        # Кэш проверки относился к прежней ревизии.
        invalidate_cache(category)
        return True, rev_id, seconds, note
    except Exception as exc:
        return False, "", time.time() - started, f"{type(exc).__name__}: {exc}"
    finally:
        # Режим Joern восстанавливается даже при ошибке: иначе один сбойный
        # прогон отключил бы построение CPG для всего процесса.
        cfg.index.joern_enabled = joern_before
        catalog.release_lock()


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


def write_fingerprint(cfg: ProjectConfig, rev_id: str) -> str:
    """Запомнить отпечаток входа, соответствующий проиндексированной ревизии.

    Вызывается из ЕДИНСТВЕННОЙ точки записи прогона (`record_index_run`),
    поэтому покрывает все пути индексации — ensure, CLI и сторожа. Без этого
    дешёвая проверка не могла бы отличить «вход не менялся» от «сравнивать не
    с чем», и сторож был бы вынужден перечитывать корпус на каждом вызове.

    Ошибка записи не роняет операцию: следующий вызов просто пойдёт полной
    сверкой — это дороже, но не неправильно.
    """
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
        return

    from .catalog import Catalog

    catalog = Catalog()
    category = category_for(cfg)
    corpus_hash, config_hash, files_now, _ = current_hashes(cfg)
    # Разработка могла быть не заведена в реестр: запись прогона должна её
    # создать, иначе результат будет потерян.
    if catalog.get(category) is None:
        catalog.register(category, cfg, cfg.config_path or "")
    catalog.record_run(
        category,
        rev_id=rev_id,
        corpus_hash=corpus_hash,
        config_hash=config_hash,
        file_count=int(getattr(report, "files", 0) or files_now),
        node_count=int(getattr(report, "nodes", 0) or 0),
        edge_count=int(getattr(report, "edges", 0) or 0),
        seconds=float(getattr(report, "seconds", 0.0) or 0.0),
        capability=str(getattr(report, "capability", "") or ""),
        trigger=trigger,
    )
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
