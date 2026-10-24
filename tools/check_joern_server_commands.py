"""Проверка: можно ли через сервер Joern строить и экспортировать граф.

Сервер работает: запрос к /query-sync занимает 22 мс против 5,53 с на старт
отдельного процесса. Это открывает путь к экономии 11 с на индексацию.

Но прежде чем переписывать построение графа на сервер, надо выяснить главное:
умеет ли сервер делать то же, что делают два отдельных запуска:
  1. ПОСТРОИТЬ граф из исходников (сейчас: joern-parse);
  2. ЭКСПОРТИРОВАТЬ граф в файлы (сейчас: joern-export).

В Joern это делается командами CPGQL внутри оболочки:
  * importCode / importCpg — построение и загрузка;
  * save — сохранение;
  * экспорт в neo4jcsv — отдельная утилита, но, возможно, есть команда.

Проверяются эти команды через сервер.
"""

from __future__ import annotations

import json
import pathlib
import time
import urllib.error
import urllib.request

PORT = 9505
BASE = f"http://127.0.0.1:{PORT}"
REPO = pathlib.Path(__file__).resolve().parents[1]

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


def query(q: str, timeout: int = 600) -> tuple[bool, str, float]:
    """Выполнить запрос CPGQL через сервер. Возвращает (успех, вывод, секунды)."""
    data = json.dumps({"query": q}).encode("utf-8")
    req = urllib.request.Request(
        BASE + "/query-sync", data=data, method="POST",
        headers={"Content-Type": "application/json"},
    )
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
        seconds = time.perf_counter() - t0
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}", time.perf_counter() - t0
    try:
        payload = json.loads(body)
        return bool(payload.get("success")), str(payload.get("stdout") or
                                                  payload.get("err") or ""), seconds
    except Exception:
        return False, body, seconds


print("=" * 78)
print("1. КАКИЕ КОМАНДЫ ДОСТУПНЫ ЧЕРЕЗ СЕРВЕР")
print("=" * 78)

# Список команд оболочки Joern.
ok, out, secs = query("help")
print(f"  help: {'успех' if ok else 'отказ'}, {secs * 1000:.0f} мс")
if ok:
    # Показываются строки про построение и сохранение графа.
    keywords = ("import", "save", "open", "export", "code", "cpg", "workspace")
    lines = [ln.strip() for ln in out.splitlines()
             if any(k in ln.lower() for k in keywords)]
    for line in lines[:25]:
        print(f"    {ln if False else line[:120]}")

print()
print("=" * 78)
print("2. ПРОВЕРКА: ЧТО В ПОСТРОЕННОМ ГРАФЕ (загрузка файла)")
print("=" * 78)
print("  Проверяется, умеет ли сервер ЗАГРУЗИТЬ уже построенный граф.")
print("  Если да — построение можно делать реже, а экспорт и запросы — через сервер.")

# Ищется построенный граф корпуса.
cpg_candidates: list[pathlib.Path] = []
for root in ("C:/AutoClaw/z.batteryservice.ru", "C:/AutoClaw/revoap.com"):
    d = pathlib.Path(root) / ".ragraf" / "cpg"
    if d.exists():
        cpg_candidates.extend(
            sorted(d.glob("*.cpg.bin"), key=lambda p: p.stat().st_mtime, reverse=True)[:1]
        )

if cpg_candidates:
    cpg_file = cpg_candidates[0]
    print(f"  граф: {cpg_file}")
    # importCpg загружает существующий граф в текущую сессию.
    ok, out, secs = query(f'importCpg("{cpg_file.as_posix()}")', timeout=900)
    print(f"  importCpg: {'успех' if ok else 'отказ'}, {secs:.2f} с")
    print(f"    вывод: {out[:300].strip()}")
    check(ok, "сервер умеет загружать готовый граф", out[:150])

    if ok:
        print("\n  === 3. Запросы к загруженному графу ===")
        # Проверяются типовые вопросы, которые задаёт RAGRAF.
        tests = [
            ("число методов", "cpg.method.size"),
            ("число вызовов", "cpg.call.size"),
            ("имена первых методов", 'cpg.method.name.take(3).l'),
        ]
        for label, q in tests:
            ok2, out2, secs2 = query(q, timeout=600)
            print(f"    {label}: {'успех' if ok2 else 'отказ'} за {secs2 * 1000:.0f} мс")
            print(f"      {out2.strip()[:150]}")
            check(ok2, f"запрос «{label}» выполнен", out2[:100])
else:
    print("  построенный граф не найден")

print()
print("=" * 78)
print("4. ЧЕГО СЕРВЕР НЕ ДЕЛАЕТ")
print("=" * 78)
print("""
  Проверяется главный вопрос: умеет ли сервер ЭКСПОРТИРОВАТЬ граф в файлы
  (это делает joern-export отдельной утилитой). Если не умеет — экспорт
  придётся оставить отдельным запуском, и экономия будет частичной.
""")
# Экспорт через сервер: проверяется наличие команды.
for candidate in ("export", "exportCpg", "save"):
    ok, out, secs = query(candidate, timeout=300)
    first_line = out.strip().splitlines()[0][:120] if out.strip() else "(пусто)"
    print(f"  команда «{candidate}»: {'успех' if ok else 'отказ'}, "
          f"{secs * 1000:.0f} мс")
    print(f"    {first_line}")

print()
print("=" * 78)
print("ИТОГ")
print("=" * 78)
print(f"пройдено: {PASS}, провалено: {FAIL}")
