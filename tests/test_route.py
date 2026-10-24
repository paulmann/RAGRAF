"""Проверка маршрутизатора: где лежит и какой индекс отвечает.

Свойства:
  1. совпадение по имени/пути/файлам делает разработку кандидатом;
  2. свежесть сама по себе кандидатом НЕ делает (иначе запрос про
     несуществующую тему рекомендовал бы случайный корпус);
  3. файл приписывается САМОМУ ГЛУБОКОМУ корпусу, а не родительскому
     источнику — иначе вопрос про чужой проект уходил бы в соседний;
  4. порядок детерминирован: два вызова дают один и тот же список;
  5. без локального индекса ответ честно говорит «индекса нет»;
  6. возраст записи не выдаётся за проверенную свежесть.
"""

from __future__ import annotations

import pathlib
import sys
import tempfile
from datetime import datetime, timezone

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf.global_map import GlobalMap  # noqa: E402
from ragraf.route import _freshness_score, _tokens, route  # noqa: E402
from ragraf.sources import SourceRegistry  # noqa: E402
from ragraf import util  # noqa: E402

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


class FakeCatalog:
    def __init__(self, entries: dict) -> None:
        self._entries = entries

    def entries(self) -> dict:
        return self._entries


def make_entry(category: str, root: pathlib.Path, rev: str,
               capability: str = "full") -> dict:
    return {
        "category": category,
        "project": category,
        "root": util.to_posix(root),
        "config": str(root / "config" / "missing.yaml"),
        "rev_id": rev,
        "capability": capability,
        "indexed_at": datetime.now(timezone.utc).isoformat(),
        "file_count": 2,
    }


print("=" * 78)
print("1. СЛОВА И СЛУЖЕБНЫЕ ЧАСТИЦЫ")
print("=" * 78)
check(_tokens("где лежат материалы по AMD и какой индекс") == ["лежат", "материалы", "amd", "индекс"],
      "служебные слова отброшены", str(_tokens("где лежат материалы по AMD и какой индекс")))
check(_tokens("") == [], "пустой запрос даёт пустой список слов")

