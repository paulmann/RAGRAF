"""Журнал расхождений: обнаружение, дедупликация, архивация (ФТ-25..ФТ-36).

Инварианты (архитектура §8):
  I1 — индекс иммутабелен в пределах ревизии: этот модуль НИКОГДА не пишет в граф;
  I2 — журнал append-only: записи не удаляются и не перезаписываются;
  I3 — ответы фасада при непустом журнале не могут молча опираться на затронутый факт;
  I4 — вынос кода в функцию (extract-method) фиксируется отдельным видом slice_boundary;
  I5 — архив переиндексации отвечает на вопрос «что изменилось в знаниях».

Что делает обнаружение:
  * сравнивает sha256 файла в рабочем дереве с записью в графе (compare);
  * определяет, какие символы появились/изменились/исчезли (разбор on-the-fly, без записи);
  * отдельно распознаёт extract-method: внутри существующего символа появился новый
    вложенный вызов на новый символ — это меняет границы срезов, а не просто текст;
  * пишет запись в control.sqlite и дописывает человекочитаемую строку в LEDGER.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from . import store, util
from .config import ProjectConfig
from .corpus import scan as scan_corpus
from .extract.php_ast import PhpExtractor
from .revisions import ControlStore


@dataclass
class DeltaEntry:
    path: str
    kind: str
    old_hash: str | None
    new_hash: str | None
    symbols_added: list[str] = field(default_factory=list)
    symbols_removed: list[str] = field(default_factory=list)
    symbols_changed: list[str] = field(default_factory=list)
    slice_boundary: bool = False
    note: str | None = None
    edges_invalidated: int = 0


# --------------------------------------------------------------------------
# Обнаружение
# --------------------------------------------------------------------------


def scan_drift(
    cfg: ProjectConfig,
    *,
    detected_by: str = "compare",
    note: str | None = None,
    paths: list[str] | None = None,
    schema_changed: list[dict] | None = None,
) -> dict:
    """Сравнить индекс с рабочим деревом и записать новые различия.

    Индекс при этом не изменяется (I1) — обновляются только журнал и его markdown.
    """
    import time

    t0 = time.perf_counter()
    control = ControlStore(cfg)
    try:
        rev = control.current_rev()
        if not rev:
            return {
                "ok": False,
                "error": "индекс не создан: сначала выполните ragraf index",
                "new_entries": 0,
                "total_entries": 0,
                "entries": [],
                "seconds": 0.0,
            }

        graph = store.connect(cfg.graph_db, readonly=True)
        try:
            known = {
                str(r["path"]): (str(r["sha256"]), r["lang"])
                for r in graph.execute(
                    "SELECT path, sha256, lang FROM file WHERE rev_id = ?", (rev,)
                ).fetchall()
            }
            node_rows = graph.execute(
                "SELECT node_id, kind, name, fqn, path, start_line, end_line FROM node"
                " WHERE rev_id = ? AND kind NOT IN ('file','namespace')",
                (rev,),
            ).fetchall()
        finally:
            graph.close()

        nodes_by_path: dict[str, list] = {}
        for r in node_rows:
            nodes_by_path.setdefault(str(r["path"]), []).append(r)

        corpus = scan_corpus(cfg)
        current = {f.path: f for f in corpus.files}

        if paths:
            wanted = {util.to_posix(p) for p in paths}
            known = {k: v for k, v in known.items() if k in wanted}
            current = {k: v for k, v in current.items() if k in wanted}

        extractor = PhpExtractor()
        deltas: list[DeltaEntry] = []

        # --- изменённые и удалённые файлы ---
        for path, (old_hash, lang) in sorted(known.items()):
            cur = current.get(path)
            if cur is None:
                deltas.append(
                    DeltaEntry(
                        path=path,
                        kind="delete",
                        old_hash=old_hash,
                        new_hash=None,
                        symbols_removed=[str(n["fqn"] or n["name"]) for n in nodes_by_path.get(path, [])],
                        note="файл отсутствует в рабочем дереве",
                    )
                )
                continue
            if cur.sha256 == old_hash:
                continue
            delta = _diff_file(cfg, extractor, path, cur, nodes_by_path.get(path, []), old_hash)
            deltas.append(delta)

        # --- новые файлы ---
        for path in sorted(set(current) - set(known)):
            cur = current[path]
            added: list[str] = []
            if cur.lang == "php":
                try:
                    res = extractor.extract(path, cur.abs_path.read_bytes())
                    added = [s.fqn for s in res.symbols if s.kind not in ("file", "namespace")]
                except Exception:
                    added = []
            deltas.append(
                DeltaEntry(
                    path=path,
                    kind="add",
                    old_hash=None,
                    new_hash=cur.sha256,
                    symbols_added=added,
                    note="новый файл в рабочем дереве",
                )
            )

        # --- изменения схемы БД (ФТ-30) ---
        for sc in schema_changed or []:
            deltas.append(
                DeltaEntry(
                    path=f"db://{sc.get('table', 'schema')}",
                    kind="schema_change",
                    old_hash=sc.get("old"),
                    new_hash=sc.get("new"),
                    note=sc.get("note") or "изменение схемы БД",
                )
            )

        # --- запись в журнал (append-only, с дедупликацией) ---
        inserted: list[dict] = []
        for d in deltas:
            kind_effective = "modify"
            if d.slice_boundary:
                # I4: вынос кода в функцию — отдельный вид, самый опасный для срезов.
                kind_effective = "slice_boundary"
            dedupe = util.stable_id(d.path, d.old_hash, d.new_hash, kind_effective)
            exists = control.conn.execute(
                "SELECT id FROM drift_entry WHERE dedupe_key = ?", (dedupe,)
            ).fetchone()
            if exists:
                continue
            row = control.conn.execute(
                """INSERT INTO drift_entry(index_rev, ts, path, kind, old_hash, new_hash,
                                           symbols_added, symbols_removed, symbols_changed,
                                           edges_invalidated, detected_by, note, dedupe_key)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    rev,
                    util.now_iso(),
                    d.path,
                    kind_effective,
                    d.old_hash,
                    d.new_hash,
                    util.dumps(d.symbols_added),
                    util.dumps(d.symbols_removed),
                    util.dumps(d.symbols_changed),
                    d.edges_invalidated,
                    detected_by,
                    d.note,
                    dedupe,
                ),
            )
            entry_id = int(row.lastrowid)
            inserted.append(
                {
                    "id": entry_id,
                    "path": d.path,
                    "kind": kind_effective,
                    "symbols_added": d.symbols_added,
                    "symbols_removed": d.symbols_removed,
                    "symbols_changed": d.symbols_changed,
                    "note": d.note,
                }
            )
            # Пометка затронутых рёбер как потенциально устаревших (для drift_report/status).
            _mark_stale_edges(control, rev, d.path, entry_id)
            _append_ledger_md(cfg, rev, kind_effective, d, detected_by)

        control.conn.commit()
        total = int(
            control.conn.execute(
                "SELECT COUNT(*) AS c FROM drift_entry WHERE index_rev = ?", (rev,)
            ).fetchone()["c"]
        )
        return {
            "ok": True,
            "rev": rev,
            "new_entries": len(inserted),
            "total_entries": total,
            "entries": inserted,
            "ledger_path": util.to_posix(cfg.ledger_md) if inserted else None,
            "seconds": round(time.perf_counter() - t0, 3),
        }
    finally:
        control.close()


