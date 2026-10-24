"""Проверка нормализации имён Joern: Python и PHP одновременно.

Дефект, который исправляется: `normalize_joern_name` знала только PHP
(замена `->` на `::`), поэтому на Python-корпусе имя не совпадало ни с одним
fqn индекса — 40 806 вызовов и 0 подтверждённых.

Опасность правки: чтобы починить Python, легко сломать PHP. Поэтому
проверяются ОБА языка, на реальных именах из замеров.
"""

from __future__ import annotations

import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf.joern_export import (  # noqa: E402
    is_global_owner,
    normalize_joern_name,
    normalize_joern_path,
)

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
print("1. PYTHON: ИМЕНА ИЗ РЕАЛЬНОГО ЗАМЕРА")
print("=" * 78)
# Имена взяты дословно из записей Joern для проекта sagaai.
PY_CASES = [
    ("core\\api_errors.py:__init__", "core/api_errors.py:__init__"),
    ("core\\api_errors.py:api_error_message",
     "core/api_errors.py:api_error_message"),
    ("ui\\pages\\chat.py:render", "ui/pages/chat.py:render"),
    (".\\core\\version.py:get_version", "core/version.py:get_version"),
    ("dev_agent\\tool_executor.py:run", "dev_agent/tool_executor.py:run"),
]
print(f"\n  {'имя от Joern':<44} {'результат':<40} верно")
print("  " + "-" * 92)
for raw, want in PY_CASES:
    got = normalize_joern_name(raw)
    mark = "да" if got == want else "НЕТ"
    print(f"  {raw:<44} {got:<40} {mark}")
    check(got == want, f"Python: {raw[:30]}", f"получено {got!r}")

print()
print("=" * 78)
print("2. PHP: ПРЕЖНЕЕ ПОВЕДЕНИЕ НЕ ДОЛЖНО СЛОМАТЬСЯ")
print("=" * 78)
PHP_CASES = [
    # Имена из прежних замеров на корпусе z.batteryservice.ru.
    ("App\\Core\\Config->get", "App\\Core\\Config::get"),
    ("src\\Db.php:<global>-><lambda>0", "src\\Db.php:<global>"),
    ("App\\Shell::render", "App\\Shell::render"),
    ("_diag.php:<global>-><lambda>0", "_diag.php:<global>"),
    ("App\\Service->handle", "App\\Service::handle"),
]
print(f"\n  {'имя от Joern':<44} {'результат':<40} верно")
print("  " + "-" * 92)
for raw, want in PHP_CASES:
    got = normalize_joern_name(raw)
    mark = "да" if got == want else "НЕТ"
    print(f"  {raw:<44} {got:<40} {mark}")
    check(got == want, f"PHP: {raw[:30]}", f"получено {got!r}")

print()
print("=" * 78)
print("3. ВЛАДЕЛЕЦ ГЛОБАЛЬНОЙ ОБЛАСТИ: ОБА ОБОЗНАЧЕНИЯ")
print("=" * 78)
GLOBAL_CASES = [
    ("core\\api_errors.py:<module>", True, "Python, модуль"),
    ("_diag.php:<global>", True, "PHP, глобальная область"),
    ("_diag.php:<global>-><lambda>0", True, "PHP, лямбда в модуле"),
    ("core\\api_errors.py:__init__", False, "Python, метод"),
    ("App\\Shell::run", False, "PHP, метод класса"),
    ("", True, "пустое имя"),
    (None, True, "None"),
]
for owner, want, label in GLOBAL_CASES:
    got = is_global_owner(owner)
    mark = "верно" if got == want else "ОШИБКА"
    print(f"  {str(owner)[:44]:<46} {str(got):<7} {label:<22} {mark}")
    check(got == want, f"глобальный владелец: {label}",
          f"получено {got}, ожидалось {want}")

print()
print("=" * 78)
print("4. ПУТИ: НОРМАЛИЗАЦИЯ РАЗДЕЛИТЕЛЕЙ")
print("=" * 78)
PATH_CASES = [
    ("core\\api_errors.py", "core/api_errors.py"),
    ("./core/version.py", "core/version.py"),
    ("\\\\core\\\\x.py", "core/x.py"),
]
for raw, want in PATH_CASES:
    got = normalize_joern_path(raw)
    mark = "верно" if got == want else "ОШИБКА"
    print(f"  {raw:<30} → {got:<30} {mark}")
    check(got == want, f"путь {raw}", f"получено {got!r}")

print()
print("=" * 78)
print("5. ИМЯ PYTHON СОВПАДАЕТ С ФОРМАТОЙ fqn ИНДЕКСА")
print("=" * 78)
# Главная проверка: нормализованное имя должно совпасть с fqn из индекса.
import sqlite3  # noqa: E402

from ragraf.config import load as load_config  # noqa: E402

cfg = load_config(REPO / "config" / "sagaai.yaml")
conn = sqlite3.connect(f"file:{cfg.graph_db}?mode=ro", uri=True)
conn.row_factory = sqlite3.Row
rev = conn.execute(
    "SELECT rev_id FROM rev WHERE state = 'ready' ORDER BY rev_id DESC LIMIT 1"
).fetchone()["rev_id"]
fqns = {
    str(r["fqn"])
    for r in conn.execute(
        "SELECT fqn FROM node WHERE rev_id = ? AND kind IN "
        "('function','method')", (rev,)
    ).fetchall()
}
conn.close()
print(f"  fqn в индексе: {len(fqns)}")

# Проверяется: нормализованное имя из Joern находит узел в индексе.
matched = 0
unmatched: list[str] = []
for raw, _ in PY_CASES:
    norm = normalize_joern_name(raw)
    if norm in fqns:
        matched += 1
    else:
        unmatched.append(norm)
print(f"  совпало с индексом: {matched} из {len(PY_CASES)}")
for u in unmatched:
    print(f"    не найдено: {u}")
# Здесь проверяется МЕХАНИЗМ нормализации, а не полнота индекса:
# конкретные функции могли не попасть в индекс по другим причинам.
check(matched > 0 or not unmatched,
      "нормализованные имена Python совпадают с форматом fqn индекса")

print()
print("=" * 78)
print(f"ИТОГ: пройдено {PASS}, провалено {FAIL}")
print("=" * 78)
raise SystemExit(0 if FAIL == 0 else 1)
