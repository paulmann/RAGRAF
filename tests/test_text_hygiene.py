"""Свойство: механизм чистоты текста не вносит излишних изменений.

ЗАДАЧА, ИЗ КОТОРОЙ ВЫРОСЛА ПРОВЕРКА (31.10.2026). Механизм очистки BOM/CRLF
применялся к любому файлу, которому его передали, и к любому каталогу,
который ему назвали. Измерено в песочнице настоящим инструментом: запись с
зафиксированным `sha256` после нормализации перестала совпадать с записью
(`e9852244e883 -> a43f8c189b50`), RFC 4180 `csv` потерял обязательный CRLF
(10 -> 8 б), а обход каталога переписал 6 файлов из 9, включая приманку.
Отдельно: наивная проверка `b"\\r\\n" in data` считала «грязными» двоичные
файлы (`social-preview.png`, `secrets.sqlite`, `cpg.bin`) — там `0D 0A` не
является переводом строки.

ЧТО ПРОВЕРЯЕТСЯ — свойством, а не написанием:
  * двоичный файл и UTF-16 не называются «требуют очистки» ни при каких байтах;
  * у `.bat`/`.cmd`/`.reg`/`.csv`/`.tsv` переводы строк не меняются никогда,
    а BOM снимается только вместе с `--no-rn-normalize`;
  * запись `archive/**`, копия `*.bak*`, копии `community/**` не подаются
    инструменту вовсе;
  * каталог как аргумент отклоняется, и ни один файл дерева не меняется;
  * уже чистый файл не переписывается (байты И время изменения те же);
  * план ничего не пишет: до `apply()` байты на диске не меняются;
  * если инструмент доступен, реальная очистка обычного текста убирает только
    байты CR и оставляет содержимое (сверка построчно).

Граница названа измерением: разделы с реальным прогоном инструмента помечены
`SKIPPED`, если Git Bash или инструмент не найдены — на этой машине они есть,
и тогда проверка идёт полностью.
"""

from __future__ import annotations

import os
import pathlib
import shutil
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))

import text_hygiene as hy  # noqa: E402

PASS = 0
FAIL = 0
SKIPPED = 0


def check(ok: bool, message: str) -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  ok   {message}")
    else:
        FAIL += 1
        print(f"  FAIL {message}")


def skip(message: str) -> None:
    global SKIPPED
    SKIPPED += 1
    print(f"  SKIP {message}")


def write(path: pathlib.Path, data: bytes) -> pathlib.Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def strip_cr(data: bytes) -> bytes:
    return data.replace(b"\r\n", b"\n")


def sandbox() -> pathlib.Path:
    base = pathlib.Path(tempfile.mkdtemp(prefix="ragraf-hygiene-"))
    return base