def _diff_file(
    cfg: ProjectConfig,
    extractor: PhpExtractor,
    path: str,
    cur,
    old_nodes: list,
    old_hash: str,
) -> DeltaEntry:
    """Разбор одного изменённого файла: какие символы появились/изменились/исчезли."""
    delta = DeltaEntry(path=path, kind="modify", old_hash=old_hash, new_hash=cur.sha256)
    if cur.lang != "php":
        delta.note = f"изменён файл языка {cur.lang}: символьный разбор не выполняется"
        return delta

    try:
        res = extractor.extract(path, cur.abs_path.read_bytes())
    except Exception as exc:
        delta.note = f"разбор не удался: {type(exc).__name__}"
        return delta

    new_symbols = {
        str(s.fqn): s for s in res.symbols if s.kind not in ("file", "namespace")
    }
    old_symbols = {str(n["fqn"]): n for n in old_nodes}

    added = [f for f in new_symbols if f not in old_symbols]
    removed = [f for f in old_symbols if f not in new_symbols]

    changed: list[str] = []
    for fqn, node in old_symbols.items():
        new = new_symbols.get(fqn)
        if new is None:
            continue
        # Основной критерий — хэш тела символа: он ловит изменение и в тех же границах.
        try:
            old_props = util.loads(node["props"]) if node["props"] else {}
        except Exception:
            old_props = {}
        old_body = old_props.get("body_sha")
        new_body = new.props.get("body_sha")
        if old_body and new_body and old_body != new_body:
            changed.append(fqn)
            continue
        if int(node["start_line"]) != new.start_line or int(node["end_line"]) != new.end_line:
            changed.append(fqn)

    delta.symbols_added = sorted(added)
    delta.symbols_removed = sorted(removed)
    delta.symbols_changed = sorted(changed)

    # Распознавание extract-method: изменились границы существующего символа И
    # появился новый символ, вызываемый из него. Само по себе появление функции —
    # обычное добавление; опасен именно перенос кода из существующей функции.
    if added and changed:
        new_calls_from_changed: set[str] = set()
        for e in res.edges:
            if e.kind == "calls" and e.src in {new_symbols[f].node_id for f in changed if f in new_symbols}:
                target = next(
                    (f for f, s in new_symbols.items() if s.node_id == e.dst), None
                )
                if target:
                    new_calls_from_changed.add(target)
        hit = sorted(new_calls_from_changed & set(added))
        if hit:
            delta.slice_boundary = True
            delta.note = (
                "границы срезов изменены: код перенесён в "
                + ", ".join(hit[:5])
                + (" и др." if len(hit) > 5 else "")
            )

    if delta.symbols_changed and not delta.note:
        delta.note = "изменились границы символов (порядок строк)"
    return delta


