"""Проверка порога накопления правок и двухуровневой реиндексации.

Запуск: python tests/test_threshold.py

Что проверяется:
  1. при отсутствии правок реиндексация не требуется;
  2. единичная правка запускает БЫСТРЫЙ режим (без CPG);
  3. накопление ниже порога полного пересбора остаётся быстрым режимом;
  4. достижение порога полного пересбора переключает на полный режим;
  5. устаревший индекс (превышен временной предел) даёт полный режим
     даже при малом числе правок;
  6. быстрый режим индексации работает и не портит контрольную сумму;
  7. пороги читаются из конфигурации, а не зашиты в код.

Проверка работает на ВРЕМЕННОМ корпусе: она правит файлы и запускает
индексацию, а делать это на рабочем проекте нельзя.
"""

from __future__ import annotations

import pathlib
import shutil
import sys
import tempfile


# Отчёт печатается по-русски, а консоль Windows по умолчанию не UTF-8: без этой
# настройки запуск падал UnicodeEncodeError ('charmap' codec can't encode),
# причём только на Windows и только вне UTF-8-консоли — в CI это валило job.
# Проверено: PYTHONIOENCODING=cp1252 -> exit 1. Настройка делает тест
# независимым от локали/консоли (Python 3.7+: TextIOWrapper.reconfigure).
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf import catalog as catalog_mod  # noqa: E402
from ragraf import corpus, util, watch  # noqa: E402
from ragraf.config import load as load_config  # noqa: E402

PASS = 0
FAIL = 0
_TEST_CATALOG: pathlib.Path | None = None


