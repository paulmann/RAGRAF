"""Проверка операции `neighbors`: готовые реализации в соседних корпусах.

Что проверяется — свойства, а не текст:

  1. сосед определяется по стеку ФАКТИЧЕСКОГО индекса, а не по списку языков
     в конфиге (в конфиге перечислено то, что корпусу РАЗРЕШЕНО);
  2. корпус с другим стеком в соседей не попадает и назван в `skipped`
     с причиной;
  3. корпус, где мой язык в меньшинстве, не объявляется «тем же стеком»:
     измеренный случай — PHP-проект с единичными файлами css/html/js/python
     покрывал 6 из 6 языков RAGRAF и по доле покрытия шёл первым;
  4. корпус без индекса назван с причиной и не роняет ответ;
  5. текущий корпус не попадает в собственные хиты;
  6. хит несёт путь, строку и СВОЮ ревизию — ревизию того корпуса, откуда он
     пришёл, а не ревизию текущего;
  7. два прогона на неизменном состоянии дают один и тот же порядок;
  8. чужой индекс не изменяется: sha256 его баз и `rev.json` до и после вызова
     совпадают (операция обязана быть читающей по отношению к соседям);
  9. проверка сама не засоряет рабочий реестр разработок.

Фикстура строится во временном каталоге библиотечным путём (`init_corpus` и
`Indexer`), а НЕ командами CLI: `index` пишет прогон в реестр разработок
(`cli._record_index_run`), и проверка оставляла бы в рабочем `catalog.json`
записи о удалённых временных каталогах. Замерено 22.10.2026: после первого
прогона в реестре появились четыре мёртвые записи.

Рабочий реестр подменяется (`FakeCatalog`), а его файл сверяется по sha256.

Запуск: python tests/test_neighbors.py
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf import init_corpus  # noqa: E402
from ragraf import neighbors as nb  # noqa: E402
from ragraf.config import load as load_config  # noqa: E402
from ragraf.indexer import Indexer  # noqa: E402

PASS = 0
FAIL = 0
REGISTRY = REPO / "catalog" / "catalog.json"


def check(cond: bool, label: str, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [OK ] {label}")
    else:
        FAIL += 1
        print(f"  [FAIL] {label} {detail}")


def sha(path: pathlib.Path) -> str:
    if not path.exists():
        return ""
    return hashlib.sha256(path.read_bytes()).hexdigest()


class FakeCatalog:
    """Реестр, подставленный проверкой: рабочий `catalog.json` не читается."""

    def __init__(self, entries: dict) -> None:
        self._entries = entries

    def entries(self) -> dict:
        return self._entries


def entry(category: str, root: pathlib.Path, config: pathlib.Path,
          rev: str = "", files: int = 2) -> dict:
    return {
        "category": category,
        "project": category,
        "root": root.as_posix(),
        "config": config.as_posix(),
        "rev_id": rev,
        "capability": "degraded",
        "indexed_at": "",
        "file_count": files,
        "index_enabled": True,
    }


A_PY = """def alpha_total(items):
    return sum(items)
"""

A_HELPER = """def alpha_label(value):
    return f'alpha:{value}'
"""

B_PY = '''def parse_invoice_total(raw):
    """Считает итог счёта: сумма строк плюс НДС."""
    total = 0
    for line in raw.splitlines():
        total += float(line.strip() or 0)
    return total * 1.2
'''

B_HELPER = '''def invoice_label(number):
    return f'schet-{number}'
'''

C_PHP = """<?php
echo 'c';
"""

C_HELPER = """<?php
function gamma_helper() { return 1; }
"""

C_THIRD = """<?php
function gamma_other() { return 2; }
"""

# Корпус, ПОВТОРЯЮЩИЙ измеренный случай: основной язык — PHP, а язык текущего
# корпуса (python) в нём в меньшинстве.
EPS_PHP = """<?php
function eps_one() { return 1; }
"""

EPS_PHP2 = """<?php
function eps_two() { return 2; }
"""

EPS_PHP3 = """<?php
function eps_three() { return 3; }
"""

EPS_PY = """def eps_helper():
    return 1
