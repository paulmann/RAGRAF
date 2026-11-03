"""Политика хранения `.ragraf`: объём, что удаляется вместе с ревизией, `VACUUM`.

Что измерено 30.10.2026 на корпусе ragraf (активная ревизия rev-0869, 945 файлов):

  * `.ragraf` целиком — 26 829 МБ, из них `cpg/` 17 831 МБ и `index/` 8 954 МБ;
  * `cpg/` — 474 файла `rev-XXXX.cpg.bin` (5,65 ГБ), 452 РАСПАКОВАННЫХ каталога
    `cpg/workspace/rev-XXXX.cpg.bin` (11,5 ГБ), `cpg/tmp` 473 МБ, одна выгрузка
    `out_*.json` 74,8 МБ и три пустых каталога `export_*`;
  * в лексике 7 729 196 строк документного слоя принадлежали 265 ревизиям, которых
    в истории нет: ротация чистила граф и лексику, а документный слой и артефакты
    CPG не чистила никогда;
  * чистка хвоста и `VACUUM` на копии: граф 965,2 → 842,7 МБ, лексика 7 843,3 →
    860,8 МБ, `VACUUM` 21,6 с, активная ревизия цела (945 файлов / 5 740 узлов /
    77 240 рёбер).

Зачем отдельный модуль
    Правило «удаляется вместе с ревизией» до этого жило рукописным списком таблиц
    в `Revisions._purge_rev_data` — списком, который надо было не забыть
    дополнить. Его и не дополнили: документный слой появился позже, и цена
    измерена выше. Здесь список таблиц не пишется руками, он ВЫЧИСЛЯЕТСЯ по схеме
    (у какой таблицы есть колонка `rev_id`), поэтому новая таблица попадает под
    политику в тот день, когда появилась.

Границы
    Модуль решает, что МОЖНО удалить, и умеет это выполнить. Он не решает, сколько
    ревизий хранить (это `retain_revs` и настройки `storage_*`), не трогает
    историю ревизий в `control.sqlite` и не удаляет ничего вне `.ragraf`.
    Активная ревизия защищена всегда: ни её строки, ни её артефакты CPG не
    попадают в план удаления ни при каких значениях настроек.
"""

from __future__ import annotations

import os
import re
import shutil
import sqlite3
import time
from pathlib import Path

from . import store as store_mod
from .config import ProjectConfig

#: Разбор имён артефактов CPG: `rev-0848.cpg.bin` и каталог того же имени.
_REV_NAME = re.compile(r"^(rev-\d{3,8})$")

#: Пустые рабочие каталоги Joern: `RAGRAF`, `RAGRAF1`, `RAGRAF457`.
_EMPTY_WORKSPACE = re.compile(r"^RAGRAF\d*$")

#: Таблица истории ревизий: удалять из неё нельзя никогда, даже если строка
#: выглядит осиротевшей. История — знание о ревизии, а не данные ревизии.
_HISTORY_TABLE = "rev"

MIB = 1_048_576
GIB = 1_073_741_824


class StoragePolicyError(RuntimeError):
    """Политика хранения отказала: удаление затронуло бы защищённые данные."""


# ---------------------------------------------------------------------------
# Настройки политики
# ---------------------------------------------------------------------------


def _settings() -> dict:
    """Значения политики из общего файла настроек; на ошибке — умолчания.

    Читается через `SettingsStore`, а не через конфиг корпуса: политика
    хранения — решение установки, а не проекта. Ошибка чтения не имеет права
    ломать `--plan`: план обязан считаться и при недоступном каталоге состояния.
    """
    from . import settings as settings_mod

    try:
        return settings_mod.SettingsStore().load()
    except Exception:  # noqa: BLE001 — план важнее источника настроек
        return dict(settings_mod.DEFAULTS)


def _int_setting(values: dict, key: str, default: int) -> int:
    try:
        return int(float(values.get(key, default)))
    except (TypeError, ValueError):
        return int(default)


# ---------------------------------------------------------------------------
# Размеры
# ---------------------------------------------------------------------------


def path_bytes(path: str | Path) -> int:
    """Размер файла или каталога. Символические ссылки не разыменовываются."""
    p = Path(path)
    try:
        if p.is_file():
            return p.stat().st_size
        if not p.is_dir():
            return 0
    except OSError:
        return 0
    total = 0
    stack = [p]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(Path(entry.path))
                        elif entry.is_file(follow_symlinks=False):
                            total += entry.stat(follow_symlinks=False).st_size
                    except OSError:
                        continue
        except OSError:
            continue
    return total


def path_file_count(path: str | Path) -> int:
    """Сколько файлов внутри каталога (для отчёта «из чего состоит объём»)."""
    p = Path(path)
    if p.is_file():
        return 1
    if not p.is_dir():
        return 0
    count = 0
    stack = [p]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(Path(entry.path))
                        elif entry.is_file(follow_symlinks=False):
                            count += 1
                    except OSError:
                        continue
        except OSError:
            continue
    return count


