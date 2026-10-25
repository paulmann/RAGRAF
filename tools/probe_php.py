"""Проверка экстрактора PHP на реальном файле пилота.

Только чтение: берёт один файл, печатает агрегаты (без выгрузки исходника).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ragraf.extract.php_ast import PhpExtractor, build_chunks  # noqa: E402

TARGETS = [
    str(pathlib.Path(__file__).resolve().parents[1].parent / "revoap.com/src/Core/Config.php"),
    str(pathlib.Path(__file__).resolve().parents[1].parent / "revoap.com/src/Pipeline/PipelineEngine.php"),
]


def main() -> int:
    ex = PhpExtractor()
    if not ex.grammar_available():
        print("ОШИБКА: грамматика PHP недоступна")
        return 2

    for rel in TARGETS:
        p = Path(rel)
        if not p.exists():
            print(f"нет файла: {rel}")
            continue
        data = p.read_bytes()
        res = ex.extract(p.name, data)
        c = res.counts()
        print(f"--- {p.name} ({res.lines} строк) статус={res.parse_status} {res.parse_note or ''}")
        print(f"    символы={c['symbols']} ({c['by_kind']})")
        print(f"    рёбра={c['edges']} ({c['by_edge']})")
        print(f"    текст={c['texts']} sql={c['sql_literals']} include={c['includes']} superglobals={c['superglobals']}")

        classes = [s for s in res.symbols if s.kind in ("class", "interface", "trait", "enum")]
        methods = [s for s in res.symbols if s.kind == "method"]
        print(f"    классы={len(classes)} методы={len(methods)}")
        for cl in classes[:4]:
            own = [m for m in methods if m.fqn.startswith(cl.fqn + "::")]
            print(f"      {cl.fqn} -> методов {len(own)}")
        stated = sum(1 for e in res.edges if e.provenance == "stated")
        derived = len(res.edges) - stated
        print(f"    провенанс: stated={stated} derived={derived}")

        chunks = build_chunks(res, data, max_lines=120)
        print(f"    чанков={len(chunks)} (по символам), первый: {chunks[0].start_line}-{chunks[0].end_line}")

        # Точечная проверка: метод, который точно есть в этом файле.
        if "Config" in p.name:
            for s in res.symbols:
                if s.kind == "method" and s.name in ("get", "load", "__construct"):
                    print(f"      {s.fqn} строки {s.start_line}-{s.end_line} vis={s.props.get('visibility')}")
        # Проверка разделения текста: комментарии не должны попасть в props/name.
        leak = [s for s in res.symbols if "//" in (s.name or "") or "/*" in (s.name or "")]
        print(f"    утечка комментариев в имена символов: {len(leak)}")
        # Проверка привязки методов к правильному классу.
        wrong = [m for m in methods if m.parent_fqn and m.parent_fqn.count("\\") > 4]
        print(f"    подозрительные parent_fqn: {len(wrong)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
