"""Проверка P1-1 и P1-2 на синтетическом корпусе, повторяющем BWO.

Почему отдельный корпус, а не собственный код RAGRAF. Проверяемые свойства —
про КОПИИ и РАЗМЕТКУ: «одно имя с разными номерами», ссылка на рабочий файл
из шаблона, файл в CP1251. В коде RAGRAF таких конструкций нет, и проверка
«на себе» ничего бы не доказала: она бы просто не нашла групп.

Корпус повторяет ситуацию BWO в миниатюре (замер HARNESS 14.10.2026):
  * php/Wizard/Files.php … Files7.php — восемь копий одного файла;
  * templates/Files.html ссылается на РАБОЧИЙ Files7.php строкой
    `BWO_FILES_ENDPOINT` и вызовом `fetch(...)`;
  * php/Wizard/upload.php — в CP1251, с русской строкой;
  * cgi-bin/import.pl и import1.pl — копии исполняемого скрипта;
  * index.php — точка входа (читает $_GET), подключает рабочие файлы.

Стиль файла совпадает с остальными проверками проекта: самостоятельный
скрипт с `check`, а не pytest-функции.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ragraf.config import load as load_config  # noqa: E402
from ragraf.facade import Facade  # noqa: E402
from ragraf.indexer import Indexer  # noqa: E402
from ragraf.init_corpus import create_config  # noqa: E402

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


FILES_PHP = """<?php
// Формы файлового мастера BWO.
function bwo_files_list() {
    $fileExtensions = array('MCH', 'MOL', 'CBT');
    echo "Allowed formats";
    return $fileExtensions;
}
"""

UPLOAD_PHP = """<?php
// Загрузка: проверка расширений (файл в CP1251).
$allowed = array('MCH', 'MOL');
$message = "Разрешённые форматы";
echo $message;
"""

TEMPLATE_HTML = """<html><head><title>Files</title></head>
<body>
Allowed formats: MCH, MOL
<script>
var BWO_FILES_ENDPOINT = '/php/Wizard/Files7.php';
fetch('php/Wizard/Files7.php');
</script>
</body></html>
"""

INDEX_PHP = """<?php
$id = $_GET['id'];
include 'php/Wizard/Files7.php';
include 'templates/Files.html';
"""

IMPORT_PL = """#!/usr/bin/perl
our %hFORMAT = (MCH => 1, MOL => 2);
my $FORMAT = 'MCH';
sub import_file { my ($f) = @_; return $hFORMAT{$f}; }
print import_file($FORMAT);
"""


def build_corpus(root: Path) -> None:
    (root / ".gitignore").write_text("probe\n", encoding="utf-8")
    wiz = root / "php" / "Wizard"
    wiz.mkdir(parents=True)
    for i in range(8):
        name = "Files.php" if i == 0 else f"Files{i}.php"
        body = FILES_PHP
        if i:
            # Копии различаются одной строкой-правкой: это семейство, а не клон.
            body = body.replace("function bwo_files_list()",
                                f"function bwo_files_list_v{i}()")
        (wiz / name).write_text(body, encoding="utf-8")
    (wiz / "upload.php").write_bytes(UPLOAD_PHP.encode("cp1251"))
    (root / "templates").mkdir()
    (root / "templates" / "Files.html").write_text(TEMPLATE_HTML, encoding="utf-8")
    (root / "index.php").write_text(INDEX_PHP, encoding="utf-8")
    cgi = root / "cgi-bin"
    cgi.mkdir()
    (cgi / "import.pl").write_text(IMPORT_PL, encoding="utf-8")
    (cgi / "import1.pl").write_text(
        IMPORT_PL.replace("import_file", "import_file_v1"), encoding="utf-8")


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="ragraf_p1_"))
    try:
        build_corpus(tmp)
        cfg_path, _scan = create_config(tmp, project="p1probe",
                                         config_path=tmp / "p1.yaml")
        # CPG (Joern) выключается: проверяются P1-1/P1-2, а не граф свойств кода,
        # и ждать минуту на JVM ради поиска по строкам незачем.
        text = cfg_path.read_text(encoding="utf-8").replace(
            "joern_enabled: true", "joern_enabled: false")
        cfg_path.write_text(text, encoding="utf-8", newline="\n")
        cfg = load_config(cfg_path)
        report = Indexer(cfg).run(with_vectors=False, budget_seconds=120)
        check(bool(report.rev_id) and report.files >= 11,
              "корпус проиндексирован", f"rev={report.rev_id} файлов={report.files}")

        fc = Facade(cfg, tool="test", telemetry=False, watch=False)
        try:
            print("\n=== 1. search_multi: один вызов на несколько запросов ===")
            res = fc.op_search_multi(
                ["Allowed formats", "'MCH', 'MOL'", "Разрешённые форматы"],
                limit=30, scope=None)
            data = res["result"]
            paths = {h["path"] for q in data["queries"] for h in q["hits"]}
            check(res["found"], "поиск нашёл совпадения", json.dumps(
                data["summary"], ensure_ascii=False))
            check(len(data["queries"]) == 3, "ответ содержит все три запроса")
            files_found = {p for p in paths if p.startswith("php/Wizard/Files")}
            check(files_found == {f"php/Wizard/Files{'' if i == 0 else i}.php"
                                  for i in range(8)},
                  "найдены все восемь копий Files*.php",
                  ", ".join(sorted(files_found)))
            check("templates/Files.html" in paths, "найден шаблон с той же строкой")
            upload_hits = [h for q in data["queries"] for h in q["hits"]
                           if h["path"] == "php/Wizard/upload.php"]
            check(bool(upload_hits), "найден файл в CP1251")
            if upload_hits:
                check(upload_hits[0]["encoding"] == "cp1251",
                      "кодировка файла названа в ответе",
                      str(upload_hits[0]["encoding"]))

            print("\n=== 2. Группировка по семействам и активный файл ===")
            families = {f["family"]: f for f in data["families"]}
            fam = families.get("php/Wizard/Files.php")
            check(fam is not None, "семейство Files*.php выделено в группу",
                  ", ".join(sorted(families)))
            if fam:
                check(fam["active"] == "php/Wizard/Files7.php",
                      "активен Files7.php (на него ссылается шаблон)",
                      fam["active"])
                check("php/Wizard/Files.php" in fam["copies"]
                      and "php/Wizard/Files6.php" in fam["copies"],
                      "остальные названы копиями", ", ".join(fam["copies"]))
                active = next(m for m in fam["members"]
                              if m["path"] == "php/Wizard/Files7.php")
                refs = active["referenced_in_text"] + active["evidence"]
                check(any(r["path"] == "templates/Files.html" for r in refs),
                      "ссылка из шаблона названа как доказательство",
                      json.dumps(refs[:2], ensure_ascii=False))
            print("\n=== 3. duplicates: группы копий с доказательством ===")
            dup = fc.op_duplicates(scope="php/Wizard", limit=20)
            groups = dup["result"]["groups"]
            fam_dup = [g for g in groups
                       if any(m["path"].startswith("php/Wizard/Files")
                              for m in g["member_details"])]
            check(bool(fam_dup), "дубликаты нашли семейство Files*.php",
                  f"групп={len(groups)}")
            if fam_dup:
                g = fam_dup[0]
                check(g["primary"] == "php/Wizard/Files7.php",
                      "главный файл семейства — Files7.php", g["primary"])
                check(len(g["members"]) == 8, "в группе восемь файлов",
                      str(len(g["members"])))
                check(any(r["path"] == "templates/Files.html"
                          for r in g["evidence"].get(g["primary"], [])),
                      "активность главного подтверждена ссылкой path:line")
                sims = g.get("similarity") or {}
                check(all(v >= 0.5 for v in sims.values()) if sims else False,
                      "сходство копий посчитано и выше порога",
                      json.dumps(sims, ensure_ascii=False))

            print("\n=== 4. entrypoints: точки входа и достижимость ===")
            ep = fc.op_entrypoints(path="php/Wizard/Files7.php")
            target = ep["result"]["target"]
            check(target["reachable"], "Files7.php достижим от точки входа",
                  json.dumps(target, ensure_ascii=False)[:200])
            check(any(r["path"] == "templates/Files.html"
                      for r in target["referenced_by_text"]),
                  "ссылка на Files7.php найдена в тексте шаблона")
            ep_all = fc.op_entrypoints(limit=50)
            kinds = ep_all["result"]["by_kind"]
            check("http" in kinds, "index.php опознан как HTTP-точка входа",
                  json.dumps(kinds, ensure_ascii=False))
            check("cli" in kinds, "Perl-скрипт с shebang опознан как CLI",
                  json.dumps(kinds, ensure_ascii=False))

            print("\n=== 5. dead_code: копии без ссылок ===")
            dead = fc.op_dead_code(scope="php/Wizard", limit=50)
            dead_paths = {f["path"] for f in dead["result"]["files"]}
            check("php/Wizard/Files.php" in dead_paths
                  and "php/Wizard/Files6.php" in dead_paths,
                  "неподключённые копии названы мёртвыми",
                  ", ".join(sorted(dead_paths)))
            check("php/Wizard/Files7.php" not in dead_paths,
                  "рабочий файл мёртвым не объявлен")
            dead_cgi = fc.op_dead_code(scope="cgi-bin", limit=50)
            cgi_paths = {f["path"] for f in dead_cgi["result"]["files"]}
            check("cgi-bin/import1.pl" in cgi_paths,
                  "копия скрипта без ссылок попала в разбор",
                  ", ".join(sorted(cgi_paths)))
            reasons = {f["path"]: f["reason"] for f in dead_cgi["result"]["files"]}
            check("исполняемый скрипт" in reasons.get("cgi-bin/import1.pl", ""),
                  "причина названа, а не выдумана",
                  reasons.get("cgi-bin/import1.pl", ""))
        finally:
            fc.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    print("=" * 78)
    print(f"ИТОГ: пройдено {PASS}, провалено {FAIL}")
    print("=" * 78)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
