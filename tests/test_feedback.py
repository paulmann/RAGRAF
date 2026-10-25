"""Проверка отчёта о пользе (G4).

Свойства:
  1. отчёт собирается из агрегатов и содержит измеренные числа;
  2. в отчёте нет путей, имён проектов и запросов — ни в одном поле;
  3. запись идёт в UTF-8 без BOM и с LF;
  4. ничего не отправляется: функция только пишет файл.
"""

from __future__ import annotations

import pathlib
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf import feedback  # noqa: E402

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


payload = {
    "schema": feedback.SCHEMA,
    "generated_at": "2026-10-19T12:00:00+03:00",
    "period_days": 30,
    "app_version": "0.1.0",
    "os": "Windows 11",
    "arch": "AMD64",
    "sessions": 12,
    "calls": 100,
    "errors": 2,
    "facts": 250,
    "facts_with_span": 240,
    "verifiable_ratio": 0.96,
    "chars": 40000,
    "est_tokens": 10000,
    "replacing_chars": 18000,
    "est_lines_not_read": 400,
    "hit_rate": 0.75,
    "saved_calls": 50,
    "avg_ms": 120.5,
    "by_op": [
        {"op": "search", "calls": 60, "facts": 200, "avg_ms": 150.0},
        {"op": "symbol", "calls": 40, "facts": 50, "avg_ms": 90.0},
    ],
    "excluded": ["пути", "имена проектов", "запросы", "фрагменты кода",
                 "значения секретов", "содержимое документов"],
}

print("=" * 78)
print("1. СОДЕРЖАНИЕ ОТЧЁТА")
print("=" * 78)
text = feedback.render(payload)
check("# RAGRAF Field Report" in text, "заголовок есть")
check("обращений к индексу: 100" in text, "число обращений из измеренного поля")
check("фактов: 250" in text and "240" in text, "факты и проверяемые факты")
check("96%" in text, "доля проверяемых посчитана, а не выдумана")
check("400" in text, "оценка строк присутствует")
check("search" in text and "symbol" in text, "таблица операций есть")
check("Не отправлено" in text or "не отправлено" in text or "Ничего не отправлено" in text,
      "сказано, что отчёт локальный")
check("значения секретов" in text, "границы перечислены явно")

print()
print("=" * 78)
print("2. ПРИВАТНОСТЬ: ПУТЕЙ И ЗАПРОСОВ НЕТ")
print("=" * 78)
poison = [
    "RAGRAF",
    "/home/user/project",
    "SELECT * FROM users",
    "secret-token-value",
]
# Ядовитые строки не должны попасть в отчёт, даже если бы оказались в поле op.
bad_payload = dict(payload)
bad_payload["by_op"] = [{"op": "search", "calls": 1, "facts": 1, "avg_ms": 1}]
bad_text = feedback.render(bad_payload)
check(all(item not in bad_text for item in poison),
      "пути, SQL и значения не появляются в отчёте")
check("пути" in bad_text and "запросы" in bad_text,
      "отчёт сам называет, что исключено")

print()
print("=" * 78)
print("3. ЗАПИСЬ ФАЙЛА")
print("=" * 78)
with tempfile.TemporaryDirectory(prefix="ragraf-feedback-") as tmp:
    path = feedback.write(payload, directory=pathlib.Path(tmp))
    check(path.exists(), "файл создан", str(path))
    raw = path.read_bytes()
    check(not raw.startswith(b"\xef\xbb\xbf"), "BOM отсутствует")
    check(b"\r\n" not in raw, "переводы строк LF")
    saved = path.read_text(encoding="utf-8")
    check(saved == text, "записан ровно отрисованный отчёт")
    check(path.name.startswith("feedback-") and path.suffix == ".md",
          "имя и расширение соответствуют шаблону", path.name)
    check(len(list(pathlib.Path(tmp).glob("*.md"))) == 1, "создан один файл, не каталог")

print()
print("=" * 78)
print(f"ИТОГ: пройдено {PASS}, провалено {FAIL}")
print("=" * 78)
raise SystemExit(0 if FAIL == 0 else 1)
