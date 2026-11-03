"""Проверка: блок `storage` в ответе согласован с файлами на диске и с подсчётом строк.

Измеренный дефект (30.10.2026): хранилище занимало 8,49 ГБ при трёх ревизиях данных, из них
3 835 216 строк документного слоя принадлежали 265 ревизиям, которых в истории нет. В ответе
`index_status` не было ни объёма, ни хвоста — дефект был невидим.

Свойства, которые проверяются (каждое — против независимого источника):

  1. `storage.stores.<x>.bytes` равен фактическому размеру файла на диске;
  2. `storage.total_gb` согласован с суммой размеров;
  3. `storage.orphan_revisions` и `orphan_document_rows` совпадают с прямым SQL-подсчётом
     строк `document_chunk`, чьих ревизий нет в таблице `rev`;
  4. `over_budget` согласован с порогом `budget_gb` в обе стороны;
  5. если хвост есть — в ответе есть `note` с числом (иначе читатель чисел его не увидит);
  6. `storage.stores.control.bytes` равен размеру `control.sqlite` — третье хранилище,
     добавленное этапом 9 (история ревизий, журналы, настройки доступа);
  7. `storage.cpg.bytes` равен фактическому объёму `.ragraf/cpg` — каталог, который
     до этапа 9 не назывался вовсе, хотя занимал вдвое больше обоих хранилищ;
  8. `storage.ragraf_total_mb` согласован с суммой трёх хранилищ, `cpg` и `tmp`.

Запуск: python tools/check_storage_disclosure.py [--root <каталог>] [--json]
Код 1 при находках.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sqlite3
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description="Объём хранилища и хвост ротации названы")
    parser.add_argument("--root", default=str(pathlib.Path(__file__).resolve().parents[1]))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    root = pathlib.Path(args.root)
    sys.path.insert(0, str(root / "src"))

    from ragraf import config as config_mod
    from ragraf.facade import Facade

    cfg_path = root / "config" / "ragraf.yaml"
    if not cfg_path.is_file():
        print("NOT-IMPLEMENTED: нет config/ragraf.yaml")
        return 1
    cfg = (getattr(config_mod, "load", None) or getattr(config_mod, "load_config"))(cfg_path)
    facade = Facade(cfg)
    result = facade.op_index_status().get("result") or {}
    storage = result.get("storage") or {}
    problems: list[str] = []
    examined = 0

    if not storage:
        print("НАХОДКА: в ответе нет блока storage — объём хранилища не назван")
        return 1

    # 1. Размеры против диска.
    for label, path_value in (("graph", cfg.graph_db), ("lexvec", cfg.lexvec_db)):
        entry = (storage.get("stores") or {}).get(label) or {}
        path = pathlib.Path(path_value)
        if not path.is_file():
            problems.append(f"{label}: файла нет, а блок storage его описывает")
            continue
        actual = path.stat().st_size
        examined += actual
        if int(entry.get("bytes") or -1) != actual:
            problems.append(f"{label}: в ответе {entry.get('bytes')} байт, на диске {actual}")

    # 2. Итог против суммы.
    total_answer = float(storage.get("total_gb") or 0.0)
    total_actual = round(sum(int(((storage.get("stores") or {}).get(k) or {}).get("bytes") or 0)
                            for k in ("graph", "lexvec")) / 1_073_741_824, 2)
    if abs(total_answer - total_actual) > 0.02:
        problems.append(f"total_gb {total_answer} не совпадает с суммой размеров {total_actual}")

    # 3. Хвост ротации против прямого подсчёта.
    graph = sqlite3.connect(f"file:{cfg.graph_db}?mode=ro", uri=True)
    try:
        history = {str(r[0]) for r in graph.execute("SELECT rev_id FROM rev")}
    finally:
        graph.close()
    lex = sqlite3.connect(f"file:{cfg.lexvec_db}?mode=ro", uri=True)
    try:
        rows = lex.execute("SELECT rev_id, COUNT(*) FROM document_chunk GROUP BY rev_id").fetchall()
    finally:
        lex.close()
    orphan_revs = {str(rev) for rev, _ in rows if str(rev) not in history}
    orphan_rows = sum(int(n) for rev, n in rows if str(rev) not in history)
    if int(storage.get("orphan_revisions") or 0) != len(orphan_revs):
        problems.append(f"orphan_revisions {storage.get('orphan_revisions')} против подсчёта "
                        f"{len(orphan_revs)}")
    if int(storage.get("orphan_document_rows") or 0) != orphan_rows:
        problems.append(f"orphan_document_rows {storage.get('orphan_document_rows')} против "
                        f"подсчёта {orphan_rows}")

    # 4. Порог в обе стороны.
    budget = float(storage.get("budget_gb") or 0)
    if budget <= 0:
        problems.append("budget_gb не задан: порог нельзя проверить")
    else:
        expected = total_answer > budget
        if bool(storage.get("over_budget")) != expected:
            problems.append(f"over_budget={storage.get('over_budget')} при объёме {total_answer} ГБ "
                            f"и пороге {budget} ГБ")

    # 6. Третье хранилище — управление (этап 9).
    control_entry = (storage.get("stores") or {}).get("control") or {}
    control_path = pathlib.Path(cfg.control_db)
    if control_path.is_file():
        if int(control_entry.get("bytes") or -1) != control_path.stat().st_size:
            problems.append(
                f"control: в ответе {control_entry.get('bytes')} байт, на диске "
                f"{control_path.stat().st_size}")
    else:
        problems.append("control: файла нет, а блок storage его описывает")

    # 7. Каталог cpg: объём против диска (независимый обход).
    cpg_entry = storage.get("cpg") or {}
    cpg_dir = pathlib.Path(cfg.ragraf_dir) / "cpg"
    actual_cpg = 0
    if cpg_dir.is_dir():
        stack = [cpg_dir]
        while stack:
            current = stack.pop()
            for item in current.iterdir():
                try:
                    if item.is_dir():
                        stack.append(item)
                    elif item.is_file():
                        actual_cpg += item.stat().st_size
                except OSError:
                    continue
    # Допуск назван явно: ответ может быть СНИМКОМ (`index_status_snapshot.json`),
    # а каталог cpg меняется на каждом joern-запросе — новый `out_*.json` или
    # `query_*.sc`. Требовать побайтового равенства значило бы красить проверку
    # из-за файла, появившегося после снимка. Ложь measuring имеет другой порядок:
    # до этапа 9 в ответе не было cpg вовсе при 17,8 ГБ на диске.
    tolerance = max(1_048_576, int(actual_cpg * 0.02))
    if abs(int(cpg_entry.get("bytes") or -1) - actual_cpg) > tolerance:
        problems.append(f"cpg: в ответе {cpg_entry.get('bytes')} байт, на диске {actual_cpg}")

    # 8. Итог по хранилищам, cpg и tmp.
    total_answer_mb = float(storage.get("ragraf_total_mb") or 0.0)
    expected_mb = round(
        sum(int(((storage.get("stores") or {}).get(key) or {}).get("bytes") or 0)
            for key in ("graph", "lexvec", "control"))
        / 1_048_576
        + int(cpg_entry.get("bytes") or 0) / 1_048_576
        + int((storage.get("tmp") or {}).get("ragraf_tmp_mb") or 0), 1)
    if abs(total_answer_mb - expected_mb) > 0.3:
        problems.append(f"ragraf_total_mb {total_answer_mb} не совпадает с суммой {expected_mb}")

    # 5. Примечание при хвосте.
    if orphan_rows and not str(storage.get("note") or "").strip():
        problems.append("хвост ротации есть, но note пуст: число не объяснено словами")

    payload = {
        "ok": not problems,
        "examined_bytes": examined,
        "total_gb": total_answer,
        "budget_gb": budget,
        "orphan_revisions": len(orphan_revs),
        "orphan_rows": orphan_rows,
        "problems": problems,
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"[check_storage_disclosure] хранилище {examined} байт "
              f"({total_answer} ГБ при пороге {budget} ГБ)")
        print(f"  хвост ротации: {len(orphan_revs)} ревизий, {orphan_rows} строк "
              f"документного слоя")
        for problem in problems:
            print(f"  НАХОДКА: {problem}")
        print("ИТОГ:", "чисто" if not problems else f"находок {len(problems)}")
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())
