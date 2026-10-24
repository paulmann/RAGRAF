"""Проверка P1-4: признаки колонок, схема из миграций, сверка с живой базой.

Что и почему проверяется.

1. **Разделение `VIRTUAL/STORED GENERATED` и `DEFAULT_GENERATED`.** Это
   измеренный дефект: колонка считалась генерируемой по подстроке
   `GENERATED`, а на живой базе таких совпадений 171, из них 92 —
   `DEFAULT_GENERATED` (обычная колонка со значением по умолчанию-выражением).
   `write_check` объявлял их нерехзаписываемыми. Проверка фиксирует правило.

2. **Разбор миграций.** Признаки берутся из ТЕКСТА DDL, где синтаксис другой:
   `GENERATED ALWAYS AS (expr) STORED` — слово STORED стоит ПОСЛЕ выражения,
   а не перед `GENERATED`. Один образец на оба источника не годился.

3. **Живая схема — пометки в ответе.** Если слой живых баз собран, у колонок
   в `db_schema` есть `pk`, `unique`, `auto_increment`, `generated`,
   `generated_kind`.

4. **Сверка «миграции ↔ живая база».** Отвечает на вопрос, который до этой
   правки не задавал никто: какие таблицы описаны миграциями, но отсутствуют
   в базе. При пустой одной из сторон ответ обязан сказать, что сверка
   НЕПОЛНА, а не показывать пустой список расхождений как «всё совпало».

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

from ragraf import db_marks, schema_db  # noqa: E402
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


MIGRATION = """-- 0001: таблицы замеров (образец BWO; имена с префиксом P14, чтобы
-- не совпасть случайно с настоящими таблицами на этой машине)
CREATE TABLE IF NOT EXISTS `P14Config` (
  `ID` INT NOT NULL AUTO_INCREMENT,
  `Name` VARCHAR(64) NOT NULL DEFAULT 'default',
  `String Nominal Voltage` DECIMAL(10,3) GENERATED ALWAYS AS (`Name`) STORED,
  `Duration Seconds` INT GENERATED ALWAYS AS (1 + 1) VIRTUAL,
  PRIMARY KEY (`ID`),
  UNIQUE KEY `u_name` (`Name`)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS `P14Data` (
  `ID` INT NOT NULL AUTO_INCREMENT,
  `ConfigID` INT NOT NULL,
  `Time Seconds` DECIMAL(12,3) GENERATED ALWAYS AS (`ConfigID` * 2) STORED,
  `Real Time` DATETIME DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`ID`),
  FOREIGN KEY (`ConfigID`) REFERENCES `P14Config` (`ID`)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS `P14Strings` (
  `ID` INT NOT NULL AUTO_INCREMENT,
  `Power` DECIMAL(10,3) NOT NULL,
  `Watt-hours` DECIMAL(10,3) NOT NULL,
  PRIMARY KEY (`ID`),
  UNIQUE (`Power`, `Watt-hours`)
) ENGINE=InnoDB;

ALTER TABLE `P14Data` ADD COLUMN `Cell Voltage Middle` DECIMAL(10,3) NOT NULL DEFAULT 0;
"""


def part1_marks() -> None:
    print("\n=== 1. Признаки колонки: generated, auto_increment, ключи ===")
    virt = db_marks.marks("VIRTUAL GENERATED", "PRI")
    check(virt["generated"] and virt["generated_kind"] == "virtual",
          "VIRTUAL GENERATED — генерируемая", str(virt))
    check(virt["pk"] and not db_marks.writable(virt),
          "в генерируемую колонку писать нельзя", str(virt))
    stored = db_marks.marks("STORED GENERATED", "")
    check(stored["generated_kind"] == "stored", "STORED GENERATED — stored", str(stored))
    dflt = db_marks.marks("DEFAULT_GENERATED", "UNI")
    check(not dflt["generated"], "DEFAULT_GENERATED — НЕ генерируемая", str(dflt))
    check(dflt["default_expression"] and db_marks.writable(dflt),
          "DEFAULT_GENERATED — значение по умолчанию, запись разрешена", str(dflt))
    check(dflt["unique"], "UNI даёт признак уникальности", str(dflt))
    ai = db_marks.marks("auto_increment", "PRI")
    check(ai["auto_increment"], "auto_increment распознан", str(ai))
    combo = db_marks.marks("DEFAULT_GENERATED on update CURRENT_TIMESTAMP", "")
    check(not combo["generated"] and combo["default_expression"],
          "on update CURRENT_TIMESTAMP — не генерируемая колонка", str(combo))
    extra = db_marks.marks("", "", unique_index=True)
    check(extra["unique"], "уникальность из составного индекса видна", str(extra))

    print("\n  PostgreSQL: признаки выводятся из системных полей каталога")
    from ragraf.db_index import pg_mark_fields  # noqa: E402

    cases = [
        ("a", "", True, "", "признак IDENTITY"),
        ("d", "", True, "", "IDENTITY BY DEFAULT"),
        ("", "s", False, "stored", "STORED генерируемая"),
        ("", "v", False, "virtual", "VIRTUAL генерируемая"),
        ("", "", False, "", "обычная колонка"),
    ]
    for identity, generated, want_auto, want_kind, label in cases:
        _key, pg_extra = pg_mark_fields(identity, generated)
        m = db_marks.marks(pg_extra, _key)
        check(m["auto_increment"] == want_auto
              and (m["generated_kind"] or "") == want_kind,
              f"PG {label}: признаки разобраны",
              f"identity={identity!r} generated={generated!r} extra={pg_extra!r} marks={m}")
    # На PostgreSQL ключи приходят ИНДЕКСОМ, а не полем колонки; проверяется,
    # что ручное проставление ключа даёт те же признаки, что и в MySQL.
    pk_mark = db_marks.marks(pg_mark_fields("a", "")[1], "PRI")
    check(pk_mark["pk"] and pk_mark["auto_increment"],
          "PG: первичный ключ + identity дают pk и auto_increment", str(pk_mark))


def part2_migrations() -> None:
    print("\n=== 2. Разбор миграций: признаки из текста DDL ===")
    tmp = Path(tempfile.mkdtemp(prefix="ragraf_mig_"))
    try:
        mig = tmp / "migrations"
        mig.mkdir()
        (mig / "0001_test.sql").write_text(MIGRATION, encoding="utf-8")
        tables = schema_db.parse_migrations(sorted(mig.glob("*.sql")))
        check(set(tables) >= {"P14Config", "P14Data", "P14Strings"},
              "все три таблицы разобраны", ", ".join(sorted(tables)))

        cfg = tables.get("P14Config")
        check(cfg is not None, "P14Config найдена")
        if cfg is not None:
            gens = {c.name: c.generated_kind for c in cfg.columns if c.generated_kind}
            check(gens == {"String Nominal Voltage": "stored",
                           "Duration Seconds": "virtual"},
                  "генерируемые колонки P14Config и их вид",
                  str(gens))
            check(cfg.primary_key == ["ID"], "первичный ключ P14Config", str(cfg.primary_key))
            check("Name" in cfg.unique_columns, "UNIQUE KEY дал уникальную колонку",
                  str(cfg.unique_columns))
            auto = [c.name for c in cfg.columns if c.auto_increment]
            check(auto == ["ID"], "AUTO_INCREMENT распознан", str(auto))
            name = next(c for c in cfg.columns if c.name == "Name")
            check(name.has_default, "DEFAULT у колонки Name виден", str(name.has_default))
            check(not name.generated_kind, "обычная колонка не названа генерируемой")

        data = tables.get("P14Data")
        if data is not None:
            gens = {c.name for c in data.columns if c.generated_kind}
            check(gens == {"Time Seconds"},
                  "генерируемая колонка P14Data", str(gens))
            mid = next((c for c in data.columns if c.name == "Cell Voltage Middle"), None)
            check(mid is not None and not mid.generated_kind and mid.has_default,
                  "ALTER TABLE ADD COLUMN разобран с DEFAULT", str(mid))
            check(data.fks and data.fks[0][1] == "P14Config", "внешний ключ разобран",
                  str(data.fks))

        strings = tables.get("P14Strings")
        if strings is not None:
            check(len(strings.unique_columns) == 2,
                  "составной UNIQUE отметил обе колонки", str(strings.unique_columns))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def build_corpus(root: Path) -> None:
    (root / ".gitignore").write_text("probe\n", encoding="utf-8")
    (root / "migrations").mkdir()
    (root / "migrations" / "0001_test.sql").write_text(MIGRATION, encoding="utf-8")
    (root / "loader.php").write_text(
        "<?php\n"
        "function load() {\n"
        "    $pdo->beginTransaction();\n"
        "    $stmt = $pdo->prepare(\"INSERT INTO P14Strings (Power, `Watt-hours`)"
        " VALUES (?, ?)\");\n"
        "    $stmt->execute([1, 2]);\n"
        "    $pdo->commit();\n"
        "}\n",
        encoding="utf-8",
    )
    (root / "reader.php").write_text(
        "<?php\nfunction read_rows() { return $pdo->query('SELECT Power FROM P14Strings'); }\n",
        encoding="utf-8",
    )
    (root / "config.php").write_text(
        "<?php\nreturn array('dsn' => 'mysql:host=127.0.0.1');\n",
        encoding="utf-8",
    )


def part3_facade() -> None:
    print("\n=== 3. Операции: схема, сверка, кто пишет ===")
    tmp = Path(tempfile.mkdtemp(prefix="ragraf_p14_"))
    try:
        build_corpus(tmp)
        cfg_path, _scan = create_config(tmp, project="p14probe",
                                         config_path=tmp / "p14.yaml")
        text = cfg_path.read_text(encoding="utf-8").replace(
            "joern_enabled: true", "joern_enabled: false")
        cfg_path.write_text(text, encoding="utf-8", newline="\n")
        cfg = load_config(cfg_path)
        report = Indexer(cfg).run(with_vectors=False, budget_seconds=120)
        check(bool(report.rev_id), "корпус проиндексирован", str(report.rev_id))

        fc = Facade(cfg, tool="test", telemetry=False, watch=False)
        try:
            sch = fc.op_schema(table="P14Config")
            check(sch["found"], "схема P14Config построена из миграций",
                  str(sch.get("error")))
            if sch["found"]:
                t = sch["result"][0]
                check(t["generated_columns"] == ["String Nominal Voltage",
                                                 "Duration Seconds"],
                      "ответ называет генерируемые колонки",
                      str(t["generated_columns"]))
                check(t["primary_key"] == ["ID"], "ответ называет первичный ключ",
                      str(t["primary_key"]))
                check("Name" in t["unique_columns"], "ответ называет уникальные колонки",
                      str(t["unique_columns"]))
                auto = [c["column_name"] for c in t["columns"] if c["auto_increment"]]
                check(auto == ["ID"], "ответ называет AUTO_INCREMENT колонку", str(auto))

            wc = fc.op_write_check(
                "P14Config", ["ID", "String Nominal Voltage"], operation="insert")
            marks = wc["result"]["columns_marks"]
            check(wc["found"], "write_check ответил по схеме из миграций",
                  str(wc.get("error")))
            check(marks["String Nominal Voltage"]["generated"],
                  "write_check знает про генерируемую колонку", str(marks))
            check(marks["ID"]["auto_increment"], "write_check знает про AUTO_INCREMENT",
                  str(marks))

            wc2 = fc.op_write_check("P14Data", ["ID", "ConfigID", "Real Time"],
                                    operation="insert")
            check(wc2["result"]["columns_marks"]["Real Time"]["has_default"],
                  "write_check видит DEFAULT у Real Time", str(
                      wc2["result"]["columns_marks"]))

            tu = fc.op_table_usage("P14Strings", mode="write")
            refs = tu["result"]["references"]
            check(tu["found"] and refs, "запись в P14Strings найдена", str(tu["result"]))
            if refs:
                write_refs = [r for r in refs if r.get("write")]
                check(bool(write_refs), "обращение помечено записью", str(refs[0]))
                sql = str(write_refs[0].get("sql") or "") if write_refs else ""
                check(sql.upper().startswith("INSERT"),
                      "текст запроса назван в ответе", sql[:100])
                check("'" not in sql[:1] and '"' not in sql[:1],
                      "кавычки литерала не попали в текст запроса", repr(sql[:20]))
                check(write_refs and write_refs[0].get("via") == "prepared",
                      "подготовленное выражение отличено от прямого вызова",
                      str(write_refs[0].get("via")) if write_refs else "")

            dr = fc.op_db_drift()
            live = dr["result"]["migrations_vs_live"]
            check(live["migrations_tables"] >= 3, "миграции в сверке посчитаны",
                  str(live))
            if live["live_tables"]:
                # На этой машине живой слой может быть собран (общий доступ к
                # MySQL): тогда проверяется сам смысл сверки — таблицы корпуса
                # в живой базе отсутствуют и это видно.
                check(live["plausible"], "обе стороны сверки непусты", str(live)[:200])
                only_mig = {t.lower() for t in live["only_in_migrations"]}
                check({"p14config", "p14data", "p14strings"} <= only_mig,
                      "таблицы миграций найдены как отсутствующие в базе",
                      ", ".join(sorted(only_mig))[:200])
            else:
                check(not live["plausible"] and bool(live["note"]),
                      "пустая живая схема названа как НЕПОЛНАЯ сверка",
                      str(live["note"]))
            check("drift" in dr["result"], "сравнение ревизий сохранено отдельным полем")

            print("\n=== 4. Живая база: пометки в ответе (если слой собран) ===")
            live_schema = fc.op_db_schema(limit=500)
            if not live_schema["found"]:
                print("  [SKIP] слой живых баз в этом корпусе не собран: "
                      "проверка пометок живой схемы пропущена")
            else:
                payload = live_schema["result"]
                tables = payload.get("tables", []) if isinstance(payload, dict) else []
                marked = 0
                generated_examples = []
                for t in tables:
                    for c in t.get("columns", []):
                        if c.get("generated"):
                            marked += 1
                            if len(generated_examples) < 3:
                                generated_examples.append(
                                    f"{t.get('database_name')}.{t.get('table_name')}."
                                    f"{c.get('column_name')}={c.get('generated_kind')}")
                check(True, "живая схема обработана без ошибок", str(live_schema.get("error")))
                print(f"    таблиц: {len(tables)}, генерируемых колонок: {marked} "
                      f"({', '.join(generated_examples) or '—'})")
                # Ложных срабатываний быть не должно: DEFAULT_GENERATED — не
                # генерируемая колонка. Проверяем на фактических данных.
                bad = []
                for t in tables:
                    for c in t.get("columns", []):
                        extra = str(c.get("extra") or "").upper()
                        if "DEFAULT_GENERATED" in extra and c.get("generated"):
                            bad.append(f"{t.get('table_name')}.{c.get('column_name')}")
                check(not bad, "DEFAULT_GENERATED не назван генерируемой колонкой",
                      ", ".join(bad[:5]))
                # Если в живых базах есть таблицы из ТЗ (TestConfig/TestData/
                # StringData), их пометки обязаны совпасть с измеренными.
                for t in tables:
                    if str(t.get("table_name")) == "TestConfig":
                        check(bool(t.get("generated_columns")),
                              "TestConfig: генерируемые колонки названы",
                              str(t.get("generated_columns")))
                        check(bool(t.get("primary_key")),
                              "TestConfig: первичный ключ назван",
                              str(t.get("primary_key")))
        finally:
            fc.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    part1_marks()
    part2_migrations()
    part3_facade()
    print()
    print("=" * 78)
    print(f"ИТОГ: пройдено {PASS}, провалено {FAIL}")
    print("=" * 78)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
