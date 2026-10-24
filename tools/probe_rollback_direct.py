"""Прямая проверка rollback: откуда берётся путь с обратными слешами.

Правка внесена (строка 518: `PurePosixPath(file_rel).as_posix()`), но тест
по-прежнему получает `d\\1.txt`. Значит либо:
  1. тест импортирует ДРУГУЮ копию модуля (есть два файла);
  2. `restored` наполняется ещё где-то;
  3. правка не действует по иной причине.

Проверяется воспроизведением сценария теста вручную, с печатью каждого шага.
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import shutil
import sys
import tempfile

SAGA = pathlib.Path(r"C:\!.Tihon\sagaai_edit_github")
sys.path.insert(0, str(SAGA))

from core import updater_apply as ua  # noqa: E402

print("=" * 78)
print("1. КАКОЙ МОДУЛЬ ЗАГРУЖЕН")
print("=" * 78)
print(f"  файл модуля: {ua.__file__}")
print(f"  есть ли PurePosixPath: {hasattr(ua, 'PurePosixPath')}")
src = pathlib.Path(ua.__file__).read_text(encoding="utf-8", errors="replace")
print(f"  правка в файле: {'PurePosixPath(file_rel)' in src}")

# Проверяется, нет ли второй копии модуля.
copies = list(SAGA.rglob("updater_apply.py"))
print(f"\n  копий updater_apply.py в проекте: {len(copies)}")
for c in copies:
    if ".venv" in c.parts or ".dev_agent" in c.parts:
        continue
    print(f"    {c.relative_to(SAGA)}")

print()
print("=" * 78)
print("2. ВОСПРОИЗВЕДЕНИЕ СЦЕНАРИЯ ТЕСТА")
print("=" * 78)
tmp = pathlib.Path(tempfile.mkdtemp(prefix="rollback_probe_"))
root = str(tmp)

# Готовится тот же сценарий: файл d/1.txt есть, d/2.txt новый.
os.makedirs(os.path.join(root, "d"), exist_ok=True)
with open(os.path.join(root, "d", "1.txt"), "w", encoding="utf-8") as f:
    f.write("old1")

c1, c2 = "new1", "new2"


def _sha(data: str) -> str:
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


# Staged-файлы пишутся двоично (как реальный механизм).
for rel, content in (("d/1.txt", c1), ("d/2.txt", c2)):
    p = os.path.join(ua.pending_dir(root), rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as f:
        f.write(content.encode("utf-8"))

manifest = {"files": {
    "d/1.txt": {"version": "2.0.0", "sha256": _sha(c1)},
    "d/2.txt": {"version": "2.0.0", "sha256": _sha(c2)},
}}
with open(os.path.join(ua.updates_dir(root), "pending.json"), "w",
          encoding="utf-8") as f:
    json.dump(manifest, f)

report = ua.apply_pending(root)
print(f"  apply_pending: ok={report['ok']} applied={report['applied']}")
if not report["ok"]:
    print(f"    error: {report.get('error')}")

# Смотрится, что записано в meta бэкапа.
backup_dir = os.path.join(ua.updates_dir(root), "backup")
runs = os.listdir(backup_dir) if os.path.isdir(backup_dir) else []
print(f"\n  запусков отката: {len(runs)}")
for run in runs:
    meta_path = os.path.join(backup_dir, run, ".run-meta.json")
    if os.path.isfile(meta_path):
        with open(meta_path, encoding="utf-8") as f:
            meta = json.load(f)
        print(f"    {run}/.run-meta.json: {meta}")
    # Что реально лежит в бэкапе.
    for dirpath, _dn, fns in os.walk(os.path.join(backup_dir, run)):
        for name in fns:
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, os.path.join(backup_dir, run))
            print(f"      файл бэкапа: {rel!r}")
            print(f"        POSIX-форма: "
                  f"{pathlib.PurePosixPath(rel).as_posix()!r}")

rb = ua.rollback(root)
print(f"\n  rollback: ok={rb['ok']}")
print(f"    restored: {rb['restored']!r}")
print(f"    error: {rb.get('error')}")

print()
print("=" * 78)
print("3. ПОЧЕМУ ПУТЬ ТАКОЙ")
print("=" * 78)
for item in rb["restored"]:
    is_posix = "\\" not in item
    print(f"  {item!r}: {'POSIX (верно)' if is_posix else 'ОБРАТНЫЕ СЛЕШИ'}")

shutil.rmtree(tmp, ignore_errors=True)
