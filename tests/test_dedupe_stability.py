"""Проверка главного риска: не зациклится ли сторож свежести.

Риск, который надо исключить до сдачи. Если `corpus_hash` при индексации
и при проверке свежести считается по РАЗНЫМ наборам файлов, значения не
совпадут, и сторож решит, что корпус изменился. Он запустит переиндексацию,
та заново запишет несогласованный хэш — и так по кругу, бесконечно.

Проверяется воспроизводимость:
  1. два подряд вызова `corpus.scan` при включённом схлопывании дают
     одинаковый набор файлов и одинаковый хэш;
  2. хэш и выбор главной копии не зависят от порядка обхода каталога;
  3. хэш в ревизии, записанной индексатором, совпадает с хэшем текущего скана;
  4. выбор главной копии детерминирован, приманки не схлопнуты.

ГДЕ ЭТО ПРОВЕРЯЕТСЯ. На СВОЁМ песочном корпусе-фикстуре: он создаётся во
временном каталоге, индексируется тем же `Indexer`, что и рабочий корпус,
и исчезает вместе с каталогом. Живой `config/revoap.yaml` входом больше
не служит: 28.10.2026 проверка краснела по чужой причине — чужой корпус
отставал (ревизия rev-0039, 2 722 файла в ревизии против 2 730 в скане),
а свойство при этом было цело. Чужой корпус в конце только осматривается
и помечается «пропущено» с причиной: переиндексация чужого корпуса —
решение владельца, а не следствие проверки.

Пятый раздел — falsification. В ревизию песочницы подсаживается
рассогласование хэшей, и проверка обязана покраснеть: свойство, которое
не умеет краснеть, не проверяет ничего.

Вне песочницы ничего не меняется: чужой корпус и его ревизии только читаются.
"""

from __future__ import annotations

import atexit
import gc
import pathlib
import random
import shutil
import sys
import tempfile
import time

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf import corpus, dedupe, store, watch  # noqa: E402
from ragraf.config import load as load_config  # noqa: E402
from ragraf.indexer import Indexer  # noqa: E402

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


# --- содержимое песочного корпуса -------------------------------------------
# Ключ — путь относительно корня песочницы, значение — текст файла.
# Пары с ОДИНАКОВЫМ текстом дают группы копий; приманки обязаны уцелеть.
GUIDE = "# Руководство\n\nОдинаковый текст: он лежит двумя копиями.\n"
MODULE = "def add(a, b):\n    return a + b\n"
FIXTURE = {
    "docs/guide.md": GUIDE,
    "archive/guide-copy.md": GUIDE,               # копия: сегменты archive и copy
    "docs/guide-v2.md": GUIDE + "\nПравка.\n",    # НЕ копия: содержимое отличается
    "docs/notes.txt": "Заметки: единственный в своём роде текст.\n",
    "src/module.py": MODULE,
    "src/module_copy.py": MODULE,                 # копия: сегмент copy
    "old/history.txt": "Приманка: путь похож на копию, содержимого-двойника нет.\n",
    "keep/plan.md": "План: уникальный текст без мусорных сегментов.\n",
}
# Кого схлопывание обязано отбросить: ровно байт-идентичные копии.
EXPECTED_DROPPED = {"archive/guide-copy.md", "src/module_copy.py"}
EXPECTED_KEPT = set(FIXTURE) - EXPECTED_DROPPED
# Приманки: похожи на копии по имени каталога или почти совпадают по тексту,
# но отбрасывать их нельзя.
DECOYS = ("docs/guide-v2.md", "old/history.txt", "docs/notes.txt")


def remove_tree(path: pathlib.Path) -> None:
    """Удалить песочницу, не считая мгновенное освобождение файла за норму.

    На Windows файл базы SQLite отпускается не в тот же миг, что закрылось
    соединение (замерено: `TemporaryDirectory.cleanup` падал с WinError 32 на
    `.ragraf/control.sqlite` при том, что все соединения были закрыты).
    Одноразовое удаление поэтому повторяется, а не считается гарантией.
    """
    for _ in range(6):
        if not path.exists():
            return
        try:
            shutil.rmtree(path)
            return
        except OSError:
            time.sleep(0.25)
    print(f"  внимание: песочница не удалена целиком: {path}")


