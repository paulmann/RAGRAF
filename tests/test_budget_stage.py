"""Измеренный дефект блока 24: отказ по бюджету называл стадию, на которой его спросили.

Замер 23.11.2026 (корпус 4 693 файла, бюджет 60 с): прогон шёл 323,8 с и сообщал
«бюджет исчерпан на стадии schema», хотя стадия schema не начиналась — проверка стояла
только на границах стадий, а тяжёлые проходы (разрешение имён, наследование, импорты)
шли между ними без проверок.

Закрепляется свойство порядка, а не написание:
1. предел проверяется ВНУТРИ тяжёлого прохода, поэтому названная стадия — та, что шла;
2. исключение, брошенное внутри прохода, не выходит из `run()` трейсбеком, а даёт
   `ok=False` с причиной;
3. активная ревизия корпуса не меняется — прерванный прогон не закрепляет новую;
4. счётчик сделанного относится к проходу, а не к нулю следующей стадии.

Проверка не воспроизводит 323 секунды: проход подменяется тем, что бросает то же
исключение, что и настоящий `check_budget`.
"""
import pathlib
import re
import sqlite3
import sys
import tempfile

ROOT = pathlib.Path(r"C:\AutoClaw\RAGRAF")
sys.path.insert(0, str(ROOT / "src"))

from ragraf import config as config_mod  # noqa: E402
from ragraf import indexer as indexer_mod  # noqa: E402
from ragraf import store as store_mod  # noqa: E402

PHP = ("<?php\nfunction f{i}($v) {{ $o = new S{i}(); return $o->get($v); }}\n"
       "class S{i} {{ public function get($v) {{ return $v; }} }}\n")

PASS = 0
FAIL = 0
problems: list[str] = []


def check(cond: bool, message: str, detail: str = '') -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        problems.append(message + (f' | {detail}' if detail else ''))


def main() -> int:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        work = pathlib.Path(tmp)
        corpus = work / "corpus"
        (corpus / "src").mkdir(parents=True)
        (corpus / "composer.json").write_text("{}", encoding="utf-8")
        for i in range(40):
            (corpus / "src" / f"u{i:03d}.php").write_text(PHP.format(i=i), encoding="utf-8")

        config_path = work / "corpus.yaml"
        config_path.write_text(
            f"project: budget-stage\nroot: {corpus.as_posix()}\n"
            "languages:\n  - php\ninclude:\n  - \"**/*.php\"\n"
            "exclude:\n  - \"**/.ragraf/**\"\n", encoding="utf-8")
        cfg = config_mod.load(config_path)

        # 1. Нормальный прогон: ревизия закреплена, её и запоминаем как рабочую.
        good = indexer_mod.Indexer(cfg)
        first = good.run(budget_seconds=600)
        check(first.ok, f"подготовительный прогон не собрался: {first.notes[-1:]}")
        good.close()
        rev_conn = store_mod.connect(cfg.graph_db) if hasattr(store_mod, "connect") else None
        if rev_conn is None:
            check(False, "нет store.connect — активную ревизию не прочитать")
            return 1
        active_before, _ = store_mod.active_revision(rev_conn)
        rev_conn.close()
        check(bool(active_before), "после успешного прогона активной ревизии нет")

        # 2. Прогон, у которого проход разрешения имён упирается в предел.
        broken = indexer_mod.Indexer(cfg)

        def boom(rev_id: str, check_budget=None):
            raise indexer_mod._BudgetExceeded("resolve_names", 67743)

        broken._resolve_external_names = boom  # type: ignore[method-assign]
        report = broken.run(budget_seconds=600)
        broken.close()

        check(report.ok is False, f"ok={report.ok!r}, ожидалось False (прерванный прогон)")
        check(report.budget_exceeded, "признак превышения бюджета не выставлен")
        check(report.budget_stage == "resolve_names",
              f"названа стадия {report.budget_stage!r} вместо 'resolve_names'")
        check(report.budget_files_done == 67743,
              f"счётчик сделанного {report.budget_files_done}, ожидалось 67743 (счётчик прохода)")
        note_text = " ".join(report.notes)
        check("resolve_names" in note_text, "причина не называет стадию прогона")

        # Файлы и время (блок 25): по ним принимают решение о бюджете, а не по рёбрам.
        conn = sqlite3.connect(cfg.graph_db)
        try:
            written = conn.execute(
                "SELECT COUNT(*) FROM file WHERE rev_id = ?", (report.rev_id,)).fetchone()[0]
        finally:
            conn.close()
        files_in_note = re.search(r"попало файлов (\d+)", note_text)
        check(files_in_note is not None and int(files_in_note.group(1)) == written,
              "причина называет число файлов, попавших в ревизию (совпадает с графом)",
              f"в причине {files_in_note.group(1) if files_in_note else '—'}, в графе {written}")
        seconds_in_note = re.search(r"прерван через ([\d.]+) с", note_text)
        check(seconds_in_note is not None
              and abs(float(seconds_in_note.group(1)) - report.budget_seconds_spent) < 0.2,
              "причина называет время прогона (совпадает с полем отчёта)",
              f"в причине {seconds_in_note.group(1) if seconds_in_note else '—'}, "
              f"поле {report.budget_seconds_spent}")
        check("ревизия не закреплена" in note_text,
              "причина не говорит, что ревизия не закреплена")

        # 3. Активная ревизия осталась прежней: прерванный прогон её не подменил.
        rev_conn = store_mod.connect(cfg.graph_db)
        active_after, _ = store_mod.active_revision(rev_conn)
        rev_conn.close()
        before_rev = (active_before or {}).get("rev_id")
        after_rev = (active_after or {}).get("rev_id")
        check(before_rev == after_rev,
              f"активная ревизия сменилась {before_rev!r} → {after_rev!r}: прерванный прогон закрепил новую")
        check(after_rev != report.rev_id,
              f"прерванная ревизия {report.rev_id!r} стала активной")

    if problems:
        for problem in problems:
            print(f"FAIL: {problem}")
        return 1
    print(f"ИТОГ: пройдено {PASS}, провалено {FAIL}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