def store_state(path: str | Path) -> dict:
    """Размер файла БД, страницы и свободные страницы.

    Свободные страницы — то, что вернул бы `VACUUM`: удаление строк файл не
    уменьшает. Поэтому «сколько занимает» и «сколько можно вернуть» — два разных
    числа, и ответ называет оба.
    """
    p = Path(path)
    entry: dict = {"path": p.name, "bytes": 0, "mb": 0.0, "missing": True}
    try:
        if not p.is_file():
            return entry
        size = p.stat().st_size
    except OSError:
        return entry
    entry.update({"bytes": size, "mb": round(size / MIB, 1), "missing": False})
    try:
        conn = sqlite3.connect(f"file:{p.as_posix()}?mode=ro", uri=True)
        try:
            page = int(conn.execute("PRAGMA page_size").fetchone()[0])
            pages = int(conn.execute("PRAGMA page_count").fetchone()[0])
            free = int(conn.execute("PRAGMA freelist_count").fetchone()[0])
        finally:
            conn.close()
        entry.update(
            {
                "pages": pages,
                "page_size": page,
                "free_pages": free,
                "free_mb": round(free * page / MIB, 1),
                "free_share": round(free / pages, 4) if pages else 0.0,
            }
        )
    except (sqlite3.Error, OSError) as error:
        entry["pages_error"] = str(error)[:160]
    return entry


# ---------------------------------------------------------------------------
# Артефакты CPG
# ---------------------------------------------------------------------------


def cpg_artifacts(cfg: ProjectConfig) -> dict:
    """Что лежит в `cpg/` и какой ревизии принадлежит.

    Три вида артефактов несут объём, и все три копились без предела:

      * `rev-XXXX.cpg.bin` — файл CPG (≈15,7 МБ на ревизию), вход для экспорта;
      * `workspace/rev-XXXX.cpg.bin` — РАСПАКОВАННЫЙ той же Joern каталог
        (≈25,4 МБ на ревизию; именно он даёт 11,5 ГБ из 17,8);
      * `export_rev-XXXX_neo4jcsv` — выгрузка узлов и рёбер.

    Имена `out_*.json` (результат запроса) и `query_*.sc` (сгенерированный скрипт)
    ревизии не принадлежат: это временные выходы, класс `temp`, а не «артефакт
    ревизии». Неопознанные имена (`probe-test.cpg.bin`) попадают в `unknown`
    и не удаляются: неопознанное имя — не повод удалять.
    """
    workdir = Path(cfg.ragraf_dir) / "cpg"
    result: dict = {
        "workdir": str(workdir),
        "bins": {},
        "workspaces": {},
        "exports": {},
        "temp": [],
        "empty_workspaces": [],
        "unknown": [],
        "tmp_bytes": 0,
        "tmp_files": 0,
        "total_bytes": 0,
        "workdir_missing": not workdir.is_dir(),
    }
    if not workdir.is_dir():
        return result

    for item in sorted(workdir.iterdir(), key=lambda p: p.name):
        name = item.name
        try:
            if item.is_file():
                if name.endswith(".cpg.bin"):
                    rev = name[: -len(".cpg.bin")]
                    if _REV_NAME.match(rev):
                        result["bins"][rev] = {"path": str(item), "bytes": item.stat().st_size}
                    else:
                        result["unknown"].append({"path": str(item), "bytes": item.stat().st_size})
                elif name.startswith("out_") or name.startswith("query_"):
                    result["temp"].append({"path": str(item), "bytes": item.stat().st_size})
                continue
            if not item.is_dir():
                continue
            if name.startswith("export_"):
                rev = name[len("export_"):].split("_")[0]
                if _REV_NAME.match(rev):
                    result["exports"][rev] = {"path": str(item), "bytes": path_bytes(item)}
                else:
                    result["unknown"].append({"path": str(item), "bytes": path_bytes(item)})
        except OSError:
            continue

    workspace = workdir / "workspace"
    if workspace.is_dir():
        try:
            entries = sorted(workspace.iterdir(), key=lambda p: p.name)
        except OSError:
            entries = []
        for item in entries:
            name = item.name
            try:
                if not item.is_dir():
                    result["unknown"].append({"path": str(item), "bytes": path_bytes(item)})
                    continue
            except OSError:
                continue
            rev = name[: -len(".cpg.bin")] if name.endswith(".cpg.bin") else name
            if name.endswith(".cpg.bin") and _REV_NAME.match(rev):
                result["workspaces"][rev] = {"path": str(item), "bytes": path_bytes(item)}
            elif _EMPTY_WORKSPACE.match(name):
                # Рабочий каталог Joern, в котором ничего нет: 457 штук и 0 МБ.
                # Не удаляется: нулевой объём — не выигрыш, а риск задеть
                # каталог работающего сервера Joern.
                result["empty_workspaces"].append(str(item))
            else:
                result["unknown"].append({"path": str(item), "bytes": path_bytes(item)})

    tmp_dir = workdir / "tmp"
    result["tmp_bytes"] = path_bytes(tmp_dir)
    result["tmp_files"] = path_file_count(tmp_dir)
    result["total_bytes"] = (
        sum(int(v["bytes"]) for v in result["bins"].values())
        + sum(int(v["bytes"]) for v in result["workspaces"].values())
        + sum(int(v["bytes"]) for v in result["exports"].values())
        + sum(int(v["bytes"]) for v in result["temp"])
        + sum(int(v["bytes"]) for v in result["unknown"])
        + int(result["tmp_bytes"])
    )
    return result


# ---------------------------------------------------------------------------
# История и таблицы
# ---------------------------------------------------------------------------


