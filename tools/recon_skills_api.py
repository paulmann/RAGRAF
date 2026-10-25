"""Разведка API навыков SagaAI: точные имена функций.

Нужны имена, чтобы установить навык штатным путём, а не правкой файлов.
Печатаются все публичные функции модуля `core.skills_library` и их
сигнатуры, а также текущее содержимое реестра.
"""

from __future__ import annotations

import inspect
import pathlib
import sys

SAGA = pathlib.Path(r"C:\!.Tihon\sagaai_edit_github")
sys.path.insert(0, str(SAGA))

from core import skills_library  # noqa: E402

print("=" * 78)
print("ПУБЛИЧНЫЕ ФУНКЦИИ core.skills_library")
print("=" * 78)
for name in sorted(dir(skills_library)):
    if name.startswith("_"):
        continue
    obj = getattr(skills_library, name)
    if not callable(obj):
        continue
    try:
        sig = str(inspect.signature(obj))
    except (TypeError, ValueError):
        sig = "(...)"
    print(f"  {name}{sig}")

print()
print("=" * 78)
print("КОНСТАНТЫ")
print("=" * 78)
for name in ("SKILLS_DIR_NAME", "REGISTRY_FILE", "MAX_FILE_SIZE",
             "MAX_ZIP_SIZE", "MAX_TOTAL_FILES"):
    value = getattr(skills_library, name, "(нет)")
    print(f"  {name} = {value}")

print()
print("=" * 78)
print("КОРНЕВОЙ КАТАЛОГ НАВЫКОВ")
print("=" * 78)
for name in dir(skills_library):
    if "root" in name.lower() or "dir" in name.lower():
        obj = getattr(skills_library, name)
        if callable(obj):
            try:
                value = obj()
            except Exception as exc:
                value = f"ошибка {type(exc).__name__}"
            print(f"  {name}() = {value}")

print()
print("=" * 78)
print("ТЕКУЩИЙ РЕЕСТР НАВЫКОВ")
print("=" * 78)
# Ищется функция чтения реестра.
for name in sorted(dir(skills_library)):
    if not callable(getattr(skills_library, name)):
        continue
    low = name.lower()
    if ("list" in low or "all" in low) and "skill" in low:
        try:
            result = getattr(skills_library, name)()
        except Exception as exc:
            print(f"  {name}(): ошибка {type(exc).__name__}: {exc}")
            continue
        print(f"  {name}(): тип {type(result).__name__}")
        if isinstance(result, dict):
            for key, value in list(result.items())[:10]:
                print(f"    {key}: {value}")
        elif isinstance(result, list):
            for item in result[:10]:
                print(f"    {item}")
        else:
            print(f"    {str(result)[:200]}")
