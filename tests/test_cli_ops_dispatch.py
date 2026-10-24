"""Проверка: каждая операция из OPS действительно диспетчеризуется в CLI.

Что здесь закрыто и почему это отдельный тест.

Дефект, из-за которого тест появился. `policy_trace` и `class_usage` были
объявлены в `OPS` (и потому принимались argparse как допустимое значение
`query <op>`), реализованы в фасаде и отдавались через MCP — но в диспетчере
`dispatch_op` ветвей для них не было. Вызов доходил до конца функции и падал
в `raise SystemExit(EXIT_USAGE)`, то есть завершался кодом 2 «неверный
аргумент». Внешний инструмент (функция RAGRAF в SagaAI) на любой ненулевой
код возвращает `ok:false`, поэтому операция выглядела как поломка
инструмента, хотя была исправна.

Почему не поймали раньше. Существующие тесты проверяли наличие имени
в списке (`"class_usage" in cli_src`) и доступность через MCP — то есть
наличие декларации, а не исполнение через CLI. Такая проверка проходит
на сломанном диспетчере: имя в списке есть, ветви нет.

Что проверяется:
  1. каждая операция из OPS проходит диспетчер и НЕ падает в SystemExit;
  2. «не найдено» — обычный ответ с кодом 0, а не ошибка;
  3. значение --mode принимается по операции, чужое — отвергается с кодом 2;
  4. --help и русский вывод не зависят от локали процесса;
  5. маркер .interpreter не портится неудачным запуском.

Запуск: любой интерпретатор с зависимостями RAGRAF (PyYAML).
"""

from __future__ import annotations

import contextlib
import io
import os
import pathlib
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

# Корпус для проверки: берётся тот, у которого индекс заведомо есть.
# `ragraf.yaml` описывает сам RAGRAF и своего `.ragraf` не имеет — фасад
# на нём не открывается, поэтому для прогона операций нужен проект
# с построенным индексом.
CONFIG = REPO / "config" / "z-batteryservice.yaml"

from ragraf import cli  # noqa: E402
from ragraf.config import load as load_config  # noqa: E402
from ragraf.facade import Facade  # noqa: E402

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


# Аргументы, с которыми каждая операция осмысленна. Операции без аргументов
# (index_status, usage, freshness, catalog, repo_map, routes, calibrate)
# вызываются как есть.
ARGS_FOR: dict[str, list[str]] = {
    "search": ["некий текст запроса"],
    "symbol": ["--name", "z-small"],
    "references": ["--fqn", "src/Bootstrap.php:z_boot"],
    "callers": ["--fqn", "src/Bootstrap.php:z_boot"],
    "callees": ["--fqn", "src/Bootstrap.php:z_boot"],
    "impact": ["--target", "z_boot"],
    "slice": ["--fqn", "src/Bootstrap.php:z_boot"],
    "code": ["--fqn", "src/Bootstrap.php:z_boot"],
    "schema": ["--table", "users"],
    "table_usage": ["--table", "users"],
    "live_read": ["--path", "src/Bootstrap.php"],
    "policy_trace": ["--name", "z_agr_can_act"],
    "class_usage": ["--mode", "missing"],
    "write_check": ["--table", "users", "--columns", "id,name"],
    "drift_ack": ["--entry-ids", ""],
    "drift_report": [],
    # Кросс-корпусный поиск: без аргументов операция отвергает вызов по делу,
    # поэтому имя символа задаётся явно.
    "neighbors": ["--name", "z_boot"],
}


def make_args(op: str) -> object:
    """Разобрать аргументы так же, как это делает настоящий CLI."""
    parser = cli.build_parser()
    argv = ["-p", str(CONFIG), "query", op]
    argv += ARGS_FOR.get(op, [])
    return parser.parse_args(argv)


print("=" * 78)
print("1. КАЖДАЯ ОПЕРАЦИЯ ИЗ OPS ПРОХОДИТ ДИСПЕТЧЕР")
print("=" * 78)
print("  Проверяется не наличие имени в списке, а сам факт исполнения:")
print("  SystemExit в диспетчере означает, что ветви для операции нет.")
print()

