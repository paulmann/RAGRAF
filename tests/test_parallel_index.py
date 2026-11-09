"""Два одновременных прогона индексатора: реестр и ревизии не портятся.

Стиль как у остальных проверок проекта: самостоятельный скрипт с функцией
`check`, запускается напрямую (`python tests/test_parallel_index.py`).

Какие дефекты закрываются (оба замерены 06.11.2026):

  1. **Потерянная запись реестра.** `Catalog.save()` читал файл один раз и писал
     его целиком. Две параллельные индексации РАЗНЫХ корпусов читали одно
     состояние, и вторая запись затирала первую — точно так же терялся прогон.
     Теперь запись идёт под коротким замком и накладывает только свои записи.

  2. **Два индексатора одного корпуса.** Команды `index` и `reindex`, `ensure`
     и кнопка в панели создавали `Indexer` напрямую, без замка: два
     одновременных `index` одного корпуса шли ПАРАЛЛЕЛЬНО и оба брали
     `rev-0001`, то есть писали одну ревизию вдвоём.

Проверки:
  1. два разных корпуса одновременно — в реестре обе записи, числа каждой
     совпадают с запечатанной ревизией её графа;
  2. один корпус в двух процессах — ревизии РАЗНЫЕ (второй дождался замка),
     реестр совпадает с графом.

Ничего не меняет за пределами временного каталога: реестр уводится в
`RAGRAF_CATALOG_DIR`, корпуса — во временный каталог и удаляются.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PASS = 0
FAIL = 0


def check(condition: bool, title: str, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  [OK ] {title}")
    else:
        FAIL += 1
        print(f"  [FAIL] {title}" + (f"\n      {detail}" if detail else ""))


def interpreter() -> str:
    """Тот же Python, что запустил проверку: venv проекта подходит."""
    return sys.executable


def make_corpus(root: Path, name: str, count: int) -> Path:
    """Корпус из `count` файлов: индексация должна идти секунды, не мгновение.

    Это важно: на трёх файлах прогон занимает доли секунды, окна гонки нет,
    и проверка «прошло» ничего не доказывает.
    """
    root.mkdir(parents=True, exist_ok=True)
    for i in range(count):
        (root / f"mod{i:02d}.py").write_text(
            f"def fn{i}(x):\n    return x + {i}\n", encoding="utf-8", newline="\n")
    cfg = root / "ragraf.yaml"
    cfg.write_text(
        f"""project: {name}
root: {root.as_posix()}

languages:
  - python

include:
  - "**/*.py"

exclude:
  - "**/.ragraf/**"

db:
  enabled: false

embedding:
  model: sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2
  dim: 384

index:
  joern_enabled: false
  budget_seconds: 600
""",
        encoding="utf-8",
        newline="\n",
    )
    return cfg


def sealed(root: Path) -> dict:
    db = root / ".ragraf" / "index" / "graph" / "graph.sqlite"
    if not db.is_file():
        return {}
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT rev_id, file_count, node_count, edge_count FROM rev"
        " WHERE state IN ('ready','sealed') ORDER BY rev_id DESC LIMIT 1"
    ).fetchone()
    conn.close()
    return dict(row) if row else {}


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        state = base / "state"
        state.mkdir()
        (state / "catalog.json").write_text(
            json.dumps({"version": 1, "entries": {}}), encoding="utf-8")
        env = dict(os.environ)
        env["RAGRAF_CATALOG_DIR"] = str(state)

        def spawn(cfg: Path) -> subprocess.Popen:
            return subprocess.Popen(
                [interpreter(), str(REPO / "tools" / "ragraf.py"), "-p", str(cfg), "index"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                encoding="utf-8", errors="replace", env=env)

        print("=== 1. Два РАЗНЫХ корпуса одновременно ===")
        alpha = base / "alpha"
        beta = base / "beta"
        cfg_a = make_corpus(alpha, "alpha", 40)
        cfg_b = make_corpus(beta, "beta", 40)
        pa, pb = spawn(cfg_a), spawn(cfg_b)
        pa.communicate(timeout=1800)
        pb.communicate(timeout=1800)

        entries = (json.loads((state / "catalog.json").read_text(encoding="utf-8"))
                   .get("entries") or {})
        check(set(entries) == {"alpha", "beta"},
              "в реестре обе записи (ни одна не затёрта)", str(sorted(entries)))
        for name, root in (("alpha", alpha), ("beta", beta)):
            entry = entries.get(name) or {}
            rev = sealed(root)
            have = (entry.get("file_count"), entry.get("node_count"), entry.get("edge_count"))
            want = (rev.get("file_count"), rev.get("node_count"), rev.get("edge_count"))
            check(have == want and entry.get("rev_id") == rev.get("rev_id"),
                  f"{name}: реестр совпадает с запечатанной ревизией",
                  f"реестр {have} {entry.get('rev_id')} / граф {want} {rev.get('rev_id')}")

        print("\n=== 2. Один корпус в двух процессах ===")
        gamma = base / "gamma"
        cfg_c = make_corpus(gamma, "gamma", 30)
        pc1, pc2 = spawn(cfg_c), spawn(cfg_c)
        out1, _ = pc1.communicate(timeout=1800)
        out2, _ = pc2.communicate(timeout=1800)

        conn = sqlite3.connect(gamma / ".ragraf" / "index" / "graph" / "graph.sqlite")
        revs = [r[0] for r in conn.execute(
            "SELECT rev_id FROM rev WHERE state IN ('ready','sealed') ORDER BY rev_id").fetchall()]
        conn.close()
        check(len(revs) >= 2 and len(set(revs)) == len(revs),
              "два прогона дали разные ревизии (второй дождался замка)",
              f"ревизии: {revs}; вывод: {out1[-120:]} / {out2[-120:]}")
        check(all(code == 0 for code in (pc1.returncode, pc2.returncode)),
              "оба процесса завершились без ошибки",
              f"{pc1.returncode}/{pc2.returncode}")

        entry = (json.loads((state / "catalog.json").read_text(encoding="utf-8"))
                 .get("entries") or {}).get("gamma") or {}
        rev = sealed(gamma)
        check((entry.get("node_count"), entry.get("edge_count"))
              == (rev.get("node_count"), rev.get("edge_count")),
              "после двух прогонов реестр совпадает с графом",
              f"реестр {(entry.get('node_count'), entry.get('edge_count'))}"
              f" / граф {(rev.get('node_count'), rev.get('edge_count'))}")

    print("\n=== ИТОГ ===")
    print(f"пройдено: {PASS}, провалено: {FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
