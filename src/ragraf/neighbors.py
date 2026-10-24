"""Соседние контуры: готовые реализации того же стека в других корпусах.

Задача
    Вопрос агента звучит так: «нужна функция X — может, она уже написана в
    соседнем проекте на том же стеке?». До этой операции ответа не было:
    `search` отвечал только по ТЕКУЩЕМУ корпусу, а `route` говорил «где лежит»,
    не читая содержимого. Агент писал заново то, что рядом уже работало.

Что считается соседом
    Запись реестра разработок (`catalog/catalog.json`), у которой:
      * есть свой индекс (`rev.json` с `rev_id` в корне корпуса);
      * корень и конфиг существуют на диске;
      * индекс открывается чтением;
      * стек по ФАКТИЧЕСКОМУ индексу пересекается с текущим не меньше чем на
        `min_shared` языков.

Почему стек берётся из индекса, а не из конфига
    В конфиге перечислены языки, которые корпусу РАЗРЕШЕНЫ; в индексе — те,
    файлы которых реально есть. У собственного корпуса RAGRAF в конфиге 21
    язык (включая php, c, cpp, csharp, которые в нём не пишут), поэтому
    сравнение по конфигу объявило бы соседом любой корпус с `python` в списке.
    Стек читается из таблицы `file` (`GROUP BY lang`), и это единственное
    измерение, которое отвечает на вопрос «на чём здесь пишут».

Как сравниваются стеки
    Доля МОЕГО стека, покрытая соседом, — неверная мера, и это измерено.
    Собственный корпус RAGRAF: python 518 файлов (97 %), powershell 7, php 5,
    css/html/javascript по 2. Корпус на PHP, где есть по одному файлу каждого
    из этих языков (z.batteryservice.ru), покрывает 6 из 6 моих языков — и
    прежняя формула ставила его первым, хотя он написан на PHP. Поэтому мера
    ДВУСТОРОННЯЯ: косинус распределения языков по файлам, усечённого до языков
    с разбором деревом (`TREE_LANGS`). Сосед «тот же стек» — это когда
    распределение похоже, а не когда каждый мой язык у него встречается.

Шкала совпадения (`match`)
    `same`    — похожесть ≥ 0.9: тот же основной язык и та же пропорция;
    `close`   — ≥ 0.6: основной язык тот же, пропорция иная;
    `partial` — ≥ 0.15: пересечение есть, стек другой;
    `none`    — общего нет (такой корпус соседом не считается).

Чтение — только чтение
    Чужой корпус НЕ переиндексируется и не изменяется: соединения с графом и
    лексикой открываются с `readonly=True`, фасад соседа не создаётся вовсе
    (он по умолчанию включает сторожа и телеметрию). Если индекс соседа
    отстал, это НЕ чинится здесь: он помечается `stale` с причиной, а
    восстановление — `ensure <корень>`, то есть осознанное действие.

Детерминизм
    Порядок соседей: score (округлён до 6 знаков), затем имя разработки.
    Порядок хитов: степень совпадения (`exact_name` → `partial_name` → `text`),
    score, порядок корпуса, путь, строка. Порядок не зависит от порядка обхода
    реестра и каталога, поэтому два прогона на неизменном состоянии дают
    побайтово один ответ.

Чего операция НЕ утверждает
    * «уже реализовано» — это вывод, а не факт. Хит несёт путь, строки и
      ревизию того корпуса, откуда он пришёл; решение принимает агент после
      чтения кода. Поэтому у каждого хита своя `index_rev`, а `index_rev`
      конверта относится ТОЛЬКО к текущему корпусу.
    * «та же самая функция» — совпадение имени бывает случайным (в двух
      проектах есть `init()`). Совпадение содержимого показывается отдельно:
      файлы с одинаковым `sha256` помечаются `same_content_as`, чтобы копия не
      считалась второй независимой реализацией.
"""

from __future__ import annotations

import math
import os
import sqlite3
from pathlib import Path
from typing import Any

from . import store, util
from .config import TREE_LANGS, ProjectConfig, load as load_config

