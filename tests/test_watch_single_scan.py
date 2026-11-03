#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Одна сверка корпуса вместо трёх, и отпечаток, который не умирает навсегда.

Запуск: python tests/test_watch_single_scan.py

Какой дефект закрепляет набор. `ensure` — первый шаг работы агента, и он зовёт
сторожа. До 01.11.2026 сторож на одном вызове:

  1. обходил дерево за отпечатком (`quick_check`), потом
  2. читал весь корпус ради контрольной суммы (`current_hashes`), потом
  3. читал его ЕЩЁ РАЗ внутри `run_reindex`, потом
  4. читал его ТРЕТИЙ раз сразу после успешной переиндексации.

Замер на корпусе 944 файла: обход с чтением — 1,20 с, без чтения — 0,18 с.
Хуже другое: отпечаток входа писался только ПОСЛЕ индексации, поэтому любое
касание файла (перезапись без изменения содержимого, распаковка, утилита чистки)
разводило его с сохранённым НАВСЕГДА — дешёвый путь больше не подтверждал
свежесть, и каждый вызов операции читал корпус целиком (измерено: 2,2 с вместо
0,24 с на `ensure`).

Свойства (каждое падает при возврате дефекта):

  1. отпечаток из полного скана равен отпечатку из дешёвого — их можно
     сравнивать, поэтому полной сверке достаточно ОДНОГО обхода;
  2. полная сверка, доказавшая «сумма совпала», ЛЕЧИТ отпечаток: следующий
     вызов идёт дешёвым путём и не читает корпус;
  3. на одном вызове сторожа полный скан выполняется ровно один раз;
  4. сторож не зацикливается: после переиндексации следующий вызов отвечает
     «актуально» и ничего не пересобирает;
  5. сумма, записанная в реестр, соответствует СОДЕРЖИМОМУ: прогон, прерванный
     по бюджету, в реестр не пишется и состояние разработки не меняет;
  6. если файлы менялись во время прогона, сумма пересчитывается заново
     (`rescan`), а не берётся из состояния до прогона.

Проверка работает на ВРЕМЕННОМ корпусе: она правит файлы и пересобирает индекс,
делать это в рабочем проекте нельзя.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf import catalog as catalog_mod  # noqa: E402
from ragraf import corpus, util, watch  # noqa: E402
from ragraf.config import load as load_config  # noqa: E402

PASS = 0
FAIL = 0
_TEST_CATALOG: Path | None = None


def _patch_catalog() -> None:
    """Подменить путь реестра во всех модулях, которые его создают.

    Реестр по умолчанию лежит в каталоге RAGRAF, а проверка работает на временных
    корпусах: записи о них не должны оставаться в рабочем реестре.
    """
    original = catalog_mod.Catalog

    def factory(*args, **kwargs):
        if kwargs.get("path") is None and not args:
            kwargs["path"] = _TEST_CATALOG
        return original(*args, **kwargs)

    catalog_mod.Catalog = factory      # type: ignore[assignment]
    watch.Catalog = factory            # type: ignore[assignment]


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
  reindex_after_changes: 1
  full_reindex_after_changes: 40
