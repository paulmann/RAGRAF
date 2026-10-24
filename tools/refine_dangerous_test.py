"""Уточнение теста: разделение «ложных» и «осознанных» срабатываний.

ЧТО ПОКАЗАЛ ЗАМЕР. Правка строки 288 меняет поведение для 3 команд:

    rm -rf списком   безопасно → ОПАСНО   (закрыта дыра, верно)
    mv  списком      безопасно → ОПАСНО   (побочный эффект)
    cp  списком      безопасно → ОПАСНО   (побочный эффект)

ПРИЧИНА. `mv` и `cp` отсутствуют и в чёрном списке (`_ALWAYS_DANGEROUS_COMMANDS`,
49 имён), и в белом (`_SAFE_SUBPROCESS_COMMANDS`, 49 имён). Для неизвестной
команды `_is_subprocess_command_safe` возвращает `False`, правило поднимается,
и требуется подтверждение.

Раньше такие команды проходили МОЛЧА — то есть «безопасными» они были
не по решению, а из-за дефекта.

ЭТО ОСОЗНАННОЕ ПОВЕДЕНИЕ, А НЕ ЛОЖНОЕ СРАБАТЫВАНИЕ. Различить опасное
`mv` от безобидного по ИМЕНИ команды нельзя: `mv a.py b.py` безвредно,
`mv /etc/passwd /dev/null` — нет. Пока аргументы не анализируются,
безопасный выбор по умолчанию — спросить.

ЧТО МЕНЯЕТСЯ В ТЕСТЕ. Проверка «безвредные команды не помечаются» сужается
до команд ИЗ БЕЛОГО СПИСКА (`ls`, `cat`, `mkdir`, `echo`, `git status`).
Отдельная проверка фиксирует поведение для команд вне списка: они требуют
подтверждения — с пояснением, что это не дефект.

Если такие подтверждения будут мешать в работе, решение — ДОПОЛНИТЬ белый
список безопасными командами, а не возвращать дефект.
"""

from __future__ import annotations

import ast
import pathlib

SAGA = pathlib.Path(r"C:\!.Tihon\sagaai_edit_github")
TARGET = SAGA / "tests" / "test_dangerous_list_form.py"
text = TARGET.read_text(encoding="utf-8")

OLD_HARMLESS = '''# Commands that are harmless and must NOT be flagged, so the fix does not
# bury the user in confirmations.
HARMLESS_LIST_FORM = [
    ("pytest", 'subprocess.run(["pytest", "tests/"])'),
    ("python script", 'subprocess.run(["python", "script.py"])'),
    ("node test", 'subprocess.run(["node", "test.js"])'),
    ("git status", 'subprocess.run(["git", "status"])'),
    ("mv inside project", 'subprocess.run(["mv", "a.py", "b.py"])'),
    ("make build", 'subprocess.run(["make", "build"])'),
]'''

NEW_HARMLESS = '''# Commands that are on the SAFE list by name and must NOT be flagged, so the
# fix does not bury the user in confirmations for ordinary work.
#
# Every entry here must be present in `core.dangerous._SAFE_SUBPROCESS_COMMANDS`.
# Adding a command to this test without adding it there would make the test
# fail - which is the point: the test documents the actual whitelist.
HARMLESS_LIST_FORM = [
    ("pytest", 'subprocess.run(["pytest", "tests/"])'),
    ("python script", 'subprocess.run(["python", "script.py"])'),
    ("node test", 'subprocess.run(["node", "test.js"])'),
    ("git status", 'subprocess.run(["git", "status"])'),
    ("make build", 'subprocess.run(["make", "build"])'),
    ("ls listing", 'subprocess.run(["ls", "-la"])'),
    ("cat file", 'subprocess.run(["cat", "README.md"])'),
    ("mkdir", 'subprocess.run(["mkdir", "newdir"])'),
    ("echo", 'subprocess.run(["echo", "hello"])'),
]

# Commands that are NOT on the safe list. After the fix they require
# confirmation - by DESIGN, not by accident.
#
# This is deliberate: the command name alone cannot tell a harmless
# `mv a.py b.py` from a destructive `mv /etc/passwd /dev/null`. Until the
# arguments are analysed, the safe default is to ask. Before the fix these
# passed SILENTLY, but only because the whole check did not work.
CONFIRMATION_BY_DESIGN = [
    ("mv", 'subprocess.run(["mv", "a.py", "b.py"])'),
    ("cp", 'subprocess.run(["cp", "a.py", "b.py"])'),
]

# Commands that must STAY silent even though they are absent from the safe
# list: reading directory listings and file contents has no side effect.
# If these start requiring confirmation, the whitelist needs extending.
MUST_STAY_SILENT = [
    ("head", 'subprocess.run(["head", "-n", "20", "file.txt"])'),
    ("tail", 'subprocess.run(["tail", "-n", "20", "file.txt"])'),
    ("wc", 'subprocess.run(["wc", "-l", "file.txt"])'),
    ("diff", 'subprocess.run(["diff", "a.txt", "b.txt"])'),
    ("stat", 'subprocess.run(["stat", "file.txt"])'),
]'''

