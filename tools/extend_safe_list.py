"""Дополнение белого списка командами только для чтения.

ЗАЧЕМ. Правка строки 288 восстановила проверку команды для списков
аргументов. Следствие: команды, которых НЕТ ни в чёрном списке, ни в белом,
теперь требуют подтверждения. Для `head`, `tail`, `wc`, `diff`, `stat`
и подобных это лишний шум: они только читают.

Тест `tests/test_dangerous_list_form.py` это и зафиксировал — он падал
с указанием, что именно надо добавить в список.

ЧТО ДОБАВЛЯЕТСЯ — строго команды БЕЗ побочных эффектов:

  чтение файлов:  head, tail, wc, diff, stat, file, du, strings,
                  readlink, realpath, basename, dirname
  поиск:          grep, tree
  вывод:          less, more
  состояние:      ps, df

ЧТО НАМЕРЕННО НЕ ДОБАВЛЯЕТСЯ, хотя это частые команды:

  `sed`  — с ключом -i перезаписывает файлы;
  `awk`  — может писать через print > file;
  `find` — умеет -delete и -exec rm.

Они остаются с обязательным подтверждением: их опасность зависит от
аргументов, а аргументы сейчас не разбираются. Это осознанный выбор
в пользу вопроса вместо молчаливого выполнения.

Заведомо опасные команды (`rm`, `shred`, `dd`, `chmod`, `chown`) в белый
список не попадают никогда: `_ALWAYS_DANGEROUS_COMMANDS` имеет приоритет
и проверяется ДО белого списка (строка 265 в исходном файле).
"""

from __future__ import annotations

import ast
import pathlib
import shutil
import subprocess

SAGA = pathlib.Path(r"C:\!.Tihon\sagaai_edit_github")
TARGET = SAGA / "core" / "dangerous.py"
SAGA_PY = str(SAGA / ".venv" / "Scripts" / "python.exe")

READ_ONLY = [
    # чтение файлов и каталогов
    "head", "tail", "wc", "diff", "stat", "file", "du", "strings",
    "readlink", "realpath", "basename", "dirname",
    # поиск и обзор
    "grep", "tree",
    # постраничный вывод
    "less", "more",
    # состояние системы
    "ps", "df",
]

OLD = '''    "type", "test", "true", "false", "uname", "hostname",
}'''

NEW = '''    "type", "test", "true", "false", "uname", "hostname",
    # Commands with NO side effects: they only read or report. Safe to run
    # without a confirmation dialog, otherwise normal inspection work would
    # ask the user on every step.
    #
    # Deliberately NOT listed, although common: `sed` (rewrites files with
    # -i), `awk` (can write via redirection), `find` (has -delete/-exec).
    # Their safety depends on arguments, and arguments are not analysed yet,
    # so they keep requiring confirmation - asking beats assuming.
    "head", "tail", "wc", "diff", "stat", "file", "du", "strings",
    "readlink", "realpath", "basename", "dirname",
    "grep", "tree", "less", "more", "ps", "df",
}'''

text = TARGET.read_text(encoding="utf-8")
print("=" * 78)
print("СОСТОЯНИЕ ДО ПРАВКИ")
print("=" * 78)
# Проверяется, какие команды уже есть в списке.
import re  # noqa: E402

block = re.search(r"_SAFE_SUBPROCESS_COMMANDS[^}]*\}", text)
if block:
    existing = set(re.findall(r'"([\w-]+)"', block.group(0)))
    print(f"  команд в белом списке: {len(existing)}")
    missing = [c for c in READ_ONLY if c not in existing]
    present = [c for c in READ_ONLY if c in existing]
    print(f"  уже есть: {present}")
    print(f"  надо добавить: {len(missing)}")
    for i in range(0, len(missing), 6):
        print("    " + ", ".join(missing[i:i + 6]))

if OLD not in text:
    if all(f'"{c}"' in text for c in READ_ONLY):
        print("\n  правка уже внесена")
        raise SystemExit(0)
    raise SystemExit("НЕ НАЙДЕН конец белого списка")