""",
        encoding="utf-8",
        newline="\n",
    )
    return cfg_path


class ScanCounter:
    """Счётчик обходов корпуса раздельно по режимам: с чтением и без."""

    def __init__(self) -> None:
        self.full = 0
        self.cheap = 0
        self._original = corpus.scan

    def __enter__(self) -> "ScanCounter":
        original = self._original

        def wrapper(cfg, *, digest: bool = True):
            if digest:
                self.full += 1
            else:
                self.cheap += 1
            return original(cfg, digest=digest)

        corpus.scan = wrapper            # type: ignore[assignment]
        return self

    def __exit__(self, *exc) -> None:
        corpus.scan = self._original     # type: ignore[assignment]


def main() -> int:
    global _TEST_CATALOG
    tmp = Path(tempfile.mkdtemp(prefix="ragraf_single_scan_"))
    _TEST_CATALOG = tmp / "catalog" / "catalog.json"
    _patch_catalog()

    try:
        # ---------------- 1. Отпечаток из полного скана ----------------
        print("\n=== 1. Отпечаток полного скана равен дешёвому ===")
        root = tmp / "proj"
        cfg = load_config(make_corpus(root, "proj", {
            "src/one.php": "<?php\nfunction one(): int { return 1; }\n",
            "src/two.php": "<?php\nfunction two(): int { return 2; }\n",
            "docs/readme.md": "# Корпус\n\nТекст.\n",
        }))
        state = watch.current_state(cfg)
        cheap_fp, cheap_files, cheap_bytes = corpus.stat_fingerprint(cfg)
        check(state.fingerprint == cheap_fp,
              "отпечаток полного скана совпадает с дешёвым",
              f"{state.fingerprint[:12]} != {cheap_fp[:12]}")
        check(state.files == cheap_files and state.total_bytes == cheap_bytes,
              "число файлов и объём совпадают с дешёвым сканом",
              f"{state.files}/{state.total_bytes} != {cheap_files}/{cheap_bytes}")
        check(bool(state.corpus_hash) and state.fingerprint,
              "состояние несёт и сумму, и отпечаток одного и того же входа")

        # ---------------- 2. Первый вызов: один полный скан ----------------
        print("\n=== 2. Один полный скан на вызов сторожа ===")
        with ScanCounter() as counter:
            fresh1 = watch.check(cfg, "proj", auto_reindex=True, use_cache=False)
        check(fresh1.reindexed, "первый вызов пересобрал индекс (индекса не было)")
        # Два обхода — это НОРМА, и разница названа, а не списана: один читает
        # корпус сторож (сумма + отпечаток), второй — сам индексатор (без чтения
        # файлов он не может их разобрать). До правки к ним добавлялись ещё два
        # сторожевых, и это ловится счётчиком, а не на глаз.
        check(counter.full == 2,
              "полный скан: один у сторожа, один у индексатора (было 3 сторожевых)",
              f"полных сканов {counter.full}")
        check(bool(watch.read_fingerprint(cfg)),
              "после прогона отпечаток входа записан")

        # ---------------- 3. Отпечаток лечится, дешёвый путь живой --------
        print("\n=== 3. Отпечаток лечится, дешёвый путь не умирает ===")
        # Касание файла без изменения содержимого: так ведёт себя перезапись
        # тем же текстом, распаковка архива, утилита чистки. Сумма корпуса при
        # этом НЕ меняется, а отпечаток расходится.
        touched = root / "src" / "one.php"
        text = touched.read_text(encoding="utf-8")
        touched.write_text(text, encoding="utf-8", newline="\n")
        saved_fp = watch.read_fingerprint(cfg)
        check(bool(saved_fp), "отпечаток читается перед касанием файла")
        with ScanCounter() as counter2:
            fresh2 = watch.check(cfg, "proj", auto_reindex=True, use_cache=False)
        check(not fresh2.reindexed and not fresh2.stale,
              "касание файла без изменения содержимого не вызывает пересбора")
        check(counter2.full == 1,
              "на сверке-лечении корпус читается ОДИН раз (индексатор не запускался)",
              f"полных сканов {counter2.full}")
        healed = watch.read_fingerprint(cfg) or {}
        now_fp, _, _ = corpus.stat_fingerprint(cfg)
        check(healed.get("fingerprint") == now_fp,
              "отпечаток ВЫЛЕЧЕН после полной сверки (иначе дешёвый путь мёртв навсегда)",
              f"{str(healed.get('fingerprint'))[:12]} != {now_fp[:12]}")
        with ScanCounter() as counter3:
            fresh3 = watch.check(cfg, "proj", auto_reindex=True, use_cache=False)
        check(counter3.full == 0,
              "следующий вызов корпус НЕ читает (дешёвый путь работает)",
              f"полных сканов {counter3.full}")
        check(counter3.cheap >= 1, "дешёвый путь действительно опрошен",
              f"дешёвых сканов {counter3.cheap}")
        check(fresh3.corpus_hash == "",
              "дешёвый путь не выдумывает сумму: поле пустое, а не подставленное",
              repr(fresh3.corpus_hash))

        # ---------------- 4. Сторож не зацикливается ----------------
        print("\n=== 4. Сторож не зацикливается ===")
        (root / "src" / "three.php").write_text(
            "<?php\nfunction three(): int { return 3; }\n", encoding="utf-8", newline="\n")
        fresh4 = watch.check(cfg, "proj", auto_reindex=True, use_cache=False)
        check(fresh4.reindexed, "правка корпуса вызвала пересбор")
        state_after = watch.current_state(cfg)
        recorded_hash, _, recorded_rev = watch.indexed_hashes("proj", cfg)
        check(state_after.corpus_hash == recorded_hash,
              "сумма в ревизии соответствует содержимому после пересбора",
              f"{state_after.corpus_hash[:12]} != {recorded_hash[:12]}")
        check(cfg.config_hash == watch.current_state(cfg).config_hash,
              "сумма конфига считается одинаково до и после прогона")
        fresh5 = watch.check(cfg, "proj", auto_reindex=True, use_cache=False)
        check(not fresh5.reindexed and not fresh5.stale,
              "следующий вызов: «актуально», без пересбора",
              f"reindexed={fresh5.reindexed} stale={fresh5.stale}")
        fresh6 = watch.check(cfg, "proj", auto_reindex=True, use_cache=False)
        check(not fresh6.reindexed and not fresh6.stale,
              "третий вызов подряд тоже «актуально» (цикла нет)",
              f"reindexed={fresh6.reindexed} stale={fresh6.stale}")
        check(fresh5.rev_id and fresh5.rev_id == fresh6.rev_id,
              "ревизия между вызовами не меняется",
              f"{fresh5.rev_id} / {fresh6.rev_id}")

        # ---------------- 5. Прерванный прогон не пишется в реестр ----------
        print("\n=== 5. Прерванный по бюджету прогон не попадает в реестр ===")
        before_entry = catalog_mod.Catalog(path=_TEST_CATALOG).get("proj")
        record = {}
        ok_small, rev_small, _, note_small = watch.run_reindex(
            cfg, "proj", trigger="test", mode="full",
            budget_seconds=0.001, state=watch.current_state(cfg), recorded=record)
        after_entry = catalog_mod.Catalog(path=_TEST_CATALOG).get("proj")
        check(not ok_small and not rev_small,
              "прогон, не закрепивший ревизию, признан неуспешным",
              f"ok={ok_small} rev={rev_small}")
        check("не закреплена" in note_small or "бюджет" in note_small,
              "причина названа, а не скрыта", note_small[:80])
        check(before_entry is not None and after_entry is not None
              and before_entry.corpus_hash == after_entry.corpus_hash
              and before_entry.rev_id == after_entry.rev_id,
              "реестр не изменился: ни суммы, ни ревизии",
              f"{getattr(before_entry, 'corpus_hash', '')[:12]} -> "
              f"{getattr(after_entry, 'corpus_hash', '')[:12]}")
        check(record.get("recorded") is False,
              "вызывающему сообщено, что запись не сделана", repr(record))

        # ---------------- 6. Файлы менялись во время прогона --------------
        print("\n=== 6. Вход изменился во время прогона: сумма пересчитывается ===")
        stale_state = watch.current_state(cfg)
        (root / "src" / "four.php").write_text(
            "<?php\nfunction four(): int { return 4; }\n", encoding="utf-8", newline="\n")
        record2 = {}
        ok_full, rev_full, _, note_full = watch.run_reindex(
            cfg, "proj", trigger="test", mode="fast",
            state=stale_state, recorded=record2)
        check(ok_full and bool(rev_full), "полный (быстрый) прогон закрепил ревизию",
              note_full[:80])
        check(record2.get("rescan") is True,
              "устаревшее состояние до прогона НЕ принято: сумма пересчитана",
              repr(record2))
        real = watch.current_state(cfg)
        check(record2.get("corpus_hash") == real.corpus_hash,
              "в реестр записана сумма ТЕКУЩЕГО содержимого, а не прежняя",
              f"{str(record2.get('corpus_hash'))[:12]} != {real.corpus_hash[:12]}")
        check(record2.get("corpus_hash") != stale_state.corpus_hash,
              "принятая сумма отличается от суммы до прогона (иначе проверка пустая)")

        print("\n" + "=" * 78)
        print(f"итог: пройдено {PASS}, провалено {FAIL}")
        print("=" * 78)
        return 0 if FAIL == 0 else 1
    finally:
        import shutil

        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