if OLD_HARMLESS not in text:
    raise SystemExit("НЕ НАЙДЕН блок HARMLESS_LIST_FORM")
text = text.replace(OLD_HARMLESS, NEW_HARMLESS, 1)

# Добавляются две новые проверки.
OLD_TEST_TAIL = '''def test_ordinary_code_is_not_flagged():'''

NEW_TEST_TAIL = '''def test_commands_outside_whitelist_require_confirmation():
    """Commands absent from the safe list must ask for confirmation.

    This pins the POST-FIX behaviour on purpose. `mv` and `cp` were silent
    before only because the danger check did not work at all; now they ask.
    That is the safe default: by name alone a harmless `mv a.py b.py` cannot
    be told apart from `mv /etc/passwd /dev/null`.

    If these confirmations become annoying, extend
    `core.dangerous._SAFE_SUBPROCESS_COMMANDS` - do not restore the bug.
    """
    silent = []
    for label, code in CONFIRMATION_BY_DESIGN:
        if not assess_code(code, tool="run_code").dangerous:
            silent.append((label, code))
    assert not silent, (
        "commands outside the whitelist passed without confirmation, which "
        "means the suppression bug is back:\\n"
        + "\\n".join(f"  {label}: {code}" for label, code in silent)
    )


def test_read_only_commands_should_stay_silent():
    """Read-only commands have no side effect and should not ask.

    These are NOT on the safe list today. The test records the expectation:
    if a confirmation dialog appears for reading a file, that is noise, and
    the whitelist should be extended with the command.

    Fails loudly with the exact list to add, so the fix is a one-liner.
    """
    flagged = []
    for label, code in MUST_STAY_SILENT:
        if assess_code(code, tool="run_code").dangerous:
            flagged.append((label, code))
    assert not flagged, (
        "read-only commands require confirmation; add them to "
        "core.dangerous._SAFE_SUBPROCESS_COMMANDS:\\n"
        + "\\n".join(f"  {label}: {code}" for label, code in flagged)
    )


def test_ordinary_code_is_not_flagged():'''

if OLD_TEST_TAIL not in text:
    raise SystemExit("НЕ НАЙДЕНА точка вставки новых тестов")
text = text.replace(OLD_TEST_TAIL, NEW_TEST_TAIL, 1)

TARGET.write_text(text, encoding="utf-8", newline="\n")
after = TARGET.read_text(encoding="utf-8")

print("  тест обновлён")
print(f"  размер: {len(after.encode('utf-8'))} б")

try:
    ast.parse(after)
    print("  синтаксис верен")
except SyntaxError as exc:
    print(f"  ПРОВАЛ синтаксиса: строка {exc.lineno}: {exc.msg}")
    raise SystemExit(1)

import re  # noqa: E402

functions = re.findall(r"^def (test_\w+)", after, re.MULTILINE)
print(f"\n  тестовых функций: {len(functions)}")
for name in functions:
    print(f"    {name}")