# Сколько корпусов реально опрашивается за вызов. Ограничение не про
# осторожность: каждый сосед — это открытие двух SQLite-баз и полнотекстовый
# запрос, поэтому двадцать корпусов превратили бы ответ в минуты ожидания.
CAP_NEIGHBORS = 6
# Сколько отпечатков входа сверяется. Сверка читает метаданные дерева
# (stat по файлам), поэтому она делается для опрошенных корпусов и не более
# этого числа: остальные отвечают «свежесть не проверена» с причиной.
CAP_VERIFY = 5
# Пороги шкалы совпадения (косинус распределения языков).
SIM_SAME = 0.9
SIM_CLOSE = 0.6
SIM_PARTIAL = 0.15
# Длина фрагмента текста в хите.
SNIPPET_CHARS = 400
# Сколько документов из одного корпуса допускается в выдачу. Документ
# отвечает «как это описано», код — «как это сделано»; для повторного
# использования нужен второй, но одна ссылка на описание полезна. Без этого
# правила описания занимали всю выдачу: замер 22.10.2026 — из шести хитов
# четыре были `.md`, включая preflight-отчёт вместо реализации.
MAX_DOC_HITS = 1
# Расширения, которые считаются документами, если роль документа не проставлена.
DOC_SUFFIXES = (".md", ".markdown", ".rst", ".txt", ".adoc")
# Префикс вида чанка слоя баз: данные и схема, а не реализации.
DB_KIND_PREFIX = "db-"
# Виды узлов, которые при поиске ПО ИМЕНИ считаются реализацией. Правила
# оформления (css_class, css_var) и служебные узлы (namespace, placeholder,
# route_node, file, external) дают совпадение имени, но реализацией не
# являются: замер 22.10.2026 — запрос «Editor» в php-корпусах отдавал
# css-правило `md-editor-file` вместо классов и функций. Отсечённое
# считается и называется в limits — не молчит.
SYMBOL_KINDS = ("function", "method", "class", "interface", "trait",
                "enum", "constant")
# Размер пачки в `IN (...)`: у SQLite ограничение на число параметров.
MERGE_BATCH = 400

MATCH_RANK = {"exact_name": 0, "partial_name": 1, "text": 2}

# Порядок выдачи символов. Правило взято из `facade._symbol_rows` НЕ по
# привычке: сортировка по `COALESCE(path,'')` ставила внешнюю заглушку
# (`kind='external'`, пустой путь) перед настоящим определением, и `callers`
# отвечал «связей 0» (замер 14.10.2026). Здесь тот же порядок, иначе сосед
# показывал бы заглушку вместо реализации.
_SYMBOL_ORDER = (
    "ORDER BY CASE WHEN kind = 'external' OR path IS NULL OR path = ''"
    " THEN 1 ELSE 0 END, COALESCE(path, ''), start_line"
)


# ---------------------------------------------------------------------------
# стек корпуса
# ---------------------------------------------------------------------------


def lang_profile(conn: sqlite3.Connection, rev: str) -> dict[str, int]:
    """Языки ФАКТИЧЕСКОГО индекса: сколько файлов каждого.

    Пустой словарь означает «в этой ревизии файлов нет» — вызывающий обязан
    сказать это словами, а не считать корпус пустым по существу.
    """
    try:
        rows = conn.execute(
            "SELECT lang, COUNT(*) AS files FROM file WHERE rev_id = ?"
            " GROUP BY lang ORDER BY files DESC, lang",
            (rev,),
        ).fetchall()
    except sqlite3.Error:
        return {}
    return {str(r["lang"]): int(r["files"]) for r in rows if r["lang"]}


def _profile_from_disk(cfg: ProjectConfig, rev: str) -> dict[str, int]:
    """Стек текущего корпуса по его базе графа (открывается чтением)."""
    try:
        conn = store.connect(cfg.graph_db, readonly=True)
    except (OSError, sqlite3.OperationalError):
        return {}
    try:
        return lang_profile(conn, rev)
    finally:
        conn.close()


def active_state(cfg: ProjectConfig) -> tuple[str, str]:
    """Активная ревизия и режим корпуса по `rev.json` — не по записи реестра.

    Реестр обновляется тем путём, который запускал индексацию, и может
    отставать от самого корпуса (замер 22.10.2026: у BSG-main в реестре
    ревизии нет, а в корпусе лежит `rev-0001`). `rev.json` пишет индексатор в
    момент публикации ревизии, поэтому он и берётся первым.
    """
    try:
        data = util.read_json(cfg.rev_file, default=None) or {}
    except OSError:
        return "", ""
    return str(data.get("rev_id") or ""), str(data.get("capability") or "")


def active_rev(cfg: ProjectConfig) -> str:
    """Активная ревизия корпуса (см. `active_state`)."""
    return active_state(cfg)[0]


def _normalize_stack(stack: str | list[str] | tuple[str, ...] | None) -> list[str] | None:
    """Разобрать явно заданный стек: строка через запятую или список."""
    if stack is None:
        return None
    if isinstance(stack, str):
        parts = stack.replace(";", ",").split(",")
    else:
        parts = [str(x) for x in stack]
    out = [p.strip().lower() for p in parts if str(p).strip()]
    # Дубли убираются с сохранением порядка: порядок влияет на снимок ответа.
    return list(dict.fromkeys(out)) or None