def write_config(root: pathlib.Path) -> pathlib.Path:
    """Конфигурация песочного корпуса: схлопывание включено, Joern выключен."""
    path = root / "corpus.yaml"
    path.write_text(
        "\n".join([
            "project: dedupe-fixture",
            f"root: {root.as_posix()}",
            "languages:",
            "  - markdown",
            "  - text",
            "  - python",
            "include:",
            '  - "**/*.md"',
            '  - "**/*.txt"',
            '  - "**/*.py"',
            "index:",
            "  dedupe_enabled: true",
            "  dedupe_scope: all",
            "  dedupe_mode: keep_one",
            "  dedupe_keep: cleanest",
            # Joern в песочнице не нужен: проверяется сверка сумм, а не граф
            # свойств кода. Выключение оставляет прогон быстрым и не поднимает JVM.
            "  joern_enabled: false",
            "  joern_server: false",
            "  retain_revs: 1",
            "",
        ]),
        encoding="utf-8",
        newline="\n",
    )
    return path


def rev_row(cfg, rev_id: str | None = None) -> dict | None:
    """Строка ревизии: по умолчанию последняя — так же, как её читает сторож.

    Именно `ORDER BY rev_id DESC` без фильтра по состоянию: сторож берёт
    активную ревизию так (`watch.indexed_hashes`), и проверка обязана смотреть
    на ту же строку, иначе она сравнивает не то, что сравнивает сторож.
    """
    conn = store.connect(cfg.graph_db, readonly=True)
    try:
        if rev_id is None:
            row = conn.execute(
                "SELECT rev_id, corpus_hash, file_count, state, created_at "
                "FROM rev ORDER BY rev_id DESC LIMIT 1"
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT rev_id, corpus_hash, file_count, state, created_at "
                "FROM rev WHERE rev_id = ?", (rev_id,)
            ).fetchone()
    finally:
        conn.close()
    return dict(row) if row is not None else None


def rev_row_ready(cfg) -> dict | None:
    """Последняя ревизия в состоянии `ready` — чтобы два правила были видны рядом."""
    conn = store.connect(cfg.graph_db, readonly=True)
    try:
        row = conn.execute(
            "SELECT rev_id, corpus_hash, file_count, state, created_at "
            "FROM rev WHERE state = 'ready' ORDER BY rev_id DESC LIMIT 1"
        ).fetchone()
    finally:
        conn.close()
    return dict(row) if row is not None else None


def set_rev_hash(cfg, rev_id: str, value: str) -> None:
    """Подсадить рассогласование: записать в ревизию чужой хэш."""
    conn = store.connect(cfg.graph_db, readonly=False)
    try:
        conn.execute("UPDATE rev SET corpus_hash = ? WHERE rev_id = ?",
                     (value, rev_id))
        conn.commit()
    finally:
        conn.close()


_FIXTURE_DIR = tempfile.TemporaryDirectory(
    prefix="ragraf-dedupe-fixture-", ignore_cleanup_errors=True
)
# Повтор очистки — на выходе из процесса: к этому моменту соединения закрыты.
atexit.register(remove_tree, pathlib.Path(_FIXTURE_DIR.name))

