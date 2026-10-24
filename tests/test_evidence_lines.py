"""Проверка точности ссылок `path:line`: строка из индекса = строка в файле.

Зачем отдельная проверка. Текст чанка у СИМВОЛА начинается со служебного
заголовка (`function f (src/x.php:12)`), а у ФАЙЛА (разметка, документация,
текстовые языки) заголовка нет: первая строка текста и есть `start_line`.
Одна формула на оба случая давала сдвиг ровно на строку. Замер 14.10.2026 до
правки: строка «бюджет исчерпан на стадии» лежит в `src/ragraf/indexer.py:99`,
а индекс отдавал `100`; ошибка была у КАЖДОЙ ссылки внутри функции, то есть у
большинства ссылок по коду.

Проверка строит синтетический корпус и сверяет ответы индекса с файлами на
диске — и для символа, и для файла без заголовка. Числа берутся из диска, а не
записаны в проверке: иначе она застынет на текущей разметке файла.

Стиль — самостоятельный скрипт с `check`, как остальные проверки проекта.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf import util  # noqa: E402
from ragraf.config import load as load_config  # noqa: E402
from ragraf.facade import Facade  # noqa: E402
from ragraf.indexer import Indexer  # noqa: E402
from ragraf.init_corpus import create_config  # noqa: E402
from ragraf.lexvec import LexVecStore  # noqa: E402

PASS = 0
FAIL = 0


def check(condition: bool, title: str, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  [OK ] {title}")
    else:
        FAIL += 1
        print(f"  [FAIL] {title}" + (f"\n      {detail}" if detail else ""))


# Литералы выбраны редкими: чтобы совпадение было ровно одно и адрес был
# однозначен.
PHP = """<?php
// файл с функцией: текст чанка начинается служебным заголовком
function probe_marker_function($x) {
    $note = "МАРКЕР-ВНУТРИ-ФУНКЦИИ";
    return $note . $x;
}
"""
MARKDOWN = """# Документ

Первая строка описания.

Здесь идёт строка с МАРКЕР-В-ДОКУМЕНТЕ и всё.

Хвост документа.
"""


def disk_lines(path: Path, needle: str) -> list[int]:
    return [
        i for i, line in enumerate(
            path.read_text(encoding="utf-8").split("\n"), 1)
        if needle in line
    ]


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="ragraf_lines_"))
    try:
        src = tmp / "src"
        src.mkdir()
        (src / "func.php").write_text(PHP, encoding="utf-8", newline="\n")
        # Третий файл нужен только чтобы корпус прошёл порог «похоже на проект»
        # (минимум файлов с кодом) — на проверку строк он не влияет.
        (src / "other.php").write_text(
            "<?php\nfunction probe_other() { return 1; }\n",
            encoding="utf-8", newline="\n")
        (tmp / "notes.md").write_text(MARKDOWN, encoding="utf-8", newline="\n")
        (tmp / ".gitignore").write_text("probe\n", encoding="utf-8", newline="\n")

        cfg_path, _scan = create_config(tmp, project="linesprobe",
                                         config_path=tmp / "lines.yaml")
        text = cfg_path.read_text(encoding="utf-8").replace(
            "joern_enabled: true", "joern_enabled: false")
        cfg_path.write_text(text, encoding="utf-8", newline="\n")
        cfg = load_config(cfg_path)

        idx = Indexer(cfg)
        try:
            report = idx.run(with_vectors=False, budget_seconds=300)
        finally:
            idx.close()
        check(report.ok, "корпус проиндексирован", str(report.notes))

        php_lines = disk_lines(src / "func.php", "МАРКЕР-ВНУТРИ-ФУНКЦИИ")
        md_lines = disk_lines(tmp / "notes.md", "МАРКЕР-В-ДОКУМЕНТЕ")
        check(len(php_lines) == 1 and len(md_lines) == 1,
              "маркеры найдены на диске по одному разу",
              f"php={php_lines} md={md_lines}")

        print("\n=== 1. Правило пересчёта строки ===")
        # Символьный чанк: первая строка — заголовок, код начинается со второй.
        symbol_text = "function probe_marker_function (src/func.php:3)\nline_one\nМАРКЕР\n"
        check(util.chunk_header_lines(symbol_text) == 1,
              "у чанка-символа заголовок распознан")
        check(util.text_line(symbol_text, symbol_text.find("МАРКЕР"), 3) == 4,
              "строка внутри функции считается без заголовка",
              str(util.text_line(symbol_text, symbol_text.find("МАРКЕР"), 3)))
        file_text = "Первая\nМАРКЕР\n"
        check(util.chunk_header_lines(file_text) == 0,
              "у чанка-файла заголовка нет")
        check(util.text_line(file_text, file_text.find("МАРКЕР"), 7) == 8,
              "строка файла считается от start_line",
              str(util.text_line(file_text, file_text.find("МАРКЕР"), 7)))

        print("\n=== 2. Литеральный поиск: символ (был сдвиг на строку) ===")
        lx = LexVecStore(cfg)
        try:
            rev = None
            row = lx.conn.execute(
                "SELECT rev_id FROM chunk_map ORDER BY rowid DESC LIMIT 1").fetchone()
            rev = str(row[0]) if row else ""
            hits, method = lx.literal_hits(rev, "МАРКЕР-ВНУТРИ-ФУНКЦИИ")
        finally:
            lx.close()
        php_hits = [h for h in hits if h["path"] == "src/func.php"]
        check(bool(php_hits), "литерал найден в функции", f"method={method}")
        if php_hits:
            check(php_hits[0]["line"] == php_lines[0],
                  "строка внутри функции совпадает с файлом",
                  f"индекс {php_hits[0]['line']}, диск {php_lines[0]}")
            check(php_hits[0].get("source_kind") == "symbol",
                  "хит помечен как символьный чанк",
                  str(php_hits[0].get("source_kind")))

        print("\n=== 3. Литеральный поиск: файл без заголовка ===")
        lx = LexVecStore(cfg)
        try:
            hits_md, method_md = lx.literal_hits(rev, "МАРКЕР-В-ДОКУМЕНТЕ")
        finally:
            lx.close()
        md_hits = [h for h in hits_md if h["path"] == "notes.md"]
        check(bool(md_hits), "литерал найден в документе", f"method={method_md}")
        if md_hits:
            check(md_hits[0]["line"] == md_lines[0],
                  "строка документа совпадает с файлом",
                  f"индекс {md_hits[0]['line']}, диск {md_lines[0]}")

        print("\n=== 4. Операция search_multi отдаёт те же строки ===")
        fc = Facade(cfg, tool="selftest", telemetry=False, watch=False)
        try:
            res = fc.op_search_multi(["МАРКЕР-ВНУТРИ-ФУНКЦИИ", "МАРКЕР-В-ДОКУМЕНТЕ"],
                                     limit=10)
        finally:
            fc.close()
        found: dict[str, list[int]] = {}
        for q in res["result"].get("queries", []):
            for h in q.get("hits", []):
                found.setdefault(h["path"], []).append(h["line"])
        check(found.get("src/func.php") == php_lines,
              "search_multi: строка в функции верна",
              f"{found.get('src/func.php')} против {php_lines}")
        check(found.get("notes.md") == md_lines,
              "search_multi: строка в документе верна",
              f"{found.get('notes.md')} против {md_lines}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    print("=" * 78)
    print(f"ИТОГ: пройдено {PASS}, провалено {FAIL}")
    print("=" * 78)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
