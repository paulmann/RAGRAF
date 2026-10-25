"""Проверка P1-3: полиглот — Perl, шаблоны, SQL вне строк PHP.

Что проверяется и почему именно так.

1. **Perl-переменные.** В BWO настройки импорта живут в пакетных переменных
   (`$FORMAT`, `%hFORMAT`, `%dFORMAT`). До правки их не было в индексе вовсе:
   профиль языка описывал подпрограммы и вызовы, а переменные — нет.
   Проверка: `search("hFORMAT")` находит файл со строкой, `references`
   показывает использования, `symbol` — объявление.

2. **SQL вне строк PHP и в Perl.** Многострочный литерал в PHP — это узлы
   `heredoc`/`nowdoc`, а не `string`; запросы оттуда не попадали ни в рёбра
   к таблицам, ни в `table_usage`. В Perl запись идёт через `<<'SQL'` и
   `$dbh->do(...)`. Проверка: `table_usage(..., mode="write")` находит
   оба загрузчика, а не пустоту.

3. **Шаблон ↔ ключ перевода.** Один и тот же ключ встречается как
   `%{reg.months}` в шаблоне и как `Translate('reg.months')` в PHP; это
   ДОЛЖЕН быть один узел графа, иначе вопрос «где используется ключ»
   распадается на два.

4. **Точки входа в реестре.** Таблица `web_route` до этой правки не
   заполнялась ни одной вставкой, то есть операция `routes` всегда отвечала
   пусто. Проверка: маршрут из кода и адрес из разметки видны, и вид у них
   разный (`route` против `endpoint`).

Стиль — самостоятельный скрипт с `check`, как остальные проверки проекта.
"""

from __future__ import annotations

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


IMPORT_PL = """#!/usr/bin/perl
our %hFORMAT = (MCH => 1, MOL => 2);
our %dFORMAT = ();
my $FORMAT = 'MCH';
sub import_file {
    my ($file) = @_;
    return $hFORMAT{$FORMAT};
}
my $sql = <<'SQL';
INSERT INTO audit_log (action, created) VALUES ('import', NOW())
SQL
$dbh->do("UPDATE orders SET status = 'done' WHERE id = 1");
print $dFORMAT{$FORMAT};
"""

BCTC_PHP = """<?php
function bctc_import($rows) {
    $sql = <<<SQL
INSERT INTO StringData (CellVoltage, Power, `Watt-hours`)
VALUES (1, 2, 3)
SQL;
    echo Translate('reg.months');
    return $sql;
}
"""

BWO_PHP = """<?php
function bwo_import($rows) {
    $sql = <<<'SQL'
INSERT INTO StringData (CellVoltage) VALUES (4)
SQL;
    return $sql;
}
"""

API_PHP = """<?php
Route::post('/api/save', function () { return 'ok'; });
$id = $_POST['id'];
echo $id;
"""

TEMPLATE = """<html><body>
<form action="php/BWO_Import.php" method="post">
  <span>%{reg.months}</span>
  <span>@{reg.days}</span>
</form>
<script src="js/import.js"></script>
</body></html>
"""

JS = """fetch('php/BWO_Import.php');
$(document).ready(function () { console.log('ready'); });
"""


