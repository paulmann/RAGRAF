"""Извлечение и разрешение наследования классов.

Стиль как у остальных проверок проекта: самостоятельный скрипт с функцией
`check`, запускается напрямую (`python tests/test_inheritance.py`).

Зачем эта проверка. Наследование — это факт, по которому агент судит о том, какие
методы вообще существуют у объекта. До этой работы рёбра `extends` существовали
только у PHP-извлекателя, да и там **все** вели на заглушки `@ext:…` (замерено на
revoap: 29 из 29, ни одного настоящего класса). То есть сведения о наследовании
в графе были, но их нельзя было использовать.

Проверяется две половины работы:

  1. ИЗВЛЕЧЕНИЕ — из объявления класса берутся имена базовых классов, и только
     они: без ключевых слов (`extends`, `public`, `implements`) и без имени
     самого класса. Пробуется восемь языков: у каждого своя форма записи и свой
     узел грамматики, и ошибка в одном не видна на остальных.

  2. РАЗРЕШЕНИЕ — ребро с заглушки переносится на настоящий класс, когда он
     единственный по имени, и **остаётся заглушкой**, когда кандидатов несколько
     или ноль. Второе важнее первого: выдуманное наследование опаснее отсутствия.

Ничего не меняет: работает в памяти на строках исходников.
"""

from __future__ import annotations

import sqlite3
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ragraf.extract.generic import PROFILES, GenericExtractor  # noqa: E402

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


# Язык -> (исходник, имя файла, ожидаемые имена баз)
CASES = {
    "python": (b"class Child(Base, Mixin):\n    def m(self):\n        pass\n", "t.py", {"Base", "Mixin"}),
    "javascript": (b"class Child extends Base {}\n", "t.js", {"Base"}),
    "typescript": (b"class Child extends Base implements IFace {}\n", "t.ts", {"Base", "IFace"}),
    "java": (b"class Child extends Base implements IFace {}\n", "T.java", {"Base", "IFace"}),
    "csharp": (b"class Child : Base, IFace {}\n", "T.cs", {"Base", "IFace"}),
    "cpp": (b"class Child : public Base {};\n", "t.cpp", {"Base"}),
    "ruby": (b"class Child < Base\nend\n", "t.rb", {"Base"}),
    "powershell": (b"class Child : Base {}\n", "t.ps1", {"Base"}),
}

# Ключевые слова, которые НЕ являются именами классов: попав в граф, они создают
# несуществующие сущности и связи с ними.
NOT_NAMES = ("extends", "implements", "public", "private", "protected", "class", ":", ",")