def code_core(profile: dict[str, int]) -> dict[str, int]:
    """Стек кода: только языки с разбором деревом, иначе весь профиль.

    Разметка, документация и данные (`markdown`, `json`, `yaml`, `text`, `sql`)
    не отвечают на вопрос «на чём здесь пишут», зато есть почти в каждом
    корпусе, и их участие делает все корпуса похожими друг на друга. У корпуса
    без кода вовсе (только документы) усечение дало бы пустой стек — тогда
    берётся полный профиль: сравнение по расширениям лучше, чем никакого.
    """
    core = {lang: files for lang, files in profile.items() if lang in TREE_LANGS}
    return core or dict(profile)


def primary_lang(profile: dict[str, int]) -> str:
    """Основной язык корпуса: больше всего файлов; ничья — по алфавиту."""
    if not profile:
        return ""
    return sorted(profile.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]


def _share(mine: dict[str, int], theirs: dict[str, int]) -> dict[str, Any]:
    """Сравнить стеки: пересечение, похожесть распределения и вид совпадения.

    Мера — КОСИНУС распределений, а не доля покрытия. Измерено 22.10.2026:
    стек собственного корпуса RAGRAF на 97 % состоит из python (518 файлов
    против 7 powershell и 5 php), а PHP-корпус с единичными файлами
    css/html/javascript/php/powershell/python покрывал 6 из 6 моих языков и
    по доле покрытия шёл первым. Косинус такую пару разводит: общих масс у неё
    почти нет, и `match` получается `none`.
    """
    shared = sorted((lang for lang in mine if lang in theirs),
                    key=lambda lang: (-mine[lang], lang))
    missing = sorted((lang for lang in mine if lang not in theirs),
                     key=lambda lang: (-mine[lang], lang))
    extra = sorted((lang for lang in theirs if lang not in mine),
                   key=lambda lang: (-theirs[lang], lang))
    coverage = len(shared) / len(mine) if mine else 0.0

    def unit(profile: dict[str, int]) -> dict[str, float]:
        total = float(sum(profile.values()) or 1)
        return {lang: files / total for lang, files in profile.items()}

    a, b = unit(mine), unit(theirs)
    dot = sum(a[lang] * b[lang] for lang in set(a) & set(b))
    norm = (math.sqrt(sum(v * v for v in a.values()))
            * math.sqrt(sum(v * v for v in b.values()))) or 1.0
    similarity = dot / norm

    primary = primary_lang(mine)
    primary_neighbor = primary_lang(theirs)
    primary_same = bool(primary) and primary == primary_neighbor
    if similarity >= SIM_SAME:
        match = "same"
    elif similarity >= SIM_CLOSE:
        match = "close"
    elif shared and similarity >= SIM_PARTIAL:
        match = "partial"
    else:
        match = "none"
    score = round(0.7 * similarity + 0.2 * coverage
                  + (0.1 if primary_same else 0.0), 6)
    return {
        "shared": shared,
        "missing": missing,
        "extra": extra,
        "coverage": round(coverage, 4),
        "similarity": round(similarity, 4),
        "primary_mine": primary,
        "primary_neighbor": primary_neighbor,
        "primary_same": primary_same,
        "covers_all": not missing,
        "match": match,
        "score": score,
    }


def _norm_root(path: str) -> str:
    """Нормализованный корень: сравнение «это тот же корпус» по путям.

    `normcase` без `realpath` недостаточно: один и тот же проект может быть
    заведён дважды (`X` и `X\\`), и тогда текущий
    корпус попал бы в собственные соседи.
    """
    text = str(path or "")
    if not text:
        return ""
    try:
        return os.path.normcase(util.to_posix(os.path.realpath(text)))
    except (OSError, ValueError):
        return os.path.normcase(util.to_posix(text))


def _neighbor_freshness(cfg: ProjectConfig) -> dict[str, Any]:
    """Свежесть индекса соседа по отпечатку входа (только метаданные).

    Возраст записи в реестре — не доказательство свежести, поэтому при
    расхождении отпечатка хит помечается `stale`, а не выдаётся за актуальный
    (тот же принцип, что в `route` и в стороже текущего корпуса).
    """
    from . import corpus as corpus_mod
    from . import watch as watch_mod

    saved = watch_mod.read_fingerprint(cfg) or {}
    if not saved.get("fingerprint"):
        return {
            "checked": False,
            "fingerprint_match": None,
            "reason": "отпечаток входа не записан: свежесть не доказана",
        }
    try:
        now_fp, files_now, _total = corpus_mod.stat_fingerprint(cfg)
    except Exception as exc:  # noqa: BLE001 — сверка не имеет права ронять ответ
        return {
            "checked": False,
            "fingerprint_match": None,
            "reason": f"отпечаток не посчитан: {type(exc).__name__}",
        }
    match = now_fp == saved.get("fingerprint")
    out: dict[str, Any] = {
        "checked": True,
        "fingerprint_match": match,
        "files_now": int(files_now),
    }
    if not match:
        out["reason"] = ("отпечаток входа разошёлся: факты могут относиться к "
                         "прежнему состоянию кода")
    return out


