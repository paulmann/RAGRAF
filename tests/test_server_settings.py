"""Проверка настроек сервера Joern и графового анализа на видеокарте.

Проверяется:
  1. обе группы настроек есть, имеют значения, границы и пояснения;
  2. значения по умолчанию соответствуют замерам (сервер включён,
     графовый анализ выключен — это решение по числам, а не по вкусу);
  3. границы защищают от ошибок: служебный порт занять нельзя;
  4. логические переключатели проверяются как да/нет;
  5. настройки записываются в конфиг корпуса с сохранением комментариев.

Запись проверяется на ВРЕМЕННОЙ копии конфига: рабочие файлы не меняются.
"""

from __future__ import annotations

import pathlib
import shutil
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf import settings as s  # noqa: E402
from ragraf.config import load as load_config  # noqa: E402

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


print("=== 1. Настройки сервера Joern ===")
for key, expected in (("joern_server", True),
                      ("joern_server_port", 9500),
                      ("joern_heap_gb", 8)):
    present = key in s.DEFAULTS
    value = s.DEFAULTS.get(key)
    described = key in s.DESCRIPTIONS
    # У логических переключателей границ нет: они принимают только да/нет.
    # Границы нужны числовым значениям, где возможна опечатка вроде порта 80.
    is_boolean = key in s.BOOLEAN_KEYS
    bounds_ok = is_boolean or key in s.BOUNDS
    print(f"  {key}: значение {value}, "
          f"{'логический переключатель' if is_boolean else f'границы {s.BOUNDS.get(key)}'}, "
          f"описание {'есть' if described else 'НЕТ'}")
    check(present, f"{key} есть в настройках")
    check(value == expected, f"{key} по умолчанию {expected}", f"получено {value}")
    check(described, f"{key} имеет пояснение")
    check(bounds_ok, f"{key} проверяется (границы или да/нет)")

print("\n=== 2. Настройки графового анализа на видеокарте ===")
for key, expected in (("graph_gpu", True), ("graph_gpu_min_nodes", 5000)):
    present = key in s.DEFAULTS
    value = s.DEFAULTS.get(key)
    print(f"  {key}: значение {value}, границы {s.BOUNDS.get(key)}")
    check(present, f"{key} есть в настройках")
    check(value == expected, f"{key} по умолчанию {expected}", f"получено {value}")
    check(key in s.DESCRIPTIONS, f"{key} имеет пояснение")

# Графовый анализ включён — это решение владельца от 9 октября 2026.
# Порог 5000 вершин: на 1059 вершинах процессор быстрее, на 5520 уже карта.
check(s.DEFAULTS["graph_gpu"] is True,
      "графовый анализ включён (решение владельца, порог 5000 вершин)")
check(s.DEFAULTS["graph_gpu_min_nodes"] == 5000,
      "порог окупаемости графового анализа — 5000 вершин")
# Настройка принята и загрузчиком конфига корпуса, а не только панелью.
_idx = load_config(REPO / "config" / "ragraf.yaml").index
check(bool(_idx.graph_gpu), "загрузчик читает включённый графовый анализ",
      f"graph_gpu={_idx.graph_gpu!r}")
check(int(_idx.graph_gpu_min_nodes) == 5000,
      "загрузчик читает порог 5000", f"min_nodes={_idx.graph_gpu_min_nodes!r}")

print("\n=== 3. Границы значений ===")
problems = s.validate({"joern_server_port": 80})
check(bool(problems), "служебный порт (80) отклонён", str(problems))
problems = s.validate({"joern_server_port": 9500})
check(not problems, "нормальный порт принят", str(problems))
problems = s.validate({"joern_heap_gb": 0})
check(bool(problems), "нулевая память отклонена", str(problems))
problems = s.validate({"graph_gpu_min_nodes": 5})
check(bool(problems), "слишком низкий порог отклонён", str(problems))

print("\n=== 4. Логические переключатели ===")
for key in ("joern_server", "graph_gpu"):
    problems = s.validate({key: "иногда"})
    check(bool(problems), f"{key}: не-логическое значение отклонено")
    problems = s.validate({key: False})
    check(not problems, f"{key}: логическое значение принято")

print("\n=== 5. Запись в конфиг корпуса (на копии) ===")
tmp = pathlib.Path(tempfile.mkdtemp(prefix="ragraf_joern_settings_"))
try:
    copy = tmp / "corpus.yaml"
    shutil.copy2(REPO / "config" / "z-batteryservice.yaml", copy)

    s.write_project_settings(copy, {"joern_server": False,
                                   "joern_server_port": 9600,
                                   "graph_gpu": True})
    values = s.read_project_settings(copy)
    print(f"  записано: joern_server={values.get('joern_server')}, "
          f"порт={values.get('joern_server_port')}, graph_gpu={values.get('graph_gpu')}")
    check(values.get("joern_server") is False, "переключатель сервера записан")
    check(values.get("joern_server_port") == 9600, "порт записан")
    check(values.get("graph_gpu") is True, "переключатель графового анализа записан")

    text = copy.read_text(encoding="utf-8")
    check(text.count("\nindex:") == 1, "секция index не задублирована")
    check("#" in text, "комментарии конфига сохранены")
    check("embedding:" in text, "секция embedding не повреждена")
    backups = list(tmp.glob("corpus.yaml.bak.*"))
    check(bool(backups), "создана резервная копия")
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print("\n=== 6. Конфиги читаются загрузчиком ===")
for name in ("z-batteryservice", "revoap"):
    cfg = load_config(REPO / "config" / f"{name}.yaml")
    idx = cfg.index
    print(f"  {name}: сервер={idx.joern_server} (порт {idx.joern_server_port}), "
          f"память {idx.joern_heap_gb} ГБ, графовый анализ={idx.graph_gpu}")
    check(hasattr(idx, "joern_server"), f"{name}: настройка сервера прочитана")
    check(hasattr(idx, "graph_gpu"), f"{name}: настройка графового анализа прочитана")

print("\n=== ИТОГ ===")
print(f"пройдено: {PASS}, провалено: {FAIL}")
raise SystemExit(0 if FAIL == 0 else 1)