def main() -> int:
    print("=== 1. Имена базовых классов извлекаются правильно ===")
    for lang, (src, name, want) in CASES.items():
        profile = PROFILES.get(lang)
        if profile is None:
            check(False, f"{lang}: профиль языка есть")
            continue
        ext = GenericExtractor(profile)
        res = ext.extract(name, src)
        got = {str(e.dst)[5:] for e in res.edges if e.kind in ("extends", "implements")}
        classes = {s.name for s in res.symbols if s.kind in ("class", "interface")}
        check(got == want, f"{lang}: извлечены базы {sorted(want)}", f"получено {sorted(got)}")
        check("Child" in classes, f"{lang}: класс объявлен", str(sorted(classes)))
        check(not (got & set(NOT_NAMES)),
              f"{lang}: ключевые слова не стали именами классов", str(sorted(got)))
        check("Child" not in got,
              f"{lang}: сам класс не попал в свои базы", str(sorted(got)))

    print("\n=== 2. Класс без базового класса не даёт рёбер ===")
    ext = GenericExtractor(PROFILES["python"])
    for src, name in ((b"class Standalone:\n    pass\n", "s.py"),
                      (b"class JustOne(object):\n    pass\n", "o.py")):
        res = ext.extract(name, src)
        got = [e for e in res.edges if e.kind in ("extends", "implements")]
        if b"object" in src:
            # `object` — настоящая база, её отсутствие было бы потерей факта.
            check(bool(got), f"{name}: база object извлечена", str(got))
        else:
            check(not got, f"{name}: без базы рёбер нет", str(got))

    print("\n=== 3. Разрешение заглушки на настоящий класс ===")
    tmp = Path(tempfile.mkdtemp(prefix="inherit_test_"))
    try:
        db = tmp / "graph.sqlite"
        conn = sqlite3.connect(db)
        conn.executescript(
            """
            CREATE TABLE node (rev_id TEXT, node_id TEXT, kind TEXT, name TEXT, fqn TEXT,
                               path TEXT, start_line INT, end_line INT, signature TEXT,
                               props TEXT, layer TEXT);
            CREATE TABLE edge (rev_id TEXT, edge_uid TEXT, src TEXT, dst TEXT, kind TEXT,
                               provenance TEXT, evidence_path TEXT, evidence_line INT,
                               confidence REAL, sources TEXT, props TEXT);
            """
        )
        conn.execute("INSERT INTO node VALUES ('rev-0001','c1','class','Child','p/Child','p.py',1,9,'','','src')")
        conn.execute("INSERT INTO node VALUES ('rev-0001','b1','class','Base','p/Base','p.py',11,19,'','','src')")
        # Единственный кандидат — разрешается.
        conn.execute("INSERT INTO edge VALUES ('rev-0001','e1','c1','@ext:Base','extends','derived','p.py',1,0.6,'','')")
        # Ноль кандидатов — базовый класс вне корпуса: остаётся заглушкой.
        conn.execute("INSERT INTO edge VALUES ('rev-0001','e2','c1','@ext:Missing','extends','derived','p.py',1,0.6,'','')")
        conn.commit()

        from ragraf.config import load as load_config  # noqa: PLC0415
        from ragraf.indexer import Indexer  # noqa: PLC0415

        cfg = load_config(REPO / "config" / "ragraf.yaml")

        class _Graph:
            def __init__(self, c):
                self.conn = c

            def add_edges(self, rows):
                self.conn.executemany(
                    "INSERT OR REPLACE INTO edge(rev_id, edge_uid, src, dst, kind,"
                    " provenance, evidence_path, evidence_line, confidence, sources, props)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    rows,
                )
                return len(rows)

            def commit(self):
                self.conn.commit()

        probe = Indexer.__new__(Indexer)
        probe.cfg = cfg
        probe.graph = _Graph(conn)
        resolved, ambiguous = Indexer._resolve_inheritance_names(probe, "rev-0001")
        check(resolved == 1, "единственный кандидат разрешён", f"resolved={resolved}")
        check(ambiguous == 1, "отсутствующий класс оставлен заглушкой", f"ambiguous={ambiguous}")

        row = conn.execute("SELECT src, dst, kind, provenance, confidence FROM edge WHERE edge_uid LIKE '%' AND dst = 'b1'").fetchone()
        check(row is not None, "ребро ведёт на настоящий узел класса")
        if row:
            check(row[2] == "extends", "вид ребра сохранён", str(row[2]))
            check(row[3] == "derived", "происхождение — выведенное", str(row[3]))
            check(float(row[4]) <= 0.8, "достоверность не выше 0,8", str(row[4]))
        left = conn.execute("SELECT COUNT(*) FROM edge WHERE dst = '@ext:Missing'").fetchone()[0]
        check(left == 1, "неразрешённая заглушка осталась на месте", str(left))

        print("\n=== 4. Неоднозначное имя НЕ разрешается ===")
        # Второй класс с тем же именем: выбрать один из двух нельзя.
        conn.execute("DELETE FROM edge")
        conn.execute("INSERT INTO node VALUES ('rev-0001','b2','class','Base','q/Base','q.py',1,9,'','','src')")
        conn.execute("INSERT INTO edge VALUES ('rev-0001','e3','c1','@ext:Base','extends','derived','p.py',1,0.6,'','')")
        conn.commit()
        resolved2, ambiguous2 = Indexer._resolve_inheritance_names(probe, "rev-0001")
        check(resolved2 == 0, "два кандидата не разрешаются", f"resolved={resolved2}")
        check(ambiguous2 == 1, "такое ребро посчитано неоднозначным", f"ambiguous={ambiguous2}")
        still = conn.execute("SELECT COUNT(*) FROM edge WHERE dst = '@ext:Base'").fetchone()[0]
        check(still == 1, "неоднозначная заглушка осталась заглушкой", str(still))
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n=== 5. Обход базовых классов (MRO) и его предохранители ===")
    # Главное здесь — не то, что метод НАХОДИТСЯ, а то, что при малейшей
    # неоднозначности поиск ОТКАЗЫВАЕТ. Связь идёт в `impact`-анализ: по ней
    # агент судит о последствиях правки, поэтому выдуманное наследование хуже
    # отсутствующего.
    from ragraf import cpg as cpg_mod  # noqa: PLC0415

    def mro_case(nodes, edges, name):
        tmp2 = Path(tempfile.mkdtemp(prefix="mro_test_"))
        db2 = tmp2 / "g.sqlite"
        c2 = sqlite3.connect(db2)
        c2.executescript(
            """
            CREATE TABLE node (rev_id TEXT, node_id TEXT, kind TEXT, name TEXT, fqn TEXT,
                               path TEXT, start_line INT, end_line INT, signature TEXT,
                               props TEXT, layer TEXT);
            CREATE TABLE edge (rev_id TEXT, edge_uid TEXT, src TEXT, dst TEXT, kind TEXT,
                               provenance TEXT, evidence_path TEXT, evidence_line INT,
                               confidence REAL, sources TEXT, props TEXT);
            """
        )
        for nid, kind, nm in nodes:
            c2.execute(
                "INSERT INTO node VALUES ('rev-0001',?,?,?,?,?,1,9,'','','src')",
                (nid, kind, nm, f"p.py:{nm}", "p.py"),
            )
        for s, d, k in edges:
            c2.execute(
                "INSERT INTO edge VALUES ('rev-0001',?,?,?,?,?, 'p.py',1,0.6,'','')",
                (f"{s}-{d}", s, d, k, "stated"),
            )
        c2.commit()
        out = cpg_mod.resolve_own_method_via_mro(
            c2, "rev-0001", "p.py:<module>.Child.run", "p.py", name
        )
        c2.close()
        shutil.rmtree(tmp2, ignore_errors=True)
        return out

    got, how = mro_case(
        [("C", "class", "Child"), ("B", "class", "Base"), ("m", "method", "hello")],
        [("C", "B", "extends"), ("B", "m", "contains")],
        "hello",
    )
    check(got is not None, "метод найден в базовом классе", f"{got} ({how})")

    got, how = mro_case(
        [("C", "class", "Child"), ("B", "class", "Base"), ("G", "class", "Grand"),
         ("m", "method", "deep")],
        [("C", "B", "extends"), ("B", "G", "extends"), ("G", "m", "contains")],
        "deep",
    )
    check(got is not None, "метод найден в предке второго уровня", f"{got} ({how})")

    got, how = mro_case(
        [("C", "class", "Child"), ("B1", "class", "Base1"), ("B2", "class", "Base2"),
         ("m1", "method", "same"), ("m2", "method", "same")],
        [("C", "B1", "extends"), ("C", "B2", "extends"),
         ("B1", "m1", "contains"), ("B2", "m2", "contains")],
        "same",
    )
    check(got is None, "два кандидата на одном уровне — ОТКАЗ", f"{got} ({how})")

    got, how = mro_case(
        [("C", "class", "Child"), ("B", "class", "Base"),
         ("m1", "method", "dup"), ("m2", "method", "dup")],
        [("C", "B", "extends"), ("B", "m1", "contains"), ("B", "m2", "contains")],
        "dup",
    )
    check(got is None, "дубль метода в одном классе — ОТКАЗ", f"{got} ({how})")

    got, how = mro_case(
        [("C", "class", "Child"), ("B1", "class", "B1"), ("B2", "class", "B2"),
         ("D", "class", "Diamond"), ("m", "method", "x")],
        [("C", "B1", "extends"), ("C", "B2", "extends"),
         ("B1", "D", "extends"), ("B2", "D", "extends"), ("D", "m", "contains")],
        "x",
    )
    check(got is None, "ромб в наследовании — ОТКАЗ", f"{got} ({how})")

    got, how = mro_case([("C", "class", "Child")], [("C", "@ext:X", "extends")], "anything")
    check(got is None, "база вне корпуса — ОТКАЗ (не догадка)", f"{got} ({how})")

    print("\n=== 6. `parent::` ищет только в базах ===")
    tmp3 = Path(tempfile.mkdtemp(prefix="mro_parent_"))
    try:
        db3 = tmp3 / "g.sqlite"
        c3 = sqlite3.connect(db3)
        c3.executescript(
            """
            CREATE TABLE node (rev_id TEXT, node_id TEXT, kind TEXT, name TEXT, fqn TEXT,
                               path TEXT, start_line INT, end_line INT, signature TEXT,
                               props TEXT, layer TEXT);
            CREATE TABLE edge (rev_id TEXT, edge_uid TEXT, src TEXT, dst TEXT, kind TEXT,
                               provenance TEXT, evidence_path TEXT, evidence_line INT,
                               confidence REAL, sources TEXT, props TEXT);
            """
        )
        for nid, kind, nm in (("C", "class", "Child"), ("B", "class", "Base"),
                              ("own", "method", "setup"), ("base", "method", "setup")):
            c3.execute("INSERT INTO node VALUES ('rev-0001',?,?,?,?,?,1,9,'','','src')",
                       (nid, kind, nm, f"p.py:{nm}", "p.py"))
        c3.execute("INSERT INTO edge VALUES ('rev-0001','e1','C','B','extends','derived','p.py',1,0.6,'','')")
        c3.execute("INSERT INTO edge VALUES ('rev-0001','e2','C','own','contains','stated','p.py',3,1.0,'','')")
        c3.execute("INSERT INTO edge VALUES ('rev-0001','e3','B','base','contains','stated','p.py',9,1.0,'','')")
        c3.commit()
        got, how = cpg_mod.resolve_own_method_via_mro(
            c3, "rev-0001", "p.py:<module>.Child.run", "p.py", "setup", skip_self=True
        )
        check(got == "base", "при skip_self цель берётся из базы, а не из своего класса",
              f"получено {got} ({how})")
        got2, _ = cpg_mod.resolve_own_method_via_mro(
            c3, "rev-0001", "p.py:<module>.Child.run", "p.py", "setup", skip_self=False
        )
        check(got2 == "own", "без skip_self сначала берётся свой метод", f"получено {got2}")
        c3.close()
    finally:
        import shutil as _sh2
        _sh2.rmtree(tmp3, ignore_errors=True)

    print("\n=== ИТОГ ===")
    print(f"пройдено: {PASS}, провалено: {FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
