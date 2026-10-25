"""Маршрутизатор: какой локальный индекс отвечает на вопрос.

Зачем отдельный слой
    Реестр разработок знает, какие корпуса заведены; карта метаданных знает,
    какие файлы где лежат. Агент, задавший вопрос «где материалы по X», до сих
    пор получал либо список файлов без корпуса, либо ответ текущего корпуса,
    даже если вопрос про другой проект. Маршрутизатор соединяет два знания и
    называет причину выбора.

Чего он НЕ делает
    * не читает содержимое файлов и не цитирует документы;
    * не вызывает моделей и не «понимает» запрос: только совпадение слов по
      именам, путям и названиям разработок;
    * не обещает свежести, которую не проверил: `fingerprint_match` считается
      фактической сверкой отпечатка, а не берётся из записи реестра.

Детерминизм
    Одинаковый запрос и одинаковое состояние дают одинаковый порядок: score,
    затем тип, затем имя. Ничья разрешается путём — как при схлопывании
    дублей (`src/ragraf/dedupe.py:165-223`), иначе порядок зависел бы от
    порядка обхода каталога и ответ дрожал бы между запусками.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import util

# Пределы, чтобы маршрутизация не превращалась в скан всего диска.
MAX_TOKENS = 12
MAX_MAP_HITS = 400
SAMPLE_FILES = 3


def _tokens(query: str) -> list[str]:
    """Слова запроса без служебных частиц. Порядок сохраняется, дубли убраны."""
    stop = {
        "где", "что", "как", "какие", "какой", "какая", "который", "это",
        "для", "про", "или", "and", "the", "with", "без", "мне", "надо",
        "нужно", "найти", "покажи", "есть", "ли", "по",
    }
    out: list[str] = []
    seen: set[str] = set()
    for raw in str(query or "").replace("\n", " ").split():
        word = raw.strip(".,;:!?()[]{}<>\"'`«»—–-").lower()
        if len(word) < 2 or word in stop or word in seen:
            continue
        seen.add(word)
        out.append(word)
        if len(out) >= MAX_TOKENS:
            break
    return out


def _age_seconds(stamp: str) -> float | None:
    if not stamp:
        return None
    try:
        moment = datetime.fromisoformat(str(stamp))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return max(0.0, (datetime.now(timezone.utc) - moment).total_seconds())


def _freshness_score(age: float | None) -> tuple[float, str]:
    """Свежесть по возрасту индекса: 1.0 — сегодня, 0 — месяц и старше."""
    if age is None:
        return 0.0, "время индексации неизвестно"
    days = age / 86400.0
    if days <= 1:
        return 1.0, "индекс создан сегодня (по записи реестра)"
    if days <= 7:
        return 0.7, f"индекс создан {days:.0f} дн. назад (по записи реестра)"
    if days <= 30:
        return 0.35, f"индекс создан {days:.0f} дн. назад (по записи реестра)"
    return 0.0, f"индекс старше месяца ({days:.0f} дн., по записи реестра)"


_PATH_CACHE: dict[str, str] = {}


def _long_path(path: str) -> str:
    """Раскрыть короткую форму 8.3 в длинную (Windows).

    Зачем: `TEMP` в окружении задан как `C:\\Users\\<пользователь>\\...`, а корень
    карты может быть записан длинной формой. Без приведения `USER~1` и
    `user.name` — разные строки, и файл приписывался источнику вместо
    корпуса. Ошибка не падает: она просто даёт неверного кандидата.
    """
    text = str(path)
    cached = _PATH_CACHE.get(text)
    if cached is not None:
        return cached
    result = text
    try:
        import ctypes

        buf = ctypes.create_unicode_buffer(32768)
        size = ctypes.windll.kernel32.GetLongPathNameW(text, buf, len(buf))
        if 0 < size < len(buf):
            result = buf.value
    except Exception:  # noqa: BLE001 — не Windows или путь недоступен
        result = text
    _PATH_CACHE[text] = result
    return result


def _norm(path: str) -> str:
    try:
        return os.path.normcase(util.to_posix(os.path.realpath(_long_path(path))))
    except (OSError, ValueError):
        return os.path.normcase(util.to_posix(path))


def _path_has_token(root_lower: str, needle: str) -> bool:
    """Совпадает ли слово запроса с СЕГМЕНТОМ пути, а не с частью чужого имени.

    Зачем: запрос «amd» совпадал с `C:/Users/<пользователь>/...`, потому что
    слово — подстрока имени пользователя. Из-за этого в маршрут попадали
    посторонние корпуса. Сегмент сравнивается целиком либо как начало с
    разделителем (`amd-project`, `amd_notes`).
    """
    for segment in root_lower.replace("\\", "/").split("/"):
        if not segment:
            continue
        if segment == needle:
            return True
        if segment.startswith(needle + "-") or segment.startswith(needle + "_") \
                or segment.startswith(needle + "."):
            return True
    return False


def _inside(path_norm: str, root_norm: str) -> bool:
    if not path_norm or not root_norm:
        return False
    # После `normcase` разделители на Windows — обратные, поэтому сравнивать
    # с прямым слэшем нельзя: `notes` и `notes\\a.md` выглядели бы разными
    # деревьями, и файл приписывался источнику вместо корпуса.
    root = root_norm.rstrip("/\\")
    return path_norm == root or path_norm.startswith(root + os.sep)


def _local_index(entry: dict[str, Any], *, verify: bool) -> dict[str, Any] | None:
    """Сведения о локальном индексе разработки.

    `verify=True` добавляет фактическую сверку отпечатка. Она читает дерево
    корпуса (только метаданные), поэтому выполняется лишь для рекомендованной
    разработки, а не для каждого кандидата.
    """
    rev = str(entry.get("rev_id") or "")
    if not rev:
        return None
    age = _age_seconds(str(entry.get("indexed_at") or ""))
    data: dict[str, Any] = {
        "category": entry.get("category"),
        "root": entry.get("root"),
        "rev_id": rev,
        "capability": entry.get("capability") or "",
        "indexed_at": entry.get("indexed_at") or "",
        "age_seconds": int(age) if age is not None else None,
        "files": int(entry.get("file_count") or entry.get("files") or 0),
    }
    if not verify:
        return data
    config_path = Path(str(entry.get("config") or ""))
    if not config_path.exists():
        data["fingerprint_match"] = None
        data["verify_error"] = "конфиг корпуса не найден"
        return data
    try:
        from . import corpus, watch
        from .config import load as load_config

        cfg = load_config(config_path)
        saved = watch.read_fingerprint(cfg) or {}
        now_fp, files_now, _total = corpus.stat_fingerprint(cfg)
        data["fingerprint_match"] = bool(
            saved.get("fingerprint") and saved.get("fingerprint") == now_fp
        )
        data["files_now"] = int(files_now)
        if data["fingerprint_match"] is False:
            data["note"] = (
                "отпечаток входа разошёлся с записанным: факты индекса могут "
                "относиться к прежнему состоянию кода"
            )
    except Exception as exc:  # noqa: BLE001 — сверка не имеет права ронять маршрут
        data["fingerprint_match"] = None
        data["verify_error"] = f"{type(exc).__name__}: {exc}"
    return data


def route(query: str, *, limit: int = 10, verify: bool = True,
          catalog: Any | None = None,
          map_path: str | Path | None = None) -> dict[str, Any]:
    """Найти кандидатов и назвать причину выбора. Без чтения содержимого.

    `catalog` и `map_path` существуют для проверок: тест подставляет временный
    реестр и временную карту, не трогая рабочие файлы проекта. В обычном вызове
    оба — `None`, и берутся штатные места.
    """
    from .catalog import Catalog
    from .global_map import GlobalMap

    tokens = _tokens(query)
    limit = max(1, min(int(limit), 50))
    notes: list[str] = []
    if not tokens:
        return {
            "query": query,
            "tokens": [],
            "candidates": [],
            "recommended": None,
            "notes": ["запрос не содержит слов: маршрут не определён"],
        }

    entries = (catalog.entries() if catalog is not None else Catalog().entries())
    # Корни разработок: самый длинный совпавший корень выигрывает — иначе
    # файл внутри проекта приписывался бы его родительскому каталогу.
    corpus_roots: list[tuple[str, str]] = []
    for key, entry in entries.items():
        name = str(entry.get("category") or key)
        root = str(entry.get("root") or "")
        if root:
            corpus_roots.append((_norm(root), name))
    corpus_roots.sort(key=lambda pair: (-len(pair[0]), pair[1]))

    map_rows: list[dict[str, Any]] = []
    map_sources: list[dict[str, Any]] = []
    map_error = ""
    try:
        from .sources import CATALOG_DIR

        map_file = Path(map_path) if map_path is not None else (CATALOG_DIR / "global_map.sqlite")
        if map_file.exists():
            gm = GlobalMap(map_file)
            try:
                map_rows = gm.find(tokens, limit=MAX_MAP_HITS)
                map_sources = gm.sources()
            finally:
                gm.close()
    except Exception as exc:  # noqa: BLE001 — карта может отсутствовать
        map_error = f"{type(exc).__name__}: {exc}"

    if map_error:
        notes.append(f"карта метаданных недоступна: {map_error}")
    elif not map_sources:
        notes.append(
            "карта метаданных пуста: маршрут опирается только на реестр; "
            "построить карту — `map scan` после `sources add`"
        )

    # Совпадения группируются: по разработке — если файл внутри её корня,
    # иначе по источнику (у него может не быть локального индекса).
    per_corpus: dict[str, dict[str, Any]] = {}
    per_source: dict[int, dict[str, Any]] = {}
    for row in map_rows:
        abs_path = f"{str(row['root']).rstrip('/')}/{row['rel_path']}"
        file_norm = _norm(abs_path)
        owner = ""
        for root_norm, name in corpus_roots:
            if _inside(file_norm, root_norm):
                owner = name
                break
        if owner:
            item = per_corpus.setdefault(owner, {"hits": 0, "samples": []})
        else:
            sid = int(row["source_id"])
            item = per_source.setdefault(sid, {
                "source_id": sid,
                "root": row["root"],
                "priority": int(row["priority"] or 0),
                "kind": row["kind"],
                "hits": 0,
                "samples": [],
            })
        item["hits"] += 1
        if len(item["samples"]) < SAMPLE_FILES:
            item["samples"].append(str(row["rel_path"]))

    # Приоритет источника наследуется разработкой, если она лежит внутри него.
    source_priority: dict[str, int] = {}
    for info in map_sources:
        src_norm = _norm(str(info["root"]))
        for root_norm, name in corpus_roots:
            if _inside(root_norm, src_norm):
                source_priority[name] = max(
                    source_priority.get(name, 0), int(info.get("priority") or 0)
                )

    candidates: list[dict[str, Any]] = []
    for key, entry in entries.items():
        name = str(entry.get("category") or key)
        project = str(entry.get("project") or "")
        root = str(entry.get("root") or "")
        category_l = name.lower()
        project_l = project.lower()
        root_l = root.lower()
        # Запись реестра может пережить свой каталог: корпус перенесли или
        # удалили. Такой кандидат обязан быть виден, но не рекомендован.
        root_exists = bool(root) and Path(root).is_dir()

        score = 0.0
        why: list[str] = []
        matched = False
        for token in tokens:
            if token in category_l:
                score += 3.0
                matched = True
                why.append(f"имя разработки содержит «{token}»")
            elif token in project_l:
                score += 2.0
                matched = True
                why.append(f"проект содержит «{token}»")
            elif _path_has_token(root_l, token):
                score += 1.0
                matched = True
                why.append(f"путь содержит сегмент «{token}»")

        found = per_corpus.get(name) or {}
        hits = int(found.get("hits") or 0)
        if hits:
            matched = True
            score += min(hits, 20) * 0.5 + 2.0
            why.append(f"внутри корпуса найдено файлов: {hits}")

        # Свежесть и режим сами по себе НЕ делают разработку кандидатом:
        # иначе запрос про несуществующую тему рекомендовал бы случайный
        # свежий корпус. Сначала совпадение по имени/пути/файлам, потом оценка.
        if not matched:
            continue

        local = _local_index(entry, verify=False)
        if local is not None:
            fresh, fresh_why = _freshness_score(
                _age_seconds(str(local.get("indexed_at") or ""))
            )
            score += fresh * 2.0
            why.append(fresh_why)
            if local.get("capability") == "full":
                score += 1.0
                why.append("режим full: доступны операции среза")
            elif local.get("capability") == "degraded":
                score += 0.3
                why.append("режим degraded: часть операций недоступна")
        else:
            why.append("локального индекса нет")

        priority = int(source_priority.get(name, 0))
        if priority:
            score += priority / 10.0
            why.append(f"приоритет источника {priority}")

        if not why:
            continue
        if not root_exists:
            score -= 2.0
            why.append("корень не найден на диске: запись реестра устарела")
        candidates.append({
            "type": "corpus",
            "category": name,
            "project": project,
            "root": root,
            "available": root_exists,
            "score": score,
            "why": why,
            "file_hits": hits,
            "sample_files": [
                f"{root.rstrip('/')}/{rel}" for rel in (found.get("samples") or [])
            ],
            "local_index": local,
        })

    # Источники без корпуса: честный ответ «здесь лежит, индекса нет».
    for sid, info in per_source.items():
        score = min(int(info["hits"]), 20) * 0.5 + 2.0
        why = [f"в источнике найдено файлов: {info['hits']}"]
        if info.get("priority"):
            score += float(info["priority"]) / 10.0
            why.append(f"приоритет источника {info['priority']}")
        candidates.append({
            "type": "source",
            "category": "",
            "project": "",
            "root": info["root"],
            "kind": info.get("kind") or "",
            "score": score,
            "why": why,
            "file_hits": int(info["hits"]),
            "sample_files": [
                f"{str(info['root']).rstrip('/')}/{rel}" for rel in info["samples"]
            ],
            "local_index": None,
        })

    # Порядок детерминирован: score, тип, имя. Ничья разрешается путём.
    candidates.sort(key=lambda c: (
        -round(float(c["score"]), 6),
        str(c.get("type") or ""),
        str(c.get("category") or c.get("root") or ""),
    ))
    candidates = candidates[:limit]

    # Фактическая сверка отпечатка — только для рекомендованного корпуса.
    recommended: dict[str, Any] | None = None
    for cand in candidates:
        if (cand["type"] == "corpus" and cand.get("local_index")
                and cand.get("available", True)):
            recommended = cand
            break
    if recommended is not None and verify:
        entry = entries.get(str(recommended["category"])) or {}
        verified = _local_index(entry, verify=True)
        recommended["local_index"] = verified
        note = (verified or {}).get("note")
        if note:
            recommended["stale"] = True
            # Возраст записи — не доказательство свежести: при расхождении
            # отпечатка убираем формулировку «создан …» и говорим причину.
            recommended["why"] = [
                w for w in recommended.get("why", [])
                if "по записи реестра" not in w
            ]
            recommended["why"].append("индекс отстал: отпечаток входа разошёлся")
            notes.append(note)
        elif (verified or {}).get("fingerprint_match") is True:
            recommended["why"].append(
                "отпечаток входа совпал: факты соответствуют коду"
            )

    if recommended is None and candidates:
        missing = [c["category"] for c in candidates
                   if c["type"] == "corpus" and c.get("available") is False]
        if missing:
            notes.append(
                "часть разработок не найдена на диске (" + ", ".join(missing[:5]) +
                "): их записи в реестре устарели"
            )
        else:
            notes.append(
                "совпадения есть, но локального индекса у них нет: "
                "создайте корпус командой ensure"
            )
    elif not candidates:
        notes.append(
            "совпадений по имени, пути или файлам нет: маршрут не определён. "
            "Если вопрос по содержимому — используйте search; если карта пуста — "
            "сначала sources add и map scan"
        )

    return {
        "query": query,
        "tokens": tokens,
        "candidates": candidates,
        "recommended": recommended,
        "notes": notes,
        "map_hits": len(map_rows),
        "corpora": len(entries),
    }