# ---------------------------------------------------------------------------
# поиск соседей
# ---------------------------------------------------------------------------


def discover(cfg: ProjectConfig, rev: str, *, mine: dict[str, int] | None = None,
             min_shared: int = 1, verify: bool = True,
             catalog: Any | None = None) -> dict[str, Any]:
    """Отобрать соседей по стеку и назвать причину каждого решения.

    Побочных действий нет: конфиги читаются, индексы открываются чтением и
    сразу закрываются. Записи, у которых индекса нет, попадают в `skipped` с
    причиной — «сосед есть, но спросить его нечем» это факт, а не пустота.
    """
    from .catalog import Catalog

    entries = (catalog.entries() if catalog is not None else Catalog().entries())
    if mine is None:
        mine = _profile_from_disk(cfg, rev)
    my_core = code_core(mine)

    my_root = _norm_root(str(cfg.root))
    notes: list[str] = []
    skipped: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    self_in_registry = False
    with_index = 0

    for key, raw in entries.items():
        if not isinstance(raw, dict):
            continue
        category = str(raw.get("category") or key)
        root = str(raw.get("root") or "")
        config_path = str(raw.get("config") or "")

        if root and _norm_root(root) == my_root:
            self_in_registry = True
            continue
        if raw.get("index_enabled") is False:
            skipped.append({"corpus": category, "reason": "индексация выключена в реестре"})
            continue
        if not root or not Path(root).is_dir():
            skipped.append({"corpus": category, "reason": "корень не найден на диске"})
            continue
        if not config_path or not Path(config_path).exists():
            skipped.append({"corpus": category, "reason": "конфиг корпуса не найден"})
            continue
        try:
            peer = load_config(config_path)
        except Exception as exc:  # noqa: BLE001 — чужой конфиг не роняет операцию
            skipped.append({"corpus": category,
                            "reason": f"конфиг не прочитан: {type(exc).__name__}"})
            continue

        peer_rev, peer_cap = active_state(peer)
        peer_rev = peer_rev or str(raw.get("rev_id") or "")
        if not peer_rev:
            skipped.append({"corpus": category, "reason": "индекс не построен"})
            continue
        try:
            conn = store.connect(peer.graph_db, readonly=True)
        except (OSError, sqlite3.OperationalError) as exc:
            skipped.append({"corpus": category,
                            "reason": f"индекс не открыт: {type(exc).__name__}"})
            continue
        try:
            profile = lang_profile(conn, peer_rev)
            if not profile:
                # Ревизия в `rev.json` могла смениться между записью файла и
                # чтением базы: берём последнюю из самой базы, а не выдумываем.
                row = conn.execute(
                    "SELECT rev_id FROM rev ORDER BY rev_id DESC LIMIT 1"
                ).fetchone()
                if row:
                    peer_rev = str(row["rev_id"])
                    profile = lang_profile(conn, peer_rev)
        finally:
            conn.close()
        with_index += 1
        if not profile:
            skipped.append({"corpus": category,
                            "reason": f"в ревизии {peer_rev} нет файлов"})
            continue

        share = _share(my_core, code_core(profile))
        if len(share["shared"]) < min_shared:
            skipped.append({
                "corpus": category,
                "reason": (f"общий стек меньше {min_shared}: "
                           f"совпало {len(share['shared'])} из {len(my_core)} языков"),
            })
            continue
        if share["match"] == "none":
            # Языки пересекаются, но корпус написан на другом: единичный файл
            # моего языка в PHP-проекте — не «тот же стек».
            skipped.append({
                "corpus": category,
                "reason": ("стек другой: похожесть распределения языков "
                           f"{share['similarity']} ниже порога {SIM_PARTIAL} "
                           f"(основной язык соседа: {share['primary_neighbor']})"),
            })
            continue

        candidates.append({
            "corpus": category,
            "project": str(raw.get("project") or peer.project or ""),
            "root": util.to_posix(root),
            "config": util.to_posix(config_path),
            "index_rev": peer_rev,
            "capability": peer_cap or str(raw.get("capability") or ""),
            "indexed_at": str(raw.get("indexed_at") or ""),
            "files": int(raw.get("file_count") or 0),
            "languages": dict(sorted(profile.items(), key=lambda kv: (-kv[1], kv[0]))),
            "hits_found": 0,
            "vector_used": False,
            "vec_ok": False,
            **share,
        })

    # Порядок задаётся числами и именами, а не порядком реестра.
    candidates.sort(key=lambda c: (-round(float(c["score"]), 6),
                                   -len(c["shared"]), c["corpus"]))

    selected = candidates[:CAP_NEIGHBORS]
    for dropped in candidates[CAP_NEIGHBORS:]:
        skipped.append({
            "corpus": dropped["corpus"],
            "reason": f"не опрошен: за вызов опрашивается не больше {CAP_NEIGHBORS} корпусов",
        })

    verified = 0
    cfgs: dict[str, ProjectConfig] = {}
    for item in selected:
        peer = load_config(str(item["config"]))
        cfgs[item["corpus"]] = peer
        if verify and verified < CAP_VERIFY:
            item["freshness"] = _neighbor_freshness(peer)
            verified += 1
        else:
            item["freshness"] = {
                "checked": False,
                "fingerprint_match": None,
                "reason": ("свежесть не проверена: предел сверок за вызов "
                           f"({CAP_VERIFY})" if verify else "сверка отключена"),
            }
        item["embedding_model"] = peer.embedding.model

    if not self_in_registry:
        notes.append(
            "текущего корпуса нет в реестре: соседи отобраны по стеку его "
            "индекса, и сам он в список не попадает"
        )
    if not mine:
        notes.append("в текущем индексе нет файлов: стек не определён, соседей нет")
    return {
        "mine": mine,
        "neighbors": selected,
        "skipped": skipped,
        "notes": notes,
        "cfgs": cfgs,
        "in_registry": len(entries),
        "with_index": with_index,
        "matched": len(candidates),
    }