backup = TARGET.with_suffix(".py.bak-readonly")
shutil.copy2(TARGET, backup)
print(f"\n  бэкап: {backup.name}")

text = text.replace(OLD, NEW, 1)
TARGET.write_text(text, encoding="utf-8", newline="\n")
after = TARGET.read_text(encoding="utf-8")
print("  правка внесена")

try:
    ast.parse(after)
    print("  синтаксис верен")
except SyntaxError as exc:
    print(f"  ПРОВАЛ синтаксиса: строка {exc.lineno}: {exc.msg}")
    raise SystemExit(1)

print()
print("=" * 78)
print("ПРОВЕРКА ПОСЛЕ ПРАВКИ")
print("=" * 78)
# Проверяется состав списка и поведение команд.
block = re.search(r"_SAFE_SUBPROCESS_COMMANDS[^}]*\}", after)
existing = set(re.findall(r'"([\w-]+)"', block.group(0)))
print(f"  команд в белом списке: {len(existing)}")

# Проверяется, что опасные НЕ попали в белый список.
DANGEROUS = ["rm", "shred", "dd", "mkfs", "chmod", "chown", "sudo", "kill"]
leaked = [c for c in DANGEROUS if c in existing]
print(f"  опасных команд в белом списке: {leaked or 'нет'}")
if leaked:
    print("  ПРОВАЛ: опасная команда в белом списке!")
    raise SystemExit(1)

# Проверяется поведение: чтение молчит, опасное предупреждает.
probe = SAGA / "tests" / "_probe_readonly.py"
probe.write_text(
    "import sys\n"
    f"sys.path.insert(0, r'{SAGA}')\n"
    "from core.dangerous import assess_code\n"
    "CASES = [\n"
    "    ('head', 'subprocess.run([\"head\", \"-n\", \"20\", \"f.txt\"])', False),\n"
    "    ('tail', 'subprocess.run([\"tail\", \"-n\", \"20\", \"f.txt\"])', False),\n"
    "    ('wc', 'subprocess.run([\"wc\", \"-l\", \"f.txt\"])', False),\n"
    "    ('diff', 'subprocess.run([\"diff\", \"a.txt\", \"b.txt\"])', False),\n"
    "    ('stat', 'subprocess.run([\"stat\", \"f.txt\"])', False),\n"
    "    ('grep', 'subprocess.run([\"grep\", \"-r\", \"TODO\", \"src\"])', False),\n"
    "    ('rm', 'subprocess.run([\"rm\", \"-rf\", \"/data\"])', True),\n"
    "    ('shred', 'subprocess.run([\"shred\", \"-u\", \"f\"])', True),\n"
    "    ('sudo', 'subprocess.run([\"sudo\", \"rm\", \"-rf\", \"/\"])', True),\n"
    "    ('sed (не в списке)', 'subprocess.run([\"sed\", \"-i\", \"s/a/b/\", \"f\"])', True),\n"
    "    ('find (не в списке)', 'subprocess.run([\"find\", \".\", \"-delete\"])', True),\n"
    "]\n"
    "for label, code, want in CASES:\n"
    "    got = assess_code(code, tool='run_code').dangerous\n"
    "    mark = 'верно' if got == want else 'ОШИБКА'\n"
    "    print(f'{label}|{got}|{want}|{mark}')\n",
    encoding="utf-8", newline="\n",
)
proc = subprocess.run([SAGA_PY, str(probe)], capture_output=True, text=True,
                      encoding="utf-8", errors="replace", timeout=300)
print(f"\n  {'команда':<20} {'опасно':<8} {'ожидалось':<10} результат")
ok_count = 0
for line in (proc.stdout or "").splitlines():
    parts = line.split("|")
    if len(parts) != 4:
        continue
    label, got, want, mark = parts
    print(f"  {label:<20} {got:<8} {want:<10} {mark}")
    if mark == "верно":
        ok_count += 1
probe.unlink(missing_ok=True)
print(f"\n  проверок пройдено: {ok_count}")
print(f"  файл: {TARGET.relative_to(SAGA)} "
      f"({len(after.encode('utf-8'))} б)")
