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


@dataclass
class EnsureOutcome:
    """Итог операции `ensure`. Поля читаются и человеком, и агентом."""

    status: str
    """`actual` | `reindexed` | `created` | `not_a_project` | `error`."""

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


def looks_like_project(root: Path) -> tuple[bool, str]:
    """Ответить, можно ли заводить корпус для этого каталога.

    Возвращает (разрешено, причина). Причина приводится и при разрешении:
    агент должен видеть, на каком основании принято решение.
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

    return False, (
        "нет ни одного маркера проекта "
        f"({', '.join(_PROJECT_MARKERS)}): корпус не создаётся"
    )


def ensure(root: Path, *, create: bool = True, index: bool = True,
           force_reindex: bool = False) -> EnsureOutcome:
    """Привести корпус проекта в актуальное состояние.

    Параметры:
      * `create` — разрешено ли заводить новый корпус;
      * `index` — разрешена ли индексация (при False только состояние);
      * `force_reindex` — проверять свежесть минуя кэш, сразу после правок.

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
                                index=index, force_reindex=force_reindex)

    return _ensure_new(catalog, resolved, start, create=create, index=index)


def _ensure_existing(catalog: Catalog, category: str, root: Path, start: float, *,
                     index: bool, force_reindex: bool) -> EnsureOutcome:
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

    fresh = watch_mod.check(cfg, category, auto_reindex=True,
                            force=force_reindex, use_cache=not force_reindex)

    outcome.rev_id = fresh.rev_id or entry.rev_id
    outcome.files = fresh.files_now or entry.file_count
    outcome.reindex_mode = fresh.reindex_mode or ""

    if fresh.reindexed:
        outcome.status = "reindexed"
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
                index: bool) -> EnsureOutcome:
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

    from .indexer import Indexer

    idx = Indexer(cfg)
    try:
        report = idx.run()
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
    if not data.get("ok", False):
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
