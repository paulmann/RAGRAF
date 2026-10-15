"""Операция `ensure`: привести RAGRAF в соответствие проекту, из которого идёт запрос.

Зачем это нужно. До сих пор корпус выбирался вручную: агенту предписывалось
самому найти нужный конфиг, проверить свежесть и решить, нужна ли
переиндексация. На практике это значит, что при первом обращении к новому
проекту RAGRAF не использовался вовсе, а у известного проекта факты могли
отставать от кода, пока агент не вспомнит о проверке.

`ensure` сводит это к одной операции с одним аргументом — каталогом проекта:

    ragraf.py ensure <каталог>          # найти корпус по каталогу
    ragraf.py ensure <каталог> --json

Порядок действий:

1. по каталогу находится зарегистрированный корпус (сравнение путей идёт через
   `catalog._norm`, то есть с учётом коротких 8.3-путей Windows);
2. если корпус есть — сверяются контрольные суммы, и при расхождении индекс
   актуализируется тем же сторожем, что работает при обычных операциях;
3. если корпуса нет — он создаётся (`init_corpus.create_config`), заводится
   в реестр и индексируется полностью.

Защита от засорения реестра. Создание корпуса для произвольного каталога
опасно: агент может запросить домашний каталог или корень диска, и в реестре
появятся бессмысленные разработки на десятки тысяч файлов. Поэтому создание
разрешено только для каталога, который выглядит как проект: есть маркеры
разработки (`.git`, `.hg`, `.svn`, файлы сборки) либо файлы кода, и при этом
каталог не является домашним, системным или корнем диска. Отказ — не ошибка,
а честный ответ `status="not_a_project"` с причиной.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .catalog import Catalog, _norm

# Имена каталогов, которые не являются проектом, когда так назван сам каталог.
# Здесь НЕТ `users`: проекты живут внутри профиля пользователя, и запрет по
# части пути отклонял бы любой из них. Отдельно и строго ниже проверяются
# ровно домашний каталог и корень диска.
_FORBIDDEN_LEAF_NAMES = frozenset(
    {
        "windows",
        "program files",
        "program files (x86)",
        "programdata",
        "appdata",
        "temp",
        "tmp",
        "system32",
        "node_modules",
        "vendor",
        "site-packages",
    }
)

# Части пути, при которых корпус не создаётся никогда: зависимости и системные
# каталоги. Это не «имя самого проекта», а место, где проекта быть не может.
_FORBIDDEN_PATH_PARTS = frozenset(
    {
        "windows",
        "program files",
        "program files (x86)",
        "programdata",
        "node_modules",
        "vendor",
        "site-packages",
        "system32",
    }
)

# Маркеры того, что каталог — корень разработки.
_PROJECT_MARKERS = (
    ".git",
    ".hg",
    ".svn",
    ".gitignore",
    "composer.json",
    "package.json",
    "pyproject.toml",
)

# --- поиск маркеров в подкаталогах -------------------------------------------
# Замер 13.10.2026: `ensure(C:\AutoClaw\BWO)` отвечал `not_a_project`, хотя это
# 38 627 файлов, а маркеры разработки лежат на глубине 1 от корня
# (`php\composer.json`, `lib\composer.json`, `test\package.json`). В самом
# каталоге маркеров нет — так выглядит «обёртка вокруг нескольких проектов»,
# и проверка по одному уровню отметала её целиком.
#
# Глубина 3 покрывает обёртки вида `<корень>/<группа>/<проект>` и оставляет
# запас в два уровня к измеренному случаю (там хватило одного). Глубже
# начинается уже содержимое самого проекта: маркер на глубине 4 — это признак
# корня разработки ВНУТРИ проекта, и заводить корпус от общей папки нельзя.
_MARKER_SEARCH_MAX_DEPTH = 3

# Предел числа осмотренных каталогов за один вызов. Обход обязан оставаться
# доказательно дешёвым: `ensure` вызывается первым шагом работы агента, а
# каталог вроде `C:\AutoClaw` содержит сотни тысяч файлов. 400 взято с большим
# запасом к измеренному случаю (маркеры BWO находятся среди первых десятков
# каталогов уровня 1) и ограничивает худший случай долями секунды. Предел не
# молчаливый: при его исчерпании причина отказа называет это прямо, а не
# выдаёт «маркеров нет» за проверенный факт.
_MARKER_SEARCH_MAX_DIRS = 400

# Каталоги, в которые обход не заходит сверх `DEFAULT_EXCLUDES`. Перечислено
# только то, чего в базовом списке НЕТ: установленные пакеты Python и история
# версий (в `DEFAULT_EXCLUDES` есть `**/.git/**`, но не `.hg`/`.svn`).
# Дублировать имена, уже лежащие в `DEFAULT_EXCLUDES` (`.ragraf`, `.git`,
# `.venv`, `node_modules`, `vendor`), нельзя: два списка разошлись бы молча.
_MARKER_SEARCH_EXTRA_EXCLUDES = ("site-packages", ".hg", ".svn")


@dataclass
class EnsureOutcome:
    """Итог операции `ensure`. Поля читаются и человеком, и агентом."""

    status: str
    """`actual` | `reindexed` | `created` | `partial` | `not_a_project` | `error`."""

    root: str = ""
    category: str = ""
    config: str = ""
    rev_id: str = ""
    files: int = 0
    seconds: float = 0.0
    reason: str = ""
    reindex_mode: str = ""
    reindex_seconds: float = 0.0
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "root": self.root,
            "category": self.category,
            "config": self.config,
            "rev_id": self.rev_id,
            "files": self.files,
            "seconds": round(self.seconds, 3),
            "reason": self.reason,
            "reindex_mode": self.reindex_mode,
            "reindex_seconds": round(self.reindex_seconds, 3),
            "notes": list(self.notes),
        }


@dataclass
class _MarkerScan:
    """Итог поиска маркеров проекта в подкаталогах."""

    hits: list[str] = field(default_factory=list)
    """Найденные маркеры в проверяемом виде: `путь/от/корня (глубина N)`."""

    dirs_visited: int = 0
    dirs_unreadable: int = 0
    limit_hit: bool = False
    """True — обход прерван пределом: «маркеров нет» в этом случае НЕ доказано."""


def _scan_markers_below(root: Path) -> _MarkerScan:
    """Найти маркеры проекта в подкаталогах `root`, не глубже предела.

    Обход идёт в ширину и детерминирован (соседи сортируются по имени), поэтому
    один и тот же каталог даёт один и тот же ответ, а причина отказа
    воспроизводима. Вложенные каталоги отсекаются общим помощником
    `util.prune_dir` по `DEFAULT_EXCLUDES` — тем же, что и в скане корпуса
    (`corpus.scan`): второй реализации сопоставления шаблонов в проекте быть
    не должно.
    """
    from . import util
    from .config import DEFAULT_EXCLUDES

    patterns = list(DEFAULT_EXCLUDES) + [
        f"**/{name}/**" for name in _MARKER_SEARCH_EXTRA_EXCLUDES
    ]
    scan = _MarkerScan()
    # Очередь обхода: (каталог, путь относительно корня, глубина). Корень — 0.
    queue: list[tuple[Path, str, int]] = [(root, "", 0)]
    while queue:
        if scan.dirs_visited >= _MARKER_SEARCH_MAX_DIRS:
            scan.limit_hit = True
            break
        directory, rel, depth = queue.pop(0)
        scan.dirs_visited += 1
        try:
            with os.scandir(directory) as listing:
                items = sorted(listing, key=lambda item: item.name)
        except OSError:
            # Нет прав доступа или каталог исчез между шагами. Это не «маркеров
            # нет»: несмотренные каталоги честно называются в причине отказа.
            scan.dirs_unreadable += 1
            continue

        child_dirs: list[tuple[Path, str, int]] = []
        for item in items:
            child_rel = f"{rel}/{item.name}" if rel else item.name
            try:
                is_dir = item.is_dir()
            except OSError:
                is_dir = False
            if depth and item.name in _PROJECT_MARKERS:
                # Глубина = число каталогов ниже корня, в которых лежит маркер:
                # `php/composer.json` найден на глубине 1 (замер BWO). Маркеры
                # самого корня (глубина 0) вызывающий уже проверил отдельно —
                # здесь они не дублируются.
                scan.hits.append(f"{child_rel} (глубина {depth})")
            if not is_dir:
                continue
            if util.prune_dir(child_rel, patterns):
                continue
            # Маркер, лежащий внутри каталога глубины d, сам имеет глубину d,
            # поэтому раскрываются каталоги до `_MARKER_SEARCH_MAX_DEPTH` включительно.
            if depth < _MARKER_SEARCH_MAX_DEPTH:
                child_dirs.append((Path(item.path), child_rel, depth + 1))

        if scan.hits:
            # Достаточно одного каталога с маркерами (в детерминированном
            # порядке обхода): названного основания хватает для решения, а
            # ответ не должен зависеть от того, сколько ещё успели осмотреть.
            break
        queue.extend(child_dirs)

    return scan


def looks_like_project(root: Path) -> tuple[bool, str]:
    """Ответить, можно ли заводить корпус для этого каталога.

    Возвращает (разрешено, причина). Причина приводится и при разрешении:
    агент должен видеть, на каком основании принято решение.

    Маркеры ищутся сначала в самом каталоге, затем — в подкаталогах до
    `_MARKER_SEARCH_MAX_DEPTH` уровней: корень разработки часто лежит на один-два
    уровня ниже (измерено на `C:\\AutoClaw\\BWO`, см. константы выше).
    Поиск ограничен по числу каталогов и не заходит в зависимости, поэтому
    запреты ниже (домашний каталог, корень диска, служебные имена) остаются
    быстрым отказом: они проверяются ДО обхода. При исчерпании предела причина
    говорит об этом прямо, а не выдаёт «маркеров нет» за проверенный факт.
    """
    text = str(root)
    norm = _norm(text)

    if not root.exists():
        return False, "каталог не существует"
    if not root.is_dir():
        return False, "путь не является каталогом"

    home = Path.home()
    if norm == _norm(home):
        return False, "домашний каталог целиком: это не проект"

    # Корень диска: у него нет родителя, равного самому себе.
    if root.parent == root:
        return False, "корень диска: это не проект"

    # Имя самого каталога: `.../temp` — не проект, но `.../temp/myproj`
    # уже может быть им, поэтому проверяется только последний сегмент.
    if root.name.strip().lower() in _FORBIDDEN_LEAF_NAMES:
        return False, f"каталог назван служебно ({root.name}): это не проект"

    parts = {part.lower() for part in root.parts if part.strip("\\/")}
    blocked = parts & _FORBIDDEN_PATH_PARTS
    if blocked:
        return False, f"путь содержит служебный каталог: {', '.join(sorted(blocked))}"

    markers = [name for name in _PROJECT_MARKERS if (root / name).exists()]
    if markers:
        return True, f"найдены маркеры проекта: {', '.join(markers)}"

    # Быстрый путь закончился: только здесь начинается обход вглубь.
    scan = _scan_markers_below(root)
    if scan.hits:
        return True, ("найдены маркеры проекта в подкаталогах: "
                      + ", ".join(scan.hits))

    tail = ""
    if scan.dirs_unreadable:
        tail = (f"; каталогов без доступа: {scan.dirs_unreadable} — "
                "их содержимое не осмотрено")

    if scan.limit_hit:
        return False, (
            f"нет маркеров проекта ни в корне, ни на глубине до "
            f"{_MARKER_SEARCH_MAX_DEPTH}; обход прерван по пределу "
            f"({_MARKER_SEARCH_MAX_DIRS} каталогов), поэтому наличие маркеров "
            f"глубже НЕ проверено: корпус не создаётся{tail}"
        )

    return False, (
        "нет ни одного маркера проекта "
        f"({', '.join(_PROJECT_MARKERS)}) ни в корне, ни на глубине до "
        f"{_MARKER_SEARCH_MAX_DEPTH}: корпус не создаётся{tail}"
    )


def ensure(root: Path, *, create: bool = True, index: bool = True,
           force_reindex: bool = False, budget_seconds: float | None = None,
           max_files: int | None = None) -> EnsureOutcome:
    """Привести корпус проекта в актуальное состояние.

    Параметры:
      * `create` — разрешено ли заводить новый корпус;
      * `index` — разрешена ли индексация (при False только состояние);
      * `force_reindex` — проверять свежесть минуя кэш, сразу после правок;
      * `budget_seconds` — предел времени на прогон (по умолчанию из конфига
        корпуса, `index.budget_seconds`): при исчерпании прогон прерывается до
        закрепления ревизии, а статус ответа — `partial`;
      * `max_files` — предел числа файлов корпуса (по умолчанию
        `index.max_files`): больше — отказ с числами и подсказкой про exclude.

    Операция НИКОГДА не поднимает исключение наружу: любой сбой возвращается
    как `status="error"` с причиной. Смысл в том, что `ensure` вызывается
    первым шагом работы агента, и его падение обрывало бы всю задачу.
    """
    start = time.time()
    root = Path(root)

    try:
        resolved = root.resolve()
    except OSError as exc:
        return EnsureOutcome(status="error", root=str(root),
                             reason=f"не удалось разобрать путь: {exc}")

    try:
        catalog = Catalog()
    except Exception as exc:  # noqa: BLE001 — реестр недоступен не должен ронять задачу
        return EnsureOutcome(status="error", root=str(resolved),
                             reason=f"реестр недоступен: {type(exc).__name__}: {exc}")

    category = catalog.category_for_root(resolved)

    if category:
        return _ensure_existing(catalog, category, resolved, start,
                                index=index, force_reindex=force_reindex,
                                budget_seconds=budget_seconds, max_files=max_files)

    return _ensure_new(catalog, resolved, start, create=create, index=index,
                        budget_seconds=budget_seconds, max_files=max_files)


def _ensure_existing(catalog: Catalog, category: str, root: Path, start: float, *,
                     index: bool, force_reindex: bool,
                     budget_seconds: float | None = None,
                     max_files: int | None = None) -> EnsureOutcome:
    """Корпус известен: сверить сумму и при расхождении переиндексировать."""
    entry = catalog.get(category)
    if entry is None:
        return EnsureOutcome(status="error", root=str(root), category=category,
                             reason="запись реестра исчезла между обращениями")

    outcome = EnsureOutcome(
        status="actual", root=str(root), category=category,
        config=str(entry.config), rev_id=entry.rev_id, files=entry.file_count,
    )

    from .config import load as load_config

    try:
        cfg = load_config(entry.config)
    except Exception as exc:  # noqa: BLE001
        outcome.status = "error"
        outcome.reason = f"конфиг корпуса не читается: {type(exc).__name__}: {exc}"
        outcome.seconds = time.time() - start
        return outcome

    if not index:
        outcome.notes.append("индексация отключена: проверена только запись реестра")
        outcome.seconds = time.time() - start
        return outcome

    from . import watch as watch_mod

    # Предел объёма: слишком крупный корпус не индексируется молча — отказ с числами.
    limit = max_files if max_files is not None else cfg.index.max_files
    known_files = int(entry.file_count or 0)
    if limit and known_files > limit:
        outcome.status = "error"
        outcome.reason = (
            f"корпус слишком крупный для индексации: файлов {known_files} при пределе "
            f"{limit}. Добавьте exclude в конфиг корпуса или поднимите index.max_files."
        )
        outcome.seconds = time.time() - start
        return outcome

    fresh = watch_mod.check(cfg, category, auto_reindex=True,
                            force=force_reindex, use_cache=not force_reindex,
                            budget_seconds=budget_seconds)

    outcome.rev_id = fresh.rev_id or entry.rev_id
    outcome.files = fresh.files_now or entry.file_count
    outcome.reindex_mode = fresh.reindex_mode or ""

    if fresh.reindexed:
        outcome.status = "reindexed"
        # Результат прогона проверяется по реестру. Замер 2026-10-14 на BWO: прогон
        # зарегистрировал ревизию rev-0002, а file_count/node_count/edge_count остались
        # нулями — то есть фактов нет, а ответ говорил «пересобран». Молчать об этом
        # нельзя: агент получил бы «индекс актуален» и пустые ответы.
        reread = None
        try:
            from .catalog import Catalog as _Catalog
            reread = _Catalog().get(category)
        except Exception:  # noqa: BLE001 — проверка не должна ломать ответ
            reread = None
        if reread is not None and int(getattr(reread, "file_count", 0) or 0) == 0:
            outcome.status = "partial"
            outcome.reason = (
                "индексация зарегистрировала ревизию, но факты не собраны (в реестре "
                "файлов 0): смотрите stage_timing и index_status"
            )
            outcome.notes.append("индекс остался пустым — фактам верить нельзя")
        outcome.reindex_seconds = fresh.reindex_seconds
        outcome.rev_id = fresh.reindex_rev or outcome.rev_id
        outcome.reason = f"индекс отставал ({fresh.reindex_mode}) и был пересобран"
    elif fresh.stale:
        # Расхождение есть, но накопление ниже порога. Так и сообщаем: молча
        # выдать устаревшие факты за текущие — именно тот дефект, ради которого
        # сторож и заведён.
        outcome.reason = fresh.note or "расхождение ниже порога переиндексации"
        outcome.notes.append("факты могут соответствовать предыдущей ревизии кода")
    else:
        outcome.reason = "сумма корпуса совпадает с проиндексированной"

    outcome.seconds = time.time() - start
    return outcome


def _ensure_new(catalog: Catalog, root: Path, start: float, *, create: bool,
                index: bool, budget_seconds: float | None = None,
                max_files: int | None = None) -> EnsureOutcome:
    """Корпуса нет: завести его, если каталог похож на проект."""
    allowed, why = looks_like_project(root)
    if not allowed:
        return EnsureOutcome(status="not_a_project", root=str(root), reason=why)

    if not create:
        return EnsureOutcome(status="not_a_project", root=str(root),
                             reason=f"{why}; создание корпуса отключено")

    from . import init_corpus
    from .config import load as load_config

    try:
        config_path, scan = init_corpus.create_config(root)
    except FileExistsError as exc:
        # Конфиг уже есть, но корпуса с таким корнем в реестре нет: это
        # расхождение реестра и диска. Берём существующий конфиг как есть —
        # перезапись затёрла бы ручные правки в нём.
        config_path = Path(str(exc).split(": ", 1)[-1].split(";", 1)[0])
        if not config_path.exists():
            return EnsureOutcome(
                status="error", root=str(root),
                reason=f"конфиг существует, но не читается: {config_path}")
        scan = None
    except ValueError as exc:
        return EnsureOutcome(status="not_a_project", root=str(root), reason=str(exc))
    except Exception as exc:  # noqa: BLE001
        return EnsureOutcome(
            status="error", root=str(root),
            reason=f"не удалось создать конфигурацию: {type(exc).__name__}: {exc}")

    outcome = EnsureOutcome(status="created", root=str(root),
                            config=str(config_path), reason=why)
    if scan is not None:
        outcome.files = scan.total_files
        outcome.notes.append(
            f"языки: {', '.join(sorted(scan.languages)) or 'не определены'}")

    if not index:
        outcome.notes.append("индексация отключена: корпус заведён без фактов")
        outcome.seconds = time.time() - start
        return outcome

    try:
        cfg = load_config(config_path)
    except Exception as exc:  # noqa: BLE001
        outcome.status = "error"
        outcome.reason = f"созданный конфиг не читается: {type(exc).__name__}: {exc}"
        outcome.seconds = time.time() - start
        return outcome

    try:
        from .watch import register_project

        register_project(cfg, config_path)
    except Exception as exc:  # noqa: BLE001 — учёт не должен отменять индексацию
        outcome.notes.append(f"реестр не обновлён: {type(exc).__name__}")

    # Предел объёма проверяется ДО прогона: отказ приходит за секунды, а не после
    # многоминутной индексации (замер BWO: дерево 38 675 файлов, корпус 5601 файл).
    limit = max_files if max_files is not None else cfg.index.max_files
    if limit and outcome.files and outcome.files > limit:
        outcome.status = "error"
        outcome.reason = (
            f"корпус слишком крупный для индексации: файлов {outcome.files} при пределе "
            f"{limit}. Добавьте exclude в конфиг корпуса или поднимите index.max_files."
        )
        outcome.seconds = time.time() - start
        return outcome

    from .indexer import Indexer

    budget = budget_seconds if budget_seconds is not None else cfg.index.budget_seconds
    idx = Indexer(cfg)
    try:
        report = idx.run(budget_seconds=budget)
    except Exception as exc:  # noqa: BLE001
        outcome.status = "error"
        outcome.reason = (
            f"индексация нового корпуса не удалась: {type(exc).__name__}: {exc}")
        outcome.seconds = time.time() - start
        return outcome
    finally:
        try:
            idx.close()
        except Exception:  # noqa: BLE001
            pass

    data = report.to_dict()
    outcome.rev_id = data.get("rev_id", "")
    outcome.files = data.get("files", outcome.files)
    if data.get("budget_exceeded"):
        # Бюджет исчерпан: ревизия не закреплена, прежний индекс рабочий. Это не
        # ошибка кода, поэтому статус отдельный, а причина берётся из отчёта.
        outcome.status = "partial"
        jumps = [str(n) for n in data.get("notes", []) if "бюджет" in str(n)]
        outcome.reason = jumps[-1] if jumps else "прогон прерван по бюджету времени"
    elif not data.get("ok", False):
        outcome.status = "error"
        outcome.reason = "индексация нового корпуса завершилась с ошибкой"
    # Результат прогона обязан попасть в реестр, иначе запись разработки
    # останется с пустой ревизией и нулями при фактически построенном индексе.
    # Прежняя версия ограничивалась регистрацией выше, и это был измеренный
    # дефект: `catalog` показывал node_count=0 при 1855 узлах в индексе.
    if data.get("ok", False):
        try:
            from .watch import record_index_run

            record_index_run(cfg, report, trigger="ensure:index")
        except Exception as exc:  # noqa: BLE001 — учёт не отменяет индексацию
            outcome.notes.append(f"реестр не обновлён: {type(exc).__name__}")
    outcome.seconds = time.time() - start
    return outcome
