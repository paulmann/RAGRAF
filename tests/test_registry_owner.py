"""Ревизия не создаётся в чужом каталоге состояния — и запись подтверждается чтением.

Стиль как у остальных проверок проекта: самостоятельный скрипт с функцией
`check`, запускается напрямую (`python tests/test_registry_owner.py`).

Какой дефект закрывается. Замер 06.11.2026 на собственном корпусе RAGRAF:
ревизии rev-1442, rev-1443, rev-1446, rev-1448, rev-1449 закреплены в графе,
а записи о прогоне в реестре нет — он показывал предыдущий номер.

Причина не «MCP-сервер с другим реестром», а ПОЛОВИНЧАТАЯ изоляция состояния:

  * прогон проверок (`tools/check_all.py:340`) каждому набору отводит каталог
    состояния: `RAGRAF_CATALOG_DIR=<временный каталог>`, куда КОПИРУЕТСЯ живой
    реестр (`tools/check_all.py:303-323`), поэтому корпус в копии опознаётся;
  * `.ragraf` корпуса при этом НЕ отводится: индекс остаётся живым;
  * наборы, которые работают с собственным конфигом проекта
    (`tests/test_doc_layer_archive.py:398`, `tests/test_text_from_index.py:21`),
    поднимают фасад, а он перед каждой операцией гоняет сторожа
    (`src/ragraf/facade.py:2161`), и сторож пересобирал ЖИВОЙ корпус;
  * ревизия появлялась в живом `.ragraf`, а запись уходила в копию реестра,
    которая удалялась вместе с набором.

Доказательство внутри самих ревизий: в `stage_timing` rev-1448 и rev-1449
сказано «каталог состояния отведён переменной RAGRAF_CATALOG_DIR»
(`src/ragraf/indexer.py:170`) и «режим без CPG делает watch.py» — то есть прогон
шёл из `run_reindex`, а не из CLI.

Проверки:
  1. прогон в своём каталоге состояния записывает владельца в журнал корпуса;
  2. прогон сторожа с ЧУЖИМ каталогом состояния не создаёт ревизию и называет
     причину в ответе (реестр и граф остаются согласованными);
  3. прогон в своём каталоге после этого пересобирает корпус и попадает в реестр;
  4. запись, которая не подтвердилась чтением файла, объявляется отказом,
     а не успехом (подмена записи пустышкой).

Ничего не меняет за пределами временного каталога: корпус, оба реестра и
хранилища создаются во временном каталоге и удаляются вместе с ним.
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
SRC = REPO / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ragraf import catalog as catalog_mod  # noqa: E402
from ragraf import config as config_mod  # noqa: E402
from ragraf import util  # noqa: E402
from ragraf import watch as watch_mod  # noqa: E402

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


def make_corpus(root: Path, name: str, count: int = 12) -> Path:
    """Небольшой корпус: индексация занимает секунды, но не мгновение."""
    root.mkdir(parents=True, exist_ok=True)
    for i in range(count):
        (root / f"mod{i:02d}.py").write_text(
            f"def fn{i}(x):\n    return x + {i}\n", encoding="utf-8", newline="\n")
    cfg_path = root / "ragraf.yaml"
    cfg_path.write_text(
        f"""project: {name}
root: {util.to_posix(root)}

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
        encoding="utf-8", newline="\n",
    )
    return cfg_path


def state_dir(base: Path, name: str) -> Path:
    path = base / name
    path.mkdir(parents=True, exist_ok=True)
    (path / "catalog.json").write_text(
        json.dumps({"version": 1, "entries": {}}), encoding="utf-8")
    return path