def revisions(cfg: ProjectConfig) -> list[dict]:
    """Ревизии, чьи ДАННЫЕ есть в графе, от новой к старой.

    Источник — таблица `rev` в `graph.sqlite`: в ней только те ревизии, данные
    которых ещё лежат в хранилищах. Полная история (`control.sqlite`,
    `rev_history`) сюда не относится и не меняется никогда.
    """
    path = Path(cfg.graph_db)
    if not path.is_file():
        return []
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT rev_id, created_at, state, capability, file_count, node_count, edge_count"
            " FROM rev ORDER BY rev_id DESC"
        ).fetchall()
    except sqlite3.Error:
        return []
    finally:
        conn.close()
    return [
        {
            "rev_id": str(row["rev_id"]),
            "created_at": str(row["created_at"]),
            "state": str(row["state"]),
            "capability": str(row["capability"]),
            "files": int(row["file_count"] or 0),
            "nodes": int(row["node_count"] or 0),
            "edges": int(row["edge_count"] or 0),
        }
        for row in rows
    ]


def tables_with_rev(conn: sqlite3.Connection) -> list[str]:
    """Таблицы, у которых есть колонка `rev_id`, — ВЫЧИСЛЕННЫЙ список.

    Ровно этот список решает, что удаляется вместе с ревизией. Рукописный список
    в `_purge_rev_data` не пополнялся, и документный слой (`document_chunk`,
    `document_node`, `document`) прожил вне политики: 7 729 196 строк от 265
    ревизий, которых в истории нет. Таблица истории исключается: её строки —
    знание о ревизии, а не данные ревизии.
    """
    names: list[str] = []
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
    except sqlite3.Error:
        return names
    for row in rows:
        name = str(row[0])
        if name == _HISTORY_TABLE or name.startswith("sqlite_"):
            continue
        try:
            columns = [str(c[1]) for c in conn.execute(f"PRAGMA table_info({name})")]
        except sqlite3.Error:
            continue
        if "rev_id" in columns:
            names.append(name)
    return names


def orphan_rows(cfg: ProjectConfig, known: set[str]) -> dict[str, dict[str, int]]:
    """Строки, чьей ревизии нет в истории: по каждой базе и каждой таблице."""
    report: dict[str, dict[str, int]] = {}
    for label, path_value in (("graph", cfg.graph_db), ("lexvec", cfg.lexvec_db)):
        path = Path(path_value)
        if not path.is_file():
            continue
        conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        try:
            per_table: dict[str, int] = {}
            for table in tables_with_rev(conn):
                try:
                    rows = conn.execute(
                        f"SELECT rev_id, COUNT(*) FROM {table} GROUP BY rev_id"
                    ).fetchall()
                except sqlite3.Error:
                    continue
                orphan = sum(int(n) for rev, n in rows if str(rev) not in known)
                if orphan:
                    per_table[table] = orphan
            if per_table:
                report[label] = per_table
        finally:
            conn.close()
    return report


def orphan_revision_ids(cfg: ProjectConfig, known: set[str]) -> list[str]:
    """Имена ревизий, чьи строки остались в хранилищах, а записи в истории нет."""
    found: set[str] = set()
    for path_value in (cfg.graph_db, cfg.lexvec_db):
        path = Path(path_value)
        if not path.is_file():
            continue
        conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        try:
            for table in tables_with_rev(conn):
                try:
                    rows = conn.execute(f"SELECT DISTINCT rev_id FROM {table}").fetchall()
                except sqlite3.Error:
                    continue
                found.update(str(row[0]) for row in rows if str(row[0]) not in known)
        finally:
            conn.close()
    return sorted(found)


# ---------------------------------------------------------------------------
# План
# ---------------------------------------------------------------------------