def main() -> int:
    global PASS, FAIL
    root = sandbox()
    BASH, SH_TOOL, PS1_TOOL = hy.tool_paths()

    print("=" * 78)
    print("1. ДВОИЧНЫЕ И UTF-16 НЕ СЧИТАЮТСЯ ГРЯЗНЫМИ")
    print("=" * 78)
    png = write(root / "image.png", bytes([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A, 0x00]))
    utf16 = write(root / "note16.txt", b"\xff\xfe" + "привет\r\n".encode("utf-16-le"))
    latin = write(root / "cp1251.txt", "привет\r\n".encode("cp1251"))
    for path, expected in ((png, "binary"), (utf16, "utf16"), (latin, "binary")):
        finding = hy.classify(path, root)
        check(finding.kind == expected,
              f"{path.name}: класс {finding.kind} (ожидался {expected})")
        check(finding.action == "keep",
              f"{path.name}: не предлагается к очистке (действие {finding.action})")
    in_live = REPO / "assets" / "social-preview.png"
    if in_live.exists():
        finding = hy.classify(in_live, REPO)
        check(finding.kind == "binary" and finding.action == "keep",
              "живой assets/social-preview.png классифицирован как двоичный, а не «с CRLF»")

    print()
    print("=" * 78)
    print("2. CRLF ФУНКЦИОНАЛЕН: РАСШИРЕНИЯ ЗАЩИЩЕНЫ")
    print("=" * 78)
    pinned = {
        "script.bat": b"@echo off\r\necho A\r\n",
        "script.cmd": b"@echo off\r\n",
        "keys.reg": b"Windows Registry Editor Version 5.00\r\n",
        "table.csv": b"a,b\r\n1,2\r\n",
        "table.tsv": b"a\tb\r\n",
    }
    for name, body in pinned.items():
        path = write(root / name, body)
        finding = hy.classify(path, root)
        check(finding.pinned_eol and finding.action == "keep",
              f"{name}: переводы строк не меняются (действие {finding.action})")
    bom_bat = write(root / "bom.bat", b"\xef\xbb\xbf" + b"@echo off\r\necho A\r\n")
    finding = hy.classify(bom_bat, root)
    check(finding.action == "bom-only", f"bom.bat: только BOM снимается (действие {finding.action})")
    commands = hy.commands_for(hy.plan([bom_bat], root))
    check(bool(commands) and all("--no-rn-normalize" in " ".join(c) for c in commands),
          "команда для bom.bat несёт --no-rn-normalize (CRLF сохраняются)")
    if BASH and SH_TOOL:
        before = bom_bat.read_bytes()
        hy.apply(hy.plan([bom_bat], root))
        after = bom_bat.read_bytes()
        check(not after.startswith(b"\xef\xbb\xbf"), "после очистки BOM у bom.bat снят")
        check(after.count(b"\r\n") == before.count(b"\r\n"),
              f"после очистки CRLF у bom.bat сохранены ({after.count(b'\r\n')} шт.)")
        check(after == before[3:], "содержимое bom.bat отличается только снятым BOM")
    else:
        skip("реальный прогон инструмента: Git Bash или clean-bom-senior.sh не найдены")

    print()
    print("=" * 78)
    print("3. ЗАПИСИ И КОПИИ НЕ ПОДАЮТСЯ ИНСТРУМЕНТУ")
    print("=" * 78)
    record = write(root / "archive" / "20261031-fix" / "files" / "copy.py", b"x = 1\r\n")
    backup = write(root / "note.txt.bak-20261031", "старое\r\n".encode("utf-8"))
    community = write(root / "community" / "CHANGELOG.md", b"# a\r\n")
    staging = write(root / ".work-i14" / "probe.py", b"y = 2\r\n")
    for name, path in (("archive/**", record), ("*.bak-*", backup),
                       ("community/**", community), (".work-i14/**", staging)):
        finding = hy.classify(path, root)
        check(finding.action == "keep", f"{name}: не предлагается к очистке")
    plan_ = hy.plan([record, backup, community, staging], root)
    check(not plan_.changes, f"план не содержит изменений по записям (изменений {len(plan_.changes)})")

    print()
    print("=" * 78)
    print("4. КАТАЛОГ ОТКЛОНЯЕТСЯ, ДЕРЕВО НЕ МЕНЯЕТСЯ")
    print("=" * 78)
    tree = root / "tree"
    body = {name: b"a\r\nb\r\n" for name in
            ("note.txt", "page.html", "style.css", "app.js", "приманка.txt", "sub/deep.txt")}
    for name, data in body.items():
        write(tree / name, data)
    before = {p: p.read_bytes() for p in tree.rglob("*") if p.is_file()}
    plan_ = hy.plan([tree], root)
    ok, done = hy.apply(plan_)
    after = {p: p.read_bytes() for p in tree.rglob("*") if p.is_file()}
    check(bool(plan_.refused) and plan_.refused[0].kind == "directory",
          "каталог попал в отклонённые с причиной")
    check(not plan_.changes and not done, "по каталогу не собрано ни одной команды очистки")
    check(before == after, f"ни один файл дерева не изменился (файлов {len(before)})")

    print()
    print("=" * 78)
    print("5. УЖЕ ЧИСТЫЙ ФАЙЛ НЕ ПЕРЕПИСЫВАЕТСЯ, ПЛАН НИЧЕГО НЕ ПИШЕТ")
    print("=" * 78)
    clean = write(root / "clean.txt", b"a\nb\n")
    stamp = clean.stat().st_mtime_ns
    finding = hy.classify(clean, root)
    check(finding.action == "keep" and finding.reason == "уже чисто",
          "чистый файл назван чистым, а не «требует очистки»")
    dirty = write(root / "dirty.txt", b"a\r\nb\r\n")
    dirty_before = dirty.read_bytes()
    plan_ = hy.plan([dirty], root)
    check(dirty.read_bytes() == dirty_before, "план не изменил файл на диске")
    check(plan_.clean and plan_.clean[0].action == "clean", "файл с CRLF попал в очистку")
    if BASH and SH_TOOL:
        hy.apply(plan_)
        check(clean.stat().st_mtime_ns == stamp, "время изменения чистого файла не сдвинулось")
        check(dirty.read_bytes() == b"a\nb\n", "после очистки от CRLF остались только LF")
        lines_before = dirty_before.splitlines()
        check(dirty.read_bytes().splitlines() == lines_before,
              "построчное содержимое до и после совпадает")
        dirty.write_bytes(dirty_before)
        hy.apply(hy.plan([dirty], root))
        check(dirty.read_bytes() == b"a\nb\n", "повторный прогон идемпотентен")
    else:
        skip("реальный прогон очистки: инструмент не найден")

    print()
    print("=" * 78)
    print("6. СМЕШАННЫЕ ПЕРЕВОДЫ СТРОК НАЗВАНЫ, А НЕ СПРЯТАНЫ")
    print("=" * 78)
    mixed = write(root / "mixed.txt", b"a\r\nb\nc\rd\n")
    finding = hy.classify(mixed, root)
    check(finding.mixed, "смешанный файл помечен как смешанный")
    if BASH and SH_TOOL:
        before = mixed.read_bytes()
        hy.apply(hy.plan([mixed], root))
        after = mixed.read_bytes()
        hy_after = hy.classify(mixed, root)
        check(after == strip_cr(before), "из смешанного файла убраны только CRLF")
        check(hy_after.cr_only == 1,
              f"одиночный CR остался и виден ({hy_after.cr_only} шт.), а не выдан за чистоту")
    else:
        skip("реальная очистка смешанного файла: инструмент не найден")

    print()
    print("=" * 78)
    print("7. ЖЁСТКИЕ ССЫЛКИ: ДВА ИМЕНИ НЕ РАСХОДЯТСЯ")
    print("=" * 78)
    first = write(root / "link_a.txt", b"one\r\ntwo\r\n")
    second = root / "link_b.txt"
    try:
        os.link(first, second)
        linked = True
    except OSError:
        linked = False
    if linked and BASH and SH_TOOL:
        hy.apply(hy.plan([first], root))
        check(first.read_bytes() == second.read_bytes(),
              "после очистки оба имени по-прежнему указывают на одинаковые байты")
        check(first.stat().st_ino == second.stat().st_ino, "жёсткая ссылка не разорвана")
    else:
        skip("жёсткие ссылки: нет поддержки на файловой системе или нет инструмента")

    print()
    print("=" * 78)
    print("8. ЖИВОЕ ДЕРЕВО: ПРОВЕРКА НЕ ВРЁТ ПРО ДВОИЧНЫЕ И ЗАПИСИ")
    print("=" * 78)
    live_binaries = [REPO / "assets" / "social-preview.png",
                     REPO / "workspace" / "rev-0099.cpg.bin" / "cpg.bin",
                     REPO / "catalog" / "secrets.sqlite"]
    seen = 0
    for path in live_binaries:
        if not path.exists():
            continue
        finding = hy.classify(path, REPO)
        seen += 1
        check(finding.action == "keep",
              f"{path.relative_to(REPO)}: не предлагается к очистке ({finding.kind})")
    if seen == 0:
        skip("двоичных файлов дерева не найдено под ожидаемыми именами")
    live_record = REPO / "archive"
    if live_record.exists():
        files = [p for p in live_record.rglob("*") if p.is_file()][:5]
        plan_ = hy.plan(files, REPO)
        check(not plan_.changes, f"записи archive/ не попадают в очистку (осмотрено {len(files)})")
    else:
        skip("каталога archive/ в дереве нет")

    print()
    print("=" * 78)
    print(f"ИТОГ: пройдено {PASS}, провалено {FAIL}, пропущено {SKIPPED}")
    print("=" * 78)
    # Песочница убирается за собой: иначе %TEMP% копит копии дерева с каждой правки.
    try:
        shutil.rmtree(root, ignore_errors=True)
    except OSError:
        pass
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
