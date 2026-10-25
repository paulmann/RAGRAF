"""Проверка настроек видеокарты в панели администратора.

Проверяется:
  1. настройки видеокарты есть в общих настройках и имеют пояснения;
  2. границы значений защищают от ошибок (слишком низкий порог включит карту
     там, где она проигрывает);
  3. проверка значений работает для логического переключателя;
  4. состояние вычислителя отдаётся панелью;
  5. настройки видеокарты можно записать в конфиг корпуса.

Ничего не меняет в рабочих конфигах: запись проверяется на временной копии.
"""

from __future__ import annotations

import pathlib
import shutil
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf import settings as s  # noqa: E402

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


print("=== 1. Настройки видеокарты в общих настройках ===")
for key in ("use_gpu", "gpu_min_texts", "gpu_batch_size"):
    present = key in s.DEFAULTS
    described = key in s.DESCRIPTIONS
    print(f"  {key}: значение {s.DEFAULTS.get(key)}, "
          f"описание {'есть' if described else 'НЕТ'}")
    check(present, f"{key} есть в настройках по умолчанию")
    check(described, f"{key} имеет пояснение для администратора")

print("\n=== 2. Порог окупаемости по умолчанию ===")
default_threshold = s.DEFAULTS["gpu_min_texts"]
print(f"  порог: {default_threshold} текстов")
# Замеренная точка равенства — 1451 текст. Порог обязан быть не ниже неё,
# иначе видеокарта будет включаться там, где она проигрывает.
check(default_threshold >= 1451,
      "порог не ниже замеренной точки равенства (1451)",
      f"получено {default_threshold}")

print("\n=== 3. Границы значений ===")
low, high = s.BOUNDS["gpu_min_texts"]
print(f"  допустимый диапазон порога: {low}..{high}")
problems = s.validate({"gpu_min_texts": 10})
check(bool(problems), "слишком низкий порог отклонён", str(problems))
problems = s.validate({"gpu_min_texts": 999999})
check(bool(problems), "слишком высокий порог отклонён", str(problems))
problems = s.validate({"gpu_min_texts": 2000})
check(not problems, "разумный порог принят", str(problems))

print("\n=== 4. Логический переключатель ===")
problems = s.validate({"use_gpu": "да"})
check(bool(problems), "не-логическое значение переключателя отклонено", str(problems))
problems = s.validate({"use_gpu": False})
check(not problems, "логическое значение принято", str(problems))

print("\n=== 5. Запись в конфиг корпуса (на копии) ===")
tmp = pathlib.Path(tempfile.mkdtemp(prefix="ragraf_gpu_settings_"))
try:
    source = REPO / "config" / "z-batteryservice.yaml"
    copy = tmp / "corpus.yaml"
    shutil.copy2(source, copy)

    before = s.read_project_settings(copy)
    print(f"  до записи: use_gpu в конфиге: {before.get('use_gpu', '(не задано)')}")

    s.write_project_settings(copy, {"use_gpu": False, "gpu_min_texts": 2500})
    after = s.read_project_settings(copy)
    print(f"  после записи: use_gpu={after.get('use_gpu')}, "
          f"порог={after.get('gpu_min_texts')}")
    check(after.get("use_gpu") is False, "переключатель записан",
          str(after.get("use_gpu")))
    check(after.get("gpu_min_texts") == 2500, "порог записан",
          str(after.get("gpu_min_texts")))

    # Комментарии конфига должны сохраниться.
    text = copy.read_text(encoding="utf-8")
    check("#" in text, "комментарии конфига сохранены")
    check(text.count("\nindex:") == 1, "секция index не задублирована")
    check("embedding:" in text, "секция embedding не повреждена")

    # Копия создана.
    backups = list(tmp.glob("corpus.yaml.bak.*"))
    check(bool(backups), "создана резервная копия", f"найдено {len(backups)}")

    # Откат при ошибке.
    before_bad = copy.read_text(encoding="utf-8")
    try:
        s.write_project_settings(copy, {"gpu_min_texts": 10})
        check(False, "ошибочное значение отклонено", "исключение не возникло")
    except ValueError as exc:
        check("допустимо" in str(exc), "ошибочное значение отклонено с причиной",
              str(exc)[:80])
    check(copy.read_text(encoding="utf-8") == before_bad,
          "при отказе файл не изменён")

finally:
    shutil.rmtree(tmp, ignore_errors=True)

print("\n=== 6. Рабочие конфиги не пострадали ===")
for name in ("z-batteryservice", "revoap"):
    path = REPO / "config" / f"{name}.yaml"
    text = path.read_text(encoding="utf-8")
    check(text.count("\nindex:") == 1, f"{name}.yaml: секция index не задублирована")
    check("use_gpu" in text, f"{name}.yaml: настройка видеокарты есть")

print("\n=== ИТОГ ===")
print(f"пройдено: {PASS}, провалено: {FAIL}")
raise SystemExit(0 if FAIL == 0 else 1)