# ---------------------------------------------------------------------------
# поиск по одному соседу
# ---------------------------------------------------------------------------


def _symbol_hits(conn: sqlite3.Connection, rev: str, name: str,
                 limit: int) -> tuple[list[dict[str, Any]], int]:
    """Символы соседа: точное совпадение, затем вхождение в fqn.

    Возвращает (строки, сколько узлов не показано по виду). Полного каскада
    `facade._symbol_rows` здесь нет намеренно: фасад открыт на ТЕКУЩЕМ корпусе,
    а чужая ревизия ему недоступна. Порядок сортировки взят оттуда же — иначе
    заглушка внешнего узла перекрыла бы определение.
    """
    rows: list[Any] = []
    try:
        rows = conn.execute(
            "SELECT node_id, kind, name, fqn, path, start_line, end_line, signature"
            " FROM node WHERE rev_id = ? AND (fqn = ? OR (name = ? AND kind <> 'file'))"
            " " + _SYMBOL_ORDER + " LIMIT ?",
            (rev, name, name, limit),
        ).fetchall()
        if not rows:
            rows = conn.execute(
                "SELECT node_id, kind, name, fqn, path, start_line, end_line, signature"
                " FROM node WHERE rev_id = ? AND fqn LIKE ?"
                " ORDER BY CASE WHEN kind = 'external' OR path IS NULL OR path = ''"
                " THEN 1 ELSE 0 END, LENGTH(COALESCE(fqn, '')) LIMIT ?",
                (rev, f"%{name}%", limit),
            ).fetchall()
    except sqlite3.Error:
        return [], 0
    out: list[dict[str, Any]] = []
    dropped = 0
    for r in rows:
        item = dict(r)
        if not item.get("path"):
            # Заглушка внешнего узла: адресовать её в чужом корпусе нечем.
            continue
        if str(item.get("kind") or "") not in SYMBOL_KINDS:
            dropped += 1
            continue
        item["match"] = "exact_name" if item.get("name") == name else "partial_name"
        out.append(item)
    return out, dropped


def _node_info(conn: sqlite3.Connection, rev: str,
               node_ids: list[str]) -> dict[str, dict[str, Any]]:
    """Сведения об узлах пачкой: имя, вид, сигнатура, строки."""
    wanted = [str(i) for i in dict.fromkeys(node_ids) if i]
    out: dict[str, dict[str, Any]] = {}
    for start in range(0, len(wanted), MERGE_BATCH):
        part = wanted[start:start + MERGE_BATCH]
        marks = ",".join("?" for _ in part)
        try:
            rows = conn.execute(
                "SELECT node_id, kind, name, fqn, signature, start_line, end_line"
                f" FROM node WHERE rev_id = ? AND node_id IN ({marks})",
                (rev, *part),
            ).fetchall()
        except sqlite3.Error:
            continue
        for r in rows:
            out[str(r["node_id"])] = {
                "kind": r["kind"], "name": r["name"], "fqn": r["fqn"],
                "signature": r["signature"],
                "start_line": r["start_line"], "end_line": r["end_line"],
            }
    return out


def _file_sha(conn: sqlite3.Connection, rev: str,
              paths: list[str]) -> dict[str, str]:
    """sha256 файлов пачкой: по нему видно побайтовую копию между корпусами."""
    wanted = [str(p) for p in dict.fromkeys(paths) if p]
    out: dict[str, str] = {}
    for start in range(0, len(wanted), MERGE_BATCH):
        part = wanted[start:start + MERGE_BATCH]
        marks = ",".join("?" for _ in part)
        try:
            rows = conn.execute(
                f"SELECT path, sha256 FROM file WHERE rev_id = ? AND path IN ({marks})",
                (rev, *part),
            ).fetchall()
        except sqlite3.Error:
            continue
        for r in rows:
            out[str(r["path"])] = str(r["sha256"])
    return out


