"""Проверка: активная ревизия — та, что ЗАПЕЧАТАНА, а не последняя по номеру.

Дефект, который эта проверка закрепляет (измерено 29.10.2026). Прогон, прерванный по
бюджету времени, успевает создать запись ревизии и не успевает её запечатать: в `graph.rev`
остаётся строка со `state=building` и нулями. Читатели, бравшие «последнюю по номеру»,
называли активной именно её. На корпусе RAGRAF такая строка (`rev-0519`, 0 файлов) лежала
в базе всё время — это не гипотеза, а факт из хранилища.

Тот же класс уже был вылечен в стороже (`src/ragraf/watch.py:206`), но не в остальных
читателях. Свойства ниже проверяют ПОВЕДЕНИЕ: снимок ревизии, реестр прогонов и панель
обязаны называть запечатанную ревизию, а пропущенную — называть вслух.

Песочница: временный каталог, живой корпус не меняется ни на байт. Реестр разработок
снимается и возвращается — иначе три таких прогона поднимали реестр с 43 до 45 и ломали
строку реестра в документах (измерено 28.10.2026).
"""

from __future__ import annotations

import atexit
import json
import pathlib
import sqlite3
import subprocess
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

PASS = 0
FAIL = 0

CATALOG = REPO / "catalog" / "catalog.json"
_CATALOG_BYTES = CATALOG.read_bytes() if CATALOG.exists() else None


def _restore_registry() -> None:
    if _CATALOG_BYTES is None:
        return
    try:
        CATALOG.write_bytes(_CATALOG_BYTES)
    except OSError:
        pass


atexit.register(_restore_registry)


def check(cond: bool, label: str, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [OK ] {label}")
    else:
        FAIL += 1
        print(f"  [FAIL] {label} {detail}")


def run(args: list[str], timeout: int = 300) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "tools/ragraf.py", *args], cwd=str(REPO),
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout)


def run_tool(path: pathlib.Path, args: list[str], timeout: int = 300) -> subprocess.CompletedProcess:
    """Прямой вызов инструмента из `tools/`.

    Через `tools/ragraf.py` его звать НЕЛЬЗЯ: первый аргумент раграфа — подкоманда, и путь
    к скрипту попал бы в неё. Измерено первым прогоном этой проверки: снимок отвечал кодом
    2 с пустым выводом, и пять свойств «провалились» из-за ошибки вызова, а не из-за кода.
    """
    return subprocess.run([sys.executable, str(path), *args], cwd=str(REPO),
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout)


def as_json(result: subprocess.CompletedProcess) -> dict:
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return {}


def plant_unsealed(graph: pathlib.Path, rev_id: str, state: str) -> None:
    """Подсадить незапечатанную ревизию с номером ВЫШЕ активной.

    Колонки с NOT NULL заполняются явно: INSERT, падающий на схеме, — это дефект теста,
    а не проверка. Числа нулевые намеренно: прерванный прогон именно такие и оставляет,
    и если читатель возьмёт эту запись, он покажет 0 файлов.
    """
    connection = sqlite3.connect(graph)
    try:
        connection.execute(
            "INSERT OR REPLACE INTO rev (rev_id, parent_rev, created_at, corpus_hash, "
            "config_hash, extractor_versions, state, capability, joern_used, file_count, "
            "node_count, edge_count, schema_version) "
            "VALUES (?, NULL, '2026-10-29T00:00:00+03:00', 'planted', 'planted', '{}', ?, "
            "'degraded', 0, 0, 0, 0, 1)", (rev_id, state))
        connection.commit()
    finally:
        connection.close()