def plan(
    cfg: ProjectConfig,
    *,
    retain: int | None = None,
    tmp_max_mb: int | None = None,
    tmp_age_hours: int | None = None,
    vacuum_free_percent: int | None = None,
    cpg_keep_revs: int | None = None,
) -> dict:
    """Что можно удалить и сколько это вернёт. Ничего не меняет.

    Порядок решения (каждый шаг назван числами в ответе):

    1. ревизии, чьи данные в хранилищах, — источник истины для «что хранить»;
    2. `retain_revs` задаёт, сколько последних хранить; активная ревизия хранится
       всегда, даже если её нет в этом списке;
    3. хвост в базах считается по схеме (все таблицы с `rev_id`), а не по списку;
    4. артефакты CPG сохраняются для `storage_keep_cpg_revs` последних ревизий
       и всегда для активной; остальные удаляются — они выводятся из исходников;
    5. временные выгрузки (`out_*.json`, `query_*.sc`) удаляются по возрасту;
    6. `cpg/tmp` и `.ragraf/tmp` ограничены пределом `storage_tmp_max_mb`
       и возрастом `storage_tmp_age_hours`;
    7. `VACUUM` предлагается там, где свободные страницы превышают
       `storage_vacuum_free_percent` — иначе переписывать гигабайтный файл
       ради процента бессмысленно.
    """
    values = _settings()
    retain = max(1, _int_setting(values, "retain_revs", 3) if retain is None else int(retain))
    tmp_max_mb = (_int_setting(values, "storage_tmp_max_mb", 512)
                  if tmp_max_mb is None else int(tmp_max_mb))
    tmp_age_hours = max(1, _int_setting(values, "storage_tmp_age_hours", 24)
                        if tmp_age_hours is None else int(tmp_age_hours))
    vacuum_free_percent = (_int_setting(values, "storage_vacuum_free_percent", 20)
                           if vacuum_free_percent is None else int(vacuum_free_percent))
    cpg_keep_revs = (_int_setting(values, "storage_keep_cpg_revs", 1)
                     if cpg_keep_revs is None else int(cpg_keep_revs))

    history = revisions(cfg)
    known = {item["rev_id"] for item in history}
    active = history[0]["rev_id"] if history else ""

    keep = [item["rev_id"] for item in history[:retain]]
    if active and active not in keep:
        keep.insert(0, active)
    keep_set = set(keep)
    tail = [item["rev_id"] for item in history if item["rev_id"] not in keep_set]

    orphans = orphan_rows(cfg, known)
    orphan_revs = orphan_revision_ids(cfg, known)

    arts = cpg_artifacts(cfg)
    cpg_keep = [item["rev_id"] for item in history[: max(1, cpg_keep_revs)]]
    if active and active not in cpg_keep:
        cpg_keep.insert(0, active)
    cpg_keep_set = set(cpg_keep)
    cpg_remove: dict[str, dict] = {}
    for source in ("bins", "workspaces", "exports"):
        for rev, entry in arts[source].items():
            if rev in cpg_keep_set:
                continue
            slot = cpg_remove.setdefault(rev, {"bin": 0, "workspace": 0, "export": 0})
            slot[{"bins": "bin", "workspaces": "workspace", "exports": "export"}[source]] = \
                int(entry["bytes"])
    cpg_remove_bytes = sum(int(v["bin"]) + int(v["workspace"]) + int(v["export"])
                           for v in cpg_remove.values())

    cutoff = time.time() - tmp_age_hours * 3600
    temp_old = [item for item in arts["temp"] if _mtime(str(item["path"])) < cutoff]
    unknown_old = [item for item in arts["unknown"] if _mtime(str(item["path"])) < cutoff]

    tmp_entries: list[dict] = []
    tmp_total = 0
    for label in ("cpg/tmp", "tmp"):
        directory = Path(cfg.ragraf_dir) / label
        if not directory.is_dir():
            continue
        size = path_bytes(directory)
        tmp_total += size
        old = _old_files(directory, cutoff)
        tmp_entries.append({"label": label, "path": str(directory), "bytes": size,
                            "files": path_file_count(directory),
                            "old_files": len(old), "old_bytes": sum(f["bytes"] for f in old)})
    overflow = max(0, tmp_total - tmp_max_mb * MIB)

    stores = {
        "graph": store_state(cfg.graph_db),
        "lexvec": store_state(cfg.lexvec_db),
        "control": store_state(cfg.control_db),
    }
    # Условие VACUUM. Удаление строк само оставляет свободные страницы, поэтому
    # порог доли решает только случай «удалять нечего, а файл раздут прежними
    # удалениями»: замер 30.10.2026 — до чистки свободно 6,1 % в графе и 2,7 %
    # в лексике, то есть по порогу 20 % VACUUM не запустился бы, хотя после
    # удаления 11,3 млн строк он возвращает 7,1 ГБ.
    vacuum: dict[str, bool] = {}
    vacuum_reason: dict[str, str] = {}
    for label in ("graph", "lexvec"):
        entry = stores[label]
        share = float(entry.get("free_share") or 0.0)
        rows_here = sum((orphans.get(label) or {}).values())
        by_threshold = bool(entry.get("free_pages")) and share >= vacuum_free_percent / 100.0
        vacuum[label] = bool(rows_here) or by_threshold
        if rows_here:
            vacuum_reason[label] = f"удаляется {rows_here} строк: место вернёт только VACUUM"
        elif by_threshold:
            vacuum_reason[label] = (f"свободных страниц {share * 100:.1f} % при пороге "
                                    f"{vacuum_free_percent} %")
        else:
            vacuum_reason[label] = (f"удалять нечего, свободных страниц {share * 100:.1f} % "
                                    f"при пороге {vacuum_free_percent} %")

    return {
        "project": cfg.project,
        "root": str(cfg.root),
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "policy": {
            "retain_revs": retain,
            "storage_keep_cpg_revs": cpg_keep_revs,
            "storage_tmp_max_mb": tmp_max_mb,
            "storage_tmp_age_hours": tmp_age_hours,
            "storage_vacuum_free_percent": vacuum_free_percent,
            "storage_auto_compact": bool(values.get("storage_auto_compact", True)),
        },
        "active": next((item for item in history if item["rev_id"] == active), None),
        "revisions_in_stores": len(history),
        "revisions_kept": keep,
        "revisions_tail": tail,
        "stores": stores,
        "total_index_mb": round(
            (int(stores["graph"]["bytes"]) + int(stores["lexvec"]["bytes"])) / MIB, 1),
        "cpg": {
            "workdir": arts["workdir"],
            "bytes": arts["total_bytes"],
            "mb": round(arts["total_bytes"] / MIB, 1),
            "bins": len(arts["bins"]),
            "workspaces": len(arts["workspaces"]),
            "exports": len(arts["exports"]),
            "empty_workspaces": len(arts["empty_workspaces"]),
            "temp_bytes": sum(int(v["bytes"]) for v in arts["temp"]),
            "tmp_bytes": int(arts["tmp_bytes"]),
            "tmp_files": int(arts["tmp_files"]),
            "keep_revs": sorted(cpg_keep_set),
            "remove_revs": len(cpg_remove),
            "remove_bytes": cpg_remove_bytes,
            "remove_mb": round(cpg_remove_bytes / MIB, 1),
            "detail": {rev: item for rev, item in sorted(cpg_remove.items())},
        },
        "orphans": {
            "revisions": len(orphan_revs),
            "revision_ids": orphan_revs,
            "rows": orphans,
            "rows_total": sum(sum(t.values()) for t in orphans.values()),
        },
        "temp": {
            "files": len(temp_old),
            "bytes": sum(int(v["bytes"]) for v in temp_old),
            "paths": [v["path"] for v in temp_old],
            "unknown_old": [v["path"] for v in unknown_old],
            "dirs": tmp_entries,
            "overflow_bytes": overflow,
            "limit_mb": tmp_max_mb,
            "age_hours": tmp_age_hours,
        },
        "vacuum": vacuum,
        "vacuum_reason": vacuum_reason,
        "delete_bytes": cpg_remove_bytes + sum(int(v["bytes"]) for v in temp_old),
    }


