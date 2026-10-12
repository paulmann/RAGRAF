"""Разрешение имён вызовов из графа свойств кода (CPG).

Стиль как у остальных проверок проекта: самостоятельный скрипт с функцией
`check`, запускается напрямую (`python tests/test_cpg_call_names.py`).

Какой дефект закрывается. Обходной путь экспорта (запросы к файлу CPG вместо
отдельного `joern-export`) не давал НИ ОДНОГО ребра: из 131 929 записей
подтверждалось 0. Причин оказалось три, и каждая ловится здесь по отдельности:

  1. Имя метода в резолвере извлекалось через `rpartition(".")` от имени вида
     `<module>.Telemetry.summary` — получалось `Tel` вместо `summary`.
     Владелец не разрешался ни разу.
  2. Цель вызова приходит заглушкой `<unknownFullName>` (так у 10 156 записей из
     10 500): резолвер пытался разрешить заглушку и отбрасывал запись, хотя
     настоящее имя вызова есть в поле `callee_name`.
  3. Имя вызова читалось только под ключом `callee_name`, а обходной путь пишет
     `calleeName` — поэтому имя оказывалось пустым и отбрасывались ВСЕ записи.

Плюс отдельное требование к честности: ребро по имени вызова (а не по полному
имени метода) обязано быть помечено слабее. Полное имя метода — доказательство
(`calls`/`stated`, 0.9); имя вызова — догадка, допускающая несколько кандидатов,
поэтому вид `calls_heuristic`, происхождение `derived`, достоверность не выше 0.6.

Ничего не меняет: работает в памяти и на одноразовом хранилище.
"""

from __future__ import annotations

import sqlite3
import sys
import tempfile
from pathlib import Path


# Отчёт печатается по-русски, а консоль Windows по умолчанию не UTF-8: без этой
# настройки запуск падал UnicodeEncodeError ('charmap' codec can't encode),
# причём только на Windows и только вне UTF-8-консоли — в CI это валило job.
# Проверено: PYTHONIOENCODING=cp1252 -> exit 1. Настройка делает тест
# независимым от локали/консоли (Python 3.7+: TextIOWrapper.reconfigure).
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ragraf import cpg as cpg_mod  # noqa: E402
from ragraf.joern_export import normalize_joern_name  # noqa: E402

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


GRAPH_DDL = """
CREATE TABLE node (rev_id TEXT, node_id TEXT, kind TEXT, name TEXT, fqn TEXT,
                   path TEXT, start_line INT, end_line INT, signature TEXT,
                   props TEXT, layer TEXT);
CREATE TABLE edge (rev_id TEXT, edge_uid TEXT, src TEXT, dst TEXT, kind TEXT,
                   provenance TEXT, evidence_path TEXT, evidence_line INT,
                   confidence REAL, sources TEXT, props TEXT);
"""


class _Cfg:
    """Стенд конфигурации: merge_calls обращается только к этим полям."""

    class _Index:
        batch_size = 100

    index = _Index()
    graph_db = None  # подменяется параметром graph_db при вызове

    @property
    def root(self):  # noqa: D102
        import tempfile
        from pathlib import Path as _P
        return _P(tempfile.gettempdir())


