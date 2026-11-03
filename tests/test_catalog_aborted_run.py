"""Проверка: прерванный прогон НЕ становится состоянием разработки.

Дефект, который закрепляется. 28.10.2026 корпус `batterypassport.ru` упёрся в
бюджет времени (`index.budget_seconds` = 120 с при потребности 131-160 с), прогон
прервался на стадии «databases», ревизия осталась в состоянии `building`. При
этом `index_status` показывал прежнюю ревизию с 842 файлами, а `catalog` —
новую с нулями (`файлов 0, узлов 0, рёбер 0`): запись прогона переписывала
ревизию и размеры. Два разных числа об одном корпусе читаются как «индекс пуст»,
хотя индекс цел.

Проверяется свойство, а не написание: после прерванного прогона реестр обязан
называть ПРЕЖНЮЮ ревизию и её числа, а сам прогон — быть видимым с пометкой
`aborted`. Отдельно проверяется, что единственная точка записи прогона
(`watch.record_index_run`) передаёт признак завершённости: без этого правка
ядра не работает, хотя сам `Catalog` её поддерживает.
"""

from __future__ import annotations

import pathlib
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf import watch  # noqa: E402
import ragraf.catalog as catalog_mod  # noqa: E402
from ragraf.catalog import Catalog, Entry  # noqa: E402

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


def field(run, key: str):
    """Поле прогона: запись читается и как словарь, и как RunRecord."""
    return run.get(key) if isinstance(run, dict) else getattr(run, key, None)


def fresh_catalog() -> Catalog:
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="ragraf-catalog-"))
    cat = Catalog(tmp / "catalog.json")
    cat.put(Entry(category="demo", root=str(tmp), config="demo.yaml"))
    return cat


print("=" * 78)
print("1. ЗАВЕРШЁННЫЙ ПРОГОН: состояние разработки обновляется")
print("=" * 78)
cat = fresh_catalog()
cat.record_run("demo", rev_id="rev-0001", corpus_hash="h1", config_hash="c1",
               file_count=842, node_count=6419, edge_count=59063,
               seconds=158.871, capability="full", trigger="guard:fast")
entry = cat.get("demo")
check(entry is not None and entry.rev_id == "rev-0001", "ревизия записана", repr(entry and entry.rev_id))
check(entry is not None and entry.file_count == 842, "файлов 842", repr(entry and entry.file_count))
check(entry is not None and entry.state == "indexed", "состояние indexed", repr(entry and entry.state))
check(entry is not None and field(entry.runs[0], "state") == "ready",
      "прогон помечен ready", repr(entry and entry.runs[0]))

print()
print("=" * 78)
print("2. ПРЕРВАННЫЙ ПРОГОН: ревизия и числа НЕ переписываются")
print("=" * 78)
cat.record_run("demo", rev_id="rev-0002", corpus_hash="h2", config_hash="c1",
               file_count=0, node_count=0, edge_count=0,
               seconds=131.267, capability="degraded", trigger="guard:full",
               completed=False)
entry = cat.get("demo")
check(entry.rev_id == "rev-0001", "действует прежняя ревизия rev-0001", repr(entry.rev_id))
check(entry.file_count == 842, "число файлов не обнулилось", repr(entry.file_count))
check(entry.node_count == 6419 and entry.edge_count == 59063,
      "узлы и рёбра прежние", f"{entry.node_count}/{entry.edge_count}")
check(entry.corpus_hash == "h1",
      "сумма корпуса описывает проиндексированное состояние", repr(entry.corpus_hash))
check(entry.state == "aborted", "состояние называет прерванный прогон", repr(entry.state))
check(field(entry.runs[0], "rev_id") == "rev-0002" and field(entry.runs[0], "state") == "aborted",
      "прерванный прогон виден в истории с пометкой", repr(entry.runs[0]))
check(field(entry.runs[1], "state") == "ready", "прежний прогон не испорчен", repr(entry.runs[1]))

print()
print("=" * 78)
print("3. СВОДКА РЕЕСТРА: catalog показывает то же, что status")
print("=" * 78)
row = cat.summary()["entries"][0]
check(row["rev_id"] == "rev-0001", "в сводке прежняя ревизия", repr(row["rev_id"]))
check(row["files"] == 842, "в сводке прежнее число файлов", repr(row["files"]))
check(row["state"] == "aborted", "в сводке видно, что прогон прерван", repr(row["state"]))

print()
print("=" * 78)
print("4. РЕГРЕССИЯ: после прерванного успешный прогон снова обновляет реестр")
print("=" * 78)
cat.record_run("demo", rev_id="rev-0004", corpus_hash="h3", config_hash="c1",
               file_count=1122, node_count=7294, edge_count=74467,
               seconds=213.5, capability="full", trigger="cli:index")
entry = cat.get("demo")
check(entry.rev_id == "rev-0004", "новая ревизия принята", repr(entry.rev_id))
check(entry.file_count == 1122, "новые числа приняты", repr(entry.file_count))
check(entry.state == "indexed", "состояние вернулось в indexed", repr(entry.state))

print()
print("=" * 78)
print("5. ЕДИНСТВЕННАЯ ТОЧКА ЗАПИСИ передаёт признак завершённости")
print("=" * 78)


class FakeCatalog:
    """Ловит аргументы, с которыми watch зовёт запись прогона."""

    calls: list[dict] = []

    def __init__(self, *args, **kwargs) -> None:
        pass

    def category_for_root(self, root: str) -> str:
        return "demo"

    def get(self, category: str):
        return object()          # разработка уже заведена

    def record_run(self, category: str, **kwargs):
        FakeCatalog.calls.append({"category": category, **kwargs})
        return None


class Cfg:
    root = "X:/demo"
    project = "demo"
    config_path = "X:/demo/demo.yaml"


class Report:
    rev_id = "rev-0009"
    ok = False                  # прогон прерван
    files = 0
    nodes = 0
    edges = 0
    seconds = 131.267
    capability = "degraded"


saved = (watch.Catalog, catalog_mod.Catalog, watch.current_hashes,
         watch.write_fingerprint, watch.invalidate_cache)
try:
    # `record_index_run` импортирует Catalog внутри функции, поэтому патчится
    # и модуль, и имя в watch: иначе подмена не действует и тест «проходит»
    # на настоящем реестре.
    watch.Catalog = FakeCatalog                                  # type: ignore[assignment]
    catalog_mod.Catalog = FakeCatalog                            # type: ignore[assignment]
    watch.current_hashes = lambda cfg: ("h", "c", 7, {})         # type: ignore[assignment]
    watch.write_fingerprint = lambda cfg, rev: None              # type: ignore[assignment]
    watch.invalidate_cache = lambda category: None               # type: ignore[assignment]
    watch.record_index_run(Cfg(), Report(), trigger="guard:full")
finally:
    (watch.Catalog, catalog_mod.Catalog, watch.current_hashes,
     watch.write_fingerprint, watch.invalidate_cache) = saved

calls = FakeCatalog.calls
check(len(calls) == 1, "прогон записан один раз", repr(len(calls)))
call = calls[0] if calls else {}
check(call.get("completed") is False, "передан completed=False", repr(call.get("completed")))
check(call.get("rev_id") == "rev-0009", "ревизия передана", repr(call.get("rev_id")))

print()
print("=" * 78)
print(f"ИТОГО: проверок {PASS + FAIL}, успешно {PASS}, провалено {FAIL}")
print("=" * 78)
raise SystemExit(0 if FAIL == 0 else 1)