cfg = load_config(str(CONFIG))
undispatched: list[str] = []
for op in cli.OPS:
    args = make_args(op)
    facade = Facade(cfg, tool="test", telemetry=False, watch=False)
    try:
        cli.dispatch_op(facade, args)
        outcome = "выполнена"
    except SystemExit as exc:
        undispatched.append(op)
        outcome = f"SystemExit({exc.code}) — ветви в диспетчере нет"
    except Exception as exc:  # ошибка данных допустима, отсутствие ветви — нет
        outcome = f"{type(exc).__name__}: {exc}"
    finally:
        facade.close()
    print(f"    {op:<14} {outcome}")

check(not undispatched, "все операции из OPS имеют ветвь в диспетчере",
      f"без ветви: {undispatched}")

print()
print("=" * 78)
print("2. «НЕ НАЙДЕНО» — ОБЫЧНЫЙ ОТВЕТ, А НЕ ОШИБКА (коды выхода)")
print("=" * 78)

# Пустой результат не должен выглядеть для вызывающего агента поломкой:
# функция RAGRAF в SagaAI считает ненулевой код отказом инструмента.
empty = {"op": "symbol", "found": False, "error": "символ не найден: zzz"}
check(cli._exit_code("symbol", empty) == cli.EXIT_OK,
      "пустой результат отдаётся кодом 0 с found=false")

# Отказ слоя остаётся ненулевым: операция есть, но выполнить её сейчас нельзя.
no_cpg = {"op": "slice", "found": False, "capability": "degraded",
          "error": "срез недоступен: ревизия в режиме degraded (CPG не построена)"}
check(cli._exit_code("slice", no_cpg) == cli.EXIT_UNAVAILABLE,
      "slice при degraded отдаётся кодом 3 (отказ слоя)")

# Полный ответ — код 0.
full = {"op": "slice", "found": True, "capability": "full"}
check(cli._exit_code("slice", full) == cli.EXIT_OK,
      "slice при full отдаётся кодом 0")

check(cli.EXIT_NOT_FOUND == cli.EXIT_UNAVAILABLE,
      "старое имя EXIT_NOT_FOUND означает теперь отказ слоя")

print()
print("=" * 78)
print("3. ЗНАЧЕНИЕ --mode ЗАВИСИТ ОТ ОПЕРАЦИИ")
print("=" * 78)

check(cli.resolve_mode("table_usage", None) == "all",
      "table_usage по умолчанию: all")
check(cli.resolve_mode("table_usage", "write") == "write",
      "table_usage принимает write (команда из инструкции проекта)")
check(cli.resolve_mode("table_usage", "read") == "read",
      "table_usage принимает read")
check(cli.resolve_mode("class_usage", "missing") == "missing",
      "class_usage принимает missing (команда из инструкции проекта)")
check(cli.resolve_mode("class_usage", "unused") == "unused",
      "class_usage принимает unused")
check(cli.resolve_mode("impact", "hard") == "hard",
      "impact принимает hard")
check(cli.resolve_mode("search", None) == "full",
      "операция без режимов получает значение по умолчанию")

for op, bad in (("table_usage", "hard"), ("class_usage", "hard"), ("search", "write")):
    try:
        cli.resolve_mode(op, bad)
        check(False, f"{op} отвергает чужое значение {bad!r}", "принято")
    except ValueError as exc:
        check(True, f"{op} отвергает чужое значение {bad!r}")
        del exc

# Аргументы уровня CLI: верная команда не должна отклоняться argparse.
parser = cli.build_parser()
ok_args = parser.parse_args(["-p", "x.yaml", "query", "class_usage", "--mode", "missing"])
check(ok_args.mode == "missing", "argparse принимает --mode missing")

print()
print("=" * 78)
print("4. ВЫВОД НЕ ЗАВИСИТ ОТ ЛОКАЛИ ПРОЦЕССА")
print("=" * 78)

