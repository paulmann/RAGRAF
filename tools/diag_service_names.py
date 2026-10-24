"""Разбор: 'github' против 'github_rest' — что считается правильным.

УСТАНОВЛЕНО:
    CONNECTOR_SERVICES = {'github_rest', 'ssh'}   # поддерживаются два
    тесты используют:    'github'                  # не поддерживается
    в проекте встречается: 'github', 'github_rest', 'gitlab', 'slack', 'ssh'

ЗНАЧИТ надо выяснить, что именно правильно:
  1. если проект перешёл на `github_rest`, а тесты не обновили — дефект тестов;
  2. если код продукта тоже зовёт `github` — дефект кода: он обращается
     к сервису, которого нет;
  3. если `gitlab` и `slack` используются, но не поддерживаются — это
     отдельная находка: код ссылается на несуществующие сервисы.

Проверяется, ГДЕ именно встречаются эти имена: в тестах, в документации
или в рабочем коде.
"""

from __future__ import annotations

import pathlib
import re

SAGA = pathlib.Path(r"C:\!.Tihon\sagaai_edit_github")
SKIP = {".venv", ".git", ".dev_agent", "__pycache__", "node_modules",
        "history", "storage"}

NAMES = ["github_rest", "github", "gitlab", "slack", "ssh"]

print("=" * 78)
print("ГДЕ ВСТРЕЧАЮТСЯ ИМЕНА СЕРВИСОВ")
print("=" * 78)
hits: dict[str, list[tuple[str, int, str]]] = {n: [] for n in NAMES}
for p in sorted(SAGA.rglob("*.py")):
    if set(p.parts) & SKIP:
        continue
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        continue
    rel = str(p.relative_to(SAGA)).replace("\\", "/")
    for i, line in enumerate(text.splitlines(), 1):
        # Ищется использование в вызовах коннекторов.
        if re.search(r'create_connection|connector_service|service\s*=\s*["\']',
                     line):
            for name in NAMES:
                if f'"{name}"' in line or f"'{name}'" in line:
                    hits[name].append((rel, i, line.strip()[:88]))

for name in NAMES:
    entries = hits[name]
    if not entries:
        continue
    print(f"\n  === {name}: {len(entries)} ===")
    for rel, line_no, line in entries[:8]:
        kind = "ТЕСТ" if rel.startswith("tests/") else "КОД "
        print(f"    [{kind}] {rel}:{line_no}")
        print(f"           {line}")

print()
print("=" * 78)
print("ГДЕ ИМЕНА ВСТРЕЧАЮТСЯ ВООБЩЕ (любой контекст)")
print("=" * 78)
for name in NAMES:
    count_code = 0
    count_test = 0
    files_code = set()
    for p in sorted(SAGA.rglob("*.py")):
        if set(p.parts) & SKIP:
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        rel = str(p.relative_to(SAGA)).replace("\\", "/")
        n = len(re.findall(rf'["\']{re.escape(name)}["\']', text))
        if not n:
            continue
        if rel.startswith("tests/"):
            count_test += n
        else:
            count_code += n
            files_code.add(rel)
    print(f"  {name:<14} в коде: {count_code:<4} в тестах: {count_test:<4} "
          f"файлов кода: {len(files_code)}")
    for rel in sorted(files_code)[:5]:
        print(f"      {rel}")

print()
print("=" * 78)
print("РЕАЛЬНО ПОДДЕРЖИВАЕМЫЕ СЕРВИСЫ")
print("=" * 78)
import sys  # noqa: E402

sys.path.insert(0, str(SAGA))
from core.connectors import CONNECTOR_SERVICES  # noqa: E402

print(f"  {sorted(CONNECTOR_SERVICES)}")
print(f"\n  определений коннекторов в списке: {len(CONNECTOR_SERVICES)}")

print()
print("=" * 78)
print("ОЦЕНКА")
print("=" * 78)
print("""
  Смотрите таблицу выше. Вывод строится по фактам:
    * если имя используется ТОЛЬКО в тестах — устарели тесты;
    * если используется в рабочем коде — код обращается к сервису,
      которого нет в списке: это дефект (возможность недоступна),
      и его надо назвать разработчику.
""")
