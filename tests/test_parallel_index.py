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

        def tail(text: str, lines: int = 6) -> str:
            """Хвост вывода дочернего процесса: без него отказ неразбираем.

            Замер 23.11.2026: под групповой нагрузкой раздел 1 падал с «в реестре
            одна запись из двух», а код возврата и текст ошибки дочернего
            процесса выбрасывались — причина была невидна, и отказ выглядел как
            дефект реестра без объяснения.
            """
            rows = [row for row in (text or "").strip().splitlines() if row.strip()]
            return " / ".join(rows[-lines:])[:600]

        print("=== 1. Два РАЗНЫХ корпуса одновременно ===")
        alpha = base / "alpha"
        beta = base / "beta"
        cfg_a = make_corpus(alpha, "alpha", 40)
        cfg_b = make_corpus(beta, "beta", 40)
        pa, pb = spawn(cfg_a), spawn(cfg_b)
        out_a, err_a = pa.communicate(timeout=1800)
        out_b, err_b = pb.communicate(timeout=1800)
        # Код возврата проверяется ОТДЕЛЬНО: прогон, который упал, но оставил
        # часть записей, раньше проходил мимо внимания.
        check(pa.returncode == 0, "alpha: index завершился без ошибки",
              f"код {pa.returncode}: {tail((out_a or '') + (err_a or ''))}")
        check(pb.returncode == 0, "beta: index завершился без ошибки",
              f"код {pb.returncode}: {tail((out_b or '') + (err_b or ''))}")

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

    print("\n=== 3. Проба записи в каталог состояния: гонка между процессами ===")
    import threading

    if str(REPO / "src") not in sys.path:
        sys.path.insert(0, str(REPO / "src"))
    from ragraf.util import dir_blocker  # noqa: E402

    shared = base / "probe-race"
    shared.mkdir(parents=True, exist_ok=True)
    verdicts: list[str | None] = []
    guard = threading.Lock()

    def probe() -> None:
        answer = dir_blocker(shared)
        with guard:
            verdicts.append(answer)

    threads = [threading.Thread(target=probe) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    check(len(verdicts) == 8 and all(item is None for item in verdicts),
          "8 одновременных проверок одного каталога: все отвечают «можно писать»",
          str([item for item in verdicts if item is not None])[:140])
    leftovers = [p.name for p in shared.iterdir() if "write-probe" in p.name]
    check(leftovers == [], "пробы не остаются после проверки", str(leftovers)[:100])

    print("\n=== 4. Читатель не видит частичный реестр ===")
    # Замер 23.11.2026: при прямой `write_text` читатель видел ПУСТОЙ `catalog.json`
    # 231 раз из 1058 чтений — файл сначала обрезается, потом наполняется. Для
    # реестра это потеря состояния: пустой файл читается как «реестра нет», слияние
    # чужих записей пропускается, и корпуса соседа исчезают (этим объясняется
    # провал раздела 1 в групповом прогоне). Проверка держит свойство: пока идёт
    # запись, чтение либо даёт прежнее/новое содержимое, либо перечитывает.
    from ragraf import util as util_mod  # noqa: E402

    race_path = base / "race" / "catalog.json"
    payload = {"version": 1, "entries": {f"corpus-{i}": {"root": "/x", "rev_id": "rev-0001"}
                                          for i in range(120)}}
    util_mod.write_json(race_path, payload)
    stop_writing = threading.Event()
    bad_reads: list[str] = []
    read_count = 0

    def reader() -> None:
        nonlocal read_count
        while not stop_writing.is_set():
            read_count += 1
            try:
                got = util_mod.read_json(race_path, default=None)
                if not isinstance(got, dict) or not (got.get("entries") or {}):
                    bad_reads.append("пусто или не словарь")
            except Exception as exc:  # noqa: BLE001 — любая неудача чтения и есть находка
                bad_reads.append(f"{type(exc).__name__}: {exc}")

    readers = [threading.Thread(target=reader, daemon=True) for _ in range(2)]
    for thread in readers:
        thread.start()
    try:
        for i in range(60):
            payload["entries"][f"corpus-w{i}"] = {"root": "/x", "rev_id": f"rev-w{i}"}
            util_mod.write_json(race_path, payload)
    finally:
        stop_writing.set()
        for thread in readers:
            thread.join(timeout=5)

    check(bad_reads == [], "читатель не видит частичный файл реестра",
          f"неудачных {len(bad_reads)} из {read_count}: {bad_reads[:2]}")
    tmp_left = [p.name for p in (base / "race").iterdir() if ".tmp-" in p.name]
    check(tmp_left == [], "временные файлы записи убраны", str(tmp_left)[:100])

    print("\n=== ИТОГ ===")
    print(f"пройдено: {PASS}, провалено: {FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