with tempfile.TemporaryDirectory(prefix="ragraf-route-") as tmp:
    base = pathlib.Path(tmp)
    src_root = base / "workspace"
    amd_root = src_root / "notes"
    other_root = src_root / "other"
    for path in (amd_root, other_root):
        path.mkdir(parents=True, exist_ok=True)
    (amd_root / "notes-a.md").write_text("материалы проекта\n", encoding="utf-8")
    (other_root / "readme.md").write_text("другой проект\n", encoding="utf-8")
    (other_root / "notes-extra.md").write_text("упоминание notes в имени\n", encoding="utf-8")

    reg = SourceRegistry(base / "sources.json")
    src = reg.add(src_root)
    gm = GlobalMap(base / "global_map.sqlite")
    gm.sync_sources(reg)
    scan = gm.scan(src)
    gm.close()
    check(scan.files == 3, "карта построена: 3 файла", str(scan.files))

    catalog = FakeCatalog({
        "notes-project": make_entry("notes-project", amd_root, "rev-0002"),
        "other-project": make_entry("other-project", other_root, "rev-0001",
                                    capability="degraded"),
    })

    print()
    print("=" * 78)
    print("2. АТРИБУЦИЯ ФАЙЛОВ САМОМУ ГЛУБОКОМУ КОРПУСУ")
    print("=" * 78)
    first = route("notes", catalog=catalog, map_path=base / "global_map.sqlite", verify=False)
    by_name = {c["category"]: c for c in first["candidates"] if c["type"] == "corpus"}
    check(set(by_name) == {"notes-project", "other-project"},
          "оба корпуса — кандидаты", str(sorted(by_name)))
    check(by_name["notes-project"]["file_hits"] == 1,
          "файл notes/notes-a.md приписан notes-project", str(by_name["notes-project"]["file_hits"]))
    check(by_name["other-project"]["file_hits"] == 1,
          "файл other/notes-extra.md приписан other-project, а не родителю",
          str(by_name["other-project"]["file_hits"]))
    check(first["recommended"]["category"] == "notes-project",
          "рекомендован notes-project", str(first["recommended"]["category"]))

    print()
    print("=" * 78)
    print("3. ДЕТЕРМИНИЗМ")
    print("=" * 78)
    second = route("notes", catalog=catalog, map_path=base / "global_map.sqlite", verify=False)
    shape_a = [(c["type"], c["category"], round(c["score"], 6)) for c in first["candidates"]]
    shape_b = [(c["type"], c["category"], round(c["score"], 6)) for c in second["candidates"]]
    check(shape_a == shape_b, "два вызова дали одинаковый порядок и оценки", f"{shape_a} != {shape_b}")
    check(first["tokens"] == second["tokens"], "слова запроса совпали")

    print()
    print("=" * 78)
    print("4. СВЕЖЕСТЬ НЕ СОЗДАЁТ КАНДИДАТА")
    print("=" * 78)
    empty = route("несуществующая-тема-zzz", catalog=catalog,
                  map_path=base / "global_map.sqlite", verify=False)
    check(empty["candidates"] == [], "нет совпадений — нет кандидатов",
          str(empty["candidates"]))
    check(empty["recommended"] is None, "рекомендации нет")
    check(bool(empty["notes"]), "причина названа", str(empty["notes"]))

    print()
    print("=" * 78)
    print("5. БЕЗ ЛОКАЛЬНОГО ИНДЕКСА")
    print("=" * 78)
    no_index = FakeCatalog({
        "notes-project": {**make_entry("notes-project", amd_root, ""),
                          "capability": "", "indexed_at": ""},
    })
    plain = route("notes", catalog=no_index, map_path=base / "global_map.sqlite", verify=False)
    check(plain["candidates"], "кандидат по файлам остаётся")
    check(plain["recommended"] is None, "локального индекса нет — рекомендации нет")
    check(any("локального индекса нет" in w for w in plain["candidates"][0]["why"]),
          "причина названа в why", str(plain["candidates"][0]["why"]))
    check(any("ensure" in n for n in plain["notes"]),
          "подсказка про ensure есть", str(plain["notes"]))

    print()
    print("=" * 78)
    print("6. ИСЧЕЗНУВШИЙ КОРЕНЬ НЕ РЕКОМЕНДУЕТСЯ")
    print("=" * 78)
    missing_root = base / "gone-project"
    gone = FakeCatalog({"gone": make_entry("gone", missing_root, "rev-0009")})
    out = route("gone", catalog=gone, map_path=base / "global_map.sqlite", verify=False)
    rows = [c for c in out["candidates"] if c["type"] == "corpus"]
    check(bool(rows), "кандидат виден, а не исчезает молча", str(rows))
    if rows:
        check(rows[0].get("available") is False,
              "кандидат помечен available=false", str(rows[0].get("available")))
        check(any("не найден на диске" in w for w in rows[0]["why"]),
              "причина названа в why", str(rows[0]["why"]))
    check(out["recommended"] is None, "устаревшая запись не рекомендуется")
    check(any("не найдена на диске" in n for n in out["notes"]),
          "предупреждение в notes", str(out["notes"]))

print()
print("=" * 78)
print("6. СВЕЖЕСТЬ НЕ ВЫДАЁТСЯ ЗА ПРОВЕРЕННУЮ")
print("=" * 78)
_, why = _freshness_score(3600)
check("реестр" in why and "обновлён" not in why,
      "возраст назван записью реестра, а не проверенной свежестью", why)

print()
print("=" * 78)
print(f"ИТОГ: пройдено {PASS}, провалено {FAIL}")
print("=" * 78)
raise SystemExit(0 if FAIL == 0 else 1)