with tempfile.TemporaryDirectory(prefix="ragraf-activerev-",
                                 ignore_cleanup_errors=True) as tmp:
    base = pathlib.Path(tmp)
    (base / "alpha.py").write_text("def alpha():\n    return 1\n", encoding="utf-8")
    (base / "beta.py").write_text("def beta():\n    return 2\n", encoding="utf-8")
    (base / "guide.md").write_text("# Руководство\n\nТекст руководства.\n", encoding="utf-8")
    config = base / "corpus.yaml"

    print("=" * 78)
    print("1. ПЕСОЧНЫЙ КОРПУС: ИНДЕКСАЦИЯ")
    print("=" * 78)
    init = run(["init", str(base), "--name", "activerev", "--config", str(config)])
    check(init.returncode == 0, "init выполнен", (init.stderr or init.stdout)[-300:])
    indexed = run(["-p", str(config), "index", "--no-vectors"])
    check(indexed.returncode == 0, "index выполнен", (indexed.stderr or indexed.stdout)[-300:])

    graph = base / ".ragraf" / "index" / "graph" / "graph.sqlite"
    check(graph.is_file(), "база графа на месте", str(graph))
    if not graph.is_file():
        raise SystemExit(1)

    sealed = as_json(run_tool(REPO / "tools" / "rev_snapshot.py", ["--project", str(config)]))
    check(sealed.get("rev_id") == "rev-0001", "активная ревизия — rev-0001",
          str(sealed))
    check(int(sealed.get("files") or 0) == 3, "файлов в ревизии 3", str(sealed.get("files")))
    check("skipped_unsealed" not in sealed,
          "пока пропущенных нет — поля нет (не пустой список ради вида)",
          str(sealed.keys()))

    print()
    print("=" * 78)
    print("2. ПОДСАДКА: ПРЕРВАННЫЙ ПРОГОН С БОЛЬШИМ НОМЕРОМ")
    print("=" * 78)
    plant_unsealed(graph, "rev-0900", "building")
    snapshot = run_tool(REPO / "tools" / "rev_snapshot.py", ["--project", str(config)])
    payload = as_json(snapshot)
    check(snapshot.returncode == 0,
          "снимок не отказывает при незапечатанной записи (готовый индекс есть)",
          f"код {snapshot.returncode}: {snapshot.stdout.strip()[:200]}")
    check(payload.get("rev_id") == "rev-0001",
          "активной названа запечатанная rev-0001, а не rev-0900", str(payload.get("rev_id")))
    check(int(payload.get("files") or 0) == 3,
          "числа от запечатанной ревизии (3 файла), а не нули прерванной",
          str(payload.get("files")))
    skipped = payload.get("skipped_unsealed") or []
    names = [item.get("rev_id") for item in skipped if isinstance(item, dict)]
    check("rev-0900" in names, "пропущенная ревизия НАЗВАНА, а не скрыта", str(skipped))

    print()
    print("=" * 78)
    print("3. ТО ЖЕ ДЛЯ failed И aborted")
    print("=" * 78)
    for state in ("failed", "aborted"):
        plant_unsealed(graph, f"rev-0901-{state}", state)
        payload = as_json(run_tool(REPO / "tools" / "rev_snapshot.py",
                                   ["--project", str(config)]))
        check(payload.get("rev_id") == "rev-0001",
              f"состояние {state} не считается активной ревизией", str(payload.get("rev_id")))

    print()
    print("=" * 78)
    print("4. ПОМОЩНИК ЯДРА: active_revision")
    print("=" * 78)
    from ragraf import store  # noqa: E402

    connection = store.connect(graph, readonly=True)
    try:
        active, skipped_rows = store.active_revision(connection)
    finally:
        connection.close()
    check(active is not None and active.get("rev_id") == "rev-0001",
          "active_revision возвращает rev-0001", str(active))
    check(len(skipped_rows) == 3,
          "три незапечатанные записи попали в список пропущенных", str(len(skipped_rows)))
    check(all(str(item.get("rev_id", "")).startswith("rev-09") for item in skipped_rows),
          "в пропущенных только подсаженные", str([item.get("rev_id") for item in skipped_rows]))
    description = store.describe_skipped(skipped_rows)
    check("rev-0900" in description and "building" in description,
          "описание пропущенных называет ревизию и состояние", description)

    print()
    print("=" * 78)
    print("5. РЕЕСТР ПРОГОНОВ НЕ ПОЛУЧАЕТ НУЛИ")
    print("=" * 78)
    again = run(["-p", str(config), "index", "--no-vectors"])
    check(again.returncode == 0, "повторная индексация выполнена",
          (again.stderr or again.stdout)[-300:])
    registry = json.loads(CATALOG.read_text(encoding="utf-8"))
    # Записи реестра лежат под ключом `entries` словарём по категориям; имён
    # `projects`/`corpora` в файле нет (проверено схемой 29.10.2026).
    items = registry.get("entries") or {}
    items = list(items.values()) if isinstance(items, dict) else list(items)
    def same_path(left: str, right: str) -> bool:
        # Реестр хранит пути с ПРЯМЫМИ слэшами (`C:/...`), а временный каталог приходит
        # с обратными. Сравнение строк «как есть» не находит запись — измерено первым
        # прогоном; нормализация через `Path` снимает разницу разделителей.
        try:
            return pathlib.Path(left).resolve() == pathlib.Path(right).resolve()
        except (OSError, ValueError):
            return False

    entry = None
    for item in items:
        if isinstance(item, dict) and same_path(str(item.get("root", "")), str(base)):
            entry = item
            break
    check(entry is not None, "запись песочницы в реестре найдена",
          str([item.get("root") for item in items if isinstance(item, dict)][:6]))
    if entry:
        check(int(entry.get("file_count") or 0) == 3,
              "реестр называет 3 файла (числа запечатанной), а не 0",
              f"file_count={entry.get('file_count')} rev={entry.get('rev_id')}")
        check(str(entry.get("rev_id") or "") != "rev-0900",
              "реестр не называет прерванную ревизию", str(entry.get("rev_id")))

    print()
    print("=" * 78)
    print("6. КОГДА ЗАПЕЧАТАННЫХ НЕТ — ЧЕСТНЫЙ ОТКАЗ, А НЕ НУЛИ")
    print("=" * 78)
    connection = sqlite3.connect(graph)
    try:
        connection.execute("UPDATE rev SET state = 'building'")
        connection.commit()
    finally:
        connection.close()
    empty = run_tool(REPO / "tools" / "rev_snapshot.py", ["--project", str(config)])
    payload = as_json(empty)
    check(empty.returncode != 0, "снимок отказал (код != 0)", str(empty.returncode))
    check(payload.get("files") is None,
          "в ответе нет числа файлов — нули не выдаются за измерение", str(payload))