def _snippet(text: str) -> str:
    head = " ".join(str(text or "").split())
    return head[:SNIPPET_CHARS]


def _is_doc(hit: Any) -> bool:
    """Документ ли это: роль документа из индекса или расширение пути."""
    if str(getattr(hit, "doc_role", "") or ""):
        return True
    return str(getattr(hit, "path", "")).lower().endswith(DOC_SUFFIXES)


def select_hits(hits: list[Any], per_corpus: int,
                kinds: list[str] | None = None) -> tuple[list[Any], dict[str, int]]:
    """Отобрать фрагменты одного соседа: без слоя баз и без засилья документов.

    Слой баз (`db-structure`, `db-sample`) — это структура и ПРИМЕРЫ СТРОК
    чужой базы, а не реализация: в замере 22.10.2026 два первых хита из шести
    были образцами таблиц. `kinds` позволяет спросить их явно.
    """
    out: list[Any] = []
    docs = 0
    dropped = {"db": 0, "docs": 0, "kinds": 0}
    for hit in hits:
        kind = str(getattr(hit, "source_kind", "") or "")
        if kinds:
            if kind not in kinds:
                dropped["kinds"] += 1
                continue
        elif kind.startswith(DB_KIND_PREFIX):
            dropped["db"] += 1
            continue
        if _is_doc(hit):
            if docs >= MAX_DOC_HITS:
                dropped["docs"] += 1
                continue
            docs += 1
        out.append(hit)
    return out[:max(1, int(per_corpus))], dropped


def _search_peer(peer: ProjectConfig, rev: str, query: str, *,
                 vector: list[float] | None, limit: int,
                 with_text: bool) -> dict[str, Any]:
    """Гибридный поиск по индексу соседа (лексика всегда, вектор — по модели).

    Вектор передаётся только если модель соседа совпадает с моделью текущего
    корпуса: чужая модель даёт другую геометрию, и ранжирование по ней было бы
    бессмысленным, хотя выглядело бы работающим.
    """
    from .lexvec import LexVecStore

    out: dict[str, Any] = {"hits": [], "texts": {}, "error": "",
                           "vector_used": False, "vec_ok": False}
    try:
        lx = LexVecStore(peer, readonly=True)
    except (OSError, sqlite3.OperationalError) as exc:
        out["error"] = f"лексика не открыта: {type(exc).__name__}"
        return out
    try:
        out["vec_ok"] = bool(lx.vec_ok)
        hits = lx.search_hybrid(rev, query, vector, limit=limit)
        out["vector_used"] = bool(vector) and out["vec_ok"]
        out["hits"] = hits
        if with_text and hits:
            out["texts"] = lx.chunk_texts(rev, [h.chunk_id for h in hits])
    except sqlite3.Error as exc:
        out["error"] = f"поиск не выполнен: {type(exc).__name__}: {exc}"
    finally:
        lx.close()
    return out


# ---------------------------------------------------------------------------
# сборка ответа
# ---------------------------------------------------------------------------


def _hit_key(item: dict[str, Any]) -> tuple:
    """Порядок хитов: сначала совпадение имени, затем оценка, затем адрес."""
    return (
        MATCH_RANK.get(str(item.get("match")), 9),
        -round(float(item.get("score") or 0.0), 6),
        int(item.get("_corpus_rank", 0)),
        str(item.get("path") or ""),
        int(item.get("start_line") or 0),
    )


