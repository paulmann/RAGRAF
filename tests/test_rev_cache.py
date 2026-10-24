"""Проверка дефекта устаревшей ревизии и его исправления (§3.2/§3.4, шаг 0).

Измеренный дефект: `rev_info()` читал файл ревизии один раз и кэшировал
навсегда, а сбрасывался кэш только при ПОЛНОЙ переиндексации. Быстрый путь по
отпечатку входа возвращался раньше, поэтому долгоживущий процесс (MCP-сервер)
после переиндексации ДРУГИМ процессом продолжал отвечать прошлым состоянием.
Замер: фасад видел `rev-0120`, когда на диске был `rev-0121`; если данные старой
ревизии успевали удалиться ротацией, ответы становились пустыми, а
`index_status` объявлял `healthy: true`.

Проверяется:
  1. фасад видит ревизию, созданную ДРУГИМ процессом;
  2. пустая (ротированная) ревизия называется сломанной, а не здоровой;
  3. ответ по неактивной ревизии считается `stale` в телеметрии.

Стиль — самостоятельный скрипт с `check`, как остальные проверки проекта.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf.config import load as load_config  # noqa: E402
from ragraf.facade import Facade  # noqa: E402
from ragraf.indexer import Indexer  # noqa: E402
from ragraf.init_corpus import create_config  # noqa: E402
from ragraf.revisions import ControlStore  # noqa: E402
from ragraf.telemetry import Telemetry  # noqa: E402

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


PHP = """<?php
function probe_%(i)d($x) {
    return $x + %(i)d;
}
"""


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="ragraf_revcache_"))
    try:
        src = tmp / "src"
        src.mkdir()
        for i in range(6):
            (src / f"mod{i}.php").write_text(PHP % {"i": i}, encoding="utf-8",
                                             newline="\n")
        (tmp / ".gitignore").write_text("probe\n", encoding="utf-8", newline="\n")
        cfg_path, _scan = create_config(tmp, project="revprobe",
                                         config_path=tmp / "rev.yaml")
        text = cfg_path.read_text(encoding="utf-8").replace(
            "joern_enabled: true", "joern_enabled: false")
        cfg_path.write_text(text, encoding="utf-8", newline="\n")
        cfg = load_config(cfg_path)

        first = Indexer(cfg)
        try:
            report = first.run(with_vectors=False, budget_seconds=300)
        finally:
            first.close()
        check(report.ok, "корпус проиндексирован впервые", str(report.notes))
        rev_before = str(report.rev_id)

        print("\n=== 1. Другой процесс пересобрал корпус ===")
        # Фасад создаётся ДО пересборки — как долгоживущий MCP-сервер.
        facade = Facade(cfg, tool="selftest", telemetry=False, watch=False)
        check(facade.current_rev() == rev_before,
              "фасад видит первую ревизию", str(facade.current_rev()))

        (src / "mod0.php").write_text(
            PHP % {"i": 100} + "// правка\n", encoding="utf-8", newline="\n")
        second = Indexer(cfg)
        try:
            report2 = second.run(with_vectors=False, budget_seconds=300)
        finally:
            second.close()
        rev_after = str(report2.rev_id)
        check(rev_after != rev_before, "пересборка дала новую ревизию",
              f"{rev_before} → {rev_after}")

        seen = facade.current_rev()
        check(seen == rev_after,
              "фасад видит ревизию, созданную ДРУГИМ процессом",
              f"видит {seen}, на диске {rev_after}")
        status = facade.op_index_status()
        check(int((status.get("counts") or {}).get("files") or 0) > 0,
              "index_status отвечает по непустой ревизии",
              str(status.get("counts")))
        facade.close()

        print("\n=== 2. Ревизия без данных — сломанная, а не здоровая ===")
        facade2 = Facade(cfg, tool="selftest", telemetry=False, watch=False)
        # Воспроизводим состояние дефекта: фасад держит ревизию, данных которой
        # в хранилищах уже нет (ротация), а файл ревизии при этом не менялся.
        stat = os.stat(cfg.rev_file)
        facade2._rev_cache = {"rev_id": "rev-0000", "capability": "full"}
        facade2._rev_stat = (stat.st_mtime_ns, stat.st_size)
        check(facade2.current_rev() == "rev-0000",
              "фасад действительно держит несуществующую ревизию")
        broken = facade2.op_index_status()
        quality = (broken.get("result") or {}).get("quality") or {}
        limits = " ".join(broken.get("limits") or [])
        check(quality.get("healthy") is False,
              "пустая ревизия объявлена сломанной", str(quality.get("healthy")))
        check("ревизия без данных" in limits,
              "причина названа словами", limits[:160])
        facade2.close()

        print("\n=== 3. Ответ по неактивной ревизии считается stale ===")
        ctrl = ControlStore.__new__(ControlStore)
        ctrl.cfg = cfg
        ctrl.conn = sqlite3.connect(str(tmp / "control.sqlite"))
        ctrl.conn.row_factory = sqlite3.Row
        tel = Telemetry(ctrl, tool="selftest", agent="Probe", task="rev-cache")
        facade3 = Facade(cfg, tool="selftest", telemetry=False, watch=False)
        facade3.telemetry = tel
        fake = {"op": "index_status", "index_rev": "rev-0000", "capability": "full",
                "result": {"rev": None}, "found": False, "stale_warnings": []}
        facade3._record("op_index_status", 5.0, fake, None)
        summary = tel.summary()
        check(summary["stale_served"] == 1,
              "ответ по неактивной ревизии учтён как stale",
              str(summary["stale_served"]))
        check(bool(fake.get("stale_warnings")),
              "в конверт добавлено предупреждение", str(fake.get("stale_warnings"))[:160])
        facade3.telemetry = None
        facade3.close()
        ctrl.conn.close()

        print("\n=== 4. Цена проверки: один stat на вызов ===")
        facade4 = Facade(cfg, tool="selftest", telemetry=False, watch=False)
        calls = {"n": 0}
        real_stat = os.stat

        def counting_stat(path, *args, **kwargs):
            calls["n"] += 1
            return real_stat(path, *args, **kwargs)

        os.stat = counting_stat  # type: ignore[assignment]
        try:
            for _ in range(20):
                facade4.current_rev()
        finally:
            os.stat = real_stat  # type: ignore[assignment]
        facade4.close()
        check(calls["n"] == 20, "на 20 вызовов — 20 stat (без лишних)",
              str(calls["n"]))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    print("=" * 78)
    print(f"ИТОГ: пройдено {PASS}, провалено {FAIL}")
    print("=" * 78)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
