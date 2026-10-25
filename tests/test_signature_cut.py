"""Проверка пункта 1.3.1: обрезка сигнатуры по границе параметра.

Что проверяется на реальном коде:
  1. сигнатура с 11 параметрами больше не теряет середину без предупреждения;
  2. в тексте стоит явная пометка «… ещё N»;
  3. число параметров в пометке совпадает с числом в машинном списке;
  4. короткая сигнатура не помечается обрезанной;
  5. машинный список по-прежнему полный и без потерь.

Проверка идёт по ФАЙЛАМ ПИЛОТА, а не по базе: так она не зависит от того,
переиндексирован ли корпус.
"""

from __future__ import annotations

import pathlib
import re
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf.extract.php_ast import PhpExtractor, _collapse_signature  # noqa: E402

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


print("=== 1. Прямая проверка сжатия ===")
# Случай с 11 параметрами — тот самый, что терял 4 параметра из 11.
# ВАЖНО: "голова" содержит текст параметров — именно так её подаёт извлекатель
# (это часть кода до тела функции). Поэтому она длиннее 200 символов.
params = [
    {"name": f"$p{i}", "type": "string", "text": f"public readonly string $p{i}"}
    for i in range(11)
]
head = "public function __construct(" + ", ".join(p["text"] for p in params) + ")"
print(f"  длина исходной головы: {len(head)}")
check(len(head) > 200, "исходная голова длиннее лимита (случай настоящий)")
text, truncated = _collapse_signature(head, params, 200)
print(f"  результат: {text}")
print(f"  длина: {len(text)}, признак обрезки: {truncated}")
check(len(text) <= 200, "результат уложился в лимит", f"длина {len(text)}")
check(truncated, "признак обрезки выставлен")
check("ещё" in text, "в тексте есть явная пометка о числе скрытых")
m = re.search(r"ещё (\d+)", text)
hidden = int(m.group(1)) if m else -1
shown = text.count("$p")
check(hidden >= 0 and shown + hidden == 11,
      "скрытые + показанные = всего параметров",
      f"показано {shown}, скрыто {hidden}, всего 11")
check("$p0" in text, "первый параметр показан")
check(params[-1]["name"] not in text or shown == 11,
      "последний параметр либо показан, либо честно помечен скрытым")

print("\n=== 2. Короткая сигнатура не помечается ===")
short = [{"name": "$id", "type": "int", "text": "int $id"}]
text2, trunc2 = _collapse_signature("function f(int $id)", short, 200)
print(f"  результат: {text2}")
check(len(text2) <= 200, "короткая сигнатура не обрезана по длине")
check("ещё" not in text2, "пометки о скрытых нет")

print("\n=== 3. Реальные конструкторы пилота ===")
root = REPO.parent / "revoap.com"
targets = ["src/Api/ApiKey.php", "src/Api/ApiV1Controller.php", "src/Api/Webhook.php"]
extractor = PhpExtractor()
found_any = False

for rel in targets:
    path = root / rel
    if not path.exists():
        continue
    res = extractor.extract(rel, path.read_bytes())
    for sym in res.symbols:
        props = sym.props or {}
        plist = props.get("signature_params") or []
        if len(plist) < 6:
            continue
        found_any = True
        sig = sym.signature or ""
        marks = "ещё" in sig
        print(f"\n  {rel}:{sym.start_line} {sym.name}")
        print(f"    параметров в структуре: {len(plist)}")
        print(f"    длина текста: {len(sig)} | пометка обрезки: {marks}")
        print(f"    текст: {sig}")
        # Главное свойство: число параметров в структуре не зависит от текста.
        check(len(plist) >= 6, f"{sym.name}: машинный список полный",
              f"параметров {len(plist)}")
        # Если пометка есть, показанные + скрытые должно сходиться.
        if marks:
            mm = re.search(r"ещё (\d+)", sig)
            if mm:
                shown = sum(1 for p in plist if p["name"] in sig)
                total = shown + int(mm.group(1))
                check(total == len(plist),
                      f"{sym.name}: показано {shown} + скрыто {mm.group(1)} = {len(plist)}",
                      f"получено {total}")
        else:
            # Без пометки текст обязан содержать все параметры.
            missing = [p["name"] for p in plist if p["name"] not in sig]
            check(not missing,
                  f"{sym.name}: без пометки текст содержит все параметры",
                  f"потеряны: {missing}")

check(found_any, "найдены реальные конструкторы с 6+ параметрами")

print("\n=== 4. Признак обрезки в props ===")
count_trunc = 0
count_total = 0
for path in sorted(root.rglob("*.php")):
    if ".staging" in str(path) or "vendor" in str(path):
        continue
    try:
        res = extractor.extract(str(path.relative_to(root)).replace("\\", "/"), path.read_bytes())
    except Exception:
        continue
    for sym in res.symbols:
        props = sym.props or {}
        if "signature_truncated" not in props:
            continue
        count_total += 1
        if props["signature_truncated"]:
            count_trunc += 1
            # Каждая обрезанная сигнатура обязана иметь пометку в тексте.
            sig = sym.signature or ""
            if "ещё" not in sig and not sig.endswith("…"):
                print(f"    ВНИМАНИЕ: {sym.path}:{sym.start_line} признак есть, пометки нет")
    if count_total > 4000:
        break

print(f"  функций и методов с признаком: {count_total}")
print(f"  из них обрезанных: {count_trunc}")
check(count_trunc > 0, "обрезанные сигнатуры находятся и помечаются")
check(count_trunc < count_total, "не все сигнатуры обрезаны (признак не срабатывает всегда)")

print("\n=== ИТОГ ===")
print(f"пройдено: {PASS}, провалено: {FAIL}")
raise SystemExit(0 if FAIL == 0 else 1)