with _FIXTURE_DIR as tmp:
    root = pathlib.Path(tmp)
    for rel, text in FIXTURE.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8", newline="\n")
    config_path = write_config(root)
    cfg = load_config(config_path)

    print("=" * 78)
    print("1. ПЕСОЧНЫЙ КОРПУС И ОЖИДАНИЕ")
    print("=" * 78)
    print(f"  песочница: {root}")
    print(f"  файлов в фикстуре: {len(FIXTURE)} — из них копий "
          f"{len(EXPECTED_DROPPED)}, приманок {len(DECOYS)}")
    print(f"  схлопывание: включено={cfg.index.dedupe_enabled}, "
          f"область={cfg.index.dedupe_scope}, правило={cfg.index.dedupe_keep}")
    check(cfg.index.dedupe_enabled,
          "в песочнице схлопывание включено (иначе проверять нечего)")
    check(cfg.index.dedupe_scope == "all",
          "в песочнице схлопываются все файлы, а не только документы",
          f"область {cfg.index.dedupe_scope!r}")

    print()
    print("=" * 78)
    print("2. ВОСПРОИЗВОДИМОСТЬ: ДВА СКАНА ПОДРЯД")
    print("=" * 78)
    first = corpus.scan(cfg)
    second = corpus.scan(cfg)
    print(f"  скан 1: файлов {len(first.files)} из {first.files_found}, "
          f"хэш {first.corpus_hash[:20]}…")
    print(f"  скан 2: файлов {len(second.files)} из {second.files_found}, "
          f"хэш {second.corpus_hash[:20]}…")
    check(first.corpus_hash == second.corpus_hash,
          "хэш корпуса воспроизводим (два скана дают одно значение)")
    check([f.path for f in first.files] == [f.path for f in second.files],
          "состав файлов воспроизводим")
    check(first.duplicates.get("dropped_files") ==
          second.duplicates.get("dropped_files"),
          "число отброшенных копий воспроизводимо")
    check([g["kept"] for g in first.duplicates.get("groups", [])] ==
          [g["kept"] for g in second.duplicates.get("groups", [])],
          "выбор главной копии детерминирован")

    kept_paths = {f.path for f in first.files}
    print(f"  схлопывание: групп {first.duplicates.get('duplicate_groups')}, "
          f"отброшено {first.duplicates.get('dropped_files')}")
    dropped_paths = {d for g in first.duplicates.get("groups", [])
                     for d in g["dropped"]}
    check(dropped_paths == EXPECTED_DROPPED,
          "отброшены ровно байт-идентичные копии",
          f"отброшено {sorted(dropped_paths)}")
    check(kept_paths == EXPECTED_KEPT,
          "в индексе остались все прочие файлы",
          f"различия: {sorted(kept_paths ^ EXPECTED_KEPT)}")
    survivors = [d for d in DECOYS if d in kept_paths]
    check(len(survivors) == len(DECOYS),
          "приманки целы: ни одна не схлопнута",
          f"потеряны {sorted(set(DECOYS) - set(survivors))}")
    check(first.files_found == len(FIXTURE),
          "до схлопывания найдены все файлы фикстуры",
          f"найдено {first.files_found}, в фикстуре {len(FIXTURE)}")

    print()
    print("=" * 78)
    print("3. ВЫБОР НЕ ЗАВИСИТ ОТ ПОРЯДКА ВХОДА")
    print("=" * 78)
    # Файлы перемешиваются и схлопывание считается заново. Если выбор главной
    # копии зависит от порядка, результат изменится — и это дефект.
    shuffled = corpus.CorpusScan(
        files=list(first.files),
        files_found=first.files_found,
    )
    random.Random(42).shuffle(shuffled.files)
    decision_a = dedupe.dedupe(shuffled, cfg)
    random.Random(7).shuffle(shuffled.files)
    decision_b = dedupe.dedupe(shuffled, cfg)

    kept_a = sorted(f.path for f in decision_a.kept_files)
    kept_b = sorted(f.path for f in decision_b.kept_files)
    print(f"  оставлено файлов: {len(kept_a)} и {len(kept_b)}")
    check(kept_a == kept_b,
          "состав оставленных файлов не зависит от порядка обхода",
          f"различий: {len(set(kept_a) ^ set(kept_b))}")
    check(sorted(g.kept for g in decision_a.groups) ==
          sorted(g.kept for g in decision_b.groups),
          "выбранные главные копии не зависят от порядка")

    print()
    print("=" * 78)
    print("4. ИНДЕКСАЦИЯ ПЕСОЧНИЦЫ: ХЭШ В РЕВИЗИИ = ХЭШ СКАНА")
    print("=" * 78)
    indexer = Indexer(cfg)
    try:
        report = indexer.run(with_vectors=False)
    finally:
        indexer.close()
    print(f"  индексация: ревизия {report.rev_id}, удалась={report.ok}, "
          f"возможности={report.capability}, {report.seconds:.2f} с")

    row = rev_row(cfg, report.rev_id)
    check(report.rev_id != "" and row is not None,
          "индексатор записал ревизию песочницы",
          f"ревизия {report.rev_id!r}")
    if row is not None:
        print(f"  ревизия {row['rev_id']}: состояние {row['state']}, "
              f"файлов {row['file_count']}, хэш {str(row['corpus_hash'])[:20]}…")
        check(str(row["corpus_hash"]) == first.corpus_hash,
              "хэш в ревизии = хэш текущего скана (сторож не зациклится)",
              f"в ревизии {str(row['corpus_hash'])[:12]}…, "
              f"сейчас {first.corpus_hash[:12]}…")
        check(row["file_count"] == len(first.files),
              "число файлов в ревизии = число в скане",
              f"в ревизии {row['file_count']}, в скане {len(first.files)}")

    # Главная проверка риска: сторож не считает корпус изменившимся.
    fresh = watch.check(cfg, auto_reindex=False, use_cache=False)
    print(f"  сторож: изменилось={fresh.changed}, устарело={fresh.stale}, "
          f"ревизия {fresh.rev_id}")
    check(not fresh.changed and not fresh.stale,
          "сторож не считает корпус изменившимся (зацикливания нет)",
          f"changed={fresh.changed}, stale={fresh.stale}, note={fresh.note}")

    print()
    print("=" * 78)
    print("5. ПОДСАЖЕННОЕ РАССОГЛАСОВАНИЕ: ПРОВЕРКА ОБЯЗАНА КРАСНЕТЬ")
    print("=" * 78)
    # Свойство проверено на целой песочнице, но этого мало: проверка, которая
    # зелена всегда, не проверяет ничего. Хэш в ревизии подменяется на заведомо
    # чужой, и сторож обязан увидеть расхождение.
    if row is not None:
        original = str(row["corpus_hash"])
        planted = "0" * 64 if not original.startswith("0" * 8) else "1" * 64
        set_rev_hash(cfg, str(row["rev_id"]), planted)
        print(f"  подсажено в ревизию {row['rev_id']}: {planted[:20]}… "
              f"(было {original[:20]}…)")
        broken = watch.check(cfg, auto_reindex=False, use_cache=False)
        print(f"  сторож на подсаженном: изменилось={broken.changed}, "
              f"устарело={broken.stale}")
        check(broken.changed and broken.stale,
              "подсаженное рассогласование хэшей замечено",
              f"changed={broken.changed}, stale={broken.stale}")

        # Возврат: свойство восстанавливается, значит красное было именно
        # из-за подсаженного, а не из-за самой песочницы.
        set_rev_hash(cfg, str(row["rev_id"]), original)
        restored = watch.check(cfg, auto_reindex=False, use_cache=False)
        check(not restored.changed,
              "после возврата хэша сторож снова спокоен",
              f"changed={restored.changed}, note={restored.note}")
    else:
        check(False, "подсаженное рассогласование проверено",
              "ревизии песочницы нет")

    # Сборка мусора вызывается явно и ВНУТРИ песочницы: соединение SQLite,
    # на которое ещё есть ссылка, держит файл базы, и на Windows удаление
    # каталога тогда падает с WinError 32 (замерено на `.ragraf/control.sqlite`).
    gc.collect()
    # Удаление делается здесь же, а не оставляется контекстному менеджеру:
    # на выходе из блока файл может быть ещё занят, и тогда песочница молча
    # остаётся в системном temp. Повторный вызов ниже довершает, если не вышло.
    remove_tree(pathlib.Path(tmp))

