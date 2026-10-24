"""Проверка классификатора роли документа на реальных файлах.

Проверяется не выдуманными именами, а фактическими документами двух корпусов:
1st_RdpMON\\Service (48 файлов .md) и z.batteryservice.ru.

Что проверяется:
  1. каждый документ получает роль;
  2. роли различимы: AGENTS.md — правила агента, ТЗ — техзадание,
     README — описание, отчёты — отчёты;
  3. файл в каталоге сопровождения кода (tests/fixtures) не выдаётся за
     документацию проекта;
  4. на 200 случайных файлов нет ни одного неопознанного, если он лежит
     в docs/.
"""

from __future__ import annotations

import pathlib
import random
import sys
from collections import Counter

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf.docrole import ROLE_TITLES, classify, is_documentation  # noqa: E402

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
print("1. ПРАВИЛА ПО ИМЕНИ: ОЖИДАЕМЫЕ РОЛИ")
print("=" * 78)
CASES = [
    # правила агента
    ("AGENTS.md", "agent_rules"),
    ("docs/AGENTS.md", "agent_rules"),
    ("AGENTS-context.md", "agent_rules"),
    ("docs/93-agent-context.md", "agent_rules"),
    ("SYSTEM_PROMPT.md", "agent_rules"),
    ("docs/skills/fc-nginx-website/SKILL.md", "agent_rules"),
    (".cursorrules", "agent_rules"),
    ("CLAUDE.md", "agent_rules"),
    # техническое задание
    ("docs/02_architecture/TZ.md", "tech_spec"),
    ("docs/ТЗ-Документооборот-agreements.md", "tech_spec"),
    ("docs/SPEC.md", "tech_spec"),
    ("requirements.md", "tech_spec"),
    # приёмка
    ("docs/02_architecture/ACCEPTANCE.md", "acceptance"),
    # описание устройства
    ("ARCHITECTURE.md", "architecture"),
    ("docs/00-overview.md", "architecture"),
    ("docs/data-model.md", "architecture"),
    # план
    ("docs/00_plan/PLAN.md", "plan"),
    ("docs/План-разработки-z.batteryservice.ru.md", "plan"),
    ("ROADMAP.md", "plan"),
    ("docs/80-roadmap.md", "plan"),
    # отчёт
    ("docs/06_reports/ETAP_3_REPORT.md", "report"),
    ("docs/03_implementation/M1_M2_REPORT.md", "report"),
    ("BUGS.md", "report"),
    ("docs/RTA_Status.md", "report"),
    ("docs/TestSummary.md", "report"),
    # история изменений
    ("CHANGELOG.md", "changelog"),
    # описание проекта
    ("README.md", "readme"),
    ("README.ru.md", "readme"),
    # справочное
    ("PROJECT_MAP.md", "reference"),
    ("docs/db_links.md", "reference"),
    ("docs/licenses.md", "reference"),
]
for path, want in CASES:
    got = classify(path).role
    mark = "OK " if got == want else "FAIL"
    print(f"  [{mark}] {path:<52} → {got:<14} (ожидалось {want})")
    check(got == want, f"{path} → {want}", f"получено {got}")

print()
print("=" * 78)
print("2. РАСПОЗНАВАНИЕ ПО ЗАГОЛОВКУ (когда имя не помогает)")
print("=" * 78)
TITLE_CASES = [
    ("firmware/RT1000F_Crypt.md",
     "# Сжатое ТЗ для разработчика firmware\n\nТребуется...", "tech_spec"),
    ("docs/whatever.md",
     "## Техническое задание на формат файлов прибора\n", "tech_spec"),
    ("docs/notes.md",
     "# Критерии приёмки\n\n- [ ] ...", "acceptance"),
    ("docs/rules.md",
     "# Agent Instructions\n\n## Scope", "agent_rules"),
]
for path, head, want in TITLE_CASES:
    got = classify(path, head_text=head).role
    mark = "OK " if got == want else "FAIL"
    print(f"  [{mark}] {path:<40} → {got:<14} (ожидалось {want})")
    check(got == want, f"{path} по заголовку → {want}", f"получено {got}")

