"""Проверка профиля исключений, управляемого настройками (уровень pro).

Что проверяется — свойства, а не написание:

  1. по умолчанию профиль ВЫКЛЮЧЕН и состав корпуса не меняется;
  2. имена проверяются как имена каталогов: шаблоны, слэши и `..` отклоняются,
     `.ragraf` добавить нельзя;
  3. включение профиля убирает файлы названных каталогов и НЕ трогает остальные
     (в корпусе лежат приманки: рабочий код рядом, каталог `Cache` с заглавной);
  4. замер эффекта (`impact`) называет файлы кода, которые выпадут;
  5. операция ОТКАЗЫВАЕТ включать профиль, если выпадают файлы кода, и не
     меняет конфиг при отказе;
  6. безопасный набор (`cache, logs`) включается, записывается в конфиг корпуса
     и после перечитывания конфига действительно меняет состав;
  7. базовый список `DEFAULT_EXCLUDES` не изменился — правка его не касается.
"""

from __future__ import annotations

import pathlib
import shutil
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf import corpus, excludes  # noqa: E402
from ragraf.config import DEFAULT_EXCLUDES, load as load_config  # noqa: E402
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


def make_corpus() -> pathlib.Path:
    """Корпус с приманками: код рядом с шумными каталогами и `Cache` с заглавной."""
    root = pathlib.Path(tempfile.mkdtemp(prefix="ragraf-exc-"))
    files = {
        "src/main.py": "print('main')\n",
        "src/Cache/keep.py": "# рабочий код в каталоге с ЗАГЛАВНОЙ буквой\n",
        "temp/script.py": "# код внутри temp: исключать нельзя молча\n",
        "temp/notes.txt": "заметки\n",
        "cache/data.json": '{"a": 1}\n',
        "cache/sub/more.json": '{"b": 2}\n',
        "logs/app.txt": "лог\n",
        "docs/readme.md": "# описание\n",
    }
    for rel, body in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    (root / "demo.yaml").write_text(
        f"project: demo\nroot: {root.as_posix()}\n"
        "languages: [python, markdown, json, text]\n"
        'include:\n  - "**/*"\n'
        # Индекс песочницы собирается без графа свойств кода и без векторов:
        # фасаду нужен существующий index/graph.sqlite, а Joern и загрузка
        # модели к составу корпуса отношения не имеют.
        "index:\n  joern_enabled: false\n  use_gpu: false\n  budget_seconds: 60\n",
        encoding="utf-8",
    )
    return root


def scanned(cfg) -> set[str]:
    return {f.path for f in corpus.scan(cfg, digest=False).files}


root = make_corpus()
config_path = root / "demo.yaml"
cfg = load_config(config_path)
base = scanned(cfg)

print("=" * 78)
print("1. ПО УМОЛЧАНИЮ ПРОФИЛЬ ВЫКЛЮЧЕН")
print("=" * 78)
check(len(base) == 8, "в корпусе 8 файлов", repr(len(base)))
check(cfg.index.exclude_noisy_enabled is False, "профиль выключен", repr(cfg.index.exclude_noisy_enabled))
check(excludes.describe(cfg)["patterns"] == [], "шаблонов не добавлено")

print()
print("=" * 78)
print("2. ИМЕНА ПРОВЕРЯЮТСЯ КАК ИМЕНА КАТАЛОГОВ")
print("=" * 78)
for bad in ("**/temp/**", "a/b", "..", ".ragraf", "*"):
    try:
        excludes.normalize(bad)
        ok = False
    except excludes.ExcludeError:
        ok = True
    check(ok, f"отклонено имя {bad!r}")
check(excludes.normalize("temp, temp ,tmp") == ["temp", "tmp"],
      "повторы схлопываются, порядок сохраняется",
      repr(excludes.normalize("temp, temp ,tmp")))
check(excludes.patterns(["temp"]) == ["**/temp/**"], "шаблон собирается как **/name/**",
      repr(excludes.patterns(["temp"])))

print()
print("=" * 78)
print("3. ЗАМЕР ЭФФЕКТА НАЗЫВАЕТ ФАЙЛЫ КОДА")
print("=" * 78)
imp_temp = excludes.impact(cfg, ["temp"])
check(imp_temp["files_dropped"] == 2, "temp: выпадет 2 файла", repr(imp_temp["files_dropped"]))
check(imp_temp["code_dropped"] == 1, "temp: из них 1 файл кода", repr(imp_temp["code_dropped"]))
check(any("temp/script.py" in e for e in imp_temp["by_name"]["temp"]["examples"]),
      "назван конкретный файл кода", repr(imp_temp["by_name"]["temp"]["examples"]))
