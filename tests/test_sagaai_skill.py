"""Проверка: навык RAGRAF полноценно виден агенту SagaAI.

Установка прошла, но текст навыка не читался функцией `get_skill_prompt`
(её в модуле нет — я угадывал имя). Надо проверить штатным путём: через
те функции, которые реально существуют:

  * `get_skill(id)` — запись реестра;
  * `get_skill_folder(id)` — каталог навыка;
  * `list_skill_files(id)` — файлы навыка;
  * `get_enabled_skills_metadata(ids)` — то, что попадает в промпт агента;
  * `build_skills_metadata_text(ids)` — блок «Available skills» в промпте.

Последние две — ключевые: если навык там есть, агент его увидит.
"""

from __future__ import annotations

import pathlib
import sys

SAGA = pathlib.Path(r"C:\!.Tihon\sagaai_edit_github")
sys.path.insert(0, str(SAGA))

from core import skills_library  # noqa: E402

SKILL_ID = "9b7ffb65"
SKILL_NAME = "RAGRAF Code Index"

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
print("1. ЗАПИСЬ РЕЕСТРА")
print("=" * 78)
skill = skills_library.get_skill(SKILL_ID)
print(f"  get_skill({SKILL_ID}):")
for key, value in (skill or {}).items():
    print(f"    {key}: {value}")
check(skill is not None, "навык найден в реестре")
if skill:
    check(skill.get("name") == SKILL_NAME, "имя совпадает",
          f"получено {skill.get('name')!r}")
    check(skill.get("adapted") is True, "навык помечен адаптированным")

print()
print("=" * 78)
print("2. ФАЙЛЫ НАВЫКА")
print("=" * 78)
folder = skills_library.get_skill_folder(SKILL_ID)
print(f"  каталог: {folder}")
check(bool(folder), "каталог навыка определён")
files = skills_library.list_skill_files(SKILL_ID)
print(f"  файлы: {files}")
check(bool(files), "в каталоге есть файлы", f"найдено {files}")
check(any("SKILL.md" in str(f) for f in files),
      "файл SKILL.md на месте", f"файлы: {files}")

# Проверяется содержимое: это то, что прочитает агент.
if folder:
    skill_md = pathlib.Path(folder) / "SKILL.md"
    print(f"\n  SKILL.md: {skill_md.exists()}")
    if skill_md.exists():
        text = skill_md.read_text(encoding="utf-8")
        print(f"  размер: {len(text)} символов")
        checks = {
            "назначение": "## Purpose" in text,
            "когда применять": "## When to use" in text,
            "перечень операций": "task_stats" in text,
            "поля ответа": "ok` is a failure" in text or "`ok`" in text,
            "границы": "cannot do" in text,
            "метка задачи": "RAGRAF_TASK" in text,
            "пример": "Concrete usage example" in text,
        }
        for label, ok in checks.items():
            print(f"    [{'OK ' if ok else '--'}] {label}")
            check(ok, f"раздел навыка: {label}")

print()
print("=" * 78)
print("3. НАВЫК ПОПАДАЕТ В ПРОМПТ АГЕНТА")
print("=" * 78)
try:
    from core import orchestrators

    enabled = orchestrators.get_enabled_skills("dev_agent") or []
    print(f"  включённых навыков: {enabled}")
    check(SKILL_ID in enabled, "навык включён оркестратору dev_agent",
          f"включено: {enabled}")

    metadata = skills_library.get_enabled_skills_metadata(enabled)
    print(f"\n  метаданные для промпта: {len(metadata)} записей")
    for item in metadata:
        print(f"    {item.get('id')}  {item.get('name')}")
    ids_in_prompt = {str(i.get("id")) for i in metadata}
    check(SKILL_ID in ids_in_prompt,
          "навык попал в метаданные промпта",
          f"в промпте: {ids_in_prompt}")

    block = skills_library.build_skills_metadata_text(enabled)
    text = str(block)
    print(f"\n  блок промпта: {len(text)} символов")
    for line in text.splitlines()[:14]:
        print(f"    {line}")
    check(SKILL_NAME in text or SKILL_ID in text,
          "навык назван в блоке промпта")
except Exception as exc:
    print(f"  ошибка: {type(exc).__name__}: {exc}")
    check(False, "проверка промпта выполнена")

print()
print("=" * 78)
print("4. ВЫКЛЮЧЕНИЕ НАВЫКА ВОЗМОЖНО (обратимость)")
print("=" * 78)
try:
    from core import orchestrators

    # Проверка: навык можно выключить и включить обратно штатными функциями.
    enabled = orchestrators.get_enabled_skills("dev_agent") or []
    without = [s for s in enabled if s != SKILL_ID]
    ok_off = orchestrators.set_enabled_skills("dev_agent", without)
    state_off = orchestrators.get_enabled_skills("dev_agent") or []
    ok_on = orchestrators.set_enabled_skills("dev_agent", enabled)
    state_on = orchestrators.get_enabled_skills("dev_agent") or []
    print(f"  выключение: {ok_off}, состояние: {state_off}")
    print(f"  включение:  {ok_on}, состояние: {state_on}")
    check(SKILL_ID not in state_off, "навык выключается")
    check(SKILL_ID in state_on, "навык включается обратно")
except Exception as exc:
    print(f"  ошибка: {type(exc).__name__}: {exc}")
    check(False, "обратимость проверена")

print()
print("=" * 78)
print(f"ИТОГ: пройдено {PASS}, провалено {FAIL}")
print("=" * 78)
raise SystemExit(0 if FAIL == 0 else 1)
