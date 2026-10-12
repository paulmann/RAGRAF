"""Скан корпуса: отбор файлов по конфигурации, хэши, языки, статистика (ФТ-37, ФТ-38).

Правила:
  * обход всего дерева, но каталоги из exclude отсекаются на входе (производительность);
  * файл, попавший в secrets_exclude, НЕ индексируется и не попадает в graph.file;
    он учитывается только счётчиком (ФТ-39);
  * двоичные файлы и файлы вне include пропускаются;
  * порядок обхода детерминирован (сортировка) — требование НФТ-5.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from . import util
from .config import ProjectConfig

SKIP_BINARY_EXT = {
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".webp", ".svgz",
    ".woff", ".woff2", ".ttf", ".eot", ".otf",
    ".zip", ".gz", ".br", ".7z", ".rar", ".tar", ".phar",
    ".pdf", ".docx", ".xlsx", ".pptx", ".mo", ".so", ".dll", ".exe",
    ".lock", ".mo", ".pyc",
}


@dataclass
class CorpusFile:
    path: str          # POSIX-путь относительно корня
    abs_path: Path
    lang: str
    sha256: str
    bytes: int
    lines: int


@dataclass
class CorpusScan:
    files: list[CorpusFile] = field(default_factory=list)
    excluded: dict[str, int] = field(default_factory=dict)
    skipped_secrets: int = 0
    skipped_binary: int = 0
    skipped_included_off: int = 0
    dirs_pruned: int = 0
    total_bytes: int = 0
    total_lines: int = 0
    by_lang: dict[str, dict[str, int]] = field(default_factory=dict)
    # --- схлопывание дублей (заполняется, когда оно включено) ---
    # Сколько файлов БЫЛО найдено на диске до схлопывания. Нужно для
    # честного отчёта: иначе нельзя отличить «в корпусе мало файлов»
    # от «много файлов, но они схлопнулись».
    files_found: int = 0
    # Сводка схлопывания: сколько групп и файлов отброшено и почему.
    duplicates: dict = field(default_factory=dict)

    @property
    def corpus_hash(self) -> str:
        """Хэш корпуса: отсортированный список (путь, sha256) — признак «вход не менялся».

        Считается по файлам, которые ПОЙДУТ В ИНДЕКС (то есть после
        схлопывания дублей, если оно включено). Это обязательное условие,
        а не деталь: `watch.py` считает свежесть тем же способом. Если бы
        хэш включал отброшенные копии, его значение никогда не совпало бы
        с записанным в ревизии, и сторож запускал бы переиндексацию
        бесконечно.
        """
        payload = "\n".join(f"{f.path}\t{f.sha256}" for f in self.files)
        return util.sha256_text(payload)

    def summary(self) -> dict:
        out = {
            "files": len(self.files),
            "bytes": self.total_bytes,
            "lines": self.total_lines,
            "by_lang": self.by_lang,
            "skipped_secrets": self.skipped_secrets,
            "skipped_binary": self.skipped_binary,
            "skipped_not_included": self.skipped_included_off,
            "excluded": self.excluded,
            "dirs_pruned": self.dirs_pruned,
            "corpus_hash": self.corpus_hash,
        }
        # Числа по дублям попадают в отчёт только когда схлопывание было:
        # иначе в отчёте появлялись бы нули, которые ничего не значат.
        if self.duplicates:
            out["files_found"] = self.files_found
            out["duplicates"] = self.duplicates
        return out


def _real_dir(path: str | Path) -> str:
    """Длинная форма пути к каталогу (без учёта коротких 8.3-имён Windows).

    Нужна для сравнения путей: `TEMP` и профиль пользователя на Windows
    возвращаются короткими именами, и без приведения к одной форме
    сопоставление `relative_to` не срабатывает.
    """
    try:
        return os.path.realpath(str(path))
    except Exception:
        return str(path)


def _count_lines_fast(path: Path, size: int) -> int:
    """Число строк: подсчёт переводов строки блоками (файлы бывают на 9 тыс. строк)."""
    if size == 0:
        return 0
    total = 0
    tail_nl = False
    with open(path, "rb") as fh:
        while True:
            block = fh.read(1 << 20)
            if not block:
                break
            total += block.count(b"\n")
            tail_nl = block.endswith(b"\n")
    return total if tail_nl else total + 1


def scan(cfg: ProjectConfig) -> CorpusScan:
    """Полный скан корпуса. Ничего не пишет — только читает.

    ВАЖНО про пути (дефект, найденный на тестовом корпусе).
    Корень корпуса может быть задан КОРОТКИМ 8.3-путём: на этой машине так
    выглядит TEMP (`C:/Users/MVD~1.AMD/AppData/Local/Temp`). При этом
    `Path(dirpath).resolve()` возвращает ДЛИННЫЙ путь, и `relative_to(root)
    выбрасывает ValueError — каталог молча пропускался, и корпус сканировался
    как пустой (0 файлов) без единой ошибки. Это опасный класс: операция
    «сработала не так», и отличить «в корпусе нет файлов» от «путь не совпал»
    было невозможно.

    Решение: и корень, и каждый каталог приводятся к одной (длинной) форме
    через `os.path.realpath`, а сравнение идёт по ней. Если всё равно не
    совпало — это регистрируется как пропущенный каталог, а не молча теряется.
    """
    result = CorpusScan()
    # Корень и его «длинная» форма: сравнение должно идти по одной форме пути.
    root_real = _real_dir(cfg.root)
    root = root_real
    exclude = list(cfg.exclude)
    secrets = list(cfg.secrets_exclude)
    include = list(cfg.include)
    langs = set(cfg.languages)

    for dirpath, dirnames, filenames in os.walk(root_real):
        dir_real = _real_dir(dirpath)
        try:
            rel = Path(dir_real).relative_to(root_real)
        except ValueError:
            # Каталог вне корня (например, символическая ссылка наружу).
            # Это не «пустой корпус»: учитываем как пропущенный, чтобы случай
            # не выглядел как отсутствие файлов.
            result.dirs_pruned += 1
            continue
        dir_rel = util.to_posix(rel)
        dir_rel = "" if dir_rel == "." else dir_rel

        pruned: list[str] = []
        for name in list(dirnames):
            child_rel = f"{dir_rel}/{name}" if dir_rel else name
            hit = util.prune_dir(child_rel, exclude)
            if hit:
                pruned.append(name)
                result.excluded[hit] = result.excluded.get(hit, 0) + 1
                result.dirs_pruned += 1
        for name in pruned:
            dirnames.remove(name)

        for name in sorted(filenames):
            rel = f"{dir_rel}/{name}" if dir_rel else name
            abs_path = Path(dirpath) / name

            hit_secret = util.matches_any(rel, secrets)
            if hit_secret:
                result.skipped_secrets += 1
                result.excluded[f"secrets:{hit_secret}"] = (
                    result.excluded.get(f"secrets:{hit_secret}", 0) + 1
                )
                continue

            hit_exc = util.matches_any(rel, exclude)
            if hit_exc:
                result.excluded[hit_exc] = result.excluded.get(hit_exc, 0) + 1
                continue

            if not util.matches_any(rel, include):
                result.skipped_included_off += 1
                continue

            ext = abs_path.suffix.lower()
            if ext in SKIP_BINARY_EXT:
                result.skipped_binary += 1
                continue

            lang = cfg.language_of(rel)
            if lang is None or lang not in langs:
                result.skipped_included_off += 1
                continue

            try:
                size = abs_path.stat().st_size
            except OSError:
                result.skipped_binary += 1
                continue
            if size == 0:
                continue
            if util.is_binary(abs_path):
                result.skipped_binary += 1
                continue

            try:
                digest = util.sha256_file(abs_path)
                lines = _count_lines_fast(abs_path, size)
            except OSError:
                result.skipped_binary += 1
                continue

            cf = CorpusFile(
                path=rel,
                abs_path=abs_path,
                lang=lang,
                sha256=digest,
                bytes=size,
                lines=lines,
            )
            result.files.append(cf)
            result.total_bytes += size
            result.total_lines += lines
            bucket = result.by_lang.setdefault(lang, {"files": 0, "lines": 0, "bytes": 0})
            bucket["files"] += 1
            bucket["lines"] += lines
            bucket["bytes"] += size

    result.files.sort(key=lambda f: f.path)

    # --- схлопывание байт-идентичных копий ---
    #
    # ВЫЗЫВАЕТСЯ ЗДЕСЬ, а не в индексаторе, по двум причинам:
    #
    #   1. `corpus_hash` считается по полю `files`. Если схлопывать после
    #      подсчёта суммы, она не совпадёт с содержимым индекса, и сторож
    #      свежести будет запускать переиндексацию бесконечно.
    #   2. `corpus.scan` — единственная точка, откуда файлы берут и индексатор
    #      (`indexer.py`), и сторож (`watch.py`). Схлопывание в любом другом
    #      месте привело бы к тому, что эти два места видели разный корпус.
    result.files_found = len(result.files)
    if bool(getattr(cfg.index, "dedupe_enabled", False)):
        from . import dedupe as _dedupe

        decision = _dedupe.dedupe(result, cfg)
        result.files = decision.kept_files
        # Итоги пересчитываются по оставленным файлам: иначе в отчёте
        # остались бы числа, включающие отброшенные копии.
        result.total_bytes = sum(f.bytes for f in result.files)
        result.total_lines = sum(f.lines for f in result.files)
        result.by_lang = {}
        for f in result.files:
            bucket = result.by_lang.setdefault(
                f.lang, {"files": 0, "lines": 0, "bytes": 0}
            )
            bucket["files"] += 1
            bucket["lines"] += f.lines
            bucket["bytes"] += f.bytes
        result.duplicates = decision.summary()
        # Детальные группы добавляются, если запрошен разбор.
        if bool(getattr(cfg.index, "dedupe_report", True)):
            result.duplicates["groups"] = [
                g.as_dict() for g in decision.groups[:50]
            ]
    return result


def resolve(cfg: ProjectConfig, rel_or_abs: str) -> Path | None:
    """Привести путь к абсолютному внутри корня. Вне корня — None (защита от обхода)."""
    p = Path(rel_or_abs)
    if not p.is_absolute():
        p = cfg.root / p
    try:
        p = p.resolve()
        p.relative_to(cfg.root.resolve())
    except (ValueError, OSError):
        return None
    return p