def build_corpus(root: Path) -> None:
    (root / ".gitignore").write_text("probe\n", encoding="utf-8")
    (root / "cgi-bin").mkdir()
    (root / "cgi-bin" / "import.pl").write_text(IMPORT_PL, encoding="utf-8")
    (root / "php").mkdir()
    (root / "php" / "BCTC_Import.php").write_text(BCTC_PHP, encoding="utf-8")
    (root / "php" / "BWO_Import.php").write_text(BWO_PHP, encoding="utf-8")
    (root / "php" / "api.php").write_text(API_PHP, encoding="utf-8")
    (root / "templates").mkdir()
    (root / "templates" / "Files.html").write_text(TEMPLATE, encoding="utf-8")
    (root / "js").mkdir()
    (root / "js" / "import.js").write_text(JS, encoding="utf-8")


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="ragraf_p13_"))
    try:
        build_corpus(tmp)
        cfg_path, _scan = create_config(tmp, project="p13probe",
                                         config_path=tmp / "p13.yaml")
        text = cfg_path.read_text(encoding="utf-8").replace(
            "joern_enabled: true", "joern_enabled: false")
        cfg_path.write_text(text, encoding="utf-8", newline="\n")
        cfg = load_config(cfg_path)
        report = Indexer(cfg).run(with_vectors=False, budget_seconds=120)
        check(bool(report.rev_id) and report.files >= 6,
              "корпус проиндексирован", f"rev={report.rev_id} файлов={report.files}")

        fc = Facade(cfg, tool="test", telemetry=False, watch=False)
        try:
            print("\n=== 1. Perl: пакетная переменная в индексе ===")
            found = fc.op_search("hFORMAT")
            paths = {h["path"] for h in found["result"]}
            check("cgi-bin/import.pl" in paths,
                  "поиск по имени переменной нашёл Perl-файл",
                  ", ".join(sorted(paths))[:160])
            sym = fc.op_symbol("%hFORMAT")
            sym_paths = {s.get("path") for s in sym["result"]}
            check("cgi-bin/import.pl" in sym_paths,
                  "объявление %hFORMAT есть как символ",
                  str(sym["result"])[:200])
            refs = fc.op_references("perl:%hFORMAT")
            ref_paths = {r.get("from", {}).get("path") for r in refs["result"]}
            check("cgi-bin/import.pl" in ref_paths,
                  "использование %hFORMAT видно через references",
                  str(refs["result"])[:200])
            refs_d = fc.op_references("perl:%dFORMAT")
            check(refs_d["found"], "использование %dFORMAT найдено",
                  str(refs_d.get("result"))[:160])

            print("\n=== 2. SQL вне строк PHP и в Perl ===")
            w = fc.op_table_usage("StringData", mode="write")
            w_paths = {r["path"] for r in w["result"]["references"]}
            check({"php/BCTC_Import.php", "php/BWO_Import.php"} <= w_paths,
                  "heredoc и nowdoc дали запись в StringData",
                  ", ".join(sorted(w_paths)))
            check(w["result"]["write_count"] >= 2, "оба обращения помечены записью",
                  str(w["result"]["write_count"]))
            audit = fc.op_table_usage("audit_log", mode="write")
            audit_paths = {r["path"] for r in audit["result"]["references"]}
            check("cgi-bin/import.pl" in audit_paths,
                  "SQL из heredoc Perl найден",
                  ", ".join(sorted(audit_paths)))
            orders = fc.op_table_usage("orders", mode="write")
            orders_paths = {r["path"] for r in orders["result"]["references"]}
            check("cgi-bin/import.pl" in orders_paths,
                  "$dbh->do(...) найден как запись",
                  ", ".join(sorted(orders_paths)))

            print("\n=== 3. Шаблон и ключ перевода — один узел ===")
            keys = fc.op_references("tpl:reg.months")
            key_paths = {r.get("from", {}).get("path") for r in keys["result"]}
            check({"templates/Files.html", "php/BCTC_Import.php"} <= key_paths,
                  "ключ reg.months найден и в шаблоне, и в коде",
                  ", ".join(sorted(str(p) for p in key_paths)))
            skey = fc.op_symbol("reg.months")
            check(bool(skey["result"]), "ключ перевода есть как символ",
                  str(skey["result"])[:160])
            skey2 = fc.op_symbol("reg.days")
            check(bool(skey2["result"]), "плейсхолдер @{reg.days} тоже в индексе",
                  str(skey2["result"])[:160])

            print("\n=== 4. Реестр точек входа заполнен ===")
            rts = fc.op_routes(limit=100)
            check(rts["found"], "реестр маршрутов не пуст",
                  "корпус построен без разбора маршрутов" if not rts["found"] else "")
            kinds = rts.get("by_kind", {})
            check(kinds.get("route", 0) >= 1, "маршрут из кода имеет вид route",
                  str(kinds))
            check(kinds.get("endpoint", 0) >= 1, "ссылка разметки имеет вид endpoint",
                  str(kinds))
            routes = {(r["route"], r["kind"]) for r in rts["result"]}
            check(("/api/save", "route") in routes, "маршрут Route::post найден",
                  str(sorted(routes))[:200])
            check(any(r[0].endswith("BWO_Import.php") and r[1] == "endpoint"
                      for r in routes),
                  "action= и fetch(...) дали endpoint",
                  str(sorted(routes))[:200])
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
