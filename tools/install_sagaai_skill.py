"""Установка навыка RAGRAF в проект SagaAI.

Навык в SagaAI — это каталог с файлом `SKILL.md` плюс запись в реестре
`skills/skills.json`. Метаданные живут ТОЛЬКО в реестре: front-matter
в `SKILL.md` не используется (проверено разведкой на существующем навыке
`Rag_Base_Creator`).

Порядок:
  1. создаётся каталог навыка и копируется текст;
  2. навык регистрируется штатной функцией проекта `register_skill`;
  3. навык помечается адаптированным (`set_skill_adapted`), иначе он виден
     агенту, но НЕ попадает в промпт (фильтр `adapted=True`);
  4. навык включается оркестратору через `set_enabled_skills`.

Все действия — штатные функции проекта: никаких правок его базы вручную.
"""

from __future__ import annotations

import pathlib
import shutil
import sys

SAGA = pathlib.Path(r"C:\!.Tihon\sagaai_edit_github")
SOURCE = pathlib.Path(r"C:\AutoClaw\RAGRAF\.staging\sagaai_skill_ragraf.md")

sys.path.insert(0, str(SAGA))

from core import skills_library  # noqa: E402

SLUG = "Ragaf_Stats"
SKILL_NAME = "RAGRAF Code Index"
DESCRIPTION = (
    "How to use the RAGRAF code-knowledge index: query interface, operations, "
    "response fields, task labels, and the /RAGRAF usage report."
)

print("=" * 78)
print("1. СОСТОЯНИЕ ДО УСТАНОВКИ")
print("=" * 78)
before = skills_library.list_skills()
print(f"  навыков в реестре: {len(before)}")
for skill in before:
    print(f"    {skill.get('id')}  {skill.get('name'):<24} "
          f"adapted={skill.get('adapted')}")

print()
print("=" * 78)
print("2. СОЗДАНИЕ КАТАЛОГА НАВЫКА")
print("=" * 78)
root = pathlib.Path(skills_library.get_skills_root())
skill_dir = root / SLUG
print(f"  каталог навыков: {root}")
print(f"  каталог навыка:  {skill_dir}")

if skill_dir.exists():
    print("  каталог уже существует")
else:
    skill_dir.mkdir(parents=True)
    print("  каталог создан")

target = skill_dir / "SKILL.md"
shutil.copy2(SOURCE, target)
print(f"  SKILL.md: {target.name} ({target.stat().st_size} б)")

print()
print("=" * 78)
print("3. РЕГИСТРАЦИЯ ШТАТНОЙ ФУНКЦИЕЙ ПРОЕКТА")
print("=" * 78)
existing = {s.get("name") for s in before}
skill_id = None
if SKILL_NAME in existing:
    # Навык уже есть: находится его идентификатор.
    for skill in before:
        if skill.get("name") == SKILL_NAME:
            skill_id = skill.get("id")
    print(f"  навык уже зарегистрирован: id={skill_id}")
else:
    try:
        skill_id = skills_library.register_skill(
            SKILL_NAME, DESCRIPTION, SLUG
        )
        print(f"  зарегистрирован: id={skill_id}")
    except Exception as exc:
        print(f"  ошибка регистрации: {type(exc).__name__}: {exc}")

print()
print("=" * 78)
print("4. ПРИЗНАК АДАПТАЦИИ (без него навык не попадает в промпт)")
print("=" * 78)
if skill_id:
    try:
        ok = skills_library.set_skill_adapted(skill_id, True)
        print(f"  set_skill_adapted({skill_id}, True) → {ok}")
    except Exception as exc:
        print(f"  ошибка: {type(exc).__name__}: {exc}")
else:
    print("  идентификатор неизвестен — пропускается")

print()
print("=" * 78)
print("5. ВКЛЮЧЕНИЕ НАВЫКА ОРКЕСТРАТОРУ DEV_AGENT")
print("=" * 78)
try:
    from core import orchestrators

    current = orchestrators.get_enabled_skills("dev_agent") or []
    print(f"  включено до: {current}")
    if skill_id and skill_id not in current:
        new_value = list(current) + [skill_id]
        ok = orchestrators.set_enabled_skills("dev_agent", new_value)
        print(f"  set_enabled_skills → {ok}")
        after_enabled = orchestrators.get_enabled_skills("dev_agent") or []
        print(f"  включено после: {after_enabled}")
    elif skill_id:
        print("  навык уже включён")
    else:
        print("  идентификатор неизвестен — пропускается")
except Exception as exc:
    print(f"  ошибка: {type(exc).__name__}: {exc}")

print()
print("=" * 78)
print("6. ПРОВЕРКА РЕЗУЛЬТАТА")
print("=" * 78)
after = skills_library.list_skills()
print(f"  навыков в реестре: {len(after)}")
for skill in after:
    mark = " <-- наш" if skill.get("name") == SKILL_NAME else ""
    print(f"    {skill.get('id')}  {str(skill.get('name')):<26} "
          f"adapted={skill.get('adapted')}{mark}")

ours = [s for s in after if s.get("name") == SKILL_NAME]
print()
if ours:
    print("  навык RAGRAF установлен:")
    for key in ("id", "name", "folder", "adapted", "developer"):
        print(f"    {key}: {ours[0].get(key)}")
    # Проверка: текст навыка читается проектом.
    text = skills_library.get_skill_prompt(ours[0]["id"]) if hasattr(
        skills_library, "get_skill_prompt") else ""
    preview = str(text)[:80] if text else "(текст не получен)"
    print(f"  текст читается: {preview!r}")
else:
    print("  ПРОВАЛ: навык не найден в реестре")
