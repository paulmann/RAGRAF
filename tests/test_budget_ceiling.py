"""Свойство: предел спрашивается ВНУТРИ привязки `@ext`-ссылок.

Измеренный дефект 23.11.2026: прогон 3 129 файлов с пределом 60 с шёл 435 с, потому что
цикл привязки `@ext`-ссылок (поиск `fqn LIKE '%<target>'` на каждое ребро) не спрашивал
бюджет вовсе, а точки контроля стояли только на стадиях и в первом цикле прохода.

Проверяется поведением, а не текстом: проход вызывается с наблюдателем бюджета, и тот
обязан быть спрошен при числе ссылок больше порции. Второе свойство: наблюдатель, который
отказывает, прерывает проход — то есть предел действительно предел.
"""
import pathlib
import sqlite3
import sys
import tempfile

ROOT = pathlib.Path(r"C:\AutoClaw\RAGRAF")
sys.path.insert(0, str(ROOT / "src"))

from ragraf import config as config_mod  # noqa: E402
from ragraf import indexer as indexer_mod  # noqa: E402

PHP = "<?php\nfunction only() { return 1; }\n"
EXT_EDGES = 12000
PASS = 0
FAIL = 0


def check(cond: bool, title: str, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [OK ] {title}")
    else:
        FAIL += 1
        print(f"  [FAIL] {title}" + (f"\n      {detail}" if detail else ""))


def main() -> int:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        work = pathlib.Path(tmp)
        corpus = work / "corpus"
        (corpus / "src").mkdir(parents=True)
        (corpus / "composer.json").write_text("{}", encoding="utf-8")
        for i in range(30):
            (corpus / "src" / f"u{i:02d}.php").write_text(PHP, encoding="utf-8")
        config_path = work / "corpus.yaml"
        config_path.write_text(
            f"project: ext-budget\nroot: {corpus.as_posix()}\n"
            "languages:\n  - php\ninclude:\n  - \"**/*.php\"\n"
            "exclude:\n  - \"**/.ragraf/**\"\nindex:\n  joern_enabled: false\n", encoding="utf-8")
        cfg = config_mod.load(config_path)

        index = indexer_mod.Indexer(cfg)
        first = index.run(budget_seconds=600, with_vectors=False)
        check(first.ok, "подготовительная ревизия собрана", str(first.notes[-1:]))
        rev = first.rev_id

        conn = index.graph.conn
        src = conn.execute("SELECT node_id FROM node WHERE rev_id = ? LIMIT 1", (rev,)).fetchone()[0]
        rows = [(rev, f"ext-{i}", str(src), f"@ext:Missing\\Class{i}\\method{i}", "calls",
                 "tree-sitter-php", f"src/u{i % 30:02d}.php", i % 100, 0.5,
                 "[]", "{}", "derived") for i in range(EXT_EDGES)]
        for start in range(0, len(rows), 2000):
            conn.executemany(
                "INSERT OR REPLACE INTO edge (rev_id, edge_uid, src, dst, kind, provenance,"
                " evidence_path, evidence_line, confidence, sources, props, evidence_kind)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", rows[start:start + 2000])
        conn.commit()

        seen: list[int] = []

        def spy(stage: str, done: int) -> None:
            seen.append(done)

        resolved, ambiguous = index._resolve_external_names(rev, check_budget=spy)
        print(f"  ссылок {EXT_EDGES}: разрешено {resolved}, неоднозначно {ambiguous}, "
              f"обращений к бюджету {len(seen)}")
        check(len(seen) >= EXT_EDGES // 500,
              "бюджет спрашивается внутри цикла @ext-ссылок, а не только на стадии",
              f"обращений {len(seen)} при {EXT_EDGES} ссылках")

        # Второе свойство: отказ наблюдателя действительно прерывает проход.
        class Refuse(Exception):
            pass

        def refuse(stage: str, done: int) -> None:
            raise Refuse()

        stopped = False
        try:
            index._resolve_external_names(rev, check_budget=refuse)
        except Refuse:
            stopped = True
        check(stopped, "отказ бюджета прерывает проход, а не игнорируется")
        index.close()

    print(f"ИТОГ: пройдено {PASS}, провалено {FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
