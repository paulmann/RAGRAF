"""Сквозная проверка сторожа на РЕАЛЬНОМ корпусе z.batteryservice.ru.

Запуск: python tests/test_watch_live.py

Что проверяется (то самое требование владельца):
  1. при обращении к RAGRAF проверяется контрольная сумма;
  2. если код изменился после индексации — происходит реиндексация;
  3. после реиндексации новая информация доступна, а сумма совпадает;
  4. повторное обращение без правок НЕ запускает индексацию;
  5. проверка сама не портит контрольную сумму.

Отличие от tests/test_watch.py: там проверка на временном корпусе (быстрая и
изолированная), здесь — на рабочем проекте, с настоящей правкой файла.
Файл восстанавливается в исходное состояние в finally, поэтому проверка
безопасна для повторных запусков.
"""

from __future__ import annotations

import pathlib
import shutil
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf import watch  # noqa: E402
from ragraf.config import load as load_config  # noqa: E402

PASS = 0
FAIL = 0


def check(cond: bool, label: str, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [OK ] {label}")
    else:
        FAIL += 1
        print(f"  [FAIL] {label} {detail}")


def main() -> int:
    cfg = load_config(REPO / "config" / "z-batteryservice.yaml")
    category = watch.category_for(cfg)
    print(f"разработка: {category}")
    print(f"корень: {cfg.root}")

    # Файл для проверки: конфигурация журнала платежей — обычный код проекта.
    target = pathlib.Path(cfg.root) / "agreements" / "journals" / "pl.php"
    probe = pathlib.Path(cfg.root) / "agreements" / "journals" / "_watch_probe.php"
    if not target.exists():
        print(f"нет файла для проверки: {target}")
        return 1

    original = target.read_text(encoding="utf-8")
    created_probe = False

    try:
        # ---------------- 1. Состояние до правки ----------------
        print("\n=== 1. Индекс актуален (правок не было) ===")
        watch.invalidate_cache(category)
        f0 = watch.check(cfg, category, auto_reindex=True, force=True, use_cache=False)
        check(not f0.changed, "изменений не обнаружено", f"changed={f0.changed}")
        check(not f0.stale, "индекс не помечен устаревшим")
        check(not f0.reindexed, "реиндексация не запускалась (нет лишней работы)")
        base_hash = f0.corpus_hash
        check(bool(base_hash), "контрольная сумма посчитана", base_hash[:16])

        # ---------------- 2. Правка существующего файла ----------------
        print("\n=== 2. Правка кода: сторож должен заметить и переиндексировать ===")
        target.write_text(original + "\n// маркер проверки сторожа\n", encoding="utf-8", newline="\n")
        watch.invalidate_cache(category)
        f1 = watch.check(cfg, category, auto_reindex=True, force=True, use_cache=False)
        check(f1.changed or f1.reindexed, "правка обнаружена")
        check(f1.reindexed, "выполнена реиндексация", f"note={f1.note}")
        check(bool(f1.reindex_rev), "получена новая ревизия", f1.reindex_rev)
        check(not f1.stale, "после реиндексации индекс актуален")
        check(f1.corpus_hash == f1.expected_hash, "сумма совпадает с проиндексированной")

        # ---------------- 3. Новый файл ----------------
        print("\n=== 3. Новый файл в корпусе ===")
        probe.write_text(
            "<?php\n\ndeclare(strict_types=1);\n\n"
            "// Временный файл проверки сторожа: создаётся и удаляется тестом.\n"
            "function z_agr_watch_probe(): string\n{\n    return 'watch-probe';\n}\n",
            encoding="utf-8", newline="\n",
        )
        created_probe = True
        watch.invalidate_cache(category)
        f2 = watch.check(cfg, category, auto_reindex=True, force=True, use_cache=False)
        check(f2.reindexed, "новый файл вызвал реиндексацию", f"note={f2.note}")

        # Новая функция должна быть видна в индексе — это подтверждает, что
        # перестроен именно индекс, а не только записана сумма.
        from ragraf.facade import Facade

        facade = Facade(cfg, tool="test", telemetry=False, watch=False)
        try:
            found = facade.op_symbol("z_agr_watch_probe", limit=5)
            check(bool(found.get("found")), "новая функция видна в индексе")
            status = facade.op_freshness(force=True)
            fresh = (status.get("result") or {})
            check(fresh.get("changed_since_index") is False,
                  "операция freshness сообщает: изменений нет")
            check("freshness" in status, "свежесть присутствует в конверте ответа")
        finally:
            facade.close()

        # ---------------- 4. Идемпотентность ----------------
        print("\n=== 4. Повторная проверка без правок ===")
        watch.invalidate_cache(category)
        f3 = watch.check(cfg, category, auto_reindex=True, force=True, use_cache=False)
        check(not f3.reindexed, "повторная реиндексация не выполняется")
        check(not f3.changed, "изменений нет")

        # ---------------- 5. Индексация не портит сумму ----------------
        print("\n=== 5. Индексация не портит контрольную сумму ===")
        watch.invalidate_cache(category)
        before = watch.check(cfg, category, auto_reindex=False, force=True, use_cache=False)
        hash_before = before.corpus_hash
        # Принудительная индексация (та же операция, что делает сторож).
        ok, rev, secs, note = watch.run_reindex(cfg, category, trigger="test")
        check(ok, "индексация выполнена", note)
        watch.invalidate_cache(category)
        after = watch.check(cfg, category, auto_reindex=False, force=True, use_cache=False)
        check(after.corpus_hash == hash_before,
              "сумма после индексации та же, что до неё",
              f"{after.corpus_hash[:16]} != {hash_before[:16]}")
        check(after.expected_hash == after.corpus_hash,
              "сумма в индексе совпадает с суммой корпуса")

        # ---------------- 7. Уборка накопленных экспортов CPG -------------
        # Найдено после включения авто-реиндексации: каждая индексация оставляла
        # экспорт CPG в десятки мегабайт, и каталог вырос до 1,5 ГБ при том, что
        # разбор идёт только по текущей ревизии.
        print("\n=== 7. Уборка накопленных экспортов CPG ===")
        from ragraf.cpg import JoernRunner

        cpg_dir = pathlib.Path(cfg.root) / ".ragraf" / "cpg"
        # Создаются искусственные каталоги экспорта старых ревизий.
        (cpg_dir / "export_rev-0001_neo4jcsv").mkdir(parents=True, exist_ok=True)
        (cpg_dir / "export_rev-0002_neo4jcsv").mkdir(parents=True, exist_ok=True)
        (cpg_dir / "export_rev-9000_neo4jcsv").mkdir(parents=True, exist_ok=True)
        runner = JoernRunner(cfg)
        removed = runner.prune_exports(keep_rev="rev-9000", keep_extra=1)
        check(removed >= 1, "старые экспорты удалены", f"удалено {removed}")
        check((cpg_dir / "export_rev-9000_neo4jcsv").exists(),
              "экспорт текущей ревизии сохранён")
        check(not (cpg_dir / "export_rev-0001_neo4jcsv").exists(),
              "экспорт ранней ревизии удалён")
        check(len(list(cpg_dir.glob("*.bin"))) > 0, "файлы CPG для повторного экспорта сохранены")

    finally:
        # Восстановление: файл возвращается в исходное состояние, временный удаляется.
        print("\n=== Восстановление исходного состояния ===")
        target.write_text(original, encoding="utf-8", newline="\n")
        if created_probe and probe.exists():
            probe.unlink()
        watch.invalidate_cache(category)
        restored = watch.check(cfg, category, auto_reindex=True, force=True, use_cache=False)
        check(not restored.changed or restored.reindexed,
              "исходное состояние восстановлено и проиндексировано",
              f"changed={restored.changed} reindexed={restored.reindexed}")
        check(restored.corpus_hash == restored.expected_hash,
              "сумма после восстановления согласована")

        # Удаляются искусственные каталоги, созданные для проверки уборки ниже.
        cleanup_cpg = pathlib.Path(cfg.root) / ".ragraf" / "cpg"
        for name in ("export_rev-0002_neo4jcsv", "export_rev-9000_neo4jcsv"):
            shutil.rmtree(cleanup_cpg / name, ignore_errors=True)

    print("\n=== ИТОГ ===")
    print(f"пройдено: {PASS}, провалено: {FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
