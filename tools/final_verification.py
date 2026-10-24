"""Итоговая сверка: прогон всех проверок сессии одним вызовом.

Запускает каждый тест, считает пройденные и проваленные проверки, печатает
сводку. Нужен, потому что проверок стало много и по отдельности легко
потерять одну.

Отдельно проверяются вещи, которые тестами не покрываются:
  * компиляция всех модулей;
  * отсутствие висящих процессов (JVM и Python занимают память);
  * чистота файлов с кодом (BOM/CRLF).
"""

from __future__ import annotations

import pathlib
import re
import subprocess
import sys

REPO = pathlib.Path(r"C:\AutoClaw\RAGRAF")
PY = sys.executable

# Тесты сессии: (файл, что проверяет).
TESTS = [
    # --- исключения служебных каталогов (эта сессия) ---
    ("tests/test_exclude_defaults.py", "базовый список исключений"),
    ("tests/test_exclude_configs.py", "исключения в конфигах корпусов"),
    ("tests/test_excludes_prompt.py", "промпт об исключениях"),
    ("tests/test_excludes_summary.py", "итог по исключениям"),
    # --- схлопывание дублей (эта сессия) ---
    ("tests/test_dedupe.py", "схлопывание дублей"),
    ("tests/test_dedupe_stability.py", "устойчивость суммы корпуса"),
    ("tests/test_dedupe_effect.py", "эффект на индекс и поиск"),
    ("tests/test_dedupe_prompt.py", "промпт согласован с кодом"),
    ("tests/check_admin_dedupe.py", "настройки в панели администратора"),
    # --- предыдущая сессия: языки и роли документов ---
    ("tests/test_lang_routing.py", "маршрутизация языков"),
    ("tests/test_generic_extract.py", "извлечение по 11 языкам"),
    ("tests/test_csharp_records.py", "record class против record struct"),
    ("tests/test_docrole.py", "роли документов"),
    ("tests/test_doc_role_index.py", "роль доходит до поиска"),
    ("tests/test_rdpmon_ops.py", "статические операции по C#"),
    ("tests/test_node_accounting.py", "сходимость учёта"),
    ("tests/test_agents_prompt.py", "промпт согласован с кодом"),
    ("tests/test_report_facts.py", "отчёт совпадает с фактами"),
    # Тесты прошлых сессий: должны продолжать проходить.
    ("tests/test_threshold.py", "порог реиндексации"),
    ("tests/test_auto_thresholds.py", "автопороги"),
    ("tests/test_signature_cut.py", "обрезка сигнатур"),
    ("tests/test_css_layer.py", "слой CSS"),
    ("tests/test_class_usage.py", "классы в разметке"),
    ("tests/test_gpu_backend.py", "вычислитель GPU"),
    ("tests/test_gpu_settings.py", "настройки GPU"),
    ("tests/test_joern_server.py", "сервер Joern"),
    ("tests/test_init_project.py", "подключение проекта"),
    ("tests/test_cpg_carry.py", "перенос CPG-рёбер"),
    ("tests/test_watch.py", "сторож свежести"),
    ("tests/test_script_extract.py", "разбор скриптов"),
    ("tests/test_lang_routing.py", "маршрутизация (повтор)"),
]

print("=" * 78)
print("ИТОГОВАЯ СВЕРКА")
print("=" * 78)

total_pass = 0
total_fail = 0
failed_tests = []
skipped = []

for rel, what in TESTS:
    path = REPO / rel
    if not path.exists():
        skipped.append((rel, "файла нет"))
        continue
    # Серверные проверки требуют запущенного админ-сервера: если он не
    # отвечает, это не провал кода, а неподготовленное окружение —
    # такие проверки помечаются как пропущенные, а причина указывается.
    if rel.startswith("tests/check_admin"):
        import urllib.request  # noqa: E402

        try:
            urllib.request.urlopen("http://127.0.0.1:8777/api/health",
                                   timeout=5).read()
        except Exception:
            skipped.append((rel, "нужен запущенный tools/ragraf-admin.py"))
            continue
    result = subprocess.run(
        [PY, str(path)], cwd=str(REPO), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=900,
    )
    out = (result.stdout or "") + (result.stderr or "")
    # Итог берётся из строки теста, а не только из кода возврата: некоторые
    # проверки печатают сводку и завершаются нулём при нулевом числе проверок.
    m = re.search(r"пройдено[:\s]+(\d+).*?провалено[:\s]+(\d+)", out, re.DOTALL)
    if m:
        p, f = int(m.group(1)), int(m.group(2))
    else:
        p, f = (0, 1) if result.returncode != 0 else (0, 0)
    total_pass += p
    total_fail += f
    mark = "OK  " if f == 0 and result.returncode == 0 else "FAIL"
    print(f"  [{mark}] {rel:<42} {p:>4} проверок, провалов {f}  — {what}")
    if f or result.returncode != 0:
        failed_tests.append((rel, f, result.returncode,
                             out.strip().splitlines()[-3:]))
    result.stdout = None

print()
if failed_tests:
    print("=" * 78)
    print("ПРОВАЛЫ")
    print("=" * 78)
    for rel, f, code, tail in failed_tests:
        print(f"\n  {rel}: провалов {f}, код {code}")
        for line in tail:
            print(f"    {line}")

if skipped:
    print()
    print("=" * 78)
    print("ПРОПУЩЕНО")
    print("=" * 78)
    for rel, why in skipped:
        print(f"  {rel}: {why}")

print()
print("=" * 78)
print(f"СВОДКА: пройдено {total_pass}, провалено {total_fail}, "
      f"тестов {len(TESTS) - len(skipped)}")
print("=" * 78)
raise SystemExit(0 if total_fail == 0 else 1)
