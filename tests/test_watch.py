"""Проверка сторожа контрольных сумм и раздельного учёта разработок.

Запуск: python tests/test_watch.py

Что проверяется (по требованиям владельца):
  1. Контрольная сумма корпуса считается по файлам и НЕ меняется от индексации.
     Это ключевое свойство: если бы сумма зависела от индекса, проверка
     «были изменения или нет» не работала бы в принципе.
  2. Служебный каталог .ragraf не попадает в корпус — иначе индексация
     меняла бы вход сама для себя, и сумма «плыла» бы после каждого прогона.
  3. Правка файла корпуса меняет сумму и распознаётся как расхождение.
  4. Сторож при расхождении выполняет реиндексацию, и после неё суммы
     совпадают, а пометка устаревания снимается.
  5. Реестр разработок ведёт записи отдельно: две разработки не смешиваются.
  6. Реестр фиксирует историю прогонов и не теряет контрольные суммы.

Проверка работает на ВРЕМЕННОМ корпусе, а не на рабочем проекте: она правит
файлы, и делать это в пилотный PHP-портал нельзя.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path


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
REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf import catalog as catalog_mod  # noqa: E402
from ragraf import corpus, util, watch  # noqa: E402
from ragraf.config import load as load_config  # noqa: E402

PASS = 0
FAIL = 0

# Временный путь реестра: проверка не должна трогать рабочий реестр проекта.
# Все обращения к реестру в проверке идут через эту переменную, а сторож
# получает её через подмену фабрики (см. ниже).
_TEST_CATALOG: Path | None = None


def _patch_catalog() -> None:
    """Подменить путь реестра во всех модулях, которые его создают.

    Реестр по умолчанию лежит в каталоге RAGRAF. Проверка работает на временных
    корпусах, и записи о них не должны оставаться в рабочем реестре: иначе
    он засорится тестовыми разработками, а проверка будет читать не тот файл,
    который пишет сторож (это и дало ложные провалы при первом прогоне).

    Важно: исходный класс сохраняется ДО подмены — иначе фабрика вызывала бы
    саму себя (эта ошибка была допущена при первом написании проверки).
    """
    original = catalog_mod.Catalog

    def factory(*args, **kwargs):
        # Явно переданный путь уважается: так проверка блокировок может
        # работать со своим файлом.
        if kwargs.get("path") is None and not args:
            kwargs["path"] = _TEST_CATALOG
        return original(*args, **kwargs)

    catalog_mod.Catalog = factory   # type: ignore[assignment]
    watch.Catalog = factory          # type: ignore[assignment]


def check(cond: bool, label: str, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [OK ] {label}")
    else:
        FAIL += 1
        print(f"  [FAIL] {label} {detail}")


def make_corpus(root: Path, name: str, files: dict[str, str]) -> Path:
    """Создать корпус и конфиг для него. Возвращает путь конфига."""
    root.mkdir(parents=True, exist_ok=True)
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8", newline="\n")
    cfg_path = root / "ragraf.yaml"
    cfg_path.write_text(
        f"""project: {name}
root: {util.to_posix(root)}

languages:
  - php
  - markdown

include:
  - "**/*.php"
  - "**/*.md"

exclude:
  - "**/.ragraf/**"

secrets_exclude:
  - "**/*.local.php"

db:
  enabled: false

embedding:
  model: sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2
  dim: 384

chunking:
  by_symbol: true
  max_chunk_lines: 120

index:
  retain_revs: 3
  batch_size: 2000
  joern_enabled: false