# Воспроизведение дефекта из лога: cp1252 в окружении процесса давал
# UnicodeEncodeError на русском тексте справки, и --help падал с кодом 1.
tool = REPO / "tools" / "ragraf.py"
env = dict(os.environ)
env["PYTHONIOENCODING"] = "cp1252"
proc = subprocess.run(
    [sys.executable, str(tool), "--help"],
    capture_output=True, text=True, encoding="utf-8", errors="replace",
    env=env, cwd=str(REPO),
)
check(proc.returncode == 0, "--help работает при PYTHONIOENCODING=cp1252",
      f"код {proc.returncode}")
check("RAGRAF" in (proc.stdout or ""), "справка содержит текст, а не ошибку",
      repr((proc.stdout or "")[:60]))
check("UnicodeEncodeError" not in (proc.stderr or ""),
      "в выводе нет UnicodeEncodeError")

print()
print("=" * 78)
print("5. МАРКЕР ИНТЕРПРЕТАТОРА НЕ ПОРТИТСЯ НЕУДАЧНЫМ ЗАПУСКОМ")
print("=" * 78)

marker = REPO / ".interpreter"
before = marker.read_text(encoding="utf-8").strip() if marker.is_file() else ""
print(f"  маркер сейчас: {before or '<нет>'}")

# Неудачный запуск: тот же скрипт, но код возврата ненулевой (нет PyYAML
# либо несуществующий проект). Маркер должен остаться прежним.
bad = subprocess.run(
    [sys.executable, str(tool), "-p", "config/zzz_no_such_config.yaml", "status"],
    capture_output=True, text=True, encoding="utf-8", errors="replace",
    cwd=str(REPO),
)
check(bad.returncode != 0, "неудачный запуск действительно завершился ошибкой",
      f"код {bad.returncode}")
after = marker.read_text(encoding="utf-8").strip() if marker.is_file() else ""
check(after == before, "неудачный запуск не изменил маркер",
      f"было {before!r}, стало {after!r}")

print()
print("=" * 78)
print("6. КОДЫ ВОЗВРАТА ЖИВОГО CLI (не только функции)")
print("=" * 78)

def run_cli(*argv: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(tool), "--json", "-p", str(CONFIG), *argv],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(REPO),
    )


r = run_cli("query", "symbol", "--name", "ZZZ_no_such_symbol_ZZZ")
check(r.returncode == cli.EXIT_OK,
      "живой CLI: пустой результат — код 0", f"код {r.returncode}")

r = run_cli("query", "class_usage", "--mode", "missing")
check(r.returncode == cli.EXIT_OK,
      "живой CLI: class_usage --mode missing работает", f"код {r.returncode}")

r = run_cli("query", "class_usage", "--mode", "hard")
check(r.returncode == cli.EXIT_USAGE,
      "живой CLI: чужое значение --mode — код 2", f"код {r.returncode}")

print()
print("=" * 78)
print("7. СВОДКА USAGE НЕ ЗАВИСИТ ОТ ФЛАГА ТЕЛЕМЕТРИИ")
print("=" * 78)

# Дефект: при telemetry=False сводка читалась «читателем» без подготовки
# схемы, и на корпусе, где колонка task ещё не добавлена, падала с
# `no such column: task`. Ответ зависел от служебного флага, а не от данных.
with_telemetry = Facade(cfg, tool="test", telemetry=True, watch=False)
try:
    a = with_telemetry.usage_summary(since_session=False)
    ok_a = "calls_total" in a
finally:
    with_telemetry.close()

without_telemetry = Facade(cfg, tool="test", telemetry=False, watch=False)
try:
    b = without_telemetry.usage_summary(since_session=False)
    ok_b = "calls_total" in b
finally:
    without_telemetry.close()

check(ok_a, "сводка читается при telemetry=True")
check(ok_b, "сводка читается при telemetry=False (была ошибка схемы)")
check(a.get("calls_total") == b.get("calls_total"),
      "оба режима дают один и тот же итог",
      f"{a.get('calls_total')} против {b.get('calls_total')}")

print()
print("=" * 78)
print(f"ИТОГ: пройдено {PASS}, провалено {FAIL}")
print("=" * 78)
raise SystemExit(0 if FAIL == 0 else 1)
