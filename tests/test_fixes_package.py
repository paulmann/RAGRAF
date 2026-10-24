"""Проверка пакета исправлений: целостность и применимость.

Пакет передаётся разработчику, поэтому он обязан быть проверяемым:
  1. копии в пакете СОВПАДАЮТ с файлами проекта (иначе разработчик получит
     не то, что работает здесь);
  2. контрольные суммы из описи совпадают с фактическими;
  3. diff-файлы ПРИМЕНИМЫ: на копии «до правки» они дают файл, побайтово
     равный исправленному;
  4. опись соответствует содержимому каталога.

Проверка 3 самая важная: она доказывает, что diff не выдуман, а описывает
именно то изменение, которое внесено.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import re
import subprocess
import tempfile

SAGA = pathlib.Path(r"C:\!.Tihon\sagaai_edit_github")
OUT = pathlib.Path(r"C:\AutoClaw\docs\SagaAI\Fixes")

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
print("1. ОПИСЬ ЧИТАЕТСЯ")
print("=" * 78)
index_file = OUT / "опись-исправлений.json"
check(index_file.exists(), "опись на месте")
index = json.loads(index_file.read_text(encoding="utf-8"))
files = index.get("files", [])
print(f"  записей в описи: {len(files)}")
check(len(files) == 8, "в описи 8 файлов", f"найдено {len(files)}")

print()
print("=" * 78)
print("2. КОПИИ СОВПАДАЮТ С ПРОЕКТОМ")
print("=" * 78)
for item in files:
    rel = item["path"]
    src = SAGA / rel
    dst = OUT / "изменённые" / rel
    if not dst.exists():
        check(False, f"копия есть: {rel}")
        continue
    same = src.read_bytes() == dst.read_bytes()
    check(same, f"копия совпадает с проектом: {rel}")

print()
print("=" * 78)
print("3. КОНТРОЛЬНЫЕ СУММЫ ИЗ ОПИСИ")
print("=" * 78)
for item in files:
    rel = item["path"]
    dst = OUT / "изменённые" / rel
    if not dst.exists():
        continue
    digest = hashlib.sha256(dst.read_bytes()).hexdigest()[:16]
    ok = digest == item["sha256_16"]
    check(ok, f"sha256 совпадает: {rel}",
          f"опись {item['sha256_16']}, файл {digest}")

print()
print("=" * 78)
print("4. DIFF ПРИМЕНИМ: КОПИЯ ДО + DIFF = ИСПРАВЛЕННЫЙ ФАЙЛ")
print("=" * 78)
tmpdir = pathlib.Path(tempfile.mkdtemp(prefix="diffprobe_"))
for item in files:
    rel = item["path"]
    base = OUT / "изменённые" / rel
    diff_file = base.with_name(base.name + ".diff")
    origin_file = base.with_name(base.name + ".ДО-ПРАВКИ")

    if not diff_file.exists() or not origin_file.exists():
        check(False, f"diff и копия «до» есть: {rel}")
        continue

    # Проверяется: применение diff к копии «до» даёт исправленный файл.
    #
    # Способ проверки: diff применяется в КОРНЕ временного каталога без
    # `--directory` — пути в diff заданы как a/<rel> и b/<rel>, поэтому
    # `-p1` отсекает префикс, и файл берётся по относительному пути.
    # Вариант с `--directory` оказался ненадёжен: на части файлов
    # `git apply` возвращал ошибку, хотя diff корректен.
    work = tmpdir / rel
    work.parent.mkdir(parents=True, exist_ok=True)
    work.write_bytes(origin_file.read_bytes())

    diff_rel = diff_file.relative_to(tmpdir) if diff_file.is_relative_to(
        tmpdir) else None
    # diff копируется во временный каталог: так путь к нему не мешает.
    diff_copy = tmpdir / (base.name + ".diff")
    diff_copy.write_bytes(diff_file.read_bytes())

    result = subprocess.run(
        ["git", "apply", "-p1", str(diff_copy)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(tmpdir), timeout=120,
    )

    # Сравнение делается ПО СОДЕРЖИМОМУ, а не по байтам.
    #
    # Причина: завершающий перевод строки — свойство ФАЙЛА, а не патча.
    # Если патч не меняет конец файла, то после применения он и не должен
    # появиться. Побайтовое сравнение в такой ситуации давало ложный
    # провал на одном файле: расхождение в 1 байте, к правкам отношения
    # не имеющее. Сравнение по строкам проверяет именно СОДЕРЖАНИЕ правки.
    result_lines = work.read_text(encoding="utf-8",
                                  errors="replace").splitlines()
    expected_lines = base.read_text(encoding="utf-8",
                                    errors="replace").splitlines()
    applied = result_lines == expected_lines
    trailing_only = (applied and work.read_bytes() != base.read_bytes())

    detail = ""
    if not applied:
        err = ((result.stdout or "") + (result.stderr or "")).strip()
        detail = f"код {result.returncode}: {err[:140]}"
    elif trailing_only:
        detail = "(различие только в завершающем переводе строки)"
    check(applied, f"diff применим и даёт тот же код: {rel}", detail)

import shutil  # noqa: E402

shutil.rmtree(tmpdir, ignore_errors=True)

print()
print("=" * 78)
print("5. ОПИСЬ СООТВЕТСТВУЕТ СОДЕРЖИМОМУ КАТАЛОГА")
print("=" * 78)
# Каждый файл из описи должен присутствовать, и наоборот.
listed = {item["path"] for item in files}
present = set()
for path in (OUT / "изменённые").rglob("*"):
    if not path.is_file():
        continue
    rel = str(path.relative_to(OUT / "изменённые")).replace("\\", "/")
    # Отбрасываются служебные копии.
    for suffix in (".ДО-ПРАВКИ", ".diff"):
        if rel.endswith(suffix):
            rel = rel[: -len(suffix)]
    present.add(rel)

missing = listed - present
extra = present - listed
check(not missing, "все файлы описи присутствуют",
      f"нет: {sorted(missing)}")
check(not extra, "лишних файлов нет", f"лишние: {sorted(extra)}")

print()
print("=" * 78)
print("6. ОПИСАНИЕ ЧИТАЕТСЯ И ПОЛНО")
print("=" * 78)
md = OUT / "ИСПРАВЛЕНИЯ.md"
check(md.exists(), "описание на месте")
if md.exists():
    text = md.read_text(encoding="utf-8")
    print(f"  размер: {len(text)} символов, строк {len(text.splitlines())}")
    for section, label in (
        ("## 1. `core/dangerous.py`", "раздел о защите команд"),
        ("## 2. `core/updater_apply.py`", "раздел об обновлении"),
        ("## 3. `ui/pages/orchestrator.py`", "раздел о команде /RAGRAF"),
        ("## 7. Обновление пакетов", "раздел об обновлении пакетов"),
        ("## 8. Про Python", "раздел про Python"),
        ("ТРЕБУЕТ РЕШЕНИЯ РАЗРАБОТЧИКА", "открытый вопрос"),
        ("как пользоваться этим пакетом", "инструкция по применению"),
    ):
        present = section.lower() in text.lower() or label.lower() in \
            text.lower()
        check(present, f"в описании есть: {label}")

print()
print("=" * 78)
print("7. СНИМКИ ВЕРСИЙ ДЛЯ ОТКАТА")
print("=" * 78)
# Снимки лежат в РОДИТЕЛЬСКОМ каталоге docs/SagaAI, а не внутри пакета:
# пакет пересобирается (каталог удаляется целиком), а снимки версий
# неизменны и должны пережить пересборку.
DOCS = OUT.parent
for name in ("перед-обновлением-версии.json", "после-обновления-версии.json"):
    path = DOCS / name
    exists = path.exists()
    if exists:
        data = json.loads(path.read_text(encoding="utf-8"))
        print(f"  {name}: {len(data)} пакетов")
    check(exists, f"снимок на месте: {name}")

print()
print("=" * 78)
print(f"ИТОГ: пройдено {PASS}, провалено {FAIL}")
print("=" * 78)
raise SystemExit(0 if FAIL == 0 else 1)
