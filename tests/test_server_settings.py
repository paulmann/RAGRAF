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

import hashlib
import json
import os
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

print("\n=== 7. Сохранение одной настройки не материализует умолчания ===")
# Причина проверки (замер 28.10.2026): `save()` писал в файл СЛИЯНИЕ с
# умолчаниями, и одно действие панели оставляло в файле все ключи. После этого
# `edition: community` читался как явное заявление владельца, действующая
# лицензия pro понижалась, и три проверки лицензий краснели по чужой причине.
sbox = pathlib.Path(tempfile.mkdtemp(prefix="ragraf_store_settings_"))
try:
    path = sbox / "settings.json"
    path.write_text(
        '{"version": 1, "settings": {"ragraf_mode": "both"}}\n',
        encoding="utf-8",
    )
    store = s.SettingsStore(path)
    saved = store.save({"graph_gpu": False})
    written = s.util.read_json(path, default={}) or {}
    stored = written.get("settings") or {}
    print(f"  в файле после сохранения ключи: {sorted(stored)}")
    check(set(stored) == {"ragraf_mode", "graph_gpu"},
          "в файл попало только явно заданное",
          f"записано {sorted(stored)}")
    check("edition" not in stored,
          "умолчание edition не стало заявлением владельца")
    check(saved.get("edition") == s.DEFAULTS["edition"]
          and store.load().get("edition") == s.DEFAULTS["edition"],
          "действующее значение edition — по умолчанию, а не записанное",
          f"получено {store.load().get('edition')!r}")
finally:
    shutil.rmtree(sbox, ignore_errors=True)


print("\n=== 8. Файл настроек не выдаёт умолчания за заявления ===")
# Причина проверки (задание И42). Значения настроек читаются слиянием с
# умолчаниями, и панель рисовала ВСЁ это как поля. Пока сборщик на фронте
# отправлял весь набор, одно сохранение записывало 56 ключей, 55 из которых
# были умолчаниями; после этого `edition: community` читался как ЯВНОЕ
# заявление владельца, и действующая лицензия pro понижалась до community.
#
# Проверяется СВОЙСТВО, а не написание: значение каждого записанного ключа
# сравнивается с s.DEFAULTS. Равенство умолчанию означает «не заявление»,
# и в файле такого быть не должно. Правило проверено на себе: детектор обязан
# сработать на подсаженном ключе-умолчании в песочнице (ниже), иначе он
# зеленел бы всегда.


def default_equal(stored: dict) -> list:
    """Ключи файла, значение которых совпадает с умолчанием."""
    out = []
    for key, value in sorted(stored.items()):
        if key in s.DEFAULTS and s.DEFAULTS[key] == value:
            out.append(key)
    return out


def stored_keys(path: pathlib.Path) -> dict:
    """Что ЗАПИСАНО в файле (без слияния с умолчаниями)."""
    raw = s.util.read_json(path, default=None)
    if not isinstance(raw, dict):
        return {}
    nested = raw.get("settings")
    source = nested if isinstance(nested, dict) else raw
    return {k: v for k, v in source.items() if k in s.STORE_KEYS}


live_path = REPO / "catalog" / "settings.json"
live = stored_keys(live_path)
print(f"  файл настроек: {live_path}")
print(f"  записанных ключей: {len(live)}: {sorted(live)}")
equal_live = default_equal(live)
check(bool(live), "файл настроек прочитан: записанные ключи есть",
      f"получено {sorted(live)}")
check(not equal_live,
      "ни один записанный ключ не равен умолчанию",
      f"равны умолчанию: {equal_live}")

# `edition` — отдельное свойство, а не ещё один ключ: записанный умолчанием он
# читается как заявление владельца и понижает действующую лицензию. Проверка
# идёт через сам слой изданий (edition.declared_edition), а не через текст.
from ragraf import edition as edition_mod  # noqa: E402
from ragraf import settings as settings_mod  # noqa: E402

_DECLARED_ENV = getattr(edition_mod, "DECLARED_ENV", "")
_saved_env = os.environ.pop(_DECLARED_ENV, None) if _DECLARED_ENV else None
_orig_store = s.SettingsStore


