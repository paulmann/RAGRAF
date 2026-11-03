"""Читатель архивов: `.zip` — только по явной настройке, с защитой от zip-slip.

Почему по умолчанию ВЫКЛЮЧЕНО. Архив — это контейнер, содержимое которого никто не
обещал: он может быть на 4 ГБ, содержать тысячу файлов и пути вида `../../etc/passwd`.
Включать разбор такого по умолчанию — значит позволить чужому файлу влиять на объём
индекса и на файловую систему. Поэтому `documents_archives_enabled = false`, и пока
он false, архив честно помечается `not_indexed_by_edition` с причиной.

Защита от zip-slip — отказ, а не «пропустим плохой элемент». Один элемент `../evil`
означает, что архив собран с нарушением: продолжать разбор и молча выбросить его —
значит скрыть факт. Архив помечается `failed` с перечислением отвергнутых элементов.

Проверки элемента (каждая — свойство, а не написание):
  * абсолютный путь (`/etc/passwd`, `\\server\\share`, `C:\\Windows`);
  * компонент `..` в любом месте пути;
  * обратные слеши (Windows-путь внутри zip);
  * символическая ссылка (режим файла в старших битах `external_attr`);
  * вложенный архив (глубина 1: разбирать архивы внутри архивов нельзя — иначе
    один файл разворачивается в дерево неизвестного размера).

Пределы названы числами и попадают в ответ: суммарный распакованный размер и число
элементов. Превышение — отказ с причиной, а не «обрезали и промолчали».

Адрес элемента: `bundle.zip!docs/readme.md:12` — форма `inner` грамматики. Внутренний
читатель получает имя `bundle.zip!docs/readme.md`, поэтому адреса его узлов уже
получаются правильными: внешний путь плюс внутренний адрес.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

from ..docnode import DocumentNode, ReadResult

#: Пределы по умолчанию. Берутся из настроек, здесь — страховка на случай, если
#: слой настроек недоступен (тогда чтение не должно падать).
DEFAULT_MAX_BYTES = 50 * 1024 * 1024
DEFAULT_MAX_ENTRIES = 500

#: Форматы, которые разрешено разбирать внутри архива по умолчанию.
DEFAULT_EXTENSIONS = (
    "md", "markdown", "txt", "text", "rst", "adoc", "log",
    "csv", "tsv", "json", "jsonl", "xml", "yaml", "yml", "toml",
    "html", "htm", "xhtml",
)


def _settings() -> dict:
    """Настройки слоя: включённость, whitelist, пределы.

    Ошибка чтения настроек не отменяет защиту: при ней действуют значения по
    умолчанию (архивы выключены).
    """
    try:
        from ..settings import SettingsStore

        data = SettingsStore().load()
    except Exception:  # noqa: BLE001 — без настроек архив выключен, и это безопасно
        data = {}
    raw_extensions = data.get("documents_archives_extensions")
    if isinstance(raw_extensions, str):
        extensions = tuple(part.strip().lower().lstrip(".")
                           for part in raw_extensions.split(",") if part.strip())
    elif isinstance(raw_extensions, (list, tuple)):
        extensions = tuple(str(part).strip().lower().lstrip(".")
                           for part in raw_extensions if str(part).strip())
    else:
        extensions = DEFAULT_EXTENSIONS
    return {
        "enabled": bool(data.get("documents_archives_enabled", False)),
        "extensions": extensions or DEFAULT_EXTENSIONS,
        "max_bytes": int(data.get("documents_archives_max_bytes")
                         or DEFAULT_MAX_BYTES),
        "max_entries": int(data.get("documents_archives_max_entries")
                           or DEFAULT_MAX_ENTRIES),
    }


def enabled() -> bool:
    return bool(_settings().get("enabled"))


def _reject_reason(name: str, info: zipfile.ZipInfo) -> str:
    """Причина отказа для элемента или пустая строка."""
    raw = str(name or "")
    if not raw.strip():
        return "пустое имя элемента"
    if raw.startswith(("/", "\\")):
        return "абсолютный путь"
    if len(raw) > 1 and raw[1] == ":" and raw[0].isalpha():
        return "путь с буквой диска"
    if "\\" in raw:
        return "обратные слеши в пути"
    parts = [part for part in raw.split("/") if part]
    if ".." in parts:
        return "компонент `..` в пути"
    mode = (info.external_attr >> 16) & 0o170000
    if mode == 0o120000:
        return "символическая ссылка"
    suffix = Path(raw).suffix.lower().lstrip(".")
    if suffix == "zip":
        return "вложенный архив: глубина 1"
    return ""


def read(rel: str, raw: bytes) -> ReadResult:
    settings = _settings()
    if not settings["enabled"]:
        return ReadResult(
            status="not_indexed_by_edition",
            reason=("архивы не индексируются: настройка "
                    "`documents_archives_enabled` выключена"),
            reader="archive",
        )
    try:
        archive = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile as exc:
        return ReadResult(status="failed",
                          reason=f"архив не открывается: {exc}", reader="archive")

    infos = [info for info in archive.infolist() if not info.is_dir()]
    if not infos:
        archive.close()
        return ReadResult(status="degraded", reason="архив пуст", reader="archive")
    if len(infos) > settings["max_entries"]:
        archive.close()
        return ReadResult(
            status="failed",
            reason=(f"элементов {len(infos)} больше предела "
                    f"{settings['max_entries']}: архив не разбирается"),
            reader="archive",
        )
    total = sum(int(info.file_size or 0) for info in infos)
    if total > settings["max_bytes"]:
        archive.close()
        return ReadResult(
            status="failed",
            reason=(f"распакованный размер {total} Б больше предела "
                    f"{settings['max_bytes']} Б: архив не разбирается"),
            reader="archive",
        )

    rejected: list[str] = []
    for info in infos:
        reason = _reject_reason(info.filename, info)
        if reason:
            rejected.append(f"{info.filename}: {reason}")
    if rejected:
        archive.close()
        return ReadResult(
            status="failed",
            reason=("архив отвергнут целиком: небезопасные элементы — "
                    + "; ".join(rejected[:5])),
            reader="archive",
            warnings=rejected[:20],
        )

    from . import read as read_inner  # локальный импорт: избежать цикла модулей

    nodes: list[DocumentNode] = []
    warnings: list[str] = []
    skipped = 0
    failed_inner = 0
    for info in infos:
        suffix = Path(info.filename).suffix.lower().lstrip(".")
        if suffix not in settings["extensions"]:
            skipped += 1
            continue
        try:
            data = archive.read(info)
        except Exception as exc:  # noqa: BLE001 — один элемент не роняет архив
            warnings.append(f"{info.filename}: не прочитан ({type(exc).__name__})")
            failed_inner += 1
            continue
        inner_rel = f"{rel}!{info.filename}"
        result = read_inner(inner_rel, data)
        if result.status != "ok" or not result.nodes:
            failed_inner += 1
            warnings.append(
                f"{info.filename}: {result.status}"
                + (f" — {result.reason[:80]}" if result.reason else "")
            )
            continue
        for node in result.nodes:
            node.provenance.setdefault("archive", rel)
            node.provenance.setdefault("archive_member", info.filename)
            nodes.append(node)
    archive.close()

    if skipped:
        warnings.append(f"пропущено по whitelist расширений: {skipped}")
    if not nodes:
        return ReadResult(
            status="degraded",
            reason=("в архиве нет элементов, разобранных слоем"
                    + (f"; предупреждений {len(warnings)}" if warnings else "")),
            reader="archive", warnings=warnings,
        )
    return ReadResult(nodes=nodes, status="ok", reader="archive",
                      metadata={"members": len(infos), "parsed": len(nodes),
                                "skipped": skipped, "failed": failed_inner,
                                "extensions": list(settings["extensions"])},
                      warnings=warnings)


def resolve_member(raw: bytes, inner_locator: str) -> tuple[bytes, str]:
    """Байты элемента архива и его относительный путь по внутреннему адресу."""
    from ..docnode import path_of

    member = path_of(inner_locator)
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        data = archive.read(member)
    return (data, member)