def main() -> int:
    print("=== 1. Имя метода извлекается из имени владельца (настоящим резолвером) ===")
    # Берётся НАСТОЯЩИЙ резолвер из Indexer, а не его копия в тесте. Копия уже
    # однажды разошлась с кодом: в тесте было исправленное правило извлечения, а
    # в проверяемом модуле — нет, и тест этого не заметил бы.
    tmp = Path(tempfile.mkdtemp(prefix="cpg_names_"))
    try:
        db = tmp / "graph.sqlite"
        conn = sqlite3.connect(db)
        conn.executescript(GRAPH_DDL)
        # Имена узлов — как в настоящем индексе: fqn = «путь:имя».
        for nid, name in (("n1", "summary"), ("n2", "_quality"), ("n3", "_cached"),
                          ("n4", "get"), ("n5", "helper"), ("n6", "run")):
            path = "p.py" if name != "_quality" else "src/ragraf/facade.py"
            conn.execute(
                "INSERT INTO node VALUES ('rev-0001',?,?,?,?,?,1,9,'','','src')",
                (nid, "method" if name in ("run", "_quality") else "function", name,
                 f"{path}:{name}", path),
            )
        conn.commit()
        conn.close()

        from ragraf.config import load as load_config  # noqa: PLC0415
        from ragraf.indexer import Indexer  # noqa: PLC0415

        # Конфигурация берётся загрузчиком с ГЛАВНОГО конфига проекта, но пути
        # хранилищ подменяются на одноразовый: собирать ProjectConfig вручную
        # нельзя (у него десять обязательных полей, и тест ломался бы на каждом
        # их изменении).
        cfg = load_config(REPO / "config" / "ragraf.yaml")
        cfg.root = tmp
        cfg.config_path = tmp / "probe.yaml"

        class _Probe(Indexer):
            """Резолвер настоящего кода, но на одноразовом хранилище."""

            def __init__(self, cfg, path):  # noqa: D107
                self.cfg = cfg
                self.graph = type("G", (), {"conn": sqlite3.connect(path)})()

        probe = _Probe(cfg, db)
        resolve = probe._make_fqn_resolver("rev-0001")

        for owner, path, expected in (
            ("src/ragraf/telemetry.py:<module>.Telemetry.summary", "", "summary"),
            ("src/ragraf/facade.py:<module>.Facade._quality", "src/ragraf/facade.py", "_quality"),
            ("src/ragraf/admin_server.py:<module>._cached", "", "_cached"),
            ("App\\Core\\Config::get", "", "get"),
        ):
            norm = normalize_joern_name(owner)
            got, conf, how = resolve(norm, path)
            check(bool(got), f"резолвер нашёл {expected} по имени владельца {owner.split(':', 1)[-1]}",
                  f"получено {got!r} ({how})")

        print("\n=== 2. Имя вызова читается под обоими ключами ===")
        # Используется ТОТ ЖЕ резолвер, что и в рабочем коде (`resolve` выше).
        # Своя копия логики в тесте — уже пройденная ошибка: копия разошлась с
        # кодом, и проверка перестала что-либо доказывать.
        def resolve_for_merge(name, path, mode="src"):
            return resolve(name, path, mode)

        # Запись ровно того вида, какой пишет обходной путь: ключ `calleeName`.
        fallback_style = [{
            "owner_method": "p.py:<module>.run",
            "owner_file": "p.py",
            "callee": "<unknownFullName>",
            "calleeName": "helper",
            "line": 12,
        }]
        stats = cpg_mod.merge_calls(_Cfg(), "rev-0001", fallback_style, resolve_for_merge, graph_db=db)
        check(int(stats.get("added", 0)) == 1,
              "запись с calleeName разрешилась (ключ обходного пути)",
              f"stats={stats}")

        conn = sqlite3.connect(db); conn.execute("DELETE FROM edge"); conn.commit(); conn.close()
        csv_style = [{
            "owner_method": "p.py:<module>.run",
            "owner_file": "p.py",
            "callee": "<unknownFullName>",
            "callee_name": "helper",
            "line": 12,
        }]
        stats2 = cpg_mod.merge_calls(_Cfg(), "rev-0001", csv_style, resolve_for_merge, graph_db=db)
        check(int(stats2.get("added", 0)) == 1,
              "запись с callee_name разрешилась (ключ CSV-экспорта)",
              f"stats={stats2}")

        print("\n=== 3. Ребро по имени помечено слабее полного имени ===")
        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT kind, provenance, confidence FROM edge").fetchone()
        if row is None:
            check(False, "ребро записано")
        else:
            check(str(row["kind"]) == "calls_heuristic",
                  "вид ребра — эвристика, а не доказанный вызов", str(dict(row)))
            check(str(row["provenance"]) == "derived",
                  "происхождение — выведенное, а не stated", str(dict(row)))
            check(float(row["confidence"]) <= 0.6,
                  "достоверность не выше 0,6", str(row["confidence"]))
        conn.close()

        print("\n=== 4. Заглушка без имени не выдумывает цель ===")
        conn = sqlite3.connect(db); conn.execute("DELETE FROM edge"); conn.commit(); conn.close()
        bare = [{
            "owner_method": "p.py:<module>.run",
            "owner_file": "p.py",
            "callee": "<unknownFullName>",
            "calleeName": "<unknownFullName>",
            "line": 12,
        }]
        stats3 = cpg_mod.merge_calls(_Cfg(), "rev-0001", bare, resolve_for_merge, graph_db=db)
        check(int(stats3.get("added", 0)) == 0,
              "без имени вызова цель не выдумывается", f"stats={stats3}")
        check(int(stats3.get("unresolved", 0)) == 1,
              "такая запись посчитана как неразрешённая", f"stats={stats3}")

        print("\n=== 5. Полное имя метода по-прежнему доказательство ===")
        conn = sqlite3.connect(db); conn.execute("DELETE FROM edge"); conn.commit(); conn.close()
        strong = [{
            "owner_method": "p.py:<module>.run",
            "owner_file": "p.py",
            "callee": "p.py:helper",
            "calleeName": "helper",
            "line": 12,
        }]
        stats4 = cpg_mod.merge_calls(_Cfg(), "rev-0001", strong, resolve_for_merge, graph_db=db)
        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row
        row4 = conn.execute("SELECT kind, provenance, confidence FROM edge").fetchone()
        conn.close()
        check(int(stats4.get("added", 0)) == 1, "запись с полным именем разрешилась", f"stats={stats4}")
        if row4 is not None:
            check(str(row4["kind"]) == "calls" and str(row4["provenance"]) == "stated",
                  "полное имя даёт доказанное ребро", str(dict(row4)))
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n=== 6. Разбор формы вызова по полю `code` (все языки) ===")
    # Это и есть разделение «свой метод» / «чужой вызов» / «чтение поля».
    # Формы разных языков проверяются вместе: PHP выражает свой объект через
    # `$this->`, Perl — через `$self->`, и без этих образцов путь привязки к
    # классу для соответствующего корпуса недостижим вообще.
    for code, want, why in (
        ('self.send_header("X", "y")', "own_method", "python: вызов своего метода"),
        ("cls.build()", "own_method", "python: вызов через класс"),
        ("$this->sendHeader('X')", "own_method", "php: $this-> — свой объект"),
        ("self::create()", "own_method", "php: self::"),
        ("static::create()", "own_method", "php: static::"),
        ("parent::__construct()", "parent_method", "php: parent:: — явно база"),
        ("$self->run()", "own_method", "perl: $self-> — свой объект"),
        ("$file->isFile()", "other", "php: ЧУЖАЯ переменная — не свой метод"),
        ("$iter_tmp2->current()", "other", "php: чужая переменная (дефект: давал OWN)"),
        ("$stmt->execute()", "other", "php: чужая переменная (PDO)"),
        ("self.wfile", "field_access", "python: чтение атрибута"),
        ("self.send_header", "field_access", "python: без скобки — чтение"),
        ("$this->name", "field_access", "php: чтение свойства"),
        ("self.counter = 0", "field_access", "присваивание полю"),
        ("self.conn.execute(x)", "other", "приёмник чужой"),
        ("$this->db->query(q)", "other", "php: цепочка через поле"),
        ("other.run()", "other", "чужой объект"),
        ("print(x)", "other", "не через self/cls"),
        ("", "other", "пустая строка"),
    ):
        got, name = cpg_mod.call_shape(code)
        check(got == want, f"{why}: {code!r}", f"получено {got!r}")
        if want in ("own_method", "parent_method"):
            check(bool(name), f"имя метода извлечено: {code!r}", repr(name))

    print("\n=== 7. Класс владельца берётся только у метода в классе ===")
    for owner, want in (
        ("src/ragraf/admin_server.py:<module>.Handler.do_GET", "Handler"),
        ("src/ragraf/catalog.py:<module>.Catalog.record_run", "Catalog"),
        ("src/ragraf/cli.py:<module>.main", None),
        ("App\\Core\\Config::set", None),
        ("", None),
    ):
        got = cpg_mod.owner_class(owner, None)
        check(got == want, f"класс из {owner or '(пусто)'}", f"получено {got!r}")

    print("\n=== 8. Чтение поля не попадает в фильтр вызовов ===")
    noise = [
        {"owner_method": "p.py:<module>.C.run", "owner_file": "p.py",
         "callee": "<unknownFullName>", "calleeName": "wfile", "code": "self.wfile", "line": 5},
        {"owner_method": "p.py:<module>.C.run", "owner_file": "p.py",
         "callee": "<unknownFullName>", "calleeName": "send_header",
         "code": "self.send_header", "line": 6},
    ]
    check(cpg_mod.parse_calls_export(noise) == [],
          "чтение атрибутов отброшено фильтром",
          str(cpg_mod.parse_calls_export(noise)))
    real = [{"owner_method": "p.py:<module>.C.run", "owner_file": "p.py",
             "callee": "<unknownFullName>", "calleeName": "helper",
             "code": "self.helper()", "line": 7}]
    check(len(cpg_mod.parse_calls_export(real)) == 1,
          "настоящий вызов своего метода фильтр пропускает")
    foreign = [{"owner_method": "p.py:<module>.C.run", "owner_file": "p.py",
                "callee": "<unknownFullName>", "calleeName": "execute",
                "code": "self.conn.execute(q)", "line": 8}]
    check(len(cpg_mod.parse_calls_export(foreign)) == 1,
          "вызов через поле другого объекта НЕ выбрасывается")

    print("\n=== ИТОГ ===")
    print(f"пройдено: {PASS}, провалено: {FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