def run(state: Path, cfg_path: Path, *args: str) -> tuple[int, str]:
    env = dict(os.environ)
    env["RAGRAF_CATALOG_DIR"] = str(state)
    env.pop("RAGRAF_TRACE_REGISTRY", None)
    env.pop("RAGRAF_TRACE_REGISTRY_FILE", None)
    proc = subprocess.run(
        [sys.executable, str(REPO / "tools" / "ragraf.py"), "-p", str(cfg_path), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=env, cwd=str(REPO))
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def graph_revs(root: Path) -> list[str]:
    db = root / ".ragraf" / "index" / "graph" / "graph.sqlite"
    if not db.is_file():
        return []
    conn = sqlite3.connect(db)
    rows = [str(r[0]) for r in conn.execute(
        "SELECT rev_id FROM rev WHERE state IN ('ready','sealed') ORDER BY rev_id")]
    conn.close()
    return rows


def registry_entry(state: Path, category: str) -> dict:
    data = json.loads((state / "catalog.json").read_text(encoding="utf-8"))
    return (data.get("entries") or {}).get(category) or {}


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        own = state_dir(base, "state-own")
        alien = state_dir(base, "state-alien")
        root = base / "corpus"
        cfg_path = make_corpus(root, "corpus")
        cfg = config_mod.load(cfg_path)

        print("=== 1. Прогон в своём каталоге состояния ===")
        code, out = run(own, cfg_path, "index")
        check(code == 0, "индексация завершилась успешно",
              out.strip()[-300:] if out else "нет вывода")
        revs = graph_revs(root)
        check(len(revs) == 1, "в графе одна запечатанная ревизия", str(revs))
        entry = registry_entry(own, "corpus")
        check(entry.get("rev_id") == (revs[-1] if revs else ""),
              "реестр своего каталога знает эту ревизию",
              f"реестр {entry.get('rev_id')} / граф {revs}")
        owner = watch_mod.registry_owner(cfg)
        check(util.to_posix(Path(owner)) == util.to_posix(own),
              "владелец индекса записан в журнал корпуса", owner or "не записан")

        print("\n=== 2. Сторож с ЧУЖИМ каталогом состояния ===")
        (root / "mod00.py").write_text(
            "def fn0(x):\n    return x + 500\n", encoding="utf-8", newline="\n")
        before = graph_revs(root)
        code, out = run(alien, cfg_path, "query", "freshness")
        after = graph_revs(root)
        check(code == 0, "обращение завершилось (отказ — не ошибка)", str(code))
        check(after == before,
              "ревизия в живом графе НЕ создана", f"было {before}, стало {after}")
        check(not registry_entry(alien, "corpus"),
              "в чужой реестр ничего не записано", str(registry_entry(alien, "corpus")))
        check("другому каталогу состояния" in out,
              "причина названа в ответе", out.strip()[-400:] if out else "нет вывода")
        # Отказ от сверки — это ОГРАНИЧЕНИЕ, а не «stale»: индекс может быть свежим,
        # его просто нельзя проверить в чужом каталоге состояния.
        check("freshness_limitation" in out,
              "невозможность сверки названа полем, а не выдана за stale",
              out.strip()[-300:])
        lock_file = alien / "locks" / "corpus.lock"
        check(not lock_file.exists(),
              "замок корпуса снят после отказа", str(lock_file))

        print("\n=== 3. Тот же корпус в своём каталоге состояния ===")
        code, out = run(own, cfg_path, "query", "freshness")
        new_revs = graph_revs(root)
        check(len(new_revs) == len(before) + 1,
              "корпус пересобран (ревизия добавилась)", f"{before} → {new_revs}")
        entry = registry_entry(own, "corpus")
        check(entry.get("rev_id") == (new_revs[-1] if new_revs else ""),
              "реестр своего каталога совпал с новой ревизией",
              f"реестр {entry.get('rev_id')} / граф {new_revs}")
        triggers = [r.get("trigger") for r in (entry.get("runs") or [])]
        check("guard:fast" in triggers or "guard:full" in triggers,
              "прогон сторожа записан с Trigger сторожа", str(triggers[:3]))

        print("\n=== 4. Запись, не подтверждённая чтением, — отказ ===")
        silent = base / "silent-catalog.json"
        silent.write_text(json.dumps({"version": 1, "entries": {}}), encoding="utf-8")

        class Dropping(catalog_mod.Catalog):
            """Реестр, который делает вид, что записал: файл не меняется."""

            def _save_unlocked(self) -> None:  # noqa: D401 — намеренная подмена
                return

        refused = ""
        try:
            Dropping(silent).record_run(
                "ghost", rev_id="rev-0001", corpus_hash="x", config_hash="y",
                file_count=1, node_count=2, edge_count=3, trigger="test:dropped")
        except Exception as exc:  # noqa: BLE001 — ожидаемый отказ
            refused = f"{type(exc).__name__}: {exc}"
        check("не подтвердилась" in refused,
              "потерянная запись объявлена отказом", refused or "исключения не было")

    print("\n=== ИТОГ ===")
    print(f"пройдено: {PASS}, провалено: {FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