def _mtime(path: str) -> float:
    try:
        return Path(path).stat().st_mtime
    except OSError:
        return 0.0


def _all_files(directory: Path) -> list[dict]:
    """Все файлы каталога с размерами и временем правки (для предела объёма)."""
    found: list[dict] = []
    stack = [directory]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(Path(entry.path))
                        elif entry.is_file(follow_symlinks=False):
                            stat = entry.stat(follow_symlinks=False)
                            found.append({"path": entry.path, "bytes": stat.st_size,
                                          "mtime": stat.st_mtime})
                    except OSError:
                        continue
        except OSError:
            continue
    return found


def _old_files(directory: Path, cutoff: float) -> list[dict]:
    """Файлы старше порога с размерами — для отчёта и поштучного удаления."""
    found: list[dict] = []
    stack = [directory]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(Path(entry.path))
                        elif entry.is_file(follow_symlinks=False):
                            stat = entry.stat(follow_symlinks=False)
                            if stat.st_mtime < cutoff:
                                found.append({"path": entry.path, "bytes": stat.st_size})
                    except OSError:
                        continue
        except OSError:
            continue
    found.sort(key=lambda item: item["path"])
    return found


# ---------------------------------------------------------------------------
# Выполнение
# ---------------------------------------------------------------------------


def apply(
    cfg: ProjectConfig,
    *,
    vacuum: bool | None = None,
    remove_cpg: bool = True,
    prune_temp: bool = True,
    retain: int | None = None,
    tmp_max_mb: int | None = None,
    tmp_age_hours: int | None = None,
    vacuum_free_percent: int | None = None,
    cpg_keep_revs: int | None = None,
    progress=None,
) -> dict:
    """Выполнить план: удалить хвост, артефакты CPG, временные выгрузки, `VACUUM`.

    Защита проверяется ДО первого удаления и повторно ПОСЛЕ:

      * активная ревизия не участвует ни в одном списке удаления — иначе отказ;
      * данные ревизий из `revisions_kept` не удаляются никогда;
      * числа активной ревизии до и после совпадают, иначе
        `StoragePolicyError`: удаление, задевшее живую ревизию, не «доводится
        до конца», а называется дефектом.
    """
    started = time.perf_counter()
    current = plan(
        cfg,
        retain=retain,
        tmp_max_mb=tmp_max_mb,
        tmp_age_hours=tmp_age_hours,
        vacuum_free_percent=vacuum_free_percent,
        cpg_keep_revs=cpg_keep_revs,
    )
    active = str((current.get("active") or {}).get("rev_id") or "")
    keep = set(current.get("revisions_kept") or [])
    before_counts = {
        "files": int((current.get("active") or {}).get("files") or 0),
        "nodes": int((current.get("active") or {}).get("nodes") or 0),
        "edges": int((current.get("active") or {}).get("edges") or 0),
    }
    orphan_ids = [str(x) for x in (current.get("orphans", {}).get("revision_ids") or [])]
    if active and active in orphan_ids:
        raise StoragePolicyError(
            f"активная ревизия {active} попала в список удаления: план неверен, ничего не удалено"
        )
    if keep & set(orphan_ids):
        raise StoragePolicyError(
            "ревизия из списка хранения попала в хвост: план неверен, ничего не удалено"
        )
    # Строки активной ревизии считаются ПО ТАБЛИЦАМ, а не берутся из записи
    # ревизии: запись остаётся прежней, даже если строки уже потеряны.
    before_store = rev_store_counts(cfg, active) if active else {}

    before_bytes = {
        "graph": int(current["stores"]["graph"]["bytes"]),
        "lexvec": int(current["stores"]["lexvec"]["bytes"]),
        "control": int(current["stores"]["control"]["bytes"]),
        "cpg": int(current["cpg"]["bytes"]),
    }

    removed_rows = _purge_revisions(cfg, orphan_ids, progress=progress)
    cpg_removed = {"bins": 0, "workspaces": 0, "exports": 0, "bytes": 0}
    if remove_cpg:
        cpg_removed = _remove_cpg_artifacts(cfg, set(current["cpg"]["keep_revs"]))
    temp_removed = {"files": 0, "bytes": 0, "dirs": []}
    if prune_temp:
        temp_removed = _prune_temp(cfg, int(current["temp"]["age_hours"]),
                                   int(current["temp"]["limit_mb"]))

    vacuum_done: dict[str, dict] = {}
    for label, path_value in (("graph", cfg.graph_db), ("lexvec", cfg.lexvec_db)):
        needed = bool(current["vacuum"].get(label))
        if vacuum is False:
            vacuum_done[label] = {"ran": False, "reason": "VACUUM отключён вызовом"}
            continue
        if vacuum is None and not needed:
            vacuum_done[label] = {
                "ran": False,
                "reason": (f"свободных страниц {current['stores'][label].get('free_share', 0) * 100:.1f} %"
                           f" при пороге {current['policy']['storage_vacuum_free_percent']} %"),
            }
            continue
        vacuum_done[label] = _vacuum(Path(path_value))

    after_counts = _active_counts(cfg, active)
    if before_counts != {k: after_counts.get(k, 0) for k in before_counts}:
        raise StoragePolicyError(
            f"числа активной ревизии {active} изменились: было {before_counts}, "
            f"стало {after_counts}. Очистка задела живые данные — разбирать до продолжения"
        )
    after_store = rev_store_counts(cfg, active) if active else {}
    changed = {key: (before_store.get(key), after_store.get(key))
               for key in set(before_store) | set(after_store)
               if before_store.get(key) != after_store.get(key)}
    if changed:
        raise StoragePolicyError(
            "строки активной ревизии изменились при очистке: "
            + ", ".join(f"{k}: {v[0]} → {v[1]}" for k, v in list(changed.items())[:6])
        )

    after_bytes = {
        "graph": path_bytes(cfg.graph_db),
        "lexvec": path_bytes(cfg.lexvec_db),
        "control": path_bytes(cfg.control_db),
        "cpg": path_bytes(Path(cfg.ragraf_dir) / "cpg"),
    }
    remaining = orphan_revision_ids(cfg, {item["rev_id"] for item in revisions(cfg)})
    return {
        "ok": True,
        "seconds": round(time.perf_counter() - started, 1),
        "active": {"rev": active, "before": before_counts, "after": after_counts},
        "before": {k: {"bytes": v, "mb": round(v / MIB, 1)} for k, v in before_bytes.items()},
        "after": {k: {"bytes": v, "mb": round(v / MIB, 1)} for k, v in after_bytes.items()},
        "freed_mb": {k: round((before_bytes[k] - after_bytes[k]) / MIB, 1) for k in before_bytes},
        "freed_total_mb": round(
            sum(before_bytes.values()) / MIB - sum(after_bytes.values()) / MIB, 1),
        "removed_rows": removed_rows,
        "removed_revisions": orphan_ids,
        "active_store": {"before": before_store, "after": after_store},
        "cpg_removed": cpg_removed,
        "temp_removed": temp_removed,
        "vacuum": vacuum_done,
        "orphans_left": remaining,
        "revisions_kept": current["revisions_kept"],
        "policy": current["policy"],
    }