def _dedupe_hits(hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Один адрес — один хит. Поиск и символ могут указать на одно место."""
    best: dict[tuple, dict[str, Any]] = {}
    for item in hits:
        key = (item.get("corpus"), item.get("path"), item.get("start_line"))
        current = best.get(key)
        if current is None or _hit_key(item) < _hit_key(current):
            best[key] = item
    return list(best.values())


def find(cfg: ProjectConfig, rev: str, *, query: str = "", name: str = "",
         limit: int = 20, per_corpus: int = 5,
         stack: str | list[str] | None = None, min_shared: int = 1,
         with_text: bool = True, verify: bool = True,
         vector: list[float] | None = None,
         mine: dict[str, int] | None = None,
         kinds: list[str] | None = None,
         catalog: Any | None = None) -> dict[str, Any]:
    """Найти готовые реализации в соседних корпусах того же стека.

    Возвращает:
      * `stack` — по чему сравнивались корпуса (языки и число файлов);
      * `neighbors` — опрошенные корпуса с причиной выбора и свежестью;
      * `hits` — найденные места с `corpus`, `path`, `start_line` и СВОЕЙ
        `index_rev`;
      * `skipped` — кого не спросили и почему;
      * `limits` — что ограничило ответ (уходит в конверт фасада).

    `mine`, `vector` и `catalog` существуют для фасада и проверок: фасад уже
    держит соединение с текущим графом и посчитанный вектор запроса, а тест
    подставляет временный реестр, не трогая рабочий.
    """
    query = str(query or "").strip()
    name = str(name or "").strip()
    if not query and not name:
        raise ValueError("нужен query или name: без запроса и без имени символа "
                         "искать нечего")
    limit = max(1, min(int(limit), 100))
    per_corpus = max(1, min(int(per_corpus), 20))
    min_shared = max(1, min(int(min_shared), 20))

    explicit = _normalize_stack(stack)
    if explicit is not None:
        mine = {lang: 1 for lang in explicit}
        stack_source = "explicit"
    else:
        stack_source = "index"
        if mine is None:
            mine = _profile_from_disk(cfg, rev)

    plan = discover(cfg, rev, mine=mine, min_shared=min_shared, verify=verify,
                    catalog=catalog)
    neighbors: list[dict[str, Any]] = plan["neighbors"]
    notes: list[str] = list(plan["notes"])
    limits: list[str] = []

    if not neighbors:
        notes.append(
            "соседей того же стека нет: другие корпуса либо не проиндексированы, "
            "либо написаны на других языках (см. skipped)"
        )

    hits: list[dict[str, Any]] = []
    same_model = cfg.embedding.model
    dropped = {"db": 0, "docs": 0, "kinds": 0, "symbols": 0}
    cfgs: dict[str, ProjectConfig] = dict(plan.get("cfgs") or {})
    for rank, neighbor in enumerate(neighbors):
        peer: ProjectConfig = cfgs.get(neighbor["corpus"])
        if peer is None:
            neighbor["error"] = "конфиг не загружен"
            continue
        root = neighbor["root"]
        noise = neighbor["freshness"].get("fingerprint_match") is False
        tried = False
        try:
            if name:
                tried = True
                try:
                    conn = store.connect(peer.graph_db, readonly=True)
                except (OSError, sqlite3.OperationalError) as exc:
                    neighbor["error"] = f"граф не открыт: {type(exc).__name__}"
                    conn = None
                if conn is not None:
                    try:
                        found, dropped_sym = _symbol_hits(
                            conn, neighbor["index_rev"], name, per_corpus)
                    finally:
                        conn.close()
                    dropped["symbols"] += dropped_sym
                    for row in found:
                        item = {
                            "corpus": neighbor["corpus"],
                            "project": neighbor["project"],
                            "index_rev": neighbor["index_rev"],
                            "capability": neighbor["capability"],
                            "path": str(row["path"]),
                            "file": util.to_posix(Path(root) / str(row["path"])),
                            "start_line": row["start_line"],
                            "end_line": row["end_line"],
                            "source_kind": "symbol",
                            "score": 1.0 if row["match"] == "exact_name" else 0.5,
                            "match": row["match"],
                            "symbol": {
                                "kind": row["kind"], "name": row["name"],
                                "fqn": row["fqn"], "signature": row["signature"],
                                "start_line": row["start_line"],
                                "end_line": row["end_line"],
                            },
                            "_corpus_rank": rank,
                        }
                        if noise:
                            item["stale"] = True
                            item["stale_reason"] = neighbor["freshness"]["reason"]
                        hits.append(item)
            if query:
                tried = True
                use_vector = (vector if (vector is not None
                                         and neighbor["embedding_model"] == same_model)
                              else None)
                if vector is not None and use_vector is None:
                    limits.append(
                        f"{neighbor['corpus']}: векторный поиск не применялся — "
                        f"модель корпуса {neighbor['embedding_model']!r} "
                        f"отличается от {same_model!r}"
                    )
                found = _search_peer(peer, neighbor["index_rev"], query,
                                     vector=use_vector,
                                     limit=max(per_corpus * 3, per_corpus + 5),
                                     with_text=with_text)
                if found.get("error"):
                    neighbor["error"] = found["error"]
                neighbor["vector_used"] = bool(found.get("vector_used"))
                neighbor["vec_ok"] = bool(found.get("vec_ok"))
                raw_hits = found.get("hits") or []
                texts = found.get("texts") or {}
                raw_hits, dropped_here = select_hits(raw_hits, per_corpus, kinds)
                for key, value in dropped_here.items():
                    dropped[key] += value
                node_ids = [h.node_id for h in raw_hits if getattr(h, "node_id", None)]
                node_map: dict[str, dict[str, Any]] = {}
                if node_ids:
                    try:
                        conn = store.connect(peer.graph_db, readonly=True)
                    except (OSError, sqlite3.OperationalError):
                        conn = None
                    if conn is not None:
                        try:
                            node_map = _node_info(conn, neighbor["index_rev"], node_ids)
                        finally:
                            conn.close()
                for h in raw_hits:
                    item = {
                        "corpus": neighbor["corpus"],
                        "project": neighbor["project"],
                        "index_rev": neighbor["index_rev"],
                        "capability": neighbor["capability"],
                        "path": str(h.path),
                        "file": util.to_posix(Path(root) / str(h.path)),
                        "start_line": h.start_line,
                        "end_line": h.end_line,
                        "source_kind": h.source_kind,
                        "score": round(float(h.score or 0.0), 6),
                        "match": "text",
                        "why": h.why,
                        "_corpus_rank": rank,
                    }
                    info = node_map.get(str(h.node_id)) if h.node_id else None
                    if info:
                        item["symbol"] = info
                    text = texts.get(str(h.chunk_id))
                    if with_text and text is not None:
                        item["snippet"] = _snippet(text)
                    elif with_text:
                        item["snippet_missing"] = True
                    if noise:
                        item["stale"] = True
                        item["stale_reason"] = neighbor["freshness"]["reason"]
                    hits.append(item)
            if not tried:
                continue
        except Exception as exc:  # noqa: BLE001 — один сосед не роняет ответ
            neighbor["error"] = f"{type(exc).__name__}: {exc}"

        neighbor["hits_found"] = sum(1 for h in hits if h["corpus"] == neighbor["corpus"])
        if neighbor.get("error"):
            limits.append(f"{neighbor['corpus']}: {neighbor['error']}")

    # Побайтовые копии: один и тот же файл в двух корпусах — не две реализации.
    # sha256 берётся из `file` того корпуса, откуда пришёл хит: сравнивать хэши,
    # посчитанные здесь по диску, значило бы читать чужие файлы целиком.
    shas: dict[tuple[str, str], str] = {}
    for neighbor in neighbors:
        paths = [h["path"] for h in hits if h["corpus"] == neighbor["corpus"]]
        if not paths:
            continue
        peer = cfgs.get(neighbor["corpus"])
        if peer is None:
            continue
        try:
            conn = store.connect(peer.graph_db, readonly=True)
        except (OSError, sqlite3.OperationalError):
            continue
        try:
            for path, sha in _file_sha(conn, neighbor["index_rev"], paths).items():
                shas[(neighbor["corpus"], path)] = sha
        finally:
            conn.close()
    by_sha: dict[str, list[str]] = {}
    for item in hits:
        sha = shas.get((item["corpus"], item["path"]))
        if sha:
            by_sha.setdefault(sha, []).append(f"{item['corpus']}:{item['path']}")
    for item in hits:
        sha = shas.get((item["corpus"], item["path"]))
        others = [x for x in by_sha.get(sha or "", [])
                  if x != f"{item['corpus']}:{item['path']}"]
        if others:
            item["same_content_as"] = others[:3]

    merged = _dedupe_hits(hits)
    merged.sort(key=_hit_key)
    merged = merged[:limit]
    for item in merged:
        item.pop("_corpus_rank", None)

    for item in neighbors:
        item.pop("_corpus_rank", None)

    if hits and not merged:
        notes.append("хиты отброшены лимитом: увеличьте limit")
    if not merged:
        notes.append(
            "готовой реализации в соседях не найдено: либо её нет, либо запрос "
            "сформулирован иначе — попробуйте имя символа (name) и синонимы"
        )
    if dropped["db"]:
        limits.append(
            f"исключено фрагментов слоя баз: {dropped['db']} — это структура и "
            "примеры строк чужой базы, а не реализация (спросить явно: kinds)"
        )
    if dropped["docs"]:
        limits.append(
            f"документов сверх {MAX_DOC_HITS} на корпус не показано: {dropped['docs']} "
            "— описание полезно ссылкой, но реализацию ищут в коде"
        )
    if dropped["kinds"]:
        limits.append(f"отброшено по видам чанков (kinds): {dropped['kinds']}")
    if dropped["symbols"]:
        limits.append(
            f"узлов с совпавшим именем не показано: {dropped['symbols']} — вид "
            "узла не реализация (правила оформления, служебные узлы)"
        )
    if plan["skipped"]:
        limits.append(
            f"не опрошено корпусов: {len(plan['skipped'])} (причины в skipped)"
        )

    return {
        "query": query,
        "name": name,
        "stack": {
            "source": stack_source,
            "languages": dict(sorted(mine.items(), key=lambda kv: (-kv[1], kv[0]))),
            "core": dict(sorted(code_core(mine).items(),
                                key=lambda kv: (-kv[1], kv[0]))),
            "primary": primary_lang(code_core(mine)),
            "shared_total": sum(len(n["shared"]) for n in neighbors),
        },
        "neighbors": neighbors,
        "hits": merged,
        "skipped": plan["skipped"],
        "totals": {
            "in_registry": plan["in_registry"],
            "with_index": plan["with_index"],
            "matched": plan["matched"],
            "queried": len(neighbors),
            "hits": len(merged),
        },
        "notes": notes,
        "limits": limits,
    }
