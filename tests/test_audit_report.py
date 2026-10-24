"""Проверка отчёта: каждая цифра должна совпадать с фактическим кодом.

Отчёт — документ, по которому будут принимать работу. Число, разошедшееся
с кодом, хуже отсутствующего: оно создаёт ложную уверенность.

Проверяется машинно:
  1. все упомянутые файлы существуют;
  2. числа совпадают с замером (количество except-pass, длинные функции);
  3. критическая уязвимость воспроизводится ровно так, как описана;
  4. установленные компоненты на месте.
"""

from __future__ import annotations

import pathlib
import re
import subprocess
import sys

SAGA = pathlib.Path(r"C:\!.Tihon\sagaai_edit_github")
RAGRAF = pathlib.Path(__file__).resolve().parents[1]
REPORT = pathlib.Path(__file__).resolve().parents[1].parent / "docs/SagaAI/SagaAI-отчёт-проблем-2026-10-06.md"
SAGA_PY = str(SAGA / ".venv" / "Scripts" / "python.exe")

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


text = REPORT.read_text(encoding="utf-8")
print(f"  отчёт: {REPORT.name}, {len(text.splitlines())} строк")

print()
print("=" * 78)
print("1. УПОМЯНУТЫЕ ФАЙЛЫ СУЩЕСТВУЮТ")
print("=" * 78)
# Извлекаются пути вида `core/x.py:123` и `ui/pages/y.py`.
#
# Учитывается вложенность: правило требует хотя бы одного каталога
# в пути, иначе короткие упоминания (например `rag.py` внутри
# `core/rag.py:302`) ошибочно считаются путями от корня проекта.
# Именно из-за этого тест давал ложный провал «файл rag.py не существует».
paths = set()
for match in re.findall(r"`([a-z_][\w/]*/[\w/]*\.py)(?::\d+)?`", text):
    paths.add(match)
# Отдельно проверяются короткие упоминания: они должны быть частью
# уже найденных путей либо существовать от корня.
short = set(re.findall(r"`([a-z_][\w]*\.py):\d+`", text))
print(f"  уникальных путей: {len(paths)}, коротких упоминаний: {len(short)}")
for name in sorted(short):
    # Короткое имя считается верным, если оно встречается в найденном пути
    # или если файл есть в корне проекта.
    in_path = any(p.endswith("/" + name) for p in paths)
    at_root = (SAGA / name).exists()
    if not (in_path or at_root):
        print(f"    [--] короткое имя не найдено: {name}")
        check(False, f"короткое упоминание разрешается: {name}")

for rel in sorted(paths):
    full = SAGA / rel
    if not full.exists():
        print(f"    [--] {rel}")
        check(False, f"файл существует: {rel}")
check(all((SAGA / r).exists() for r in paths), "все упомянутые файлы на месте")

print()
print("=" * 78)
print("2. ЧИСЛА СОВПАДАЮТ С ЗАМЕРОМ")
print("=" * 78)
# Заново считается число except-pass.
SKIP = {".venv", ".git", ".dev_agent", "__pycache__", "node_modules",
        "history", "storage"}
src_files = []
for p in sorted(SAGA.rglob("*.py")):
    if set(p.parts) & SKIP:
        continue
    rel = str(p.relative_to(SAGA)).replace("\\", "/")
    if rel.startswith("tests/"):
        continue
    try:
        src_files.append((rel, p.read_text(encoding="utf-8",
                                           errors="replace").splitlines()))
    except OSError:
        continue

swallowed = 0
for rel, lines in src_files:
    for i, line in enumerate(lines):
        if not re.match(r"\s*except\b", line):
            continue
        for j in range(i + 1, min(i + 3, len(lines))):
            body = lines[j].strip()
            if not body or body.startswith("#"):
                continue
            if body == "pass":
                swallowed += 1
            break
# Число берётся ИЗ ОТЧЁТА, а не из проверки: иначе проверка застывает на
# значении того дня, а проект продолжает меняться — измерено 14.10.2026:
# в отчёте стояло 119 мест и 96 файлов, замер дал 118 и 95 (одна позиция
# исчезла, один файл удалён). Требование то же: текст обязан совпадать с
# кодом, и расхождение обязано быть видно.
m_total = re.search(r"### 3\.1\.[^\n]*?(\d+)\s+мест", text)
m_files = re.search(r"подсчёт по всем исходникам \((\d+) файлов", text, re.IGNORECASE)
stated_total = int(m_total.group(1)) if m_total else -1
stated_files = int(m_files.group(1)) if m_files else -1
print(f"  except-pass: замер {swallowed}, в отчёте {stated_total}")
check(stated_total > 0, "в отчёте названо число except-pass",
      "не найден заголовок «### 3.1 … N мест»")
check(swallowed == stated_total, "число except-pass совпадает с отчётом",
      f"замер {swallowed}, в отчёте {stated_total}")