def _mark_stale_edges(control: ControlStore, rev: str, path: str, entry_id: int) -> None:
    """Пометить рёбра файла как потенциально устаревшие (без удаления из графа)."""
    graph_path = control.cfg.graph_db
    if not graph_path.exists():
        return
    try:
        graph = store.connect(graph_path, readonly=True)
    except Exception:
        return
    try:
        rows = graph.execute(
            "SELECT edge_uid FROM edge WHERE rev_id = ? AND evidence_path = ?", (rev, path)
        ).fetchall()
    except Exception:
        rows = []
    finally:
        graph.close()
    for r in rows:
        control.conn.execute(
            "INSERT INTO stale_edge(index_rev, edge_uid, path, reason, drift_entry_id)"
            " VALUES(?,?,?,?,?)",
            (rev, str(r["edge_uid"]), path, "файл изменён после индексации", entry_id),
        )


# --------------------------------------------------------------------------
# Человекочитаемый журнал
# --------------------------------------------------------------------------

LEDGER_HEADER = """# Журнал расхождений индекса и кода

Файл дописывается автоматически (append-only). Записи не удаляются и не правятся:
ошибка исправляется новой записью. Индекс здесь **не обновляется** — он приводится
в соответствие с кодом только командой `ragraf reindex --commit`.

Формат записи: дата · ревизия · способ обнаружения · файл · вид изменения · символы.

Виды: `add` (новый файл) · `modify` (изменён) · `delete` (удалён) · `rename` ·
`schema_change` (изменение схемы БД) · `slice_boundary` (изменены границы срезов —
код перенесён в другую функцию; самое опасное для анализа изменение).
"""


