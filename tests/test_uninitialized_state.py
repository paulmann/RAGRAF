"""Проверка: первый запуск на корпусе без состояния не выносит OperationalError.

Измеренный дефект (23.11.2026, 192.168.31.31): `check`/`reindex` на каталоге, где
`.ragraf` ещё не создан, пытались открыть базу в несуществующем каталоге и печатали
`OperationalError: ...control.sqlite не существует` внутри отчёта.

Свойство: первый запуск — не ошибка. Команда либо создаёт состояние и работает,
либо отказывает НАЗВАННОЙ причиной; текст исключения наружу не выходит. Проверка
формулируется через свойство и код, а не через буквальную строку.
"""
from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
CLI = ROOT / "tools" / "ragraf.py"

passed = 0
failed = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global passed, failed
    if condition:
        passed += 1
        print(f"  [OK ] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}{': ' + detail if detail else ''}")


def run(cfg: pathlib.Path, *args: str, timeout: int = 600) -> subprocess.CompletedProcess:
    env = dict(os.environ, PYTHONPATH=str(ROOT / "src"), PYTHONIOENCODING="utf-8")
    return subprocess.run([sys.executable, str(CLI), "-p", str(cfg), *args],
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", env=env, timeout=timeout)


with tempfile.TemporaryDirectory(prefix="ragraf-firstrun-") as tmp:
    root = pathlib.Path(tmp) / "corpus"
    root.mkdir()
    (root / ".gitignore").write_text(".ragraf/\n", encoding="utf-8")
    for name in ("main", "util", "model"):
        (root / f"{name}.py").write_text(f"def {name}():\n    return 1\n", encoding="utf-8")
    cfg = pathlib.Path(tmp) / "corpus.yaml"
    cfg.write_text(
        f"project: firstrun\nroot: {root.as_posix()}\nindex:\n  vectors: false\ndb:\n  enabled: false\n",
        encoding="utf-8")

    # 1. Состояния нет вовсе: каталог .ragraf не создан.
    check("до прогона .ragraf нет", not (root / ".ragraf").exists())

    result = run(cfg, "reindex")
    output = (result.stdout or "") + (result.stderr or "")
    check("reindex на свежем корпусе: код 0", result.returncode == 0,
          f"код {result.returncode}, {output.strip()[-160:]}")
    check("reindex: OperationalError не выносится", "OperationalError" not in output,
          output.strip()[-160:])
    check("reindex: состояние создано", (root / ".ragraf" / "control.sqlite").exists())

    # 2. Повторный check работает как обычно.
    result = run(cfg, "check")
    output = (result.stdout or "") + (result.stderr or "")
    check("check после reindex: код 0", result.returncode == 0,
          f"код {result.returncode}, {output.strip()[-160:]}")
    check("check: индексация не требуется", "не требуется" in output, output.strip()[-160:])

    # 3. Свойство кода 3 живёт там, где оно уместно: недоступный каталог состояния.
    #    Подменяем .ragraf файлом — каталог, в который нельзя писать.
    (root / ".ragraf" / "control.sqlite").unlink()
    import shutil
    shutil.rmtree(root / ".ragraf")
    (root / ".ragraf").write_text("занято файлом\n", encoding="utf-8")
    result = run(cfg, "reindex")
    output = (result.stdout or "") + (result.stderr or "")
    check("reindex на занятом пути: код 3", result.returncode == 3,
          f"код {result.returncode}, {output.strip()[-160:]}")
    check("reindex: причина названа", "занят" in output or "недоступ" in output,
          output.strip()[-160:])

print()
print(f"ИТОГ: пройдено {passed}, провалено {failed}")
raise SystemExit(0 if failed == 0 else 1)