def _active_counts(cfg: ProjectConfig, rev: str) -> dict:
    for item in revisions(cfg):
        if item["rev_id"] == rev:
            return {"files": item["files"], "nodes": item["nodes"], "edges": item["edges"]}
    return {}


def rev_store_counts(cfg: ProjectConfig, rev: str) -> dict[str, int]:
    """ФАКТИЧЕСКИЕ строки активной ревизии в хранилищах, по таблицам.

    Зачем второе число рядом с `rev.file_count`: оно не читается из записи
    ревизии, а считается по строкам. Запись может остаться прежней, пока строки
    уже ушли — именно так выглядел дефект с документным слоем: ревизия называла
    945 файлов, а строк не было. Защита, сверяющая только запись, такого не ловит.
    """
    out: dict[str, int] = {}
    for label, path_value in (("graph", cfg.graph_db), ("lexvec", cfg.lexvec_db)):
        path = Path(path_value)
        if not path.is_file():
            continue
        conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        try:
            for table in tables_with_rev(conn):
                try:
                    count = int(conn.execute(
                        f"SELECT COUNT(*) FROM {table} WHERE rev_id = ?", (rev,)).fetchone()[0])
                except sqlite3.Error:
                    continue
                out[f"{label}.{table}"] = count
        finally:
            conn.close()
    return out


