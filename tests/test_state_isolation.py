"""Прогон не меняет живое состояние: путь `catalog/` выбирается, а не зашит.

Зачем эта проверка
    Измерено 30.10.2026 (этап 7): прогон проверок писал в ЖИВОЙ
    `catalog/settings.json` и в живой реестр разработок. Прогон менял то
    состояние, на котором сам же и проверяет, — поэтому замеры после прогона
    могли врать, а `test_ensure` в пакетном прогоне давал 3 из 17 при 17 из 17
    в одиночном.

Свойства (каждое — отдельная проверка):
    1. `paths.catalog_dir()` слушает `RAGRAF_CATALOG_DIR`, по умолчанию — каталог
       установки.
    2. `paths.settings_path()`: `RAGRAF_SETTINGS_PATH` сильнее каталога, каталог
       сильнее умолчания.
    3. `SettingsStore()` пишет туда, куда указывает окружение, а живой файл
       настроек остаётся байт в байт.
    4. `Catalog()` пишет туда же, а живой реестр остаётся байт в байт.
    5. Дочерний процесс (так наборы запускает раннер) пишет в отведённый
       каталог и не трогает живой.
    6. `test_ensure` даёт ноль провалов ДВА прогона подряд — плавающий провал
       закрыт, а не замаскирован.
    7. `paths` только выбирает путь: отсутствующий каталог не создаётся.

Чего проверка НЕ делает: не утверждает, что ни один набор не пишет в живой
`catalog/` вообще. Это измеряет раннер (`tools/check_all.py`, снимок живого
состояния до и после каждого набора) — у него для этого есть все данные.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import shutil
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ragraf import paths  # noqa: E402
from ragraf.catalog import Catalog  # noqa: E402
from ragraf.settings import SettingsStore  # noqa: E402

PASS = 0
FAIL = 0

LIVE = REPO / "catalog"
LIVE_FILES = ("settings.json", "catalog.json", "registry_history.jsonl")


def check(condition: bool, title: str, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  [OK ] {title}")
    else:
        FAIL += 1
        print(f"  [FAIL] {title}" + (f"\n      {detail}" if detail else ""))


def digest(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def live_snapshot() -> dict[str, str | None]:
    return {name: digest(LIVE / name) for name in LIVE_FILES}


def with_env(**values: str):
    """Временно выставить переменные окружения и вернуть прежние значения."""
    class _Env:
        def __enter__(self):
            self.saved = {k: os.environ.get(k) for k in values}
            os.environ.update(values)
            return self

        def __exit__(self, *exc):
            for key, old in self.saved.items():
                if old is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = old
            return False

    return _Env()


def main() -> int:
    sandbox = Path(tempfile.mkdtemp(prefix="ragraf-state-iso-"))
    before = live_snapshot()
    try:
        print("=== 1. Выбор каталога состояния ===")
        default_dir = paths.catalog_dir()
        with with_env(RAGRAF_CATALOG_DIR=str(sandbox / "state")):
            chosen = paths.catalog_dir()
            check(chosen == sandbox / "state", "RAGRAF_CATALOG_DIR принимается", str(chosen))
            check(paths.settings_path() == sandbox / "state" / "settings.json",
                  "настройки идут за каталогом состояния", str(paths.settings_path()))
            described = paths.describe()
            check(described["catalog_source"] == "env",
                  "источник пути назван", json.dumps(described, ensure_ascii=False))
        check(paths.catalog_dir() == default_dir,
              "переменная снята — путь вернулся к умолчанию", str(paths.catalog_dir()))
        if os.environ.get("RAGRAF_CATALOG_DIR"):
            # Набор запущен под изоляцией раннера: умолчание здесь — отведённый
            # каталог, и проверять «каталог установки» бессмысленно. Об этом
            # говорится явно, чтобы пропуск не выглядел тишиной.
            print("  [--] умолчание не проверяется: задан RAGRAF_CATALOG_DIR")
        else:
            check(default_dir == REPO / "catalog",
                  "умолчание — каталог установки", str(default_dir))

        print("\n=== 1a. Каталог конфигурации корпуса отводится ===")
        # Измерено 31.10.2026: `ensure(create=True)` клал конфигурацию пробы в
        # живой `config/` проекта, а `config/**` входит в корпус RAGRAF; имя
        # содержит pid, поэтому КАЖДЫЙ прогон проверок делал корпус устаревшим
        # (две проверки подряд — две переиндексации, 49,6 и 42,8 с).
        with with_env(RAGRAF_CONFIG_DIR=str(sandbox / "conf")):
            check(paths.config_dir() == sandbox / "conf",
                  "RAGRAF_CONFIG_DIR принимается", str(paths.config_dir()))
            check(paths.describe().get("config_source") == "env",
                  "источник каталога конфигурации назван",
                  json.dumps(paths.describe(), ensure_ascii=False))
        check(paths.config_dir() == REPO / "config",
              "без переменной каталог конфигурации — каталог установки",
              str(paths.config_dir()))

        print("\n=== 2. Приоритет переменных ===")
        explicit = sandbox / "explicit-settings.json"
        with with_env(RAGRAF_CATALOG_DIR=str(sandbox / "state2"),
                      RAGRAF_SETTINGS_PATH=str(explicit)):
            check(paths.settings_path() == explicit,
                  "явный файл настроек сильнее каталога", str(paths.settings_path()))
            check(paths.catalog_dir() == sandbox / "state2",
                  "каталог состояния при этом не подменён", str(paths.catalog_dir()))

        print("\n=== 3. Настройки пишутся в отведённое место ===")
        state = sandbox / "state3"
        state.mkdir(parents=True, exist_ok=True)
        with with_env(RAGRAF_CATALOG_DIR=str(state)):
            store = SettingsStore()
            check(store.path == state / "settings.json", "хранилище взяло путь из окружения",
                  str(store.path))
            store.save({"dedupe_report": False})
            written = state / "settings.json"
            check(written.exists(), "файл настроек создан в песочнице", str(written))
            payload = json.loads(written.read_text(encoding="utf-8"))
            check("dedupe_report" in payload.get("settings", {}),
                  "значение действительно записано", json.dumps(payload, ensure_ascii=False)[:200])
        check(live_snapshot()["settings.json"] == before["settings.json"],
              "живые настройки не изменились",
              "файл отличается от снимка до проверки")

        print("\n=== 4. Реестр пишется в отведённое место ===")
        state4 = sandbox / "state4"
        state4.mkdir(parents=True, exist_ok=True)
        with with_env(RAGRAF_CATALOG_DIR=str(state4)):
            catalog = Catalog()
            check(catalog.path == state4 / "catalog.json", "реестр взял путь из окружения",
                  str(catalog.path))
            data = catalog.load()
            data.setdefault("entries", {})["probe_iso"] = {"root": str(sandbox), "name": "probe_iso"}
            catalog.save()
            check((state4 / "catalog.json").exists(), "реестр записан в песочницу",
                  str(state4 / "catalog.json"))
            check((state4 / "registry_history.jsonl").exists(),
                  "журнал состава идёт за реестром", str(state4 / "registry_history.jsonl"))
        check(live_snapshot()["catalog.json"] == before["catalog.json"],
              "живой реестр не изменился", "файл отличается от снимка до проверки")

        print("\n=== 5. Дочерний процесс: как наборы запускает раннер ===")
        state5 = sandbox / "state5"
        state5.mkdir(parents=True, exist_ok=True)
        code = (
            "import sys, json; sys.path.insert(0, r'%s');"
            "from ragraf.settings import SettingsStore;"
            "from ragraf.catalog import Catalog;"
            "s = SettingsStore(); s.save({'dedupe_report': False});"
            "c = Catalog(); d = c.load(); d.setdefault('entries', {})['child'] = {'root': 'x'}; c.save();"
            "print(json.dumps({'settings': str(s.path), 'catalog': str(c.path)}))"
        ) % str(SRC)
        env = dict(os.environ, RAGRAF_CATALOG_DIR=str(state5))
        proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=120, env=env, cwd=str(REPO))
        check(proc.returncode == 0, "дочерний процесс завершился успехом",
              (proc.stderr or "")[-300:])
        reported = {}
        try:
            reported = json.loads((proc.stdout or "").strip().splitlines()[-1])
        except (ValueError, IndexError):
            reported = {}
        check(str(state5) in str(reported.get("settings", "")),
              "потомок писал настройки в песочницу", json.dumps(reported, ensure_ascii=False))
        check(live_snapshot() == before, "живое состояние после потомка не изменилось",
              json.dumps({k: (before[k], v) for k, v in live_snapshot().items() if v != before[k]},
                         ensure_ascii=False))

        print("\n=== 6. test_ensure: два прогона подряд без провалов ===")
        # Плавающий провал 3 из 17 воспроизведён 30.10.2026 остатком каталога
        # `~/_ragraf_ensure_probe`: ensure отвечал `actual`, ветка `created` не
        # исполнялась, и 12 проверок не запускались. Здесь проверяется, что
        # прогон ВТОРОЙ раз даёт тот же результат, что первый.
        for attempt in (1, 2):
            run = subprocess.run([sys.executable, str(REPO / "tests" / "test_ensure.py")],
                                 cwd=str(REPO), capture_output=True, text=True,
                                 encoding="utf-8", errors="replace", timeout=900)
            tail = [ln for ln in (run.stdout or "").splitlines() if ln.strip()]
            summary = tail[-1] if tail else ""
            check(run.returncode == 0 and "провалено: 0" in summary,
                  f"прогон {attempt}: test_ensure без провалов", f"код {run.returncode}: {summary}")

        print("\n=== 7. Модули, которые пишет прогон, берут путь из `paths` ===")
        # Свойство сформулировано через текст модуля, а не через вызов: иначе
        # достаточно вернуть зашитый путь в новый модуль, и проверка промолчит.
        for rel in ("src/ragraf/settings.py", "src/ragraf/catalog.py", "src/ragraf/sources.py"):
            text = (REPO / rel).read_text(encoding="utf-8")
            hardcoded = 'parents[2] / "catalog"' in text or "REPO / \"catalog\"" in text
            check(not hardcoded and "paths." in text,
                  f"{rel}: путь состояния не зашит",
                  "модуль собирает путь к catalog/ сам, а не через paths")

        # Тот же класс для конфигурации корпуса: путь к `config/` не собирается
        # в модуле, который пишет файл.
        init_text = (REPO / "src" / "ragraf" / "init_corpus.py").read_text(encoding="utf-8")
        check("paths.config_dir()" in init_text
              and 'parents[2]\n                             / "config"' not in init_text,
              "init_corpus.py: каталог конфигурации берётся из paths",
              "модуль собирает путь к config/ сам")

        print("\n=== 8. paths только выбирает путь ===")
        absent = sandbox / "absent" / "catalog"
        with with_env(RAGRAF_CATALOG_DIR=str(absent)):
            check(paths.catalog_dir() == absent, "путь назван", str(paths.catalog_dir()))
            check(not absent.exists(), "каталог не создан при выборе пути", str(absent))
    finally:
        shutil.rmtree(sandbox, ignore_errors=True)
        if sandbox.exists():
            print(f"  [ВНИМАНИЕ] песочница не убрана: {sandbox}")

    print("\n=== ИТОГ ===")
    print(f"пройдено: {PASS}, провалено: {FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