def _declared_from(path: pathlib.Path) -> tuple:
    """Что слой изданий считает заявленным для конкретного файла настроек."""
    settings_mod.SettingsStore = lambda *a, **k: _orig_store(path)  # type: ignore[assignment]
    try:
        return edition_mod.declared_edition()
    finally:
        settings_mod.SettingsStore = _orig_store  # type: ignore[assignment]


try:
    live_declared, live_source = _declared_from(live_path)
    print(f"  издание по файлу настроек: {live_declared!r} ({live_source})")
    # Три РАЗНЫХ случая, и путать их нельзя: проверка, прошедшая по чужому
    # основанию, — ложный зелёный. Ключа нет — заявления нет; ключ равен
    # умолчанию — заявления тоже нет (и такого в файле быть не должно);
    # ключ отличается от умолчания — заявление есть и обязано читаться.
    if "edition" not in live:
        check(live_declared == "",
              "незаписанное издание не читается как заявление",
              f"получено {live_declared!r} ({live_source})")
    elif live.get("edition") == s.DEFAULTS["edition"]:
        check(live_declared == "",
              "edition, записанный умолчанием, не читается как заявление",
              f"получено {live_declared!r} ({live_source})")
    else:
        check(bool(live_declared),
              "edition записан явно — и прочитан как заявление",
              f"получено {live_declared!r} ({live_source})")

    print("  --- проверка самой проверки: песочница ---")
    sbox = pathlib.Path(tempfile.mkdtemp(prefix="ragraf_settings_decl_"))
    try:
        seeded = sbox / "seeded.json"
        seeded.write_text(json.dumps({
            "version": 1,
            "settings": {
                # Подсаженные умолчания: детектор обязан их назвать.
                "edition": s.DEFAULTS["edition"],
                "dedupe_scope": s.DEFAULTS["dedupe_scope"],
                # Приманки: не умолчания и не то, что надо трогать.
                "ragraf_mode": "both",
                "dedupe_report": False,
            },
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        before = hashlib.sha256(seeded.read_bytes()).hexdigest()
        seeded_stored = stored_keys(seeded)
        equal_seeded = default_equal(seeded_stored)
        after = hashlib.sha256(seeded.read_bytes()).hexdigest()
        print(f"  подсажено умолчаний: {equal_seeded}")
        check(sorted(equal_seeded) == ["dedupe_scope", "edition"],
              "детектор называет ровно подсаженные умолчания",
              f"получено {sorted(equal_seeded)}")
        check("ragraf_mode" not in equal_seeded and "dedupe_report" not in equal_seeded,
              "приманки не названы умолчаниями")
        check(before == after, "проверка не изменила файл настроек")
        seeded_declared, _ = _declared_from(seeded)
        check(seeded_declared == s.DEFAULTS["edition"],
              "записанное умолчание edition читается как заявление — "
              "именно поэтому такое не должно попадать в файл",
              f"получено {seeded_declared!r}")

        clean = sbox / "clean.json"
        clean.write_text(json.dumps({
            "version": 1,
            "settings": {"dedupe_scope": "all", "ragraf_mode": "both"},
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        check(not default_equal(stored_keys(clean)),
              "чистый файл проходит проверку",
              f"получено {default_equal(stored_keys(clean))}")
        clean_declared, clean_source = _declared_from(clean)
        check(clean_declared == "",
              "незаписанное издание не читается как заявление",
              f"получено {clean_declared!r} ({clean_source})")
    finally:
        shutil.rmtree(sbox, ignore_errors=True)
finally:
    if _DECLARED_ENV and _saved_env is not None:
        os.environ[_DECLARED_ENV] = _saved_env
    settings_mod.SettingsStore = _orig_store  # type: ignore[assignment]

print("\n=== ИТОГ ===")
# Первая строка — для чекера (он ищет «проверок» и «провалов»),
# вторая — для раннера tests (он ищет «пройдено» и «провалено»).
print(f"проверок: {PASS}, провалов: {FAIL}")
print(f"пройдено: {PASS}, провалено: {FAIL}")
raise SystemExit(0 if FAIL == 0 else 1)