def _append_ledger_md(cfg: ProjectConfig, rev: str, kind: str, d: DeltaEntry, detected_by: str) -> None:
    """Дописать запись в LEDGER.md (I2: только добавление)."""
    if not cfg.ledger_md.exists():
        util.write_text(cfg.ledger_md, LEDGER_HEADER + "\n")
    lines = [f"## {util.now_iso()} · {rev} · detected_by {detected_by}", ""]
    lines.append(f"- `{d.path}` — {kind}")
    if d.symbols_added:
        lines.append(f"  - добавлено символов: {len(d.symbols_added)} ({_names(d.symbols_added)})")
    if d.symbols_removed:
        lines.append(f"  - удалено символов: {len(d.symbols_removed)} ({_names(d.symbols_removed)})")
    if d.symbols_changed:
        lines.append(f"  - изменены символы: {len(d.symbols_changed)} ({_names(d.symbols_changed)})")
    if d.edges_invalidated:
        lines.append(f"  - потенциально устаревших рёбер: {d.edges_invalidated}")
    if d.note:
        lines.append(f"  - примечание: {d.note}")
    lines.append("")
    util.append_text(cfg.ledger_md, "\n".join(lines) + "\n")


def _names(items: list[str], limit: int = 6) -> str:
    head = ", ".join(items[:limit])
    return head + (f" и ещё {len(items) - limit}" if len(items) > limit else "")


# --------------------------------------------------------------------------
# Архивация при переиндексации (ФТ-32..ФТ-34)
# --------------------------------------------------------------------------


def archive_and_reset(cfg: ProjectConfig, new_rev: str) -> dict:
    """Архивировать журнал перед новой ревизией и ответить «что изменилось в знаниях».

    Записи журнала НЕ удаляются (история знаний неприкосновенна): они помечаются
    архивной записью с указанием диапазона ревизий, а файл LEDGER.md получает
    заголовок о переходе. Новые записи будут относиться уже к новой ревизии.
    """
    control = ControlStore(cfg)
    try:
        prev = control.conn.execute(
            "SELECT rev_id FROM rev_history WHERE rev_id <> ? AND state IN ('ready','rotated')"
            " ORDER BY rev_id DESC LIMIT 1",
            (new_rev,),
        ).fetchone()
        from_rev = str(prev["rev_id"]) if prev else "rev-0000"

        rows = control.conn.execute(
            "SELECT id, ts, path, kind, symbols_added, symbols_removed, symbols_changed, note,"
            " detected_by, edges_invalidated FROM drift_entry WHERE index_rev = ? ORDER BY id",
            (from_rev,),
        ).fetchall()

        report = _build_archive_report(cfg, from_rev, new_rev, rows)
        cfg.archive_dir.mkdir(parents=True, exist_ok=True)
        stamp = util.now_iso().replace(":", "").replace("-", "").replace("+", "_")
        report_path = cfg.archive_dir / f"{from_rev}_to_{new_rev}_{stamp}.md"
        util.write_text(report_path, report)

        control.conn.execute(
            "INSERT INTO drift_archive(project, from_rev, to_rev, archived_at, entries, report_path)"
            " VALUES(?,?,?,?,?,?)",
            (cfg.project, from_rev, new_rev, util.now_iso(), len(rows), util.to_posix(report_path)),
        )
        # Пометить stale_edge прошлой ревизии как закрытые: расчёт по новой ревизии.
        control.conn.execute(
            "UPDATE stale_edge SET acked_at = ? WHERE index_rev = ? AND acked_at IS NULL",
            (util.now_iso(), from_rev),
        )
        control.conn.commit()

        # Переходный заголовок в LEDGER.md: старые записи остаются ниже, не стираются.
        util.append_text(
            cfg.ledger_md,
            f"\n---\n\n# ПЕРЕХОД: {from_rev} → {new_rev} ({util.now_iso()})\n\n"
            f"Журнал за ревизию {from_rev} архивирован ({len(rows)} записей) в "
            f"`{util.to_posix(report_path)}`. Ниже — записи новой ревизии {new_rev}.\n"
            f"Предыдущие записи не удалены: полная история сохраняется.\n\n---\n\n",
        )
        return {
            "from_rev": from_rev,
            "to_rev": new_rev,
            "entries": len(rows),
            "report_path": util.to_posix(report_path),
        }
    finally:
        control.close()


