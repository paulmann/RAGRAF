"""Разбор 14 ошибок: сервис 'github' не проходит валидацию коннекторов.

ПАДЕНИЕ (все 14 тестов, в фикстуре):
    connectors.create_connection("github", "GitHub-Test", "ghp-foo-token")
    -> ValueError: Unsupported connector service
       (core/connectors.py:110, _validate_service)

Надо понять: это дефект КОДА или устаревший тест.

  * Если набор поддерживаемых сервисов сузили (например github вынесли
    в отдельный модуль и требуют другое имя), а тесты остались на старом
    имени — дефект в тестах.
  * Если GitHub-коннектор заявлен рабочим, но его нельзя создать через
    общий API — дефект в коде: возможность недоступна пользователю.

Проверяется фактический список поддерживаемых сервисов и то, что реально
предлагает интерфейс.
"""

from __future__ import annotations

import pathlib
import re
import sys

SAGA = pathlib.Path(r"C:\!.Tihon\sagaai_edit_github")
sys.path.insert(0, str(SAGA))

from core import connectors as c  # noqa: E402

print("=" * 78)
print("1. ПОДДЕРЖИВАЕМЫЕ СЕРВИСЫ")
print("=" * 78)
# Ищутся константы со списком сервисов.
for name in dir(c):
    if name.isupper() and ("SERVICE" in name or "SUPPORTED" in name):
        value = getattr(c, name)
        print(f"  {name} = {value}")

print()
print("=" * 78)
print("2. ФУНКЦИЯ _validate_service")
print("=" * 78)
src = (SAGA / "core" / "connectors.py").read_text(encoding="utf-8")
lines = src.splitlines()
start = next((i for i, ln in enumerate(lines, 1)
              if ln.startswith("def _validate_service")), None)
if start:
    end = next((i for i in range(start + 1, len(lines) + 1)
                if i <= len(lines) and lines[i - 1].startswith("def ")),
               len(lines) + 1)
    for j in range(start - 1, min(end - 1, start + 25)):
        print(f"  {j + 1:>5}: {lines[j][:92]}")

print()
print("=" * 78)
print("3. ЧТО ЗНАЕТ ТЕСТ И ЧТО ЗНАЕТ КОД")
print("=" * 78)
test_text = (SAGA / "tests" / "test_github_connector.py").read_text(
    encoding="utf-8", errors="replace")
# Ищутся используемые имена сервисов в тесте.
services = set(re.findall(r'create_connection\(\s*["\'](\w+)["\']',
                          test_text))
print(f"  сервисы в тесте: {sorted(services)}")
# Ищутся реальные вызовы create_connection в коде проекта.
used = set()
for p in SAGA.rglob("*.py"):
    if {".venv", ".git", ".dev_agent", "__pycache__"} & set(p.parts):
        continue
    try:
        t = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        continue
    for m in re.findall(r'create_connection\(\s*["\'](\w+)["\']', t):
        used.add(m)
print(f"  сервисы, используемые в проекте: {sorted(used)}")

print()
print("=" * 78)
print("4. ПРОВЕРКА ВЫЗОВОМ: КАКИЕ ИМЕНА ПРИНИМАЮТСЯ")
print("=" * 78)
for name in ("github", "github_rest", "github_connector", "ssh",
             "openai", "gigachat"):
    try:
        c.create_connection(name, "probe", "x")
        print(f"  {name:<20} принят")
    except Exception as exc:
        print(f"  {name:<20} отклонён ({type(exc).__name__}: {str(exc)[:50]})")

print()
print("=" * 78)
print("5. ЕСТЬ ЛИ МОДУЛИ КОННЕКТОРОВ ДЛЯ ЭТИХ СЕРВИСОВ")
print("=" * 78)
for name in ("github_connector.py", "github_connector_rest.py",
             "ssh_connector.py", "github_tools.py", "ssh_tools.py"):
    path = SAGA / "core" / name
    print(f"  {name:<28} {'есть' if path.exists() else 'нет'}")

print()
print("=" * 78)
print("ОЦЕНКА")
print("=" * 78)
print("""
  Если модуль `github_connector.py` существует, а сервис `github` не
  принимается валидатором, это расхождение между составом кода и списком
  поддерживаемых сервисов. Тогда решение зависит от того, что считалось:
    * если GitHub-коннектор выведен из эксплуатации — тесты устарели;
    * если он должен работать — дефект в коде (валидатор не знает сервис).
""")