print()
print("=" * 78)
print("6. ЧУЖОЙ КОРПУС: НЕ ВХОД ПРОВЕРКИ, ТОЛЬКО ОСМОТР")
print("=" * 78)
LIVE_CONFIG = REPO / "config" / "revoap.yaml"
if LIVE_CONFIG.exists():
    live_cfg = load_config(LIVE_CONFIG)
    live_row = rev_row(live_cfg)
    if live_row is not None:
        print(f"  чужой корпус revoap.com: активная ревизия {live_row['rev_id']} "
              f"({str(live_row['created_at'])[:10]}), состояние {live_row['state']}, "
              f"файлов {live_row['file_count']}")
        # Активная ревизия читается по `rev_id DESC`, как это делает сторож,
        # а не по `state='ready'`: два разных правила давали два разных номера
        # на одном и том же корпусе (замер 28.10.2026: rev-0040 с 0 файлов
        # против rev-0039 с 2 722).
        ready = rev_row_ready(live_cfg)
        if ready is not None:
            print(f"  последняя ревизия в состоянии ready: {ready['rev_id']} "
                  f"({str(ready['created_at'])[:10]}), файлов {ready['file_count']}")
        print("  пропущено, причина: чужой корпус отстал — сверка «хэш в ревизии = "
              "хэш скана» на нём считалась бы по чужому состоянию, а не по "
              "свойству; переиндексация чужого корпуса — отдельное решение "
              "владельца (границы задания И41)")
    else:
        print("  пропущено, причина: в чужом корпусе нет записанной ревизии")
else:
    print(f"  пропущено, причина: конфигурации чужого корпуса нет ({LIVE_CONFIG})")

print()
print("=" * 78)
print(f"ИТОГ: пройдено {PASS}, провалено {FAIL}")
print("=" * 78)
raise SystemExit(0 if FAIL == 0 else 1)
