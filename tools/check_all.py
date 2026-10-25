"""Раннер проверок проекта: один запуск, один вердикт.

Зачем. Единого раннера в проекте не было: 77 файлов в `tests/` — самостоятельные
скрипты, и запускать их приходилось по одному (в инструкции AGENTS.md так и
написано). Из-за этого «прогнать проверки» на практике значило «прогнать те,
которые вспомнил», и часть проверок не запускалась месяцами.

Что делает:
  * запускает каждый файл ОТДЕЛЬНЫМ процессом (у проверок свои фикстуры и свои
    коды возврата) с ограничением времени;
  * печатает вердикт по каждой и итог, а в конце — строку для отчёта;
  * делит проверки на группы, потому что не все они равны по требованиям:
      `core`      — ядро индекса, чужие проекты и сервисы не нужны (по умолчанию);
      `external`  — нужны другие корпуса на этой машине (z.batteryservice.ru,
                    SagaAI, RDP-Diagnostic-Tool и т.п.);
      `service`   — нужен запущенный сервис (админ-панель, MCP по HTTP, Joern,
                    модель эмбеддингов, видеокарта);
      `all`       — всё подряд (может включать заведомо красные проверки чужих
                    проектов — см. `--list`).

Почему группы заданы списком, а не «угаданы» по имени файла: угадывание
ошибается молча (проверка попадёт в `core`, потребует чужой корпус и упадёт по
чужой причине). Список видно в `--list`, и его можно поправить одной строкой.

Границы: раннер НЕ заменяет `tools/Invoke-ProjectDoctrine.ps1` (чекер конвенций
по исходникам) — он запускает проверки `tests/`. Для полного вердикта нужно и
то, и другое; в конце раннер об этом напоминает.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYTHON = sys.executable

#: Ядро: проверки, которым не нужны ни другие проекты, ни запущенные сервисы.
CORE = [
    "test_lang_routing.py", "test_generic_extract.py", "test_inheritance.py",
    "test_cpg_call_names.py", "test_graph_algo.py", "test_capability_consistency.py",
    "test_text_from_index.py", "test_dedupe.py", "test_ensure.py",
    "test_mcp.py", "test_mcp_compat.py", "test_access_live.py", "test_mcp_http.py",
    "test_db_index.py", "test_db_marks.py", "test_db_cross.py",
    "test_cli_ops_dispatch.py", "test_task_stats.py", "test_telemetry_facts.py",
    "test_catalog_state.py", "test_budget_stage.py", "test_evidence_lines.py",
    "test_structure_p1.py", "test_polyglot_p13.py", "test_p2_features.py",
    "test_docrole.py", "test_exclude_defaults.py", "test_auto_thresholds.py",
    "test_audit_report.py", "test_dedupe_prompt.py",
    # Проверки ядра, которым не нужны ни чужие корпуса, ни сервисы.
    "test_cli_agent_env.py", "test_codes_in_index.py", "test_csharp_records.py",
    "test_dedupe_stability.py", "test_doc_role_index.py", "test_drift_e2e.py",
    "test_exclude_configs.py", "test_excludes_summary.py", "test_init_project.py",
    "test_mcp_agent.py", "test_node_accounting.py", "test_ragraf_interpreter.py",
    "test_ragraf_mode.py", "test_script_extract.py", "test_signature_cut.py",
    "test_telemetry_agent.py", "test_threshold.py", "test_watch.py",
]

#: Нужны другие корпуса на этой машине (путь и наличие — не наша зона).
EXTERNAL = [
    "test_css_layer.py", "test_class_usage.py", "test_report_facts.py",
    "test_dedupe_effect.py", "test_rdpmon_ops.py", "test_sagaai_catalog.py",
    "test_sagaai_function.py", "test_sagaai_instruction.py",
    "test_sagaai_page_loads.py", "test_sagaai_skill.py", "test_task_stats_sagaai.py",
    "test_watch_live.py", "test_agents_prompt.py", "test_excludes_prompt.py",
    "test_dangerous_fix_final.py", "test_new_langs_indexed.py",
    "test_slash_ragraf.py", "test_fixes_package.py",
]

#: Нужен запущенный сервис, внешний процесс или устройство.
SERVICE = [
    "check_admin_agents.py", "check_admin_dedupe.py", "test_gpu_backend.py",
    "test_gpu_settings.py", "test_gpu_threshold.py", "test_joern_names.py",
    "test_joern_server.py", "test_joern_speed.py", "test_server_reuse.py",
    "test_server_settings.py", "test_server_speed.py", "test_agent_stats_live.py",
    "test_cpg_carry.py", "test_sagaai_child_output_encoding.py",
]


def discover() -> list[str]:
    return sorted(p.name for p in (ROOT / "tests").glob("*.py"))


def group_of(name: str) -> str:
    if name in CORE:
        return "core"
    if name in EXTERNAL:
        return "external"
    if name in SERVICE:
        return "service"
    return "other"


def run_one(name: str, timeout: int) -> dict:
    started = time.perf_counter()
    try:
        proc = subprocess.run([PYTHON, str(ROOT / "tests" / name)], cwd=str(ROOT),
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=timeout)
        out = (proc.stdout or "") + (proc.stderr or "")
        code = proc.returncode
        timed_out = False
    except subprocess.TimeoutExpired as exc:
        out = str(exc.stdout or "") + str(exc.stderr or "")
        code = -1
        timed_out = True
    seconds = time.perf_counter() - started
    match = re.search(r"пройдено[:\s]+(\d+)[^\n]*?провалено[:\s]+(\d+)", out)
    passed = int(match.group(1)) if match else 0
    failed = int(match.group(2)) if match else 0
    tail = [ln.strip() for ln in out.strip().splitlines() if ln.strip()]
    return {
        "name": name, "exit": code, "seconds": round(seconds, 2),
        "timed_out": timed_out, "passed": passed, "failed": failed,
        "summary": tail[-1][:100] if tail else "",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Раннер проверок RAGRAF")
    parser.add_argument("--group", default="core",
                        choices=["core", "external", "service", "all"],
                        help="какие проверки запускать (по умолчанию ядро)")
    parser.add_argument("--timeout", type=int, default=300,
                        help="предел на одну проверку, секунд")
    parser.add_argument("--list", action="store_true",
                        help="показать состав групп и не запускать")
    args = parser.parse_args()

    found = discover()
    if args.list:
        for group in ("core", "external", "service", "other"):
            names = [n for n in found if group_of(n) == group]
            print(f"{group}: {len(names)}")
            for name in names:
                print(f"  {name}")
        unknown = [n for n in found if group_of(n) == "other"]
        if unknown:
            print("\nВНЕ ГРУПП (укажите группу в раннере):")
            for name in unknown:
                print(f"  {name}")
        return 0

    if args.group == "all":
        selected = found
    else:
        selected = [n for n in found if group_of(n) == args.group]
    if not selected:
        print(f"проверок в группе {args.group} не найдено")
        return 1

    print(f"группа: {args.group} | проверок: {len(selected)} | предел на "
          f"проверку: {args.timeout} с")
    results = []
    started = time.perf_counter()
    for name in selected:
        result = run_one(name, args.timeout)
        results.append(result)
        if result["timed_out"]:
            flag = "ПРЕДЕЛ"
        elif result["exit"] == 0:
            flag = "ok"
        else:
            flag = f"ПРОВАЛ({result['exit']})"
        print(f"  {flag:12} {name:42} {result['seconds']:7.1f} с  "
              f"{result['passed']} пройдено, {result['failed']} провалено",
              flush=True)

    total_seconds = time.perf_counter() - started
    ok = [r for r in results if r["exit"] == 0]
    bad = [r for r in results if r["exit"] != 0]
    checks_passed = sum(r["passed"] for r in results)
    checks_failed = sum(r["failed"] for r in results)
    print()
    print("=" * 78)
    print(f"ИТОГ: файлов {len(results)}, прошло {len(ok)}, провалено {len(bad)} | "
          f"проверок {checks_passed} пройдено, {checks_failed} провалено | "
          f"{total_seconds:.1f} с")
    for result in bad:
        note = result["summary"] or ("предел времени" if result["timed_out"] else "")
        print(f"  ПРОВАЛ: {result['name']}: {note}")
    print("Строка для отчёта: "
          f"проверок {len(results)}: пройдено {len(ok)}, провалено {len(bad)}")
    print("Полный вердикт = этот раннер + "
          "tools/Invoke-ProjectDoctrine.ps1 (конвенции исходников)")
    print("=" * 78)
    return 0 if not bad else 1


if __name__ == "__main__":
    raise SystemExit(main())