def _build_archive_report(cfg: ProjectConfig, from_rev: str, to_rev: str, rows: list) -> str:
    """Отчёт «что изменилось в знаниях» (I5): три обязательных раздела."""
    by_kind: dict[str, list] = {}
    for r in rows:
        by_kind.setdefault(str(r["kind"]), []).append(r)

    changed_paths = sorted({str(r["path"]) for r in rows})
    total_symbols = 0
    for r in rows:
        for col in ("symbols_added", "symbols_removed", "symbols_changed"):
            if r[col]:
                try:
                    total_symbols += len(util.loads(r[col]))
                except Exception:
                    pass

    out: list[str] = []
    out.append(f"# Переиндексация {from_rev} → {to_rev} · {util.now_iso()}")
    out.append("")
    out.append(f"Записей журнала: {len(rows)} · файлов: {len(changed_paths)} · "
               f"затронуто символов: {total_symbols}")
    out.append("")
    out.append("## Что изменилось в корпусе")
    if not rows:
        out.append("")
        out.append("Изменений между ревизиями не зафиксировано.")
    for kind in ("add", "modify", "delete", "rename", "schema_change", "slice_boundary"):
        items = by_kind.get(kind) or []
        if not items:
            continue
        out.append("")
        out.append(f"### {kind} — {len(items)}")
        for r in items[:40]:
            detail = []
            for col, label in (("symbols_added", "+"), ("symbols_changed", "~"), ("symbols_removed", "-")):
                if r[col]:
                    try:
                        names = util.loads(r[col])
                        detail.append(f"{label}{len(names)}: " + _names([str(x) for x in names], 4))
                    except Exception:
                        pass
            line = f"- `{r['path']}`"
            if detail:
                line += " — " + "; ".join(detail)
            out.append(line)
            if r["note"]:
                out.append(f"  - примечание: {r['note']}")
        if len(items) > 40:
            out.append(f"- … и ещё {len(items) - 40}")

    out.append("")
    out.append("## Что в знаниях перепроверено, а что не подтвердилось")
    stale_total = 0
    for r in rows:
        if r["edges_invalidated"]:
            stale_total += int(r["edges_invalidated"])
    out.append("")
    out.append(f"Рёбер, помеченных устаревшими в течение ревизии: {stale_total}.")
    out.append("")
    out.append(
        "Все они пересобраны в новой ревизии. Связи, которые не подтвердились при "
        "повторном разборе, отсутствуют в новом снимке — это и есть уточнение знаний."
    )

    out.append("")
    out.append("## Изменённые границы срезов")
    sb = by_kind.get("slice_boundary") or []
    if not sb:
        out.append("")
        out.append("Изменений границ срезов не зафиксировано.")
    else:
        out.append("")
        out.append(
            "Код переносился между функциями — срезы вокруг этих символов изменились, "
            "выводы, сделанные до переиндексации, могли опираться на прежние границы:"
        )
        for r in sb:
            out.append(f"- `{r['path']}` — {r['note'] or 'границы срезов изменены'}")
    out.append("")
    out.append("---")
    out.append("")
    out.append(
        f"Проект: {cfg.project}. Полный список записей — в `control.sqlite`, "
        f"таблица `drift_entry`, ревизия `{from_rev}`."
    )
    return "\n".join(out) + "\n"
