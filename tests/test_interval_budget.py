"""Пересбор по интервалу не блокирует ответ, когда он не влезает в бюджет.

Стиль как у остальных проверок проекта: самостоятельный скрипт с функцией
`check`, запускается напрямую (`python tests/test_interval_budget.py`).

Какой дефект закрывается. Жалоба HARNESS 05.11.2026 звучала как «RAGRAF MCP
недоступен всю сессию: ensure/index_status — таймауты 10+ минут». Замер
06.11.2026 на собственном корпусе объясняет механизм целиком:

  * полный прогон — 558 с, быстрый — 129 с, а бюджет одного прогона
    по умолчанию — 120 с;
  * значит прогон, запущенный внутри обращения, не мог завершиться НИ РАЗУ:
    бюджет исчерпывался, ревизия не закреплялась, в истории копились записи
    `building` с нулями;
  * следующее обращение снова видело «индекс старше предела» — и снова
    тратило минуты. Круг замкнут: ответа не было никогда.

Правило, которое проверяется: если измеренная стоимость пересбора больше
бюджета, сторож НЕ запускает его внутри обращения. Он отвечает по действующей
ревизии с честной пометкой «устарел» и названной причиной; обновление остаётся
отдельным осознанным действием (`reindex`).
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ragraf import catalog as catalog_mod  # noqa: E402
from ragraf import config as config_mod  # noqa: E402
from ragraf import store as store_mod  # noqa: E402
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


def make_corpus(root: Path, *, budget: float) -> object:
    root.mkdir(parents=True, exist_ok=True)
    (root / "main.py").write_text("x = 1\n", encoding="utf-8", newline="\n")
    cfg_path = root / "ragraf.yaml"
    cfg_path.write_text(
        f"""project: {root.name}
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
  budget_seconds: {budget}
  reindex_interval_minutes: 1
""",
        encoding="utf-8",
        newline="\n",
    )
    return config_mod.load(cfg_path)


def seal_old_revision(cfg, rev_id: str, minutes_old: int) -> None:
    """Ревизия с ПРОШЛОЙ датой: иначе предел интервала не достигается."""
    created = (datetime.now(timezone.utc) - timedelta(minutes=minutes_old))
    conn = store_mod.connect(cfg.graph_db)
    store_mod.init_graph(conn)
    conn.execute(
        "INSERT INTO rev (rev_id, created_at, state, capability, file_count,"
        " node_count, edge_count) VALUES (?,?,?,?,?,?,?)",
        (rev_id, created.isoformat(timespec="seconds"), "ready", "full", 1, 1, 0),
    )
    conn.commit()
    conn.close()


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        state = base / "state"
        state.mkdir()
        (state / "catalog.json").write_text(
            json.dumps({"version": 1, "entries": {}}), encoding="utf-8")
        os.environ["RAGRAF_CATALOG_DIR"] = str(state)

        print("=== 1. Стоимость больше бюджета → пересбор не запускается ===")
        root = base / "corpus"
        cfg = make_corpus(root, budget=60)
        seal_old_revision(cfg, "rev-0001", minutes_old=600)
        # Замер 500 с в журнале прогонов: столько стоит полный пересбор.
        cat = catalog_mod.Catalog()
        cat.register("corpus", cfg, cfg.config_path)
        cat.record_run("corpus", rev_id="rev-0001", corpus_hash="h", config_hash="c",
                       seconds=500.0, capability="full", trigger="cli:index")

        mode, info = watch_mod.choose_mode(cfg, "rev-0001", changed=True)
        check(mode == "none", "режим — none (пересбор не влезает в бюджет)",
              f"mode={mode}, reason={info.get('reason')}")
        check(info.get("skip_reason") == "cost_exceeds_budget",
              "причина названа машинно", str(info.get("skip_reason")))
        check("бюджет" in str(info.get("reason", "")),
              "в причине есть бюджет и стоимость", str(info.get("reason")))
        check(info.get("interval_minutes") is True,
              "достижение предела интервала при этом видно", str(info.get("interval_minutes")))

        print("\n=== 2. Ответ сторожа не блокируется, но и не врёт ===")
        fresh = watch_mod.check(cfg, "corpus", auto_reindex=True, force=True,
                                use_cache=False)
        check(fresh.stale is True, "состояние помечено устаревшим", str(fresh.stale))
        check("бюджет" in str(fresh.note or ""),
              "причина в ответе названа", str(fresh.note))
        check(fresh.reindexed is False and fresh.reindex_rev == "",
              "пересбор внутри проверки не запускался",
              f"reindexed={fresh.reindexed} rev={fresh.reindex_rev}")
        # Действующая ревизия остаётся доступной: ответ по ней, а не отказ.
        check(fresh.rev_id == "rev-0001", "ответ ссылается на действующую ревизию",
              str(fresh.rev_id))

        print("\n=== 3. Бюджет увеличен → пересбор разрешён ===")
        root2 = base / "corpus2"
        cfg2 = make_corpus(root2, budget=1000)
        seal_old_revision(cfg2, "rev-0001", minutes_old=600)
        cat2 = catalog_mod.Catalog()
        cat2.register("corpus2", cfg2, cfg2.config_path)
        cat2.record_run("corpus2", rev_id="rev-0001", corpus_hash="h", config_hash="c",
                        seconds=500.0, capability="full", trigger="cli:index")
        mode2, info2 = watch_mod.choose_mode(cfg2, "rev-0001", changed=True)
        check(mode2 == "full", "при большом бюджете режим — full",
              f"mode={mode2}, reason={info2.get('reason')}")
        check(not info2.get("skip_reason"),
              "пропуск не объявлен", str(info2.get("skip_reason")))

        os.environ.pop("RAGRAF_CATALOG_DIR", None)

    print("\n=== ИТОГ ===")
    print(f"пройдено: {PASS}, провалено: {FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
