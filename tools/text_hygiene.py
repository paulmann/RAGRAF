"""Политика чистоты текста: что нормализовать можно, а что трогать нельзя.

ЗАЧЕМ ЭТОТ МОДУЛЬ. Правило проекта требует приводить текстовые файлы к
`BOM=False`, `CRLF=False` инструментом `clean-bom-senior`. Проверка
(`tools/check_bom_crlf.py`) при этом смотрела на байты наивно и знала только
свой список файлов, а очистка вызывалась по путям, которые этот список
повторял. Отсюда три класса вреда, и все три измерены 31.10.2026 в песочнице
(`%TEMP%\\hygiene-sandbox`) прогоном настоящего инструмента и проверкой байтов
до/после:

1. ЧУЖИЕ БАЙТЫ. Запись с зафиксированным `sha256` после нормализации перестаёт
   совпадать с записью: `archive_copy.py` (CRLF внутри) — `e9852244e883 ->
   a43f8c189b50`, то есть копия «до правки» перестала быть копией. Тем же
   способом ломается сверка `community/**` с публичным `main` (их хэши
   перечислены в `docs/03_implementation/RELEASE-SOURCES.md`).
2. ФУНКЦИОНАЛЬНЫЙ CRLF. `.bat`, `.cmd`, `.reg` — исполняемые файлы Windows,
   `.csv`/`.tsv` — данные по RFC 4180 (там CRLF обязателен). Инструмент с
   явным путём переводит их на LF: `rfc4180.csv` 10 -> 8 б, `reg_lf.reg`
   96 -> 92 б, `straight.bat` 38 -> 34 б. Замер исполнения: на этой машине
   `cmd.exe` одинаково исполняет LF- и CRLF-батник (7 матриц: `goto`, метка в
   скобках, `call :sub`, вложенный `for`, последняя строка без перевода —
   расхождений 0). То есть LF-батник здесь работает, но это свойство
   конкретного `cmd.exe`, а не контракта формата; менять байты исполняемого
   файла ради гигиены нельзя.
3. НЕ ТОТ ОБХЁМ. Аргумент-каталог инструмент обходит по своему списку
   расширений (`php css js txt xml htm html`) и переписывает всё, включая
   приманки и чужие проекты: в песочнице изменены `note.txt`, `page.html`,
   `style.css`, `app.js`, `sub/deep.txt` и приманка `приманка.txt` — 6 из 9
   файлов, при `--apply` без списка. Именно так 29.10.2026 было переписано
   727 существующих файлов дерева вместо одного нужного.

Плюс наивная проверка (`b"\\r\\n" in data`) даёт ложные срабатывания на
двоичных файлах: в дереве проекта так «с CRLF» числятся `assets/social-preview.png`,
`catalog/secrets.sqlite`, `workspace/rev-0099.cpg.bin` (байты `0D 0A` внутри
двоичного формата). Инструмент такие файлы не трогает (проверено: `image.png`
и `utf16.txt` остались байт в байт), а проверка сообщала о них как о дефекте —
и следующий шаг «дочистить с `--force`» испортил бы файл.

ЧТО ДЕЛАЕТ МОДУЛЬ.
  * `classify()` — по фактическим байтам относит файл к классу (`text`,
    `binary`, `utf16`, `empty`, `symlink`, `missing`, `outside`) и называет
    причину, по которой файл защищён от нормализации;
  * `plan()` — считает, что можно очистить, что только снять BOM (для
    `.bat`/`.cmd`/`.reg` — с `--no-rn-normalize`, CRLF сохраняются), а что
    оставить как есть; НИЧЕГО не пишет;
  * `apply()` — выполняет очистку РАЗРЕШЁННЫХ путей через инструмент, по
    одному файлу за аргумент, пакетами; каталоги отклоняются целиком.

ГРАНИЦЫ. Модуль не решает, «правильный» ли у файла стиль: он отвечает только
на вопрос «можно ли этот файл переписывать» и «что именно в нём изменится».
Содержимое не проверяется на смысл; смена кодировки (cp1251 и подобное) не
выполняется — это `--force` инструмента и отдельное решение.
"""

from __future__ import annotations

import argparse
import os
import pathlib
import shutil
import subprocess
import sys
from dataclasses import dataclass, field

REPO = pathlib.Path(__file__).resolve().parents[1]

#: Сколько файлов подаётся инструменту за один вызов: командная строка Windows
#: ограничена ~32 КБ, длинные пути приближают к границе в одиночку.
BATCH_LIMIT = 12

#: Файлы больше этого не осматриваются: чтение целиком стоит дороже пользы,
#: а такие файлы в проекте — двоичные артефакты (`cpg.bin`, базы).
MAX_BYTES = 8 * 1024 * 1024