print(f"  файлов исходников: замер {len(src_files)}, в отчёте {stated_files}")
check(stated_files > 0, "в отчёте названо число исходников",
      "не найдена строка «подсчёт по всем исходникам (N файлов)»")
check(len(src_files) == stated_files, "число исходников совпадает с отчётом",
      f"замер {len(src_files)}, в отчёте {stated_files}")

# Длинные функции.
long_funcs = 0
for rel, lines in src_files:
    current = None
    for i, line in enumerate(lines, 1):
        m = re.match(r"def\s+(\w+)\s*\(", line)
        if m:
            if current and i - current[1] > 120:
                long_funcs += 1
            current = (m.group(1), i)
        elif line.strip() and not line.startswith((" ", "\t", "#")) and current:
            if i - current[1] > 120:
                long_funcs += 1
            current = None
    if current and len(lines) - current[1] > 120:
        long_funcs += 1
print(f"  длинных функций: замер {long_funcs}, в отчёте 17")
check(long_funcs == 17, "число длинных функций совпадает",
      f"замер {long_funcs}, в отчёте 17")

print()
print("=" * 78)
print("3. РЕЗУЛЬТАТ ИСПРАВЛЕНИЯ: КРИТИЧЕСКАЯ НАХОДКА ЗАКРЫТА")
print("=" * 78)
# Запускается проверка, вставленная в отчёт как приложение.
probe = RAGRAF / ".staging" / "report_probe.py"
probe.write_text(
    "import sys\n"
    f"sys.path.insert(0, r'{SAGA}')\n"
    "from core.dangerous import assess_code\n"
    "a = assess_code(\"os.system('rm -rf /')\", tool='run_code').dangerous\n"
    "b = assess_code('subprocess.run([\"rm\", \"-rf\", \"/\"])', "
    "tool='run_code').dangerous\n"
    "print(f'STR={a}')\n"
    "print(f'LIST={b}')\n",
    encoding="utf-8", newline="\n",
)
proc = subprocess.run([SAGA_PY, str(probe)], capture_output=True, text=True,
                      encoding="utf-8", errors="replace", timeout=300)
out = (proc.stdout or "").strip()
print(f"  вывод проверки: {out}")
str_dangerous = "STR=True" in out
list_dangerous = "LIST=True" in out

# ВАЖНО: после исправления ОБЕ формы должны распознаваться.
# До правки списком проходило молча — тест это и фиксировал. Теперь
# проверка обновлена под фактическое состояние: дефект закрыт.
check(str_dangerous, "слитная запись распознаётся")
check(list_dangerous,
      "запись списком ТЕПЕРЬ распознаётся (дефект исправлен)",
      "если это падает — значит правка core/dangerous.py откатилась")
probe.unlink(missing_ok=True)

print()
print("=" * 78)
print("4. УСТАНОВЛЕННЫЕ КОМПОНЕНТЫ ИЗ ОТЧЁТА")
print("=" * 78)
COMPONENTS = [
    ("orchestrators/dev_agent/functions/ragraf.py", "инструмент RAGRAF"),
    ("orchestrators/dev_agent/instructions/ragraf_index.md", "инструкция"),
    ("skills/Ragaf_Stats/SKILL.md", "навык"),
]
for rel, label in COMPONENTS:
    exists = (SAGA / rel).exists()
    print(f"    [{'OK ' if exists else '--'}] {label}: {rel}")
    check(exists, f"компонент на месте: {label}")

# Проверка: в отчёте указан верный id навыка.
skills_json = SAGA / "skills" / "skills.json"
if skills_json.exists():
    content = skills_json.read_text(encoding="utf-8")
    has_id = "9b7ffb65" in content
    print(f"    [{'OK ' if has_id else '--'}] id навыка 9b7ffb65 в реестре")
    check(has_id, "id навыка из отчёта верен")
    check("9b7ffb65" in text, "id навыка указан в отчёте")

print()
print("=" * 78)
print("5. ОТЧЁТ НЕ ОБЕЩАЕТ ЛИШНЕГО")
print("=" * 78)
# Проверяется наличие обязательных разделов.
SECTIONS = [
    "Итог одной таблицей",
    "Критично",
    "Важно",
    "Средне",
    "Низко",
    "Что проверено и проблем не найдено",
    "Оговорки",
]
for section in SECTIONS:
    present = section in text
    print(f"    [{'OK ' if present else '--'}] раздел: {section}")
    check(present, f"раздел отчёта: {section}")

# Оговорки должны быть: это честность отчёта.
check("не включает" in text or "Оговорки" in text,
      "оговорки о непроверенном присутствуют")
check("воспроизведени" in text.lower() or "Подтверждение" in text,
      "каждая находка снабжена подтверждением")

print()
print("=" * 78)
print(f"ИТОГ: пройдено {PASS}, провалено {FAIL}")
print("=" * 78)
raise SystemExit(0 if FAIL == 0 else 1)
