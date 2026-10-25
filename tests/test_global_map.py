"""Проверка глобальной карты метаданных.

Главные свойства:
  1. содержимое файлов НЕ читается — даже секретоносных;
  2. секретоносный файл попадает только в `secret_place` (путь и правило);
  3. исключения (`vendor`, `node_modules`, `.ragraf`) работают, а `bin` — нет:
     его нельзя исключать глобально (в других проектах там рабочий код);
  4. два скана неизменённого дерева дают один отпечаток;
  5. предел числа файлов даёт честный `truncated`, а не молчаливое усечение;
  6. удаление источника убирает его строки из карты и не трогает диск.
"""

from __future__ import annotations

import builtins
import pathlib
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf.global_map import GlobalMap  # noqa: E402
from ragraf.sources import SourceRegistry  # noqa: E402

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


MARKER = "MARKER_7f3a_never_indexed"


def build_tree(base: pathlib.Path) -> None:
    files = {
        "docs/a.md": "# Документ\n",
        "docs/b.txt": "текст\n",
        "src/c.py": "print('x')\n",
        "data/d.csv": "a,b\n1,2\n",
        "bin/tool.php": "<?php echo 1;\n",
        ".env": f"API_VALUE={MARKER}\n",
        "sub/.env": f"OTHER_VALUE={MARKER}\n",
        "docs/credentials-main.json": '{"user":"u","pass":"p"}\n',
        "vendor/v.js": "var x=1;\n",
        "node_modules/n.js": "var y=2;\n",
        ".ragraf/x.db": "not a real db\n",
    }
    for rel, text in files.items():
        path = base / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


with tempfile.TemporaryDirectory(prefix="ragraf-map-") as tmp:
    base = pathlib.Path(tmp) / "source"
    base.mkdir(parents=True, exist_ok=True)
    build_tree(base)

    reg = SourceRegistry(pathlib.Path(tmp) / "sources.json")
    src = reg.add(base)

    gm = GlobalMap(pathlib.Path(tmp) / "global_map.sqlite")
    gm.sync_sources(reg)

    print("=" * 78)
    print("1. СКАН: ТОЛЬКО МЕТАДАННЫЕ")
    print("=" * 78)

    # Страховка: если скан попытается открыть секретоносный файл, тест упадёт.
    real_open = builtins.open

    def guarded_open(file, *args, **kwargs):
        text = str(file)
        if text.endswith(".env") or "credentials-main.json" in text:
            raise AssertionError(f"скан открыл секретоносный файл: {text}")
        return real_open(file, *args, **kwargs)

    builtins.open = guarded_open
    try:
        result = gm.scan(src)
    finally:
        builtins.open = real_open

    check(result.status == "ok", "скан завершён без ошибок", result.status)
    check(result.files == 5, "в карту попало 5 обычных файлов", str(result.files))
    check(result.secrets == 3, "3 секретоносных места, а не файла", str(result.secrets))
    check(gm.file_count(src.id) == 5, "file_meta содержит 5 строк")

    marks = gm.secret_places(src.id)
    rules = {row["rel_path"]: row["rule"] for row in marks}
    check(set(rules) == {".env", "sub/.env", "docs/credentials-main.json"},
          "секретоносные пути перечислены по правилам", str(sorted(rules)))
    check(rules.get(".env") == "**/.env", "правило для .env названо", str(rules.get(".env")))

    # find с пустым списком ничего не ищет (по контракту), поэтому проверяем
    # наличие/отсутствие конкретных путей отдельными запросами.
    check(bool(gm.find(["a.md"])), "обычный файл находится по имени")
    check(not gm.find([".env"]), "секретоносный файл НЕ находится как файл")
    check(not gm.find(["v.js"]), "исключённый vendor не попал в карту")
    check(bool(gm.find(["tool.php"])), "bin не исключён глобально: рабочий код на месте")
    check(bool(gm.find([base.name.lower()])),
          "файлы находятся по имени корня источника (а не только по rel_path)")

    print()
    print("=" * 78)
    print("2. СОДЕРЖИМОЕ НЕ УТЕКЛО")
    print("=" * 78)

    hits = []
    for name in ("global_map.sqlite", "global_map.sqlite-wal", "global_map.sqlite-shm"):
        path = pathlib.Path(tmp) / name
        if path.exists():
            if MARKER.encode("utf-8") in path.read_bytes():
                hits.append(name)
    check(not hits, "маркер содержимого не найден ни в базе, ни в WAL", str(hits))

    print()
    print("=" * 78)
    print("3. ДЕТЕРМИНИЗМ И ПРЕДЕЛЫ")
    print("=" * 78)

    before = result.fingerprint
    again = gm.scan(src)
    check(again.fingerprint == before, "повторный скан дал тот же отпечаток")
    check(again.files == result.files, "число файлов не изменилось")

    limited = gm.scan(src, limits={"max_files": 2})
    check(limited.truncated is True, "предел файлов помечен как truncated")
    check(limited.files <= 2, "файлов записано не больше предела", str(limited.files))
    check(bool(limited.reason), "причина усечения названа", limited.reason)

    # После усечённого скана карта заменена его результатом — это ожидаемо:
    # скан атомарен, а не «дополняет» прошлое состояние.
    check(gm.file_count(src.id) <= 2, "карта отражает последний скан, а не смесь")

    print()
    print("=" * 78)
    print("4. УДАЛЕНИЕ ИСТОЧНИКА")
    print("=" * 78)

    gm.purge(src.id)
    check(gm.file_count(src.id) == 0, "строки файлов удалены")
    check(gm.secret_places(src.id) == [], "строки секретных мест удалены")
    check((base / ".env").exists(), "файлы на диске не тронуты")

    # Снятие источника в реестре убирает его и из карты при синхронизации.
    gm.scan(src)
    reg.remove(src.id)
    gm.sync_sources(reg)
    check(gm.file_count(src.id) == 0, "источник, снятый в реестре, удалён из карты")
    check(src.root not in [row["root"] for row in gm.sources()],
          "запись источника исчезла из таблицы source")

    gm.close()

print()
print("=" * 78)
print(f"ИТОГ: пройдено {PASS}, провалено {FAIL}")
print("=" * 78)
raise SystemExit(0 if FAIL == 0 else 1)
