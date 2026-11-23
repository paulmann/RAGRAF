"""Уборка после `partial`: прерванная ревизия не остаётся в индексе навсегда.

Измеренный дефект (23.11.2026). После прерывания по бюджету в `graph.sqlite` остаётся
ревизия в состоянии `building`; её данные не удаляло ничто:
  * ротация берёт только `state='ready'` (`src/ragraf/revisions.py:326`);
  * `compact` считал сиротами лишь ревизии без записи в `rev`, а прерванная запись есть;
  * `storage.plan` называл её АКТИВНОЙ и держал в списке хранения.
Замер: 3 604 строки переживают успешный прогон, индекс 1,5 → 3,1 МБ.

Проверяются четыре свойства, а не текст:
1. план называет активной последнюю ЗАПЕЧАТАННУЮ, а не последнюю записанную;
2. прерванная ревизия перечислена как незапечатанная и попадает в список удаления;
3. `compact --apply` удаляет её строки;
4. строки закреплённой ревизии при этом не меняются.
"""
import pathlib
import sqlite3
import sys
import tempfile

ROOT = pathlib.Path(r"C:\AutoClaw\RAGRAF")
sys.path.insert(0, str(ROOT / "src"))

from ragraf import config as config_mod  # noqa: E402
from ragraf import indexer as indexer_mod  # noqa: E402
from ragraf import storage as storage_mod  # noqa: E402

PHP = ("<?php\nfunction g{i}($v) {{ $o = new T{i}(); return $o->get($v); }}\n"
       "class T{i} {{ public function get($v) {{ return $v; }} }}\n")

PASS = 0
FAIL = 0
problems: list[str] = []


def check(cond: bool, message: str) -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        problems.append(message)


def counts(db: pathlib.Path) -> dict[str, int]:
    conn = sqlite3.connect(db)
    try:
        return {str(rev): int(n) for rev, n in conn.execute(
            "SELECT rev_id, COUNT(*) FROM node GROUP BY rev_id").fetchall()}
    finally:
        conn.close()


def main() -> int:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        work = pathlib.Path(tmp)
        corpus = work / "corpus"
        (corpus / "src").mkdir(parents=True)
        (corpus / "composer.json").write_text("{}", encoding="utf-8")
        for i in range(200):
            (corpus / "src" / f"u{i:03d}.php").write_text(PHP.format(i=i), encoding="utf-8")
        config_path = work / "corpus.yaml"
        config_path.write_text(
            f"project: partial-clean\nroot: {corpus.as_posix()}\n"
            "languages:\n  - php\ninclude:\n  - \"**/*.php\"\n"
            "exclude:\n  - \"**/.ragraf/**\"\n", encoding="utf-8")
        cfg = config_mod.load(config_path)

        good = indexer_mod.Indexer(cfg)
        first = good.run(budget_seconds=600)
        good.close()
        check(first.ok, "подготовительный прогон не собрался")

        broken = indexer_mod.Indexer(cfg)

        def boom(rev_id: str, check_budget=None):
            raise indexer_mod._BudgetExceeded("resolve_names", 20)

        broken._resolve_external_names = boom  # type: ignore[method-assign]
        second = broken.run(budget_seconds=600)
        broken.close()
        check(second.rev_id != first.rev_id, "прерванный прогон не создал свою ревизию")
        before = counts(cfg.graph_db)

        plan = storage_mod.plan(cfg)
        check((plan.get("active") or {}).get("rev_id") == first.rev_id,
              f"активной названа {(plan.get('active') or {}).get('rev_id')}, ожидалась {first.rev_id}")
        check(second.rev_id in (plan.get("revisions_unsealed") or []),
              f"прерванная ревизия не названа незапечатанной: {plan.get('revisions_unsealed')}")
        check(second.rev_id in (plan.get("orphans", {}).get("revision_ids") or []),
              "прерванная ревизия не попала в список удаления")
        check(second.rev_id not in (plan.get("revisions_kept") or []),
              "прерванная ревизия попала в список хранения")

        storage_mod.apply(cfg, vacuum=True, remove_cpg=False, prune_temp=False)
        after = counts(cfg.graph_db)
        check(second.rev_id not in after, "строки прерванной ревизии остались после уборки")
        check(before.get(first.rev_id) == after.get(first.rev_id),
              f"закреплённая ревизия потеряла узлы: {before.get(first.rev_id)} → {after.get(first.rev_id)}")

        # Запись ревизии тоже уходит: иначе план вечно обещает удалить то, чего нет.
        conn = sqlite3.connect(cfg.graph_db)
        try:
            states = dict(conn.execute("SELECT rev_id, state FROM rev").fetchall())
        finally:
            conn.close()
        check(second.rev_id not in states,
              f"запись прерванной ревизии осталась в rev: {states}")
        check(states.get(first.rev_id) == "ready",
              f"запись закреплённой ревизии потеряна или изменилась: {states}")
        plan_after = storage_mod.plan(cfg)
        check(not (plan_after.get("revisions_unsealed") or []),
              f"план после уборки снова называет незапечатанные: {plan_after.get('revisions_unsealed')}")

    if problems:
        for problem in problems:
            print(f"FAIL: {problem}")
        return 1
    print(f"ИТОГ: пройдено {PASS}, провалено {FAIL}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