def check(cond: bool, label: str, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [OK ] {label}")
    else:
        FAIL += 1
        print(f"  [FAIL] {label} {detail}")


def _patch_catalog() -> None:
    """Реестр подменяется на временный: проверка не должна трогать рабочий."""
    original = catalog_mod.Catalog

    def factory(*args, **kwargs):
        if kwargs.get("path") is None and not args:
            kwargs["path"] = _TEST_CATALOG
        return original(*args, **kwargs)

    catalog_mod.Catalog = factory  # type: ignore[assignment]
    watch.Catalog = factory  # type: ignore[assignment]


def main() -> int:
    global _TEST_CATALOG
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="ragraf_threshold_"))
    _TEST_CATALOG = tmp / "catalog" / "catalog.json"
    _patch_catalog()

    root = tmp / "proj"
    (root / "src").mkdir(parents=True)
    files = {
        "src/a.php": "<?php\nfunction t_a(): int { return 1; }\n",
        "src/b.php": "<?php\nfunction t_b(): int { return 2; }\n",
        "src/c.php": "<?php\nfunction t_c(): int { return 3; }\n",
    }
    for rel, text in files.items():
        (root / rel).write_text(text, encoding="utf-8", newline="\n")

    cfg_path = root / "ragraf.yaml"
    cfg_path.write_text(
        "project: threshold\n"
        f"root: {util.to_posix(root)}\n"
        "\n"
        "languages:\n  - php\n"
        "\n"
        "include:\n  - \"**/*.php\"\n"
        "\n"
        "exclude:\n  - \"**/.ragraf/**\"\n"
        "\n"
        "db:\n  enabled: false\n"
        "\n"
        "index:\n"
        "  retain_revs: 3\n"
        "  batch_size: 2000\n"
        "  joern_enabled: false\n"
        "  reindex_after_changes: 3\n"
        "  full_reindex_after_changes: 7\n"
        "  reindex_interval_minutes: 30\n"
        # Автоподбор выключается: этот тест проверяет МЕХАНИЗМ порогов (какой
        # режим выбирается при данном числе правок), а не их подбор. Иначе
        # автоподбор на лёгком временном корпусе законно снизит пороги и тест
        # будет проверять не то. Сам автоподбор проверяется отдельно —
        # tests/test_auto_thresholds.py.
        "  auto_thresholds: false\n",
        encoding="utf-8", newline="\n",
    )
    cfg = load_config(cfg_path)
    category = "threshold"

    try:
        # ---------------- 1. Пороги читаются из конфига ----------------
        print("\n=== 1. Пороги из конфигурации ===")
        check(cfg.index.reindex_after_changes == 3, "порог быстрой = 3",
              str(cfg.index.reindex_after_changes))
        check(cfg.index.full_reindex_after_changes == 7, "порог полного = 7",
              str(cfg.index.full_reindex_after_changes))
        check(cfg.index.reindex_interval_minutes == 30, "интервал = 30 мин",
              str(cfg.index.reindex_interval_minutes))

        # ---------------- 2. Первичная индексация ----------------
        print("\n=== 2. Первичная индексация ===")
        ok, rev, secs, note = watch.run_reindex(cfg, category, trigger="test", mode="full")
        check(ok, "индексация выполнена", note)
        check(bool(rev), "ревизия получена", rev)
        watch.invalidate_cache(category)

        # ---------------- 3. Без правок — пересбор не нужен ----------------
        print("\n=== 3. Без правок ===")
        mode, info = watch.choose_mode(cfg, rev, changed=False)
        check(mode == "none", "режим none: пересбор не требуется", f"режим={mode}")

        # ---------------- 4. Одна правка — быстрый режим ----------------
        print("\n=== 4. Одна правка: ожидается быстрый режим ===")
        target = root / "src" / "a.php"
        target.write_text(
            "<?php\nfunction t_a(): int { return 1; }\nfunction t_a2(): int { return 11; }\n",
            encoding="utf-8", newline="\n",
        )
        watch.invalidate_cache(category)
        fresh = watch.check(cfg, category, auto_reindex=True, force=True, use_cache=False)
        check(fresh.reindexed, "реиндексация выполнена", fresh.note)
        check(fresh.reindex_mode == "fast", "режим быстрый (без CPG)", fresh.reindex_mode)
        check(fresh.reindex_seconds < 20, "быстрый режим уложился в разумное время",
              f"{fresh.reindex_seconds:.1f} с")
        check(fresh.changes_threshold == 3, "порог быстрой передан в ответ",
              str(fresh.changes_threshold))
        check(fresh.full_threshold == 7, "порог полного передан в ответ",
              str(fresh.full_threshold))

        # ---------------- 5. Выбор режима по накоплению ----------------
        print("\n=== 5. Выбор режима при разном накоплении ===")
        original_measure = watch.measure_accumulation
        for changes, expected in ((1, "fast"), (2, "fast"), (3, "fast"),
                                  (6, "fast"), (7, "full"), (15, "full")):
            watch.measure_accumulation = lambda c, r, _f=changes, _a=1: (_f, _a)
            try:
                mode, _ = watch.choose_mode(cfg, rev, changed=True)
            finally:
                watch.measure_accumulation = original_measure
            check(mode == expected, f"правок {changes} → режим {expected}",
                  f"получен {mode}")

        # ---------------- 6. Устаревший индекс — полный режим ----------------
        print("\n=== 6. Устаревший индекс: полный режим даже без накопления ===")
        watch.measure_accumulation = lambda c, r: (1, cfg.index.reindex_interval_minutes + 5)
        try:
            mode, info = watch.choose_mode(cfg, rev, changed=True)
        finally:
            watch.measure_accumulation = original_measure
        check(mode == "full", "превышение интервала даёт полный режим", f"режим={mode}")
        check(info.get("interval_minutes") is True, "признак достижения интервала выставлен")

        # ---------------- 7. Быстрый режим не портит сумму ----------------
        print("\n=== 7. Быстрый режим не портит контрольную сумму ===")
        before = corpus.scan(cfg).corpus_hash
        ok, rev2, secs2, note2 = watch.run_reindex(cfg, category, trigger="test", mode="fast")
        check(ok, "быстрая индексация выполнена", note2)
        after = corpus.scan(cfg).corpus_hash
        check(before == after, "сумма до и после совпадает",
              f"{before[:12]} != {after[:12]}")
        # Режим Joern должен вернуться в исходное состояние.
        check(cfg.index.joern_enabled is False,
              "режим CPG восстановлен после прогона", str(cfg.index.joern_enabled))

        watch.invalidate_cache(category)
        post = watch.check(cfg, category, auto_reindex=False, force=True, use_cache=False)
        check(not post.changed, "после быстрой индексации расхождения нет")

    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n=== ИТОГ ===")
    print(f"пройдено: {PASS}, провалено: {FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