"""

registry_before = sha(REGISTRY)

with tempfile.TemporaryDirectory(prefix="ragraf-neighbors-") as tmp:
    base = pathlib.Path(tmp)
    roots = {}
    for name, files in (
        ("alpha", {"alpha.py": A_PY, "alpha_util.py": A_HELPER,
                   "notes.md": "# Альфа\n"}),
        ("beta", {"beta.py": B_PY, "beta_util.py": B_HELPER,
                  "readme.md": "# Бета\n"}),
        ("gamma", {"page.php": C_PHP, "extra.php": C_HELPER,
                   "third.php": C_THIRD}),
        ("epsilon", {"main.php": EPS_PHP, "two.php": EPS_PHP2,
                     "three.php": EPS_PHP3, "tool.py": EPS_PY,
                     "style.css": ".eps { color: #000; }\n",
                     "app.js": "const eps = 1;\n",
                     "index.html": "<p>eps</p>\n"}),
        ("delta", {"delta.py": A_PY, "delta2.py": A_HELPER,
                   "notes.md": "# Дельта\n"}),
    ):
        root = base / name
        root.mkdir(parents=True, exist_ok=True)
        for rel, text in files.items():
            (root / rel).write_text(text, encoding="utf-8")
        roots[name] = root

    print("=" * 78)
    print("1. КОНФИГИ И ИНДЕКСАЦИЯ ФИКСТУРЫ")
    print("=" * 78)
    configs = {}
    for name, root in roots.items():
        cfg_path = base / f"{name}.yaml"
        try:
            path, _scan = init_corpus.create_config(
                root, project=name, config_path=cfg_path,
            )
        except (FileExistsError, ValueError) as exc:
            print(f"  [FAIL] конфиг корпуса {name}: {exc}")
            raise SystemExit(1)
        configs[name] = path
    check(len(configs) == 5, "конфиги пяти корпусов созданы", str(len(configs)))

    for name in ("alpha", "beta", "gamma", "epsilon"):
        cfg = load_config(configs[name])
        indexer = Indexer(cfg)
        try:
            report = indexer.run(with_vectors=False)
        finally:
            indexer.close()
        check(bool(report.ok), f"корпус {name} проиндексирован",
              str(getattr(report, "notes", ""))[-200:])

    cfg_a = load_config(configs["alpha"])
    rev_a = nb.active_rev(cfg_a)
    check(bool(rev_a), "активная ревизия текущего корпуса прочитана", rev_a)

    catalog = FakeCatalog({
        "alpha": entry("alpha", roots["alpha"], configs["alpha"], rev_a),
        "beta": entry("beta", roots["beta"], configs["beta"], nb.active_rev(
            load_config(configs["beta"]))),
        "gamma": entry("gamma", roots["gamma"], configs["gamma"], nb.active_rev(
            load_config(configs["gamma"]))),
        "epsilon": entry("epsilon", roots["epsilon"], configs["epsilon"],
                         nb.active_rev(load_config(configs["epsilon"]))),
        # Зарегистрирован, но индекс не строился: сосед есть, спросить нечем.
        "delta": entry("delta", roots["delta"], configs["delta"], ""),
    })

    print()
    print("=" * 78)
    print("2. СТЕК И ОТБОР СОСЕДЕЙ")
    print("=" * 78)
    plan = nb.discover(cfg_a, rev_a, mine=None, min_shared=1, verify=False,
                       catalog=catalog)
    stack = plan["mine"]
    check("python" in stack, "стек текущего корпуса — python", str(stack))
    check("php" not in stack,
          "php в стек не попал: файлов этого языка в индексе нет", str(stack))
    names = [n["corpus"] for n in plan["neighbors"]]
    check(names[:1] == ["beta"], "первым идёт корпус с тем же стеком", str(names))
    check("alpha" not in names and "alpha" not in
          {s["corpus"] for s in plan["skipped"]},
          "текущий корпус не считается соседом", str(names))
    by_name = {n["corpus"]: n for n in plan["neighbors"]}
    eps = by_name.get("epsilon")
    check(eps is None or eps["match"] != "same",
          "корпус, где мой язык в меньшинстве, не объявлен тем же стеком",
          str(eps and (eps["match"], eps["similarity"], eps["primary_neighbor"])))
    check(eps is None or eps["primary_neighbor"] == "php",
          "основной язык соседа назван верно",
          str(eps and eps["primary_neighbor"]))
    skipped = {s["corpus"]: s["reason"] for s in plan["skipped"]}
    check("общий стек" in skipped.get("gamma", ""),
          "корпус с другим стеком назван в skipped", str(skipped.get("gamma")))
    check("индекс не построен" in skipped.get("delta", ""),
          "корпус без индекса назван с причиной", str(skipped.get("delta")))

    print()
    print("=" * 78)
    print("3. ПОИСК ГОТОВОЙ РЕАЛИЗАЦИИ")
    print("=" * 78)
    first = nb.find(cfg_a, rev_a, query="parse_invoice_total",
                    name="parse_invoice_total", limit=20, per_corpus=5,
                    with_text=True, verify=False, catalog=catalog)
    hits = first["hits"]
    check(bool(hits), "хиты найдены", json.dumps(first["totals"], ensure_ascii=False))
    check(all(h["corpus"] == "beta" for h in hits),
          "все хиты из соседнего корпуса",
          str(sorted({h["corpus"] for h in hits})))
    check(bool(hits) and hits[0]["match"] == "exact_name",
          "первым идёт точное совпадение имени",
          hits[0]["match"] if hits else "нет хитов")
    exact = [h for h in hits if h["match"] == "exact_name"]
    check(bool(exact) and exact[0].get("symbol", {}).get("name") == "parse_invoice_total",
          "символ назван по имени", str(exact[0].get("symbol") if exact else None))
    check(bool(exact) and int(exact[0]["start_line"] or 0) > 0,
          "у хита есть номер строки", str(exact[0] if exact else None))
    rev_b = nb.active_rev(load_config(configs["beta"]))
    check(bool(hits) and all(h["index_rev"] == rev_b for h in hits),
          "у каждого хита своя ревизия корпуса-источника", f"ожидалась {rev_b}")
    check(bool(hits) and all(str(h["file"]).startswith(roots["beta"].as_posix())
                             for h in hits),
          "абсолютный путь указывает в корпус-источник")
    with_text = [h for h in hits if h.get("snippet")]
    check(bool(with_text),
          "фрагмент текста пришёл из индекса, а не чтением файла",
          str(len(with_text)))

    print()
    print("=" * 78)
    print("4. ДЕТЕРМИНИЗМ")
    print("=" * 78)
    second = nb.find(cfg_a, rev_a, query="parse_invoice_total",
                     name="parse_invoice_total", limit=20, per_corpus=5,
                     with_text=True, verify=False, catalog=catalog)
    check(json.dumps(first, ensure_ascii=False, sort_keys=True)
          == json.dumps(second, ensure_ascii=False, sort_keys=True),
          "два прогона дают побайтово одинаковый ответ")

    print()
    print("=" * 78)
    print("5. ЧУЖОЙ ИНДЕКС НЕ ИЗМЕНЯЕТСЯ")
    print("=" * 78)
    peer = load_config(configs["beta"])
    targets = (peer.graph_db, peer.lexvec_db, peer.control_db, peer.rev_file)
    before = {p.name: sha(p) for p in targets}
    nb.find(cfg_a, rev_a, query="итог счёта", name="parse_invoice_total",
            limit=10, per_corpus=5, with_text=True, verify=False, catalog=catalog)
    after = {p.name: sha(p) for p in targets}
    check(before == after and all(before.values()),
          "базы и rev.json соседа не изменились",
          str({k: (before.get(k), after.get(k)) for k in before
               if before.get(k) != after.get(k)}))

    print()
    print("=" * 78)
    print("6. ГРАНИЦЫ")
    print("=" * 78)
    raised = False
    try:
        nb.find(cfg_a, rev_a, query="", name="", catalog=catalog)
    except ValueError as exc:
        raised = "query или name" in str(exc)
    check(raised, "пустой запрос и пустое имя отвергаются с причиной")

    php_only = nb.find(cfg_a, rev_a, query="echo", limit=10, stack="php",
                       min_shared=1, verify=False, catalog=catalog)
    php_names = [n["corpus"] for n in php_only["neighbors"]]
    check("gamma" in php_names and "beta" not in php_names,
          "явный --stack php меняет набор соседей", str(php_names))

    two = nb.find(cfg_a, rev_a, query="parse_invoice_total", min_shared=3,
                  verify=False, catalog=catalog)
    check(two["neighbors"] == [] and two["hits"] == [],
          "min_shared выше числа общих языков отсекает соседа",
          str([n["corpus"] for n in two["neighbors"]]))

print()
print("=" * 78)
print("7. ПРОВЕРКА НЕ ЗАСОРЯЕТ РЕЕСТР")
print("=" * 78)
check(sha(REGISTRY) == registry_before,
      "файл реестра разработок не изменился",
      f"{registry_before[:12]} → {sha(REGISTRY)[:12]}")

print()
print("=" * 78)
print(f"ИТОГО: пройдено {PASS}, провалено {FAIL}")
print("=" * 78)
raise SystemExit(0 if FAIL == 0 else 1)
