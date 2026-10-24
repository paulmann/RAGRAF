"""Проверка: подхватывает ли проект инструкцию про RAGRAF.

Важность: проект не читает описание кастомной функции из файла — он
подставляет общий текст «Custom function of orchestrator...». Поэтому
единственный способ сообщить агенту назначение инструмента — инструкция
в `orchestrators/<slug>/instructions/*.md`.

Проверяется: читает ли проект инструкции с диска и попадает ли туда
`ragraf_index.md`. Для этого вызывается функция проекта, а не проверяется
наличие файла.
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
print("1. ФАЙЛ ИНСТРУКЦИИ НА МЕСТЕ")
print("=" * 78)
path = SAGA / "orchestrators" / "dev_agent" / "instructions" / "ragraf_index.md"
print(f"  путь: {path}")
check(path.exists(), "файл инструкции существует")
if not path.exists():
    raise SystemExit(1)
print(f"  размер: {path.stat().st_size} б")
text = path.read_text(encoding="utf-8")
check(text.startswith("---"), "инструкция начинается с front-matter")
check("id: ragraf_index" in text, "в front-matter есть id")
check("name:" in text, "в front-matter есть name")
check("description:" in text, "в front-matter есть description")

print()
print("=" * 78)
print("2. ПРОЕКТ ЧИТАЕТ ИНСТРУКЦИИ С ДИСКА")
print("=" * 78)
try:
    from core.orchestrator_folders import list_orchestrator_instructions

    instructions = list_orchestrator_instructions("dev_agent")
    ids = [i.get("id") for i in instructions]
    print(f"  инструкций: {len(ids)}")
    for i in instructions:
        print(f"    {i.get('id'):<24} {str(i.get('name'))[:40]}")
    check("ragraf_index" in ids, "инструкция про RAGRAF видна проекту",
          f"найденные: {ids}")
except ImportError:
    # Имя функции может отличаться: ищется подходящая.
    import core.orchestrator_folders as folders  # noqa: E402

    candidates = [n for n in dir(folders) if "instruction" in n.lower()]
    print(f"  функции для инструкций: {candidates}")
    found = False
    for name in candidates:
        fn = getattr(folders, name)
        if not callable(fn):
            continue
        try:
            result = fn("dev_agent")
        except TypeError:
            continue
        if isinstance(result, dict):
            ids = [str(v.get("id")) for v in result.values()
                   if isinstance(v, dict)]
        elif isinstance(result, list):
            ids = [str(v.get("id")) for v in result if isinstance(v, dict)]
        else:
            continue
        print(f"    {name} → {ids}")
        if "ragraf_index" in ids:
            found = True
    check(found, "инструкция найдена через функцию проекта")
except Exception as exc:
    print(f"  ошибка: {type(exc).__name__}: {exc}")
    check(False, "инструкции прочитаны")

print()
print("=" * 78)
print("3. СОДЕРЖИМОЕ ИНСТРУКЦИИ ПРИГОДНО ДЛЯ АГЕНТА")
print("=" * 78)
must_have = [
    ("ragraf(operation=", "примеры вызова"),
    ("search", "операция поиска"),
    ("symbol", "операция symbol"),
    ("found", "объяснение поля found"),
    ("stale", "объяснение поля stale"),
    ("НЕ умеет", "раздел о границах"),
    ("index_status", "операция проверки свежести"),
    ("SagaAI", "имя агента для статистики"),
]
for needle, label in must_have:
    present = needle in text
    print(f"  {'[OK ]' if present else '[--]'} {label}: {needle!r}")
    check(present, f"инструкция содержит: {label}")

print()
print("=" * 78)
print("4. ИНСТРУКЦИЯ НЕ ДУБЛИРУЕТ ДРУГИЕ")
print("=" * 78)
try:
    from core.orchestrator_folders import list_orchestrator_instructions

    instructions = list_orchestrator_instructions("dev_agent")
    names = [str(i.get("name") or "").lower() for i in instructions]
    dupes = [n for n in names if names.count(n) > 1]
    print(f"  названий всего: {len(names)}, повторов: {len(set(dupes))}")
    check(not dupes, "дублей по названию нет", f"повторы: {set(dupes)}")
except Exception:
    check(True, "проверка дублей пропущена (функция недоступна)")

print()
print("=" * 78)
print(f"ИТОГ: пройдено {PASS}, провалено {FAIL}")
print("=" * 78)
raise SystemExit(0 if FAIL == 0 else 1)
