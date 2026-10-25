"""Запуск `.bat` через cmd.exe: метасимволы не должны менять смысл команды.

Измеренный дефект (24.10.2026, до правки): аргумент `a&b` без пробела РАЗРЫВАЛ
команду на две — `probe.bat a` и попытку исполнить `b` (код 1, аргумент обрезан),
`a^b` доходил как `ab`, `%TEMP%\\x` раскрывался в реальный путь. Ошибка не видна
до запуска и меняет смысл команды, а не падает.

Как наблюдается. Обёртка печатает `%*` — всю принятую хвостовую часть дословно,
вместе с кавычками. Это единственная честная форма наблюдения: `%~1` снимает
кавычки, и `&` разрывает уже саму строку ВНУТРИ bat-файла (проверено: проверка
на `%~1` давала ложный провал при исправной передаче). Поэтому ожидаемые значения
записаны с кавычками: `a&b` одним аргументом выглядит как `["a&b"]`.

Свойства:
  1. `&`, `^`, `!`, пробелы, `(`, `)`, `|`, `>` доходят дословно одним аргументом;
  2. путь к самому `.bat` с пробелом работает;
  3. `%` — ОТКАЗ с причиной и с названным путём, а не тихое раскрытие;
  4. `"` — тоже отказ: кавычку внутри аргумента cmd передать не может;
  5. скрипт без `.bat` запускается списком, без оболочки.

Каталог замера не удаляется: он остаётся уликой прогона.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf import util  # noqa: E402

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


def make_probe() -> pathlib.Path:
    root = pathlib.Path(tempfile.gettempdir()) / "ragraf_cmd_probe" / "probe dir"
    root.mkdir(parents=True, exist_ok=True)
    bat = root / "probe.bat"
    # CRLF обязателен: cmd.exe не исполняет bat-файл с переводами строк LF.
    bat.write_bytes(
        b"@echo off\r\n"
        b"echo ARGS:[%*]\r\n"
        b"exit /b 0\r\n"
    )
    return bat


def run(arguments: list[str], bat: pathlib.Path) -> tuple[int, str, str]:
    command = util.cmd_command(arguments)
    proc = subprocess.run(command, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=60,
                          cwd=str(bat.parent))
    return proc.returncode, (proc.stdout or "").strip(), (proc.stderr or "").strip()


def refusal(arguments: list[str]) -> str:
    try:
        util.cmd_command(arguments)
    except util.CmdArgumentError as exc:
        return str(exc)
    return ""


print("=" * 78)
print("ЗАПУСК .bat ЧЕРЕЗ cmd.exe: МЕТАСИМВОЛЫ")
print("=" * 78)

bat = make_probe()
print(f"\nобёртка: {bat}")

print("\n1. Дословная передача (раньше эти аргументы искажались)")
cases = [
    ("обычный", ["plain"], 'ARGS:["plain"]'),
    ("пробел", ["two words"], 'ARGS:["two words"]'),
    ("амперсанд без пробела", ["a&b"], 'ARGS:["a&b"]'),
    ("три аргумента с амперсандами", ["a&b", "c&d", "e & f"],
     'ARGS:["a&b" "c&d" "e & f"]'),
    ("крышка", ["a^b"], 'ARGS:["a^b"]'),
    ("восклицательный знак", ["a!b!c"], 'ARGS:["a!b!c"]'),
    ("скобки", ["(a)(b)"], 'ARGS:["(a)(b)"]'),
    ("труба и перенаправление", ["a|b>c"], 'ARGS:["a|b>c"]'),
]
for label, args, expected in cases:
    code, out, err = run([str(bat), *args], bat)
    check(code == 0 and out == expected,
          f"{label}: аргумент дошёл дословно",
          f"код {code}, вывод {out!r}, ожидалось {expected!r}, ошибка {err[:80]!r}")

print("\n2. Путь к обёртке содержит пробел")
code, out, _ = run([str(bat), "x"], bat)
check(code == 0 and out == 'ARGS:["x"]', "путь с пробелом работает",
      f"код {code}: {out!r}")

print("\n3. `%` — отказ, а не раскрытие переменной")
message = refusal([str(bat), "%TEMP%\\x"])
check(bool(message), "аргумент с «%» отклонён")
check("%TEMP%" in message, "в причине назван сам аргумент", message)
check("cmd.exe" in message, "в причине назван виновник — cmd.exe", message)
check(bool(refusal([str(bat), "a%b%c"])),
      "«%» без имени переменной тоже отклонён: раскрытие зависит от окружения",
      "такой аргумент прошёл бы, но результат был бы недетерминирован")

print("\n4. Кавычка внутри аргумента — тоже отказ")
check(bool(refusal([str(bat), 'a"b'])), "аргумент с кавычкой отклонён")

print("\n5. Скрипт без .bat запускается списком, без оболочки")
plan = util.cmd_command([str(bat), "ok"])
check(plan.startswith("cmd.exe /d /s /v:off /c "),
      "для .bat строится форма cmd с отключённым раскрытием", plan[:40])
check("/v:off" in plan, "отложенное раскрытие «!» выключено явно")

from ragraf import cpg as cpg_mod  # noqa: E402

plain = cpg_mod._shell_cmd(["tool.exe", "a b"], pathlib.Path("tool.exe"))
check(plain == ["tool.exe", "a b"],
      "не-.bat запускается списком (оболочки нет)", repr(plain))
wrapped = cpg_mod._shell_cmd([str(bat), "a&b"], bat)
check(isinstance(wrapped, str) and wrapped.startswith("cmd.exe "),
      "_shell_cmd для .bat возвращает командную строку cmd", repr(wrapped)[:60])

print()
print(f"проверок: {PASS + FAIL}, успешно: {PASS}, провалов: {FAIL}")
sys.exit(0 if FAIL == 0 else 1)