def _purge_revisions(cfg: ProjectConfig, revs: list[str], *, batch: int = 25,
                     progress=None) -> dict[str, dict[str, int]]:
    """Удалить строки перечисленных ревизий из всех таблиц с колонкой `rev_id`.

    Порциями по `batch` ревизий: одна транзакция на порцию и контрольная точка
    WAL после неё. Замер 30.10.2026: 7,7 млн строк одной транзакцией дают WAL
    в несколько гигабайт, порция ограничивает его сотнями мегабайт и позволяет
    видеть прогресс.
    """
    removed: dict[str, dict[str, int]] = {}
    if not revs:
        return removed
    for label, path_value in (("graph", cfg.graph_db), ("lexvec", cfg.lexvec_db)):
        path = Path(path_value)
        if not path.is_file():
            continue
        conn = store_mod.connect(path)
        try:
            tables = tables_with_rev(conn)
            vectors = (store_mod.table_exists(conn, "vec_chunk")
                       and store_mod.table_exists(conn, "vec_meta"))
            for start in range(0, len(revs), batch):
                chunk = revs[start:start + batch]
                marks = ",".join("?" for _ in chunk)
                counters = removed.setdefault(label, {})
                if vectors:
                    try:
                        ids = [str(r[0]) for r in conn.execute(
                            f"SELECT chunk_id FROM vec_meta WHERE rev_id IN ({marks})", chunk
                        ).fetchall()]
                    except sqlite3.Error:
                        ids = []
                    for i in range(0, len(ids), 400):
                        part = ids[i:i + 400]
                        marks_ids = ",".join("?" for _ in part)
                        try:
                            cur = conn.execute(
                                f"DELETE FROM vec_chunk WHERE chunk_id IN ({marks_ids})", part)
                            if cur.rowcount and cur.rowcount > 0:
                                counters["vec_chunk"] = counters.get("vec_chunk", 0) + int(cur.rowcount)
                        except sqlite3.Error:
                            break
                for table in tables:
                    try:
                        cur = conn.execute(f"DELETE FROM {table} WHERE rev_id IN ({marks})", chunk)
                    except sqlite3.Error:
                        continue
                    if cur.rowcount and cur.rowcount > 0:
                        counters[table] = counters.get(table, 0) + int(cur.rowcount)
                conn.commit()
                try:
                    conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
                except sqlite3.Error:
                    pass
                if progress is not None:
                    progress(label, min(start + batch, len(revs)), len(revs))
        finally:
            conn.close()
    return removed


def artifact_paths(cfg: ProjectConfig, rev_id: str) -> list[Path]:
    """Три пути артефактов одной ревизии: файл CPG, каталог Joern, выгрузка.

    Пути вычисляются ПРЯМО, без обхода `cpg/`: ротация чистит по одной ревизии
    в цикле, и обход каталога на 900 имён внутри этого цикла был бы дороже самого
    удаления. Обход нужен только там, где надо узнать, ЧТО вообще есть
    (`cpg_artifacts`).
    """
    workdir = Path(cfg.ragraf_dir) / "cpg"
    return [
        workdir / f"{rev_id}.cpg.bin",
        workdir / "workspace" / f"{rev_id}.cpg.bin",
        workdir / f"export_{rev_id}_neo4jcsv",
    ]


def remove_artifacts(cfg: ProjectConfig, rev_ids: object) -> dict:
    """Удалить артефакты перечисленных ревизий. Возвращает число и объём.

    Удаляются только файлы и каталоги, чьё имя целиком совпадает с ожидаемым
    для этой ревизии. Каталог `export_*` может нести суффикс формата, поэтому
    дополнительные варианты ищутся там же по образцу `export_<rev>_*`.
    """
    result = {"bins": 0, "workspaces": 0, "exports": 0, "bytes": 0}
    targets = [str(item) for item in rev_ids]  # type: ignore[union-attr]
    if not targets:
        return result
    workdir = Path(cfg.ragraf_dir) / "cpg"
    if not workdir.is_dir():
        return result
    for rev in targets:
        binary = workdir / f"{rev}.cpg.bin"
        if binary.is_file():
            try:
                size = binary.stat().st_size
                binary.unlink()
                result["bins"] += 1
                result["bytes"] += size
            except OSError:
                pass
        packed = workdir / "workspace" / f"{rev}.cpg.bin"
        if packed.is_dir():
            size = path_bytes(packed)
            shutil.rmtree(packed, ignore_errors=True)
            if not packed.exists():
                result["workspaces"] += 1
                result["bytes"] += size
        try:
            exports = sorted(p for p in workdir.glob(f"export_{rev}_*"))
        except OSError:
            exports = []
        for export in exports:
            if not export.is_dir():
                continue
            size = path_bytes(export)
            shutil.rmtree(export, ignore_errors=True)
            if not export.exists():
                result["exports"] += 1
                result["bytes"] += size
    return result


def _remove_cpg_artifacts(cfg: ProjectConfig, keep: set[str]) -> dict:
    """Удалить файлы и каталоги CPG ревизий, которых нет в `keep`.

    Удаляются ТОЛЬКО имена, разобранные как ревизия. Всё, что не разобралось
    (`probe-test.cpg.bin`, каталоги без имени ревизии), остаётся: неопознанное
    имя — не повод удалять, и это правило дороже нескольких мегабайт.
    """
    result = {"bins": 0, "workspaces": 0, "exports": 0, "bytes": 0}
    workdir = Path(cfg.ragraf_dir) / "cpg"
    if not workdir.is_dir():
        return result
    arts = cpg_artifacts(cfg)
    known = set(arts["bins"]) | set(arts["workspaces"]) | set(arts["exports"])
    return remove_artifacts(cfg, sorted(known - keep))