print()
print("=" * 78)
print("Указатель ревизии против запечатанной записи")
print("=" * 78)
# Замер 31.10.2026: `.ragraf/index/rev.json` называл `rev-1083` (в истории
# `state=rotated`, данные удалены), активной была `rev-1087`. Фасад верил
# указателю и отвечал «ревизия без данных: 0 файлов, 0 узлов, 0 чанков», а чекер
# осматривал ноль рёбер и объявлял находкой пустоту. Правило вынесено в
# `facade.rev_pointer_guard`, поэтому проверяется без сборки фасада.
from ragraf.facade import rev_pointer_guard  # noqa: E402

stale = {"rev_id": "rev-1083", "capability": "full", "joern_used": True,
         "sealed_at": "2026-10-31T16:21:28+03:00", "cpg": {"mode": "built"}}
active = {"rev_id": "rev-1087", "capability": "full", "joern_used": True,
          "sealed_at": "2026-10-31T16:19:29+03:00"}
got = rev_pointer_guard(dict(stale), active)
check(got.get("rev_id") == "rev-1087",
      "указатель на ротированную ревизию не побеждает запечатанную",
      f"получено {got.get('rev_id')!r}")
check(got.get("pointer_rev_id") == "rev-1083",
      "расхождение названо: какой ревизией назывался указатель",
      str(got.get("pointer_rev_id")))
check(bool(got.get("pointer_note")), "причина подмены названа словами",
      str(got.get("pointer_note")))
same = {"rev_id": "rev-1087", "capability": "full"}
check(rev_pointer_guard(dict(same), active) == same,
      "совпадающий паспорт не переписывается")
check(rev_pointer_guard(dict(stale), None) == stale,
      "без запечатанной ревизии паспорт остаётся как есть")

print()
print("=" * 78)
print(f"ИТОГ: пройдено {PASS}, провалено {FAIL}")
print("=" * 78)
raise SystemExit(0 if FAIL == 0 else 1)
