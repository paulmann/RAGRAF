"""Проверка: возврат файла снимает предупреждение о расхождении.

Последовательность как у человека: изменить файл → `drift` (предупреждение
появилось) → вернуть файл байт-в-байт → `drift` снова → предупреждения нет,
а история журнала не потеряна.
"""
from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import uuid

REPO = pathlib.Path(__file__).resolve().parents[1]
PY = sys.executable
TOOL = str(REPO / "tools" / "ragraf.py")

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


def run(args: list[str], env: dict) -> tuple[int, str]:
    merged = dict(os.environ)
    merged.update(env)
    done = subprocess.run([PY, TOOL, *args], cwd=str(REPO), capture_output=True,
                          text=True, encoding="utf-8", errors="replace", env=merged)
    return done.returncode, (done.stdout or "") + (done.stderr or "")


def payload(text: str) -> dict:
    start = text.find("{")
    while start != -1:
        try:
            return json.loads(text[start:])
        except json.JSONDecodeError:
            start = text.find("{", start + 1)
    return {}


WORK = pathlib.Path(tempfile.mkdtemp(prefix="ragraf-restore-"))
created: pathlib.Path | None = None
STATE = WORK / "state"
CORPUS = WORK / ("corpus-" + uuid.uuid4().hex[:8])
ENV = {"RAGRAF_CATALOG_DIR": str(STATE)}
TARGET = CORPUS / "widget.ts"
ORIGINAL = "export const box = () => 1;\nexport const lid = () => 2;\n"

try:
    CORPUS.mkdir(parents=True)
    TARGET.write_text(ORIGINAL, encoding="utf-8")
    (CORPUS / "main.ts").write_text(
        'import { box } from "./widget";\nexport const use = () => box();\n',
        encoding="utf-8")
    (CORPUS / "extra.ts").write_text(
        "export const idle = () => 0;\n", encoding="utf-8")

    before = {p.name for p in (REPO / "config").glob("*.yaml")}
    code, out = run(["init", str(CORPUS)], ENV)
    check(code == 0, "корпус создан", out[-200:])
    fresh = sorted(p for p in (REPO / "config").glob("*.yaml") if p.name not in before)
    check(len(fresh) == 1, "конфиг корпуса создан", str([p.name for p in fresh]))
    config = fresh[0]
    created = config
    code, out = run(["-p", str(config), "index", "--no-vectors"], ENV)
    check(code == 0, "корпус проиндексирован", out[-200:])

    TARGET.write_text(ORIGINAL.replace("=> 1", "=> 42"), encoding="utf-8")
    code, out = run(["--json", "-p", str(config), "drift"], ENV)
    data = payload(out)
    entries = data.get("entries") or []
    kinds = [str(e.get("kind")) for e in entries]
    check(code == 0 and "modify" in kinds,
          "первый drift: изменение замечено", out[-200:])

    TARGET.write_text(ORIGINAL, encoding="utf-8")
    code, out = run(["--json", "-p", str(config), "drift"], ENV)
    data = payload(out)
    kinds = [str(e.get("kind")) for e in (data.get("entries") or [])]
    check(code == 0 and "restored" in kinds,
          "второй drift: возврат замечен", out[-200:])

    code, out = run(["--json", "-p", str(config), "query", "search", "box"], ENV)
    data = payload(out)
    warned = data.get("stale_warnings") or []
    check(not warned, "после возврата предупреждения нет", json.dumps(warned)[:200])
    drift = data.get("drift") or {}
    check(int(drift.get("files") or 0) == 0,
          "сводка расхождений пуста", str(drift))

    # История остаётся: запись об изменении и запись о возврате.
    import sqlite3
    conn = sqlite3.connect(f"file:{(CORPUS / '.ragraf' / 'control.sqlite').as_posix()}?mode=ro",
                           uri=True)
    kinds = [r[0] for r in conn.execute(
        "SELECT kind FROM drift_entry WHERE path = 'widget.ts' ORDER BY id")]
    conn.close()
    check("modify" in kinds and "restored" in kinds,
          "журнал сохранил обе записи (append-only)", str(kinds))
except Exception as exc:  # noqa: BLE001 — проверка обязана назвать падение
    import traceback
    FAIL += 1
    print(f"  [FAIL] проверка упала: {type(exc).__name__}: {exc}")
    traceback.print_exc()
finally:
    # Убирается РОВНО созданный прогоном конфиг: в `config/` этой машины лежат
    # конфиги всех корпусов, и уборка «всё, кроме белого списка» стёрла их
    # 22.11.2026 (57 файлов). Имя берётся из `before`/`fresh`, а не из каталога.
    if created is not None:
        created.unlink(missing_ok=True)
    shutil.rmtree(WORK, ignore_errors=True)
    print(f"ИТОГ: пройдено {PASS}, провалено {FAIL}")
    raise SystemExit(0 if FAIL == 0 and PASS > 0 else 1)
