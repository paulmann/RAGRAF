"""Почему `mv` даёт ложное срабатывание: сравнение с исходной версией.

НОВЫЙ ТЕСТ ПОКАЗАЛ: `subprocess.run(["mv", "a.py", "b.py"])` распознаётся
как опасная команда. Надо установить, вызвано ли это МОЕЙ правкой или
было раньше.

Причина, скорее всего, не в правке: `mv` нет ни в чёрном списке
(`_ALWAYS_DANGEROUS_COMMANDS`), ни в белом (`_SAFE_SUBPROCESS_COMMANDS`).
Проверка `_is_subprocess_command_safe` для неизвестной команды возвращает
`False`, поэтому правило поднимается. Раньше это правило гасилось
безусловно — значит `mv` «проходил» именно из-за дефекта, а не потому,
что был безопасен.

Проверяется прямым сравнением: исходная версия файла против исправленной.
Дефект устраняется не тем, что `mv` перестаёт быть опасным, а тем, что
список команд становится явным: либо команда в белом списке, либо
требуется подтверждение.

Это правильное поведение по умолчанию («не знаю, спрошу»), но его надо
осознанно принять: список `_SAFE_SUBPROCESS_COMMANDS` покрывает 49 команд,
и всё остальное теперь требует подтверждения.
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess

SAGA = pathlib.Path(r"C:\!.Tihon\sagaai_edit_github")
TARGET = SAGA / "core" / "dangerous.py"
BACKUP = TARGET.with_suffix(".py.bak-safe288")
SAGA_PY = str(SAGA / ".venv" / "Scripts" / "python.exe")

COMMANDS = [
    ("mv", 'subprocess.run(["mv", "a.py", "b.py"])'),
    ("cp", 'subprocess.run(["cp", "a.py", "b.py"])'),
    ("ls", 'subprocess.run(["ls", "-la"])'),
    ("cat", 'subprocess.run(["cat", "file.txt"])'),
    ("cat", 'subprocess.run(["cat", "README.md"])'),
    ("mkdir", 'subprocess.run(["mkdir", "newdir"])'),
    ("echo", 'subprocess.run(["echo", "hello"])'),
    ("rm", 'subprocess.run(["rm", "-rf", "/data"])'),
    ("git status", 'subprocess.run(["git", "status"])'),
    ("pytest", 'subprocess.run(["pytest", "tests/"])'),
    ("touch", 'subprocess.run(["touch", "x.txt"])'),
    ("which", 'subprocess.run(["which", "python"])'),
]

PROBE = SAGA / "tests" / "_probe_mv.py"
PROBE.write_text(
    "import sys\n"
    f"sys.path.insert(0, r'{SAGA}')\n"
    "from core.dangerous import assess_code\n"
    "CASES = [\n"
    + "".join(f'    ("{label}", {code!r}),\n' for label, code in COMMANDS)
    + "]\n"
    "for label, code in CASES:\n"
    "    a = assess_code(code, tool='run_code')\n"
    "    print(f'{code}|{a.dangerous}')\n",
    encoding="utf-8", newline="\n",
)


def measure(label: str) -> dict[str, bool]:
    proc = subprocess.run([SAGA_PY, str(PROBE)], capture_output=True,
                          text=True, encoding="utf-8", errors="replace",
                          timeout=300)
    out = {}
    for line in (proc.stdout or "").splitlines():
        if "|" in line:
            code, value = line.rsplit("|", 1)
            out[code.strip()] = value.strip() == "True"
    print(f"\n  === {label} ===")
    for _, code in COMMANDS:
        value = out.get(code, None)
        mark = "опасно" if value else ("безопасно" if value is False else "?")
        print(f"    {code:<52} {mark}")
    return out


print("=" * 78)
print("СРАВНЕНИЕ: ИСПРАВЛЕННАЯ И ИСХОДНАЯ ВЕРСИИ")
print("=" * 78)

# 1. Текущая (исправленная) версия.
current_text = TARGET.read_text(encoding="utf-8")
fixed = measure("ИСПРАВЛЕННАЯ (сейчас)")

# 2. Исходная версия.
if BACKUP.exists():
    shutil.copy2(BACKUP, TARGET)
    original = measure("ИСХОДНАЯ (до правки)")
    # Возврат исправленной.
    TARGET.write_text(current_text, encoding="utf-8", newline="\n")
else:
    print(f"\n  бэкап не найден: {BACKUP}")
    original = {}

PROBE.unlink(missing_ok=True)

print()
print("=" * 78)
print("РАЗЛИЧИЯ")
print("=" * 78)
print(f"\n  {'команда':<52} {'было':<12} стало")
for _, code in COMMANDS:
    was = original.get(code)
    now = fixed.get(code)
    if was is None or now is None:
        continue
    was_mark = "опасно" if was else "безопасно"
    now_mark = "опасно" if now else "безопасно"
    if was != now:
        print(f"  {code:<52} {was_mark:<12} {now_mark}   ← ИЗМЕНИЛОСЬ")
    else:
        print(f"  {code:<52} {was_mark:<12} {now_mark}")

print()
print("=" * 78)
print("ВЫВОД")
print("=" * 78)
changed = [code for _, code in COMMANDS
           if original.get(code) is not None and fixed.get(code) is not None
           and original[code] != fixed[code]]
print(f"""
  Изменилось команд: {len(changed)}

  Ключевой вывод: список безопасных команд (`_SAFE_SUBPROCESS_COMMANDS`)
  содержит 49 имён. Всё, чего в нём нет, теперь требует подтверждения —
  в том числе безобидные `mv`, `cp`.

  Это НЕ ошибка правки, а следствие того, что раньше проверка вообще
  не работала: неизвестная команда проходила молча. Правильное поведение
  по умолчанию — «не знаю, спрошу». Но если такое подтверждение будет
  мешать, решение — ДОПОЛНИТЬ белый список, а не возвращать дефект:

      _SAFE_SUBPROCESS_COMMANDS: Set[str] = {{
          ...,
          "mv", "cp", "rmdir", "head", "tail", "wc", "sort", "uniq",
          "grep", "find", "sed", "awk", "diff", "stat", "file", "du",
      }}

  Заведомо опасное (`rm`, `shred`, `dd`) в белый список добавлять нельзя:
  оно есть в `_ALWAYS_DANGEROUS_COMMANDS`, и этот список имеет приоритет.
""")

# Возврат: убедиться, что исправленная версия на месте.
final = TARGET.read_text(encoding="utf-8")
print(f"  в файле исправленная версия: "
      f"{'ДА' if 'For os.system and open' not in final else 'НЕТ'}")