#: Расширения, у которых CRLF ФУНКЦИОНАЛЕН. `.bat`/`.cmd` — исполняемые файлы
#: Windows, `.reg` — файл реестра, `.csv`/`.tsv` — RFC 4180 (CRLF обязателен).
EOL_PINNED = frozenset({".bat", ".cmd", ".reg", ".csv", ".tsv"})

#: Расширения, у которых BOM несут смысл: их читает Excel и Windows PowerShell
#: 5.1, и BOM там — признак кодировки, а не грязь. Снятие BOM у них делается
#: только осознанно, поэтому здесь они не попадают в «снять BOM».
BOM_SENSITIVE = frozenset({".txt", ".csv", ".tsv", ".ps1", ".psm1", ".psd1", ".reg"})

#: Каталоги, содержимое которых является ЗАПИСЬЮ, а не рабочим текстом:
#: `archive` — копии до правки с `sha256` в манифесте (§8.23 правил),
#: `community` — побайтовые копии публичного `main`, сверяемые по хэшам,
#: остальные — машинные артефакты и зависимости, которые чистить бессмысленно.
PROTECTED_DIRS = frozenset({
    "archive", "community", "workspace", ".ragraf", ".staging", ".zwork",
    ".tools", ".venv", "venv", "node_modules", "__pycache__", ".ort_gpu_probe",
    ".agents", ".dev_agent", ".pytest_cache", ".git",
})

#: Префиксы каталогов машинных артефактов: `.work-i14`, `stage-i19`.
PROTECTED_PREFIXES = (".work", "stage-i", ".stage-")

#: Маркеры имени файла-записи: копия «до правки», бэкап, резерв.
RECORD_MARKERS = (".bak", ".orig-", ".backup", ".save", "~")

ACTIONS = ("clean", "bom-only", "keep")


@dataclass
class Finding:
    """Результат осмотра одного пути."""

    path: pathlib.Path
    rel: str
    kind: str
    action: str
    reason: str
    bom: bool = False
    crlf: int = 0
    lf: int = 0
    cr_only: int = 0
    pinned_eol: bool = False
    size: int = 0

    @property
    def mixed(self) -> bool:
        """Переводы строк разные: после очистки останутся одиночные CR."""
        return self.crlf > 0 and (self.cr_only > 0 or self.lf > 0)

    @property
    def flags(self) -> str:
        out = []
        if self.bom:
            out.append("BOM")
        if self.crlf:
            out.append(f"CRLF={self.crlf}")
        if self.cr_only:
            out.append(f"CR-один={self.cr_only}")
        if self.kind == "utf16":
            out.append("UTF-16")
        if self.kind == "binary":
            out.append("двоичный")
        return " ".join(out) or "чисто"


@dataclass
class Plan:
    """Что модуль собирается сделать. Ничего не выполнено до `apply()`."""

    clean: list[Finding] = field(default_factory=list)
    bom_only: list[Finding] = field(default_factory=list)
    keep: list[Finding] = field(default_factory=list)
    already: list[Finding] = field(default_factory=list)
    refused: list[Finding] = field(default_factory=list)

    @property
    def changes(self) -> list[Finding]:
        return self.clean + self.bom_only


# --------------------------------------------------------------------------
# Классификация
# --------------------------------------------------------------------------


def _count_eol(data: bytes) -> tuple[int, int, int]:
    """Число CRLF, одиночных LF и одиночных CR — по байтам, без разбора текста."""
    crlf = data.count(b"\r\n")
    lf = data.count(b"\n") - crlf
    cr_only = data.count(b"\r") - crlf
    return crlf, lf, cr_only