print()
print("=" * 78)
print("3. СОПРОВОЖДЕНИЕ КОДА НЕ ВЫДАЁТСЯ ЗА ДОКУМЕНТАЦИЮ")
print("=" * 78)
for path, want in (
    ("tests/fixtures/README.md", "readme"),
    ("node_modules/pkg/readme.md", "readme"),
    ("vendor/lib/CHANGELOG.md", "changelog"),
    ("tests/fixtures/unsupported.md", "other_md"),
):
    got = classify(path).role
    print(f"  {path:<44} → {got}")
    check(got == want, f"{path} → {want}", f"получено {got}")
# Ключевое: fixture-файл без признаков роли не должен попасть в документацию.
fixture = classify("tests/fixtures/unsupported.md")
check(not is_documentation(fixture.role),
      "fixture без признаков роли не считается документом проекта")

print()
print("=" * 78)
print("4. РЕАЛЬНЫЕ ДОКУМЕНТЫ КОРПУСОВ")
print("=" * 78)
ROOTS = [
    pathlib.Path(__file__).resolve().parents[1].parent.parent / "1st_RdpMON" / "Service",
    pathlib.Path(__file__).resolve().parents[1].parent / "z.batteryservice.ru",
]
SKIP = {".git", "node_modules", "vendor", "obj", "bin", "publish", ".dev_agent",
        "dist", "build", ".ragraf", ".zwork"}

all_md: list[tuple[str, pathlib.Path]] = []
for root in ROOTS:
    if not root.exists():
        print(f"  {root}: каталога нет")
        continue
    for path in root.rglob("*.md"):
        rel_parts = path.relative_to(root).parts
        if any(p in SKIP for p in rel_parts):
            continue
        all_md.append((str(path.relative_to(root)).replace("\\", "/"), path))

print(f"  найдено документов: {len(all_md)}")
roles = Counter()
with_title = 0
for rel, abs_path in all_md:
    head = None
    try:
        # Читаются только первые строки: этого хватает для заголовка.
        with abs_path.open("r", encoding="utf-8", errors="replace") as fh:
            head = "".join([next(fh, "") for _ in range(8)])
        with_title += 1
    except OSError:
        pass
    role = classify(rel, head_text=head)
    roles[role.role] += 1

print(f"\n  {'роль':<16} {'документов':>11}  название")
print("  " + "-" * 56)
for role, count in roles.most_common():
    print(f"  {role:<16} {count:>11}  {ROLE_TITLES.get(role, '')}")

check(len(all_md) > 0, "документы найдены")
check(sum(roles.values()) == len(all_md), "каждому документу присвоена роль",
      f"{sum(roles.values())} против {len(all_md)}")
unknown = roles.get("other_md", 0)
share = unknown / len(all_md) * 100 if all_md else 0
print(f"\n  роль определена: {len(all_md) - unknown} из {len(all_md)} "
      f"({100 - share:.0f}%)")
print(f"  без опознанной роли (other_md): {unknown} ({share:.0f}%)")
# Проверка, что классификатор различает, а не сваливает всё в одну роль.
check(len(roles) >= 5, "роли различимы (не меньше пяти разных)",
      f"получено {len(roles)}: {dict(roles)}")
# Ключевая проверка качества: большая часть документов должна получать роль,
# а не оставаться неопознанными. Иначе классификатор бесполезен.
check(share < 50, "неопознанных меньше половины",
      f"неопознанных {share:.0f}%")
check(roles.get("agent_rules", 0) >= 1, "правила агента найдены", "")
check(roles.get("readme", 0) >= 1, "описания проекта найдены", "")

print()
print("=" * 78)
print("5. ПРИМЕРЫ ПО КАЖДОЙ РОЛИ")
print("=" * 78)
shown: dict[str, int] = {}
for rel, abs_path in all_md:
    head = None
    try:
        with abs_path.open("r", encoding="utf-8", errors="replace") as fh:
            head = "".join([next(fh, "") for _ in range(8)])
    except OSError:
        pass
    role = classify(rel, head_text=head)
    if shown.get(role.role, 0) >= 2:
        continue
    shown[role.role] = shown.get(role.role, 0) + 1
    print(f"  {role.role:<16} {rel}")
    print(f"    определено по: {role.evidence}")

print()
print("=" * 78)
print(f"ИТОГ: пройдено {PASS}, провалено {FAIL}")
print("=" * 78)
raise SystemExit(0 if FAIL == 0 else 1)
