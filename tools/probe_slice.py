"""Диагностика обнаружения slice_boundary на фикстуре."""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf import store, util  # noqa: E402
from ragraf.config import load as load_config  # noqa: E402
from ragraf.drift import _diff_file  # noqa: E402
from ragraf.extract.php_ast import PhpExtractor  # noqa: E402


def main() -> int:
    proj = REPO / "tests" / "fixtures" / "proj"
    cfg = load_config(REPO / "config" / "fixture.yaml")
    rev = util.read_json(cfg.rev_file, default={}).get("rev_id")
    conn = store.connect(cfg.graph_db, readonly=True)
    nodes = conn.execute(
        "SELECT node_id, kind, name, fqn, path, start_line, end_line, props FROM node"
        " WHERE rev_id = ? AND path = 'src/Engine.php' AND kind NOT IN ('file','namespace')",
        (rev,),
    ).fetchall()
    conn.close()
    print(f"ревизия {rev}; узлов в Engine.php: {len(nodes)}")
    for n in nodes:
        props = util.loads(n["props"]) if n["props"] else {}
        print(f"  {n['kind']:<9} {n['fqn']:<40} строки {n['start_line']}-{n['end_line']}"
              f"  body_sha={props.get('body_sha')}")

    cur = proj / "src" / "Engine.php"
    ex = PhpExtractor()
    res = ex.extract("src/Engine.php", cur.read_bytes())
    print(f"\nновый разбор: {len(res.symbols)} символов, {len(res.edges)} рёбер")
    new_syms = {str(s.fqn): s for s in res.symbols if s.kind not in ("file", "namespace")}
    old = {str(n["fqn"]): n for n in nodes}
    added = [f for f in new_syms if f not in old]
    print(f"добавлено: {added}")
    for fqn, n in old.items():
        new = new_syms.get(fqn)
        if new is None:
            continue
        props = util.loads(n["props"]) if n["props"] else {}
        if props.get("body_sha") != new.props.get("body_sha"):
            print(f"изменено: {fqn} ({props.get('body_sha')} -> {new.props.get('body_sha')})")

    cls = "App\\Pipeline\\Engine"
    changed_ids = {new_syms[f].node_id for f in new_syms if f in old}
    print("\nрёбра из изменённых символов, ведущие к новым:")
    added_ids = {f: new_syms[f].node_id for f in added}
    for e in res.edges:
        if e.src in changed_ids:
            for f, nid in added_ids.items():
                if e.dst == nid:
                    print(f"  {e.kind}/{e.provenance} -> {f} (строка {e.evidence_line})")

    delta = _diff_file(cfg, ex, "src/Engine.php", type("C", (), {
        "lang": "php", "sha256": util.sha256_file(cur), "abs_path": cur,
    })(), nodes, "oldhash")
    print(f"\nрезультат _diff_file: kind={delta.kind} slice={delta.slice_boundary}")
    print(f"  added={delta.symbols_added}")
    print(f"  changed={delta.symbols_changed}")
    print(f"  note={delta.note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