def _is_utf16(data: bytes) -> bool:
    return data[:2] in (b"\xff\xfe", b"\xfe\xff") or data[:4] in (b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff")


def _is_text(data: bytes) -> bool:
    """Текст — это то, что декодируется как UTF-8 и не содержит NUL."""
    if b"\x00" in data:
        return False
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def _protected_dir(rel: str) -> str | None:
    """Название защищённого каталога в пути, если он там есть."""
    parts = pathlib.PurePath(rel).parts[:-1]
    for part in parts:
        low = part.lower()
        if low in PROTECTED_DIRS:
            return part
        if any(low.startswith(prefix) for prefix in PROTECTED_PREFIXES):
            return part
    return None


def _is_record_name(name: str) -> bool:
    low = name.lower()
    return any(marker in low for marker in RECORD_MARKERS)


def classify(path: pathlib.Path, root: pathlib.Path = REPO) -> Finding:
    """Отнести файл к классу и решить, можно ли его переписывать."""
    path = pathlib.Path(path)
    try:
        rel = str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        rel = str(path)

    if path.is_symlink():
        return Finding(path, rel, "symlink", "keep",
                       "символическая ссылка: правка переписала бы цель", size=0)
    if not path.exists():
        return Finding(path, rel, "missing", "keep", "файла нет", size=0)
    if path.is_dir():
        return Finding(path, rel, "directory", "keep",
                       "каталог: обход дерева запрещён (переписывает чужие файлы)")

    size = path.stat().st_size
    if size > MAX_BYTES:
        return Finding(path, rel, "large", "keep",
                       f"больше {MAX_BYTES // (1024 * 1024)} МиБ — не осматривается", size=size)

    data = path.read_bytes()
    crlf, lf, cr_only = _count_eol(data)
    suffix = path.suffix.lower()
    pinned = suffix in EOL_PINNED
    bom = data.startswith(b"\xef\xbb\xbf")

    if size == 0:
        return Finding(path, rel, "empty", "keep", "пустой файл", size=0)
    if _is_utf16(data):
        return Finding(path, rel, "utf16", "keep",
                       "UTF-16: переводы строк и кодировка — часть формата", size=size)
    if not _is_text(data):
        return Finding(path, rel, "binary", "keep",
                       "двоичный или не UTF-8: байты 0D 0A здесь не перевод строки",
                       bom=bom, crlf=crlf, lf=lf, cr_only=cr_only, size=size)

    base = dict(bom=bom, crlf=crlf, lf=lf, cr_only=cr_only, size=size, pinned_eol=pinned)

    protected = _protected_dir(rel)
    if protected:
        return Finding(path, rel, "text", "keep",
                       f"запись в «{protected}»: байты зафиксированы и сверяются хэшем", **base)
    if _is_record_name(path.name):
        return Finding(path, rel, "text", "keep",
                       "копия «до правки»: её байты — это и есть ценность", **base)

    if pinned:
        nothing = not bom and crlf == 0
        if nothing:
            return Finding(path, rel, "text", "keep", f"{suffix}: CRLF функционален, файл уже в своём виде", **base)
        if bom and data[3:].isascii():
            return Finding(path, rel, "text", "bom-only",
                           f"{suffix}: снимается только BOM, CRLF сохраняются", **base)
        return Finding(path, rel, "text", "keep",
                       f"{suffix}: CRLF функционален — переводы строк не меняются", **base)

    if not bom and crlf == 0:
        return Finding(path, rel, "text", "keep", "уже чисто", **base)
    reason = "BOM и CRLF" if bom and crlf else ("BOM" if bom else "CRLF")
    if suffix in BOM_SENSITIVE and bom and not data[3:].isascii():
        reason = f"{reason}; BOM у {suffix} с не-ASCII сохраняет инструмент (своя политика)"
    return Finding(path, rel, "text", "clean", reason, **base)


def plan(paths, root: pathlib.Path = REPO) -> Plan:
    """Посчитать план по явным путям. Каталоги не раскрываются."""
    result = Plan()
    for item in paths:
        finding = classify(pathlib.Path(item), root)
        if finding.action == "clean":
            result.clean.append(finding)
        elif finding.action == "bom-only":
            result.bom_only.append(finding)
        elif finding.reason == "уже чисто":
            result.already.append(finding)
        elif finding.kind in ("directory", "missing", "symlink", "large", "binary", "utf16", "empty", "outside"):
            result.refused.append(finding)
        else:
            result.keep.append(finding)
    return result


# --------------------------------------------------------------------------
# Выполнение
# --------------------------------------------------------------------------


def tool_paths() -> tuple[str | None, str | None, str | None]:
    """Найти инструмент и оболочку. Возвращает (bash, sh-инструмент, ps1-порт)."""
    bash = os.environ.get("RAGRAF_CLEAN_BOM_BASH")
    if not bash:
        candidate = pathlib.Path(r"C:\Program Files\Git\bin\bash.exe")
        bash = str(candidate) if candidate.exists() else shutil.which("bash")
    base = os.environ.get("RAGRAF_CLEAN_BOM_DIR")
    roots = [pathlib.Path(base)] if base else [REPO.parent / "Clean_BOM_Senior",
                                               pathlib.Path(r"C:\AutoClaw\Clean_BOM_Senior")]
    sh_tool = ps1_tool = None
    for root in roots:
        candidate = root / "clean-bom-senior.sh"
        if sh_tool is None and candidate.exists():
            sh_tool = str(candidate)
        candidate = root / "clean-bom-senior.ps1"
        if ps1_tool is None and candidate.exists():
            ps1_tool = str(candidate)
    return bash or None, sh_tool, ps1_tool


def default_runner(commands: list[list[str]]) -> list[tuple[list[str], int, str]]:
    """Выполнить команды по очереди, вернуть (команда, код, вывод)."""
    done = []
    for command in commands:
        proc = subprocess.run(command, capture_output=True, text=True,
                              encoding="utf-8", errors="replace")
        done.append((command, proc.returncode, (proc.stdout or "") + (proc.stderr or "")))
    return done


def commands_for(plan_: Plan) -> list[list[str]]:
    """Собрать команды очистки: по одному файлу за аргумент, пакетами."""
    bash, sh_tool, ps1_tool = tool_paths()
    commands: list[list[str]] = []
    if bash and sh_tool:
        tool_arg = sh_tool.replace("\\", "/")
        for batch in (plan_.clean, plan_.bom_only):
            for start in range(0, len(batch), BATCH_LIMIT):
                part = [str(f.path) for f in batch[start:start + BATCH_LIMIT]]
                flag = "--no-rn-normalize " if batch is plan_.bom_only else ""
                commands.append([bash, "-c",
                                 "bash '" + tool_arg + "' " + flag + " ".join(f"'{p}'" for p in part)])
        return commands
    if ps1_tool:
        for batch in (plan_.clean, plan_.bom_only):
            for finding in batch:
                command = ["pwsh", "-NoLogo", "-NoProfile", "-NonInteractive", "-File", ps1_tool]
                if batch is plan_.bom_only:
                    command.append("--no-rn-normalize")
                command.append(str(finding.path))
                commands.append(command)
        return commands
    return []


def apply(plan_: Plan, runner=default_runner) -> tuple[bool, list[tuple[list[str], int, str]]]:
    """Выполнить очистку разрешённых путей. Защищённые не подаются вовсе."""
    commands = commands_for(plan_)
    if plan_.changes and not commands:
        return False, []
    done = runner(commands) if commands else []
    ok = all(code == 0 for _, code, _ in done)
    return ok, done


# --------------------------------------------------------------------------
# Отчёт
# --------------------------------------------------------------------------


def describe(plan_: Plan, stream=sys.stdout) -> None:
    def line(text: str = "") -> None:
        print(text, file=stream)

    line("=" * 78)
    line("ЧИСТОТА ТЕКСТА: ЧТО БУДЕТ ИЗМЕНЕНО, А ЧТО ЗАЩИЩЕНО")
    line("=" * 78)
    line()
    line(f"  требуют очистки:            {len(plan_.clean)}")
    line(f"  только BOM (CRLF сохранить): {len(plan_.bom_only)}")
    line(f"  защищены причиной:          {len(plan_.keep)}")
    line(f"  уже чисты:                  {len(plan_.already)}")
    line(f"  отклонены (не текст/каталог): {len(plan_.refused)}")
    for title, group in (("ТРЕБУЮТ ОЧИСТКИ", plan_.clean),
                         ("ТОЛЬКО BOM", plan_.bom_only),
                         ("ЗАЩИЩЕНЫ", plan_.keep),
                         ("ОТКЛОНЕНЫ", plan_.refused)):
        if not group:
            continue
        line()
        line(f"  {title}:")
        for finding in group:
            line(f"    {finding.rel:<58} {finding.flags:<22} {finding.reason}")
    if plan_.clean or plan_.bom_only:
        line()
        line("  команда очистки (явные пути, каталоги не подаются):")
        for command in commands_for(plan_):
            shown = " ".join(command) if isinstance(command, list) else str(command)
            line(f"    {shown[:160]}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Политика чистоты текста: осмотр и очистка по явным путям.")
    parser.add_argument("--paths", nargs="+", default=[], help="явные пути к файлам")
    parser.add_argument("--paths-from", help="файл со списком путей, по одному в строке")
    parser.add_argument("--apply", action="store_true",
                        help="выполнить очистку разрешённых путей (по умолчанию только отчёт)")
    parser.add_argument("--check", action="store_true",
                        help="код 1, если есть что очищать (для гейта)")
    args = parser.parse_args(argv)

    paths = list(args.paths)
    if args.paths_from:
        source = pathlib.Path(args.paths_from)
        if not source.exists():
            print(f"нет файла списка: {source}", file=sys.stderr)
            return 2
        paths.extend(line.strip() for line in source.read_text(encoding="utf-8").splitlines()
                     if line.strip() and not line.startswith("#"))
    if not paths:
        parser.error("нужен --paths или --paths-from: обход дерева запрещён")

    plan_ = plan(paths)
    describe(plan_)

    if args.apply and plan_.changes:
        ok, done = apply(plan_)
        print()
        print(f"  выполнено команд: {len(done)}, код: {'0' if ok else 'ошибка'}")
        if not done:
            print("  инструмент не найден: очистка не выполнена", file=sys.stderr)
            return 3
        if not ok:
            return 1

    if args.check and plan_.changes:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