def _prune_temp(cfg: ProjectConfig, age_hours: int, limit_mb: int = 0) -> dict:
    """Удалить временные выгрузки Joern и привести временные каталоги к пределу.

    Два критерия, и оба нужны. Возраст (`storage_tmp_age_hours`) убирает то, что
    заведомо пересоздаётся следующим запросом: `out_*.json` (результат запроса,
    74,8 МБ на живом корпусе) и `query_*.sc`. Предел (`storage_tmp_max_mb`)
    ограничивает объём независимо от возраста — иначе один долгий прогон
    оставляет каталог сверх предела, и он живёт сутки.
    """
    result = {"files": 0, "bytes": 0, "dirs": []}
    workdir = Path(cfg.ragraf_dir) / "cpg"
    cutoff = time.time() - max(1, int(age_hours)) * 3600
    if workdir.is_dir():
        for pattern in ("out_*.json", "query_*.sc"):
            for path in sorted(workdir.glob(pattern)):
                try:
                    if path.stat().st_mtime >= cutoff:
                        continue
                    size = path.stat().st_size
                    path.unlink()
                    result["files"] += 1
                    result["bytes"] += size
                except OSError:
                    continue
    for label in ("cpg/tmp", "tmp"):
        directory = Path(cfg.ragraf_dir) / label
        if not directory.is_dir():
            continue
        removed_here = 0
        for item in _old_files(directory, cutoff):
            try:
                Path(item["path"]).unlink()
                result["files"] += 1
                result["bytes"] += int(item["bytes"])
                removed_here += 1
            except OSError:
                continue
        if removed_here:
            result["dirs"].append({"path": str(directory), "files": removed_here})
    if limit_mb:
        budget = int(limit_mb) * MIB
        for label in ("cpg/tmp", "tmp"):
            directory = Path(cfg.ragraf_dir) / label
            if not directory.is_dir():
                continue
            total = path_bytes(directory)
            if total <= budget:
                continue
            # Самые старые первыми: свежий файл может быть нужен запущенному
            # процессу, старый — уже нет.
            candidates = _all_files(directory)
            candidates.sort(key=lambda item: item["mtime"])
            removed_here = 0
            for item in candidates:
                if total <= budget:
                    break
                try:
                    Path(item["path"]).unlink()
                    total -= int(item["bytes"])
                    result["files"] += 1
                    result["bytes"] += int(item["bytes"])
                    removed_here += 1
                except OSError:
                    continue
            if removed_here:
                result["dirs"].append({"path": str(directory), "files": removed_here,
                                       "reason": "превышен storage_tmp_max_mb"})
    return result


def _vacuum(path: Path) -> dict:
    """`VACUUM` файла БД: единственный способ вернуть место после удаления строк.

    Перед ним WAL сводится в основной файл (`wal_checkpoint(TRUNCATE)`): иначе
    `VACUUM` переписывает базу, а журнал остаётся вторым файлом того же объёма.
    """
    if not path.is_file():
        return {"ran": False, "reason": "файла нет"}
    started = time.perf_counter()
    before = path.stat().st_size
    try:
        conn = store_mod.connect(path)
        try:
            try:
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except sqlite3.Error:
                pass
            conn.execute("VACUUM")
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        finally:
            conn.close()
    except sqlite3.Error as error:
        return {"ran": False, "reason": f"{type(error).__name__}: {str(error)[:120]}"}
    after = path.stat().st_size
    return {"ran": True, "seconds": round(time.perf_counter() - started, 1),
            "before_mb": round(before / MIB, 1), "after_mb": round(after / MIB, 1),
            "freed_mb": round((before - after) / MIB, 1)}


# ---------------------------------------------------------------------------
# Автоматическая очистка после ротации
# ---------------------------------------------------------------------------


def auto_compact(cfg: ProjectConfig) -> dict:
    """Очистка артефактов и временных файлов без `VACUUM`, зовётся ротацией.

    Зачем автоматически. Артефакты CPG растут при каждой полной переиндексации,
    а команду `compact` никто не запускает по расписанию: без автоочистки
    политика остаётся объявлением (замер 30.10.2026: 17,8 ГБ в `.ragraf/cpg`
    от 473 ревизий, данных которых в хранилищах нет).

    Почему без `plan()`: план полностью пересчитывает хвост по всем таблицам
    (измерено 3,5 с на живом корпусе), а ротация вызывается при каждой полной
    переиндексации. Здесь считаются только две вещи, которые растут сами по
    себе, — артефакты CPG и временные файлы. Строки хвоста убирает та же ротация
    (`_purge_rev_data`), а `VACUUM` требует отдельной команды: он блокирует
    восьмигигабайтный файл на десятки секунд, и индексация не то место.
    """
    values = _settings()
    if not bool(values.get("storage_auto_compact", True)):
        return {"skipped": "storage_auto_compact выключен"}
    keep_revs = max(1, _int_setting(values, "storage_keep_cpg_revs", 1))
    keep = {item["rev_id"] for item in revisions(cfg)[:keep_revs]}
    arts = cpg_artifacts(cfg)
    known = set(arts["bins"]) | set(arts["workspaces"]) | set(arts["exports"])
    removed = remove_artifacts(cfg, sorted(known - keep))
    pruned = _prune_temp(cfg, _int_setting(values, "storage_tmp_age_hours", 24),
                         _int_setting(values, "storage_tmp_max_mb", 512))
    return {"cpg_removed": removed, "temp_removed": pruned,
            "cpg_kept_revs": sorted(keep)}