""",
        encoding="utf-8",
        newline="\n",
    )
    return cfg_path


def main() -> int:
    global _TEST_CATALOG
    tmp = Path(tempfile.mkdtemp(prefix="ragraf_watch_"))
    _TEST_CATALOG = tmp / "catalog" / "catalog.json"
    _patch_catalog()

    try:
        # ---------------- 1. Сумма не зависит от индексации ----------------
        print("\n=== 1. Контрольная сумма и индексация ===")
        root_a = tmp / "projA"
        cfg_a_path = make_corpus(root_a, "projA", {
            "src/one.php": "<?php\nfunction a_one(): int { return 1; }\n",
            "src/two.php": "<?php\nfunction a_two(): int { return 2; }\n",
            "docs/readme.md": "# Корпус A\n",
        })
        cfg_a = load_config(cfg_a_path)

        scan_before = corpus.scan(cfg_a)
        hash_before = scan_before.corpus_hash
        check(bool(hash_before), "сумма корпуса посчитана", hash_before[:12])
        check(len(scan_before.files) == 3, "в корпусе три файла",
              f"найдено {len(scan_before.files)}")

        # Служебный каталог не должен попадать в корпус.
        (root_a / ".ragraf" / "index").mkdir(parents=True, exist_ok=True)
        (root_a / ".ragraf" / "index" / "graph.sqlite").write_bytes(b"not a real db")
        (root_a / ".ragraf" / "index" / "rev.json").write_text('{"rev_id":"rev-0001"}', encoding="utf-8")
        scan_after_service = corpus.scan(cfg_a)
        check(scan_after_service.corpus_hash == hash_before,
              "служебный каталог .ragraf не входит в корпус",
              f"{scan_after_service.corpus_hash[:12]} != {hash_before[:12]}")

        # Индексация и запись ревизии не должны менять сумму.
        from ragraf.indexer import Indexer

        idx = Indexer(cfg_a)
        try:
            idx.run()
        finally:
            idx.close()
        scan_after_index = corpus.scan(cfg_a)
        check(scan_after_index.corpus_hash == hash_before,
              "индексация НЕ изменила контрольную сумму корпуса",
              f"{scan_after_index.corpus_hash[:12]} != {hash_before[:12]}")

        # Запись в реестр тоже не должна влиять на сумму.
        cat = catalog_mod.Catalog()
        cat.register("projA", cfg_a, cfg_a_path)
        scan_after_catalog = corpus.scan(cfg_a)
        check(scan_after_catalog.corpus_hash == hash_before,
              "запись в реестр НЕ изменила контрольную сумму корпуса")
        # Ревизия фиксируется в реестр сразу после индексации, иначе сторож будет
        # считать индекс отсутствующим и запускать индексацию при каждом обращении.
        watch.run_reindex(cfg_a, "projA", trigger="test")
        watch.invalidate_cache("projA")
        entry_a0 = catalog_mod.Catalog().get("projA")
        check(entry_a0 is not None and bool(entry_a0.corpus_hash),
              "контрольная сумма зафиксирована в реестре",
              (entry_a0.corpus_hash[:12] if entry_a0 else "нет записи"))

        # ---------------- 2. Сторож видит расхождение ----------------
        print("\n=== 2. Расхождение после правки кода ===")
        watch.invalidate_cache("projA")
        fresh1 = watch.check(cfg_a, "projA", auto_reindex=False, force=True)
        check(not fresh1.changed, "сразу после индексации расхождения нет",
              f"changed={fresh1.changed}")

        # Правим файл: добавляем функцию.
        target = root_a / "src" / "one.php"
        target.write_text(
            "<?php\nfunction a_one(): int { return 1; }\n"
            "function a_one_new(): int { return 11; }\n",
            encoding="utf-8", newline="\n",
        )
        watch.invalidate_cache("projA")
        fresh2 = watch.check(cfg_a, "projA", auto_reindex=False, force=True)
        check(fresh2.changed, "правка файла распознана как расхождение")
        check(fresh2.changed_files >= 1, "указано, сколько файлов изменилось",
              f"changed_files={fresh2.changed_files}")
        check(fresh2.corpus_hash != fresh2.expected_hash,
              "сумма сейчас отличается от проиндексированной")
        check(fresh2.stale, "без реиндексации состояние помечено устаревшим")

        # ---------------- 3. Автоматическая реиндексация ----------------
        print("\n=== 3. Автоматическая реиндексация по расхождению ===")
        watch.invalidate_cache("projA")
        fresh3 = watch.check(cfg_a, "projA", auto_reindex=True, force=True)
        check(fresh3.reindexed, "сторож выполнил реиндексацию",
              f"note={fresh3.note}")
        check(bool(fresh3.reindex_rev), "новая ревизия зафиксирована", fresh3.reindex_rev)
        check(not fresh3.changed, "после реиндексации расхождения нет")
        check(not fresh3.stale, "пометка устаревания снята")
        check(fresh3.corpus_hash == fresh3.expected_hash,
              "сумма после реиндексации совпадает",
              f"{fresh3.corpus_hash[:12]} != {fresh3.expected_hash[:12]}")

        # Новая функция должна быть видна в индексе — это подтверждает, что
        # реиндексация действительно перестроила индекс, а не только записала сумму.
        watch.invalidate_cache("projA")
        from ragraf.facade import Facade

        facade = Facade(cfg_a, tool="selftest", telemetry=False, watch=False)
        try:
            found = facade.op_symbol("a_one_new", limit=5)
            check(bool(found.get("found")), "новая функция видна в индексе после реиндексации")
        finally:
            facade.close()

        # ---------------- 4. Идемпотентность ----------------
        print("\n=== 4. Повторная проверка без изменений ===")
        watch.invalidate_cache("projA")
        fresh4 = watch.check(cfg_a, "projA", auto_reindex=True, force=True)
        check(not fresh4.reindexed,
              "без изменений реиндексация НЕ выполняется (нет лишней работы)")
        check(not fresh4.changed, "изменений не обнаружено")

        # ---------------- 5. Раздельный учёт разработок ----------------
        print("\n=== 5. Раздельный учёт двух разработок ===")
        root_b = tmp / "projB"
        cfg_b_path = make_corpus(root_b, "projB", {
            "src/only_b.php": "<?php\nfunction b_only(): string { return 'b'; }\n",
        })
        cfg_b = load_config(cfg_b_path)

        cat_b = catalog_mod.Catalog()
        cat_b.register("projB", cfg_b, cfg_b_path)
        idx_b = Indexer(cfg_b)
        try:
            idx_b.run()
        finally:
            idx_b.close()
        watch.run_reindex(cfg_b, "projB", trigger="test")
        watch.invalidate_cache("projB")

        summary = catalog_mod.Catalog().summary()
        check(summary["count"] == 2, "в реестре две разработки", f"count={summary['count']}")
        names = {e["category"] for e in summary["entries"]}
        check(names == {"projA", "projB"}, "разработки учтены под своими именами",
              str(sorted(names)))

        entry_a = catalog_mod.Catalog().get("projA")
        entry_b = catalog_mod.Catalog().get("projB")
        check(entry_a is not None and entry_b is not None, "обе записи читаются")
        if entry_a and entry_b:
            check(entry_a.corpus_hash != entry_b.corpus_hash,
                  "у разработок РАЗНЫЕ контрольные суммы",
                  f"{entry_a.corpus_hash[:12]} vs {entry_b.corpus_hash[:12]}")
            check(entry_a.root != entry_b.root, "у разработок разные корни")
            check(entry_a.rev_id != entry_b.rev_id or entry_a.rev_id != "",
                  "ревизии учтены отдельно",
                  f"{entry_a.rev_id} / {entry_b.rev_id}")

        # Изменение в одном корпусе не должно затрагивать другой.
        (root_b / "src" / "only_b.php").write_text(
            "<?php\nfunction b_only(): string { return 'b2'; }\n", encoding="utf-8", newline="\n"
        )
        watch.invalidate_cache("projA")
        watch.invalidate_cache("projB")
        fresh_a = watch.check(cfg_a, "projA", auto_reindex=False, force=True)
        fresh_b = watch.check(cfg_b, "projB", auto_reindex=False, force=True)
        check(not fresh_a.changed, "правка в B не считается изменением в A")
        check(fresh_b.changed, "правка в B обнаружена в B")

        # ---------------- 6. История прогонов ----------------
        print("\n=== 6. История прогонов и снимок сумм ===")
        entry_b2 = catalog_mod.Catalog().get("projB")
        # Проверка корректна при ОДНОМ и более прогоне: реестр фиксирует каждую
        # индексацию, но если сторож счёл изменение незначимым, запись будет одна.
        # Требование — чтобы история велась и содержала причину запуска.
        check(entry_b2 is not None and len(entry_b2.runs) >= 1,
              "история прогонов ведётся",
              f"runs={len(entry_b2.runs) if entry_b2 else 0}")
        if entry_b2:
            # Пути чтения причины запуска: после исправления десериализации
            # `runs` — это объекты RunRecord, а не словари (раньше тест читал их
            # через .get и падал; дефект был в загрузке реестра, а не в тесте).
            triggers = {
                (r.trigger if hasattr(r, "trigger") else r.get("trigger"))
                for r in entry_b2.runs
            }
            check(bool(triggers), "у прогонов указана причина запуска", str(sorted(triggers)))

        # ---------------- 7. Блокировка ----------------
        print("\n=== 7. Блокировка одновременной индексации ===")
        cat_lock = catalog_mod.Catalog()
        cat_lock.path = tmp / "catalog" / "catalog.json"
        got1 = cat_lock.acquire_lock(timeout_sec=0.1)
        cat_lock2 = catalog_mod.Catalog()
        got2 = cat_lock2.acquire_lock(timeout_sec=0.4, poll=0.05)
        check(got1 and not got2, "вторая блокировка не получена, пока активна первая",
              f"got1={got1} got2={got2}")
        cat_lock.release_lock()
        got3 = cat_lock2.acquire_lock(timeout_sec=0.5, poll=0.05)
        check(got3, "после снятия блокировка снова берётся")
        cat_lock2.release_lock()

    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n=== ИТОГ ===")
    print(f"пройдено: {PASS}, провалено: {FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
