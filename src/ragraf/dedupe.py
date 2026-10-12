"""Схлопывание байт-идентичных копий файлов при индексации.

Задача
    В каталогах разработки один и тот же документ лежит десятками копий.
    Замер по `C:\\AutoClaw` (tools/find_doc_dupes.py): 5559 документов дали
    **513 групп копий и 3856 лишних файлов** — 44,7 МБ против 2,3 МБ полезного
    текста. Худший случай — один ADR в 104 копиях, размноженный вложением
    каталогов (`old/второй PHP-корпус/www/www/www/www/...`). В подключённых корпусах
    тоже есть лишнее: myproject 125 копий, z 11.

    Последствие для индекса: каждая копия даёт свои чанки, свои векторы и свой
    текст в полнотекстовом поиске. Один текст обрабатывается десятки раз,
    а выдача поиска заполняется его же копиями — при этом агент видит десять
    «разных» результатов вместо одного.

Что здесь делается
    Файлы группируются по `sha256` (он уже считается при сканировании).
    Из каждой группы выбирается одна «главная» копия, остальные помечаются
    как дубли и в индекс не идут.

    **Файлы на диске не удаляются и не изменяются.** Инструмент только
    перестаёт считать их отдельными сущностями индекса. Это принципиально:
    удаление файлов — необратимая операция, и решать её должен владелец,
    а не индексатор.

Чего здесь НЕТ
    * Схлопывания похожих, но не идентичных файлов. Сравнение идёт по
      контрольной сумме содержимого: `README.md` в 103 разных версиях
      (замер) не схлопнется ни один. Правило разведки — «схлопывать по хэшу,
      а не по имени» — выполняется буквально.
    * Работы с символическими ссылками как с дублями: у них разные пути,
      но если содержимое совпадает, они попадут в общую группу, и выбор
      «главной» пройдёт по обычному правилу. Отдельной обработки ссылок нет.

Почему правило выбора главной копии именно такое
    Проверены четыре правила на 513 реальных группах
    (tools/measure_dedupe_rules.py). Считалось, сколько групп получило
    «главным» путь из каталога-копии (backup, old, publish, release, out,
    copy, www, _to.upload, verify):

        без мусорных сегментов   38  ← выбрано
        самый свежий             65
        самый короткий           76
        самый длинный           242

    Выбор детерминирован: при полном равенстве берётся лексикографически
    первый путь. Это обязательное условие, а не мелочь: если выбор зависит
    от порядка обхода каталога, то «главным» при каждом сканировании может
    стать разный файл, сумма корпуса изменится, и сторож свежести начнёт
    бесконечно запускать переиндексацию.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .corpus import CorpusFile, CorpusScan

# Расширения документации: проверяются в режиме схемы «только документы».
DOC_EXTENSIONS: tuple[str, ...] = (".md", ".markdown", ".rst", ".txt")

# Сегменты пути, указывающие на копию, а не на источник.
#
# Список собран по факту, а не придуман: каждое значение встретилось в путях
# реальных групп при замере (tools/find_doc_dupes.py). Проверяется вхождение
# подстроки в путь в нижнем регистре, поэтому `_to.upload` покрывает и
# `_to.upload.пилотный PHP-портал`.
COPY_SEGMENTS: tuple[str, ...] = (
    "_backup", "backup", "old", "publish", "release", "dist", "build",
    "out", "copy", "_to.upload", "archive", "tmp", ".tmp", "verify",
    "www", "site-packages", "node_modules", "vendor",
)

# Режимы схемы.
SCOPE_DOCS = "docs"
SCOPE_ALL = "all"
SCOPES = (SCOPE_DOCS, SCOPE_ALL)

# Режимы обработки группы.
MODE_KEEP_ONE = "keep_one"
MODE_SKIP_ALL = "skip_all"
MODES = (MODE_KEEP_ONE, MODE_SKIP_ALL)

# Правила выбора главной копии.
KEEP_CLEANEST = "cleanest"   # без мусорных сегментов, затем короткий путь
KEEP_SHORTEST = "shortest"
KEEP_LONGEST = "longest"
KEEP_NEWEST = "newest"       # требует времени изменения: см. примечание ниже
KEEP_RULES = (KEEP_CLEANEST, KEEP_SHORTEST, KEEP_LONGEST, KEEP_NEWEST)


@dataclass
class DuplicateGroup:
    """Группа байт-идентичных файлов и решение по ней."""

    sha256: str
    kind: str                  # что это за файлы: язык главного файла
    bytes: int
    lines: int
    kept: str                  # путь оставленного файла
    dropped: list[str] = field(default_factory=list)
    reason: str = ""           # чем определён выбор

    @property
    def saved_files(self) -> int:
        return len(self.dropped)

    def as_dict(self) -> dict:
        return {
            "sha256": self.sha256[:16],
            "kind": self.kind,
            "bytes": self.bytes,
            "lines": self.lines,
            "kept": self.kept,
            "dropped": self.dropped,
            "dropped_count": len(self.dropped),
            "reason": self.reason,
        }


@dataclass
class DedupeResult:
    """Итог схлопывания: что оставлено, что отброшено и почему."""

    enabled: bool = False
    scope: str = SCOPE_DOCS
    mode: str = MODE_KEEP_ONE
    keep_by: str = KEEP_CLEANEST
    # Файлы, которые остаются в индексе (главные копии + все уникальные).
    kept_files: list[CorpusFile] = field(default_factory=list)
    # Файлы, исключённые как дубли.
    dropped_files: list[CorpusFile] = field(default_factory=list)
    groups: list[DuplicateGroup] = field(default_factory=list)
    # Сколько файлов вообще рассматривалось (для честного отчёта).
    considered: int = 0

    @property
    def dropped_count(self) -> int:
        return len(self.dropped_files)

    @property
    def dropped_bytes(self) -> int:
        return sum(f.bytes for f in self.dropped_files)

    @property
    def dropped_lines(self) -> int:
        return sum(f.lines for f in self.dropped_files)

    def summary(self) -> dict:
        """Краткая сводка для ответа операции."""
        return {
            "enabled": self.enabled,
            "scope": self.scope,
            "mode": self.mode,
            "keep_by": self.keep_by,
            "considered_files": self.considered,
            "duplicate_groups": len(self.groups),
            "dropped_files": self.dropped_count,
            "dropped_bytes": self.dropped_bytes,
            "dropped_lines": self.dropped_lines,
            "kept_files": len(self.kept_files),
        }


def is_document(path: str) -> bool:
    """Относится ли файл к документации. Определяется по расширению."""
    low = path.lower()
    return low.endswith(DOC_EXTENSIONS)


def dirty_score(path: str) -> tuple[int, int, str]:
    """Ключ сортировки «насколько путь похож на копию».

    Меньше — лучше. Порядок компонентов:
      1. сколько мусорных сегментов в пути (0 — чистый путь);
      2. глубина пути (оригинал обычно ближе к корню);
      3. сам путь (детерминизм при полном равенстве).

    Почему мусорные сегменты считаются, а не просто проверяются. Пути вида
    `old/второй PHP-корпус/www/www/www/www/docs/Stats.md` содержат и `old`, и
    несколько `www`. Путь `1/cgi-bin/format/FBO.v2/fbo_csv.txt` не содержит
    ни одного. Счёт даёт более точное сравнение, чем признак «есть/нет»:
    из двух мусорных путей выберется менее мусорный.
    """
    low = path.lower().replace("\\", "/")
    parts = low.split("/")
    bad = sum(1 for part in parts if any(seg in part for seg in COPY_SEGMENTS))
    return (bad, len(parts), low)


def choose_keeper(files: list[CorpusFile], keep_by: str) -> tuple[CorpusFile, str]:
    """Выбрать главную копию из группы. Возвращает файл и обоснование."""
    if len(files) == 1:
        return files[0], "единственный файл в группе"

    if keep_by == KEEP_LONGEST:
        # Самый длинный путь: проверялся как гипотеза «копии внутри копий».
        picked = max(files, key=lambda f: (len(f.path), f.path))
        return picked, "самый длинный путь (проверочное правило)"

    if keep_by == KEEP_SHORTEST:
        picked = min(files, key=lambda f: (len(f.path), f.path))
        return picked, "самый короткий путь"

    if keep_by == KEEP_NEWEST:
        # Время изменения берётся из файла. Замечание: у копий, сделанных
        # разом (например распаковкой архива), время может совпадать,
        # поэтому третьим компонентом всё равно идёт путь.
        def mtime_key(f: CorpusFile) -> tuple[float, str]:
            try:
                return (f.abs_path.stat().st_mtime, f.path)
            except OSError:
                return (0.0, f.path)

        picked = max(files, key=mtime_key)
        return picked, "самый свежий по времени изменения"

    # По умолчанию (и при KEEP_CLEANEST): сначала чистый путь, затем короткий.
    picked = min(files, key=lambda f: dirty_score(f.path))
    bad, depth, _ = dirty_score(picked.path)
    reason = ("путь без сегментов-признаков копии"
              if bad == 0 else f"наименее «мусорный» путь ({bad} сегм.)")
    return picked, reason


def dedupe(scan: CorpusScan, cfg) -> DedupeResult:
    """Схлопнуть байт-идентичные файлы корпуса.

    cfg — конфигурация проекта. Читаются настройки `index.dedupe_*`.

    Возвращается результат с двумя списками: `kept_files` (то, что пойдёт
    в индекс) и `dropped_files` (дубли). Если схлопывание выключено,
    `kept_files` равен исходному списку файлов — то есть поведение
    не меняется вообще.
    """
    index_cfg = cfg.index
    result = DedupeResult(
        enabled=bool(getattr(index_cfg, "dedupe_enabled", False)),
        scope=str(getattr(index_cfg, "dedupe_scope", SCOPE_DOCS)).lower(),
        mode=str(getattr(index_cfg, "dedupe_mode", MODE_KEEP_ONE)).lower(),
        keep_by=str(getattr(index_cfg, "dedupe_keep", KEEP_CLEANEST)).lower(),
    )

    # Проверка значений: неизвестное значение — это ошибка настройки,
    # а не повод молча работать по-другому.
    if result.scope not in SCOPES:
        raise ValueError(
            f"dedupe_scope={result.scope!r}: допустимо {SCOPES}"
        )
    if result.mode not in MODES:
        raise ValueError(
            f"dedupe_mode={result.mode!r}: допустимо {MODES}"
        )
    if result.keep_by not in KEEP_RULES:
        raise ValueError(
            f"dedupe_keep={result.keep_by!r}: допустимо {KEEP_RULES}"
        )

    if not result.enabled:
        # Выключено: возвращается вход без изменений.
        result.kept_files = list(scan.files)
        return result

    # Выбираются файлы, участвующие в сравнении.
    if result.scope == SCOPE_DOCS:
        candidates = [f for f in scan.files if is_document(f.path)]
    else:
        candidates = list(scan.files)
    result.considered = len(candidates)

    # Группировка по содержимому. Порядок внутри группы приводится к
    # детерминированному сразу: от него зависит выбор при полном равенстве.
    by_sha: dict[str, list[CorpusFile]] = {}
    for f in candidates:
        by_sha.setdefault(f.sha256, []).append(f)
    for files in by_sha.values():
        files.sort(key=lambda f: f.path)

    dropped_paths: set[str] = set()

    for sha, files in by_sha.items():
        if len(files) < 2:
            continue
        files = list(files)
        if result.mode == MODE_SKIP_ALL:
            # Ни одна копия не индексируется. Нужен, когда дубль сам по себе
            # не нужен (например, зеркало сторонней библиотеки).
            for f in files:
                dropped_paths.add(f.path)
            result.groups.append(
                DuplicateGroup(
                    sha256=sha, kind=files[0].lang, bytes=files[0].bytes,
                    lines=files[0].lines, kept="", dropped=[f.path for f in files],
                    reason="режим skip_all: не индексируется ни одна копия",
                )
            )
            continue

        keeper, reason = choose_keeper(files, result.keep_by)
        dropped = [f.path for f in files if f.path != keeper.path]
        for f in files:
            if f.path != keeper.path:
                dropped_paths.add(f.path)
        result.groups.append(
            DuplicateGroup(
                sha256=sha, kind=keeper.lang, bytes=keeper.bytes,
                lines=keeper.lines, kept=keeper.path, dropped=dropped,
                reason=reason,
            )
        )

    # Итоговые списки собираются в исходном порядке — он уже отсортирован
    # по пути в corpus.scan(), и менять его незачем.
    result.kept_files = [f for f in scan.files if f.path not in dropped_paths]
    result.dropped_files = [f for f in scan.files if f.path in dropped_paths]
    # Группы упорядочиваются по объёму потерянных файлов: в отчёте важнее
    # те, что дали больше всего лишних копий.
    result.groups.sort(key=lambda g: (-len(g.dropped), g.kept))
    return result


def apply_to_scan(scan: CorpusScan, cfg) -> DedupeResult:
    """Схлопнуть дубли и вернуть результат, не меняя исходный скан.

    Отдельная функция нужна, чтобы вызывающий код явно видел: исходный
    `CorpusScan` не меняется. Изменять его на месте опасно — тот же объект
    используется для подсчёта `corpus_hash`, и подмена файлов после подсчёта
    дала бы сумму, не соответствующую индексу.
    """
    return dedupe(scan, cfg)