check(imp_temp["safe"] is False, "включение признано небезопасным")
imp_safe = excludes.impact(cfg, ["cache", "logs"])
check(imp_safe["files_dropped"] == 3, "cache+logs: выпадет 3 файла", repr(imp_safe["files_dropped"]))
check(imp_safe["code_dropped"] == 0, "cache+logs: файлов кода нет", repr(imp_safe["code_dropped"]))
check(imp_safe["safe"] is True, "включение признано безопасным")

print()
print("=" * 78)
print("4. ОПЕРАЦИЯ ОТКАЗЫВАЕТ, ЕСЛИ ВЫПАДАЕТ КОД, И НЕ МЕНЯЕТ КОНФИГ")
print("=" * 78)
from ragraf.indexer import Indexer  # noqa: E402

_idx = Indexer(cfg)
try:
    _rep = _idx.run(with_vectors=False)
finally:
    _idx.close()
check(_rep.ok, "индекс песочницы собран", str(getattr(_rep, "error", "")))
facade = Facade(cfg, tool="dev", telemetry=False, watch=False)
before = config_path.read_text(encoding="utf-8")
out = facade.op_excludes(action="apply", dirs="temp")
check(out.get("found") is False, "отказ вместо записи", repr(out.get("found")))
check("КОДА" in str(out.get("error") or ""), "причина названа", str(out.get("error"))[:80])
check(config_path.read_text(encoding="utf-8") == before, "конфиг корпуса не изменён")

print()
print("=" * 78)
print("5. БЕЗОПАСНЫЙ НАБОР ВКЛЮЧАЕТСЯ И ДЕЙСТВУЕТ")
print("=" * 78)
out = facade.op_excludes(action="apply", dirs="cache,logs")
check(out.get("found") is True, "включение выполнено", str(out.get("error") or ""))
written = out.get("result", {}).get("written") or {}
check(written.get("exclude_noisy_enabled") is True, "записано enabled=true", repr(written))
check(written.get("exclude_noisy_dirs") == "cache,logs", "записан список имён", repr(written.get("exclude_noisy_dirs")))
cfg2 = load_config(config_path)
after = scanned(cfg2)
check(after == base - {"cache/data.json", "cache/sub/more.json", "logs/app.txt"},
      "выпали ровно файлы названных каталогов", repr(sorted(base - after)))
check("src/Cache/keep.py" in after, "каталог Cache с заглавной НЕ задет (регистрозависимо)")
check("temp/script.py" in after, "код в temp остался: его не называли")

print()
print("=" * 78)
print("6. ВЫКЛЮЧЕНИЕ ВОЗВРАЩАЕТ ПРЕЖНИЙ СОСТАВ")
print("=" * 78)
out = facade.op_excludes(action="disable")
check(out.get("found") is True, "выключение выполнено")
cfg3 = load_config(config_path)
check(scanned(cfg3) == base, "состав корпуса вернулся к исходному")

print()
print("=" * 78)
print("7. БАЗОВЫЙ СПИСОК НЕ ТРОНУТ")
print("=" * 78)
check(len(DEFAULT_EXCLUDES) == 28, "шаблонов в DEFAULT_EXCLUDES 28", repr(len(DEFAULT_EXCLUDES)))
check("**/.ragraf/**" in DEFAULT_EXCLUDES, "самоисключение .ragraf на месте")
# Проверка по СМЫСЛУ, а не по подстроке: `**/.pytest_cache/**` содержит слово
# «cache», но это кеш тестов, а не каталог `cache` проекта. Поэтому из шаблонов
# извлекаются имена каталогов и сравниваются как имена.
base_dirs = {p[3:-3] for p in DEFAULT_EXCLUDES
             if p.startswith("**/") and p.endswith("/**")}
check({"temp", "tmp", "cache", "logs"}.isdisjoint(base_dirs),
      "temp/tmp/cache/logs в базовый список не внесены",
      repr(sorted({"temp", "tmp", "cache", "logs"} & base_dirs)))

print()
print("=" * 78)
print("8. УРОВЕНЬ ИЗДАНИЯ: Community отказывает, Pro и Enterprise разрешают")
print("=" * 78)
from ragraf import edition  # noqa: E402

check(edition.baseline_problems(edition.load_baseline()) == [],
      "данные слоя изданий непротиворечивы")
for name, expect in (("community", False), ("pro", True), ("enterprise", True)):
    decision = edition.resolve(declared=name).decide("excludes")
    check(bool(decision.get("allowed")) is expect,
          f"{name}: доступ {'есть' if expect else 'закрыт'}",
          str(decision.get("reason") or "")[:80])

shutil.rmtree(root, ignore_errors=True)
print()
print("=" * 78)
print(f"ИТОГО: проверок {PASS + FAIL}, успешно {PASS}, провалено {FAIL}")
print("=" * 78)
raise SystemExit(0 if FAIL == 0 else 1)
