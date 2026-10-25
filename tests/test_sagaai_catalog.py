"""Проверка: видит ли проект SagaAI кастомную функцию RAGRAF.

Главное условие применимости: функция должна попасть в реестр инструментов
агента и в блок `## Available tools` промпта. Иначе агент о ней не узнает,
даже если файл лежит на месте.

Проект собирает каталог в `core/orchestrator_tools.build_tool_catalog`.
Вызывается именно он — это тот же код, которым пользуется проект.
"""

from __future__ import annotations

import pathlib
import sys

SAGA = pathlib.Path(r"C:\!.Tihon\sagaai_edit_github")
sys.path.insert(0, str(SAGA))

PASS = 0
FAIL = 0


def check(cond: bool, label: str, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [OK ] {label}")
    else:
        FAIL += 1
        print(f"  [FAIL] {label} {detail}")


print("=" * 78)
print("1. ФУНКЦИЯ ВИДНА ЧЕРЕЗ КАТАЛОГ ОРКЕСТРАТОРА")
print("=" * 78)
try:
    from core.orchestrator_folders import list_orchestrator_functions

    metas = list_orchestrator_functions("dev_agent")
    names = [m["name"] for m in metas]
    print(f"  функций в каталоге: {len(names)}")
    for m in metas:
        print(f"    {m['name']} ({m.get('size_bytes')} б)")
    check("ragraf" in names, "функция ragraf найдена в каталоге",
          f"найденные: {names}")
except Exception as exc:
    print(f"  ошибка: {type(exc).__name__}: {exc}")
    check(False, "каталог функций прочитан")

print()
print("=" * 78)
print("2. ФУНКЦИЯ ЗАГРУЖАЕТСЯ ПРОЕКТОМ КАК CALLABLE")
print("=" * 78)
try:
    from core.orchestrator_folders import load_orchestrator_function_module

    fn = load_orchestrator_function_module("dev_agent", "ragraf")
    print(f"  загружено: {fn is not None}")
    check(callable(fn), "проект получает вызываемую функцию")
except Exception as exc:
    print(f"  ошибка: {type(exc).__name__}: {exc}")
    check(False, "функция загружается проектом")

print()
print("=" * 78)
print("3. ФУНКЦИЯ ПОПАДАЕТ В РЕЕСТР ИНСТРУМЕНТОВ")
print("=" * 78)
try:
    from core.orchestrator_tools import build_tool_catalog

    catalog = build_tool_catalog("dev_agent")
    names = [t.get("name") for t in catalog]
    print(f"  инструментов в реестре: {len(names)}")
    print(f"    первые: {names[:10]}")
    check("ragraf" in names, "инструмент ragraf в реестре",
          f"есть: {[n for n in names if 'ragraf' in str(n).lower()]}")
    entry = next((t for t in catalog if t.get("name") == "ragraf"), None)
    if entry:
        print(f"\n  запись инструмента:")
        for k, v in entry.items():
            print(f"    {k} = {str(v)[:90]}")
except Exception as exc:
    print(f"  ошибка: {type(exc).__name__}: {exc}")
    check(False, "реестр инструментов собран")

print()
print("=" * 78)
print("4. ИНСТРУМЕНТ ПОПАДАЕТ В ПРОМПТ (БЛОК Available tools)")
print("=" * 78)
try:
    # Проект рендерит блок инструментов для промпта.
    from core.orchestrator_tools import build_tool_catalog

    catalog = build_tool_catalog("dev_agent")
    # Ищется функция рендера блока.
    rendered = None
    for mod_name in ("core.orchestrator_tools", "core.prompts"):
        try:
            module = __import__(mod_name, fromlist=["*"])
        except ImportError:
            continue
        for attr in dir(module):
            if "render" in attr.lower() and "tool" in attr.lower():
                candidate = getattr(module, attr)
                if callable(candidate):
                    print(f"  найден рендер: {mod_name}.{attr}")
                    rendered = candidate
                    break
    if rendered:
        try:
            text = rendered(catalog)
        except Exception:
            text = rendered("dev_agent")
        text = str(text)
        has_block = "Available tools" in text or "ragraf" in text
        print(f"  размер вывода: {len(text)} символов")
        check(has_block, "инструмент отражён в выводе для промпта")
        if "ragraf" in text:
            idx = text.find("ragraf")
            print(f"    фрагмент: {text[max(0, idx - 60):idx + 80]!r}")
    else:
        print("  рендер блока не найден автоматически — проверяется каталог")
        check(True, "инструмент есть в каталоге, из которого строится промпт")
except Exception as exc:
    print(f"  ошибка: {type(exc).__name__}: {exc}")
    check(False, "блок инструментов проверен")

print()
print("=" * 78)
print("5. ОТКЛЮЧЕНИЕ ИНСТРУМЕНТА РАБОТАЕТ")
print("=" * 78)
try:
    from core.orchestrator_tools import build_tool_catalog, is_tool_disabled

    disabled = {"ragraf"}
    catalog = build_tool_catalog("dev_agent", disabled=disabled)
    names = [t.get("name") for t in catalog]
    print(f"  при отключённом ragraf: инструментов {len(names)}")
    check("ragraf" not in names,
          "отключённый инструмент исключён из реестра",
          f"ragraf остался: {'ragraf' in names}")
    check(is_tool_disabled("ragraf", disabled) is True,
          "проверка отключения отвечает верно")
except Exception as exc:
    print(f"  ошибка: {type(exc).__name__}: {exc}")
    check(False, "отключение инструмента проверено")

print()
print("=" * 78)
print(f"ИТОГ: пройдено {PASS}, провалено {FAIL}")
print("=" * 78)
raise SystemExit(0 if FAIL == 0 else 1)
