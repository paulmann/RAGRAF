"""Проверка различения record class и record struct в C#.

Зачем. Грамматика C# даёт один и тот же тип узла (record_declaration) и для
ссылочного record class, и для значимого record struct. Первая версия профиля
отнесла все записи к структурам — и в индексе реального C#-проекта
`public sealed record AlertRule(...)` оказался обычной структурой, хотя это
ссылочный тип. Разница существенная: по ней судят о копировании и передаче
по ссылке, а в отчёте о коде это влияет на выводы.

Проверяется по каждому виду объявления C#, что он получает правильный вид.
"""

from __future__ import annotations

import pathlib
import sys


# Отчёт печатается по-русски, а консоль Windows по умолчанию не UTF-8: без этой
# настройки запуск падал UnicodeEncodeError ('charmap' codec can't encode),
# причём только на Windows и только вне UTF-8-консоли — в CI это валило job.
# Проверено: PYTHONIOENCODING=cp1252 -> exit 1. Настройка делает тест
# независимым от локали/консоли (Python 3.7+: TextIOWrapper.reconfigure).
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf.extract.generic import PROFILES, GenericExtractor  # noqa: E402

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


PROBE = pathlib.Path(__file__).resolve().parent / "_csharp_records.cs"
PROBE.write_text(
    "using System;\n"
    "\n"
    "namespace Demo;\n"
    "\n"
    "public sealed record AlertRule(string RuleId, int Severity);\n"
    "public record Person { public string Name { get; init; } = \"\"; }\n"
    "public readonly record struct Point(int X, int Y);\n"
    "public record struct Pair(int A, int B);\n"
    "public class Service : IDisposable { public void Dispose() { } }\n"
    "public struct Vector { public int X; }\n"
    "public interface IThing { void Do(); }\n"
    "public enum Severity { Low, High }\n"
    "public static class Helper { public static int Add(int a, int b) => a + b; }\n",
    encoding="utf-8", newline="\n",
)

print("=" * 78)
print("РАЗЛИЧЕНИЕ ВИДОВ ОБЪЯВЛЕНИЙ C#")
print("=" * 78)

extractor = GenericExtractor(PROFILES["csharp"])
res = extractor.extract(str(PROBE), PROBE.read_bytes())

kinds: dict[str, str] = {}
for s in res.symbols:
    if s.kind not in ("file", "external", "property", "method", "module"):
        kinds[s.name] = s.kind

print(f"\n  {'объявление':<14} {'вид в индексе':<12} ожидалось")
print("  " + "-" * 50)
EXPECTED = {
    "AlertRule": "class",     # record class — ссылочный тип
    "Person": "class",        # record class с телом
    "Point": "struct",        # record struct — значимый тип
    "Pair": "struct",         # record struct без readonly
    "Service": "class",
    "Vector": "struct",
    "IThing": "interface",
    "Severity": "enum",
    "Helper": "class",
}
for name, want in EXPECTED.items():
    got = kinds.get(name, "—")
    print(f"  {name:<14} {got:<12} {want}")
    check(got == want, f"{name}: вид = {want}", f"получено {got!r}")

print()
check(res.parse_status == "ok", "файл разобран без ошибок",
      f"статус {res.parse_status}")

# Отдельно: методы внутри классов должны быть method, а не function.
methods = {s.name: s.kind for s in res.symbols if s.kind == "method"}
check(methods.get("Dispose") == "method", "метод класса распознан как method",
      f"получено {methods.get('Dispose')!r}")
check(methods.get("Add") == "method", "статический метод класса — method",
      f"получено {methods.get('Add')!r}")

print()
print("=" * 78)
print(f"ИТОГ: пройдено {PASS}, провалено {FAIL}")
print("=" * 78)

# Пробный файл — временный: он не должен остаться в дереве.
PROBE.unlink(missing_ok=True)
raise SystemExit(0 if FAIL == 0 else 1)
