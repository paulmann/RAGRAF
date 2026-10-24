"""Точечная диагностика вывода типов на одной фикстуре (без выгрузки кода в отчёт)."""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf.extract.php_ast import PhpExtractor  # noqa: E402

SRC = REPO / "tests" / "fixtures" / "proj" / "src"


def main() -> int:
    ex = PhpExtractor()
    for name in ("Config.php", "Engine.php", "Repository.php"):
        p = SRC / name
        if not p.exists():
            print(f"нет файла {name}")
            continue
        res = ex.extract(name, p.read_bytes())
        print(f"--- {name}")
        print(f"  use_map: {ex._use_map}")
        for cls, props in ex._prop_types.items():
            print(f"  типы свойств {cls}: {props}")
        print(f"  объявления: {sorted(ex._declarations)}")
        for e in res.edges:
            if e.kind in ("calls", "calls_builtin", "calls_heuristic") and (
                "get" in str(e.dst) or "run" in str(e.dst) or "formatStage" in str(e.dst)
            ):
                src_name = next(
                    (s.fqn for s in res.symbols if s.node_id == e.src), e.src[:8]
                )
                dst_name = next(
                    (s.fqn for s in res.symbols if s.node_id == e.dst), e.dst
                )
                print(f"    {src_name} -> {dst_name} [{e.kind}/{e.provenance}] строка {e.evidence_line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
