"""Проверка: почему `pip install` списком всё ещё проходит после правки.

ЗАМЕР ПОСЛЕ ПРАВКИ:
    rm -rf списком      → распознано
    curl списком        → распознано
    shred списком       → распознано
    pip install списком → ПРОПУЩЕНО (осталось)

ПРИЧИНА (по чтению кода). `pip install` — правило из ДРУГОГО списка:
не `_PYTHON_RULES`, а `_SHELL_RULES`, который проверяется в
`_check_shell_rules`. Там никаких `safe_pattern` нет вообще:

    def _check_shell_rules(code):
        reasons = []
        for pattern, rule in _SHELL_COMPILED:
            if pattern.search(code):
                reasons.append(rule.reason)
        return reasons

Значит шаблон `\\bpip\\s+install\\b` требует ПРОБЕЛЬНЫХ символов между
`pip` и `install`. В записи `["pip", "install", "x"]` между ними стоит
кавычка и запятая — шаблон не совпадает, правило не срабатывает.

Почему `rm` и `curl` всё же поймались: они есть в ОБОИХ списках —
и в `_PYTHON_RULES` (через проверку команды из `_ALWAYS_DANGEROUS_COMMANDS`),
и в `_SHELL_RULES` (по шаблону с пробелом).

То есть `_ALWAYS_DANGEROUS_COMMANDS` — это фактический «чёрный список»
для списков аргументов. Проверяется, что в нём есть, а чего нет:
`pip` там отсутствует, поэтому и проходит.

Правка `dangerous.py:288` закрыла дыру для команд из чёрного списка,
но не для «опасных подкоманд» (`pip install`, `npm install -g`,
`git push --force`), которых в чёрном списке нет по построению:
сама команда `pip` безвредна, опасна её подкоманда.

Здесь это только измеряется — ничего не меняется.
"""

from __future__ import annotations

import pathlib
import sys

SAGA = pathlib.Path(r"C:\!.Tihon\sagaai_edit_github")
sys.path.insert(0, str(SAGA))

from core import dangerous as d  # noqa: E402

print("=" * 78)
print("1. СОСТАВ СПИСКОВ")
print("=" * 78)
print(f"  _ALWAYS_DANGEROUS_COMMANDS: {len(d._ALWAYS_DANGEROUS_COMMANDS)} команд")
always = sorted(d._ALWAYS_DANGEROUS_COMMANDS)
for i in range(0, len(always), 5):
    print("    " + ", ".join(always[i:i + 5]))

print(f"\n  _SAFE_SUBPROCESS_COMMANDS: {len(d._SAFE_SUBPROCESS_COMMANDS)} команд")
safe = sorted(d._SAFE_SUBPROCESS_COMMANDS)
for i in range(0, len(safe), 5):
    print("    " + ", ".join(safe[i:i + 5]))

# Ключевая проверка: что в одном списке и нет в другом.
both = sorted(d._ALWAYS_DANGEROUS_COMMANDS & d._SAFE_SUBPROCESS_COMMANDS)
only_safe = sorted(d._SAFE_SUBPROCESS_COMMANDS - d._ALWAYS_DANGEROUS_COMMANDS)
print(f"\n  в ОБОИХ списках: {both}")
print(f"\n  только в «безопасных» ({len(only_safe)}):")
for i in range(0, len(only_safe), 5):
    print("    " + ", ".join(only_safe[i:i + 5]))

print()
print("=" * 78)
print("2. КОМАНДЫ С ОПАСНЫМИ ПОДКОМАНДАМИ")
print("=" * 78)
# Измеряется: проходит ли команда, опасная своими АРГУМЕНТАМИ.
CASES = [
    ("pip install (установка пакета)",
     'subprocess.run(["pip", "install", "evil-package"])'),
    ("npm install -g (глобальная установка)",
     'subprocess.run(["npm", "install", "-g", "evil"])'),
    ("git push --force (перезапись истории)",
     'subprocess.run(["git", "push", "--force", "origin", "main"])'),
    ("git reset --hard (потеря правок)",
     'subprocess.run(["git", "reset", "--hard", "HEAD~10"])'),
    ("docker run --privileged (доступ к хосту)",
     'subprocess.run(["docker", "run", "--privileged", "-v", "/:/host", "x"])'),
    ("kubectl delete namespace",
     'subprocess.run(["kubectl", "delete", "namespace", "prod"])'),
    ("terraform destroy",
     'subprocess.run(["terraform", "destroy", "-auto-approve"])'),
    ("conda remove",
     'subprocess.run(["conda", "remove", "-y", "numpy"])'),
]
print(f"\n  {'случай':<44} опасно")
for label, code in CASES:
    result = d.assess_code(code, tool="run_code")
    mark = "ДА" if result.dangerous else "пропущено"
    print(f"  {label:<44} {mark}")

print()
print("=" * 78)
print("3. ЧТО ИМЕННО ОСТАЛОСЬ НЕЗАКРЫТЫМ")
print("=" * 78)
holes = []
for label, code in CASES:
    if not d.assess_code(code, tool="run_code").dangerous:
        holes.append((label, code))
print(f"\n  команд, проходящих без предупреждения: {len(holes)} из {len(CASES)}")
for label, code in holes:
    print(f"    - {label}")
    print(f"      {code}")

print()
print("=" * 78)
print("4. ПОЧЕМУ ШАБЛОНЫ НЕ ЛОВЯТ СПИСКИ")
print("=" * 78)
import re  # noqa: E402

# Проверяется сам шаблон из _SHELL_RULES.
rule = None
for r in d._SHELL_RULES:
    if "pip" in r.pattern and "install" in r.pattern:
        rule = r
        break
if rule:
    print(f"  шаблон: {rule.pattern!r}")
    compiled = re.compile(rule.pattern, re.IGNORECASE)
    samples = [
        "pip install evil",                    # слитно
        'subprocess.run(["pip","install","x"])',  # списком
        "subprocess.run('pip install x', shell=True)",  # строкой
    ]
    for sample in samples:
        matched = bool(compiled.search(sample))
        print(f"    {'СОВПАЛ ' if matched else 'не совпал'}  {sample[:60]}")
    print("""
  Вывод: шаблон написан для СЛИТНОЙ записи. Между `pip` и `install`
  в списке стоят кавычка, запятая и пробел — `\\s+` их не покрывает.
""")

print("=" * 78)
print("ИТОГ")
print("=" * 78)
print(f"""
  Правка строки 288 закрыла обход для команд из `_ALWAYS_DANGEROUS_COMMANDS`
  (rm, shred, curl, wget, sudo, dd, mkfs и другие) — их список в модуле
  содержит {len(d._ALWAYS_DANGEROUS_COMMANDS)} имён.

  Осталось незакрытым: команды, которые безвредны САМИ ПО СЕБЕ, но
  опасны своей подкомандой — `pip install`, `npm install -g`,
  `git push --force`, `docker run --privileged`, `terraform destroy`.
  Они не входят в чёрный список по построению, а шаблоны `_SHELL_RULES`
  написаны для слитной записи и не ловят список аргументов.
""")
