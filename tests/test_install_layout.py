#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Установка не пишет состояние внутрь каталога интерпретатора.

Запуск: python tests/test_install_layout.py

Зачем набор. Этап 15 начался с замера на чистой машине (Debian, venv,
python 3.13.18, колесо 0.5.6):

    $ ragraf init proj
    конфигурация уже существует: /home/cbs-test/i15-clean/venv/lib/python3.13/config/proj.yaml

То есть конфигурация корпуса легла в каталог интерпретатора: `paths.REPO`
считается как `parents[2]` от `src/ragraf/paths.py`, а в установленном колесе
это `<venv>/lib/python3.13`. Туда же попадали реестр, настройки, секреты и
ЛИЦЕНЗИЯ — а обновление venv уносит их вместе с интерпретатором.

Что проверяется:

  1. рабочая копия (рядом с кодом есть `pyproject.toml` и `src/ragraf`) —
     каталог состояния остаётся в корне копии, как было;
  2. установка (есть `lib/python3.13/site-packages/ragraf`, нет
     `pyproject.toml`) — состояние уходит в `RAGRAF_HOME` (по умолчанию
     `~/.ragraf`), а НЕ в каталог интерпретатора;
  3. переменная `RAGRAF_CATALOG_DIR` сильнее обоих раскладов;
  4. лицензия и открытый ключ берутся из того же каталога состояния
     (`edition.license_file()`), а не из `REPO/catalog`.

Расклад «установка» воспроизводится в `%TEMP%`: копируется настоящий
`paths.py`, рядом создаётся пустой `pyproject.toml`-отсутствие и каталог
`lib/python3.13`. Набор ничего не пишет в живое состояние: `RAGRAF_HOME`
указывает в тот же временный каталог.

Подсадка (проверено при написании): вернуть `REPO / "catalog"` в
`catalog_dir()` — пункт 2 проваливается тремя проверками.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
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


PROBE = r"""
import json, sys
sys.path.insert(0, __SITE__)   # путь подставляется ниже: python в этой среде
                               # изолирован и PYTHONPATH игнорирует
from ragraf import paths, edition
print(json.dumps({
    "catalog": str(paths.catalog_dir()),
    "config": str(paths.config_dir()),
    "settings": str(paths.settings_path()),
    "license": str(edition.license_file()),
    "layout": paths.describe().get("layout"),
    "source": paths.describe().get("catalog_source"),
    "install_root": paths.describe().get("install_root"),
    "home": paths.describe().get("home_dir"),
}, ensure_ascii=False))
"""


def run_probe(pythonpath: Path, env_extra: dict[str, str]) -> dict:
    env = {k: v for k, v in os.environ.items()
           if k not in ("RAGRAF_CATALOG_DIR", "RAGRAF_CONFIG_DIR", "RAGRAF_HOME",
                        "RAGRAF_SETTINGS_PATH")}
    env["PYTHONPATH"] = str(pythonpath)
    env.update(env_extra)
    code = PROBE.replace("__SITE__", repr(str(pythonpath)))
    proc = subprocess.run([sys.executable, "-c", code], cwd=str(pythonpath.parent),
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", env=env)
    if proc.returncode != 0:
        raise SystemExit(f"проба упала: {proc.stderr[-800:]}")
    return json.loads(proc.stdout.strip().splitlines()[-1])


def make_install_layout(base: Path) -> Path:
    """`<base>/lib/python3.13/site-packages/ragraf/paths.py` и все соседи."""
    site = base / "lib" / "python3.13" / "site-packages"
    pkg = site / "ragraf"
    pkg.mkdir(parents=True, exist_ok=True)
    # Копируется ВЕСЬ пакет: `edition.py` тянет `signing` и `util`, и без них
    # проба падала бы на импорте, а не на проверяемом свойстве.
    shutil.rmtree(pkg, ignore_errors=True)
    shutil.copytree(REPO / "src" / "ragraf", pkg)
    return site


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="i15-layout-"))
    try:
        home = tmp / "home"
        home.mkdir()

        print("\n=== 1. Рабочая копия: путь остаётся в корне копии ===")
        checkout = run_probe(REPO / "src", {"RAGRAF_HOME": str(home)})
        print(f"  расклад: {checkout['layout']}, источник: {checkout['source']}")
        check(checkout["layout"] == "checkout", "расклад опознан как рабочая копия",
              checkout["layout"])
        check(Path(checkout["catalog"]).resolve() == (REPO / "catalog").resolve(),
              "каталог состояния — в корне копии", checkout["catalog"])
        check(Path(checkout["config"]).resolve() == (REPO / "config").resolve(),
              "каталог конфигов — в корне копии", checkout["config"])
        check(checkout["source"] == "checkout",
              "источник пути назван «checkout», а не «install»", checkout["source"])

        print("\n=== 2. Установка: состояние уходит в домашний каталог ===")
        install = tmp / "venv"
        site = make_install_layout(install)
        installed = run_probe(site, {"RAGRAF_HOME": str(home)})
        print(f"  расклад: {installed['layout']}, каталог: {installed['catalog']}")
        check(installed["layout"] == "install", "расклад опознан как установка",
              installed["layout"])
        check(Path(installed["catalog"]).resolve() == (home / "catalog").resolve(),
              "каталог состояния — в RAGRAF_HOME", installed["catalog"])
        check(Path(installed["config"]).resolve() == (home / "config").resolve(),
              "каталог конфигов — в RAGRAF_HOME", installed["config"])
        check(Path(installed["license"]).resolve() == (home / "catalog" / "edition.license").resolve(),
              "лицензия ищется в каталоге состояния", installed["license"])
        root = Path(installed["install_root"]).resolve()
        mine = Path(installed["catalog"]).resolve()
        check(root not in mine.parents and root != mine,
              "состояние НЕ внутри каталога установки", f"корень {root}, состояние {mine}")
        check(Path(installed["settings"]).resolve() == (home / "catalog" / "settings.json").resolve(),
              "файл настроек — в каталоге состояния", installed["settings"])

        print("\n=== 3. Переменная окружения сильнее обоих раскладов ===")
        forced = tmp / "forced"
        env = run_probe(site, {"RAGRAF_CATALOG_DIR": str(forced),
                               "RAGRAF_HOME": str(home)})
        check(Path(env["catalog"]).resolve() == forced.resolve(),
              "RAGRAF_CATALOG_DIR отводит каталог и на установке", env["catalog"])
        check(env["source"] == "env", "источник пути назван «env»", env["source"])
        env2 = run_probe(REPO / "src", {"RAGRAF_CATALOG_DIR": str(forced),
                                        "RAGRAF_HOME": str(home)})
        check(Path(env2["catalog"]).resolve() == forced.resolve(),
              "RAGRAF_CATALOG_DIR отводит каталог и в рабочей копии", env2["catalog"])

        print("\n=== 4. Домашний каталог по умолчанию ===")
        default = run_probe(site, {})
        expected = Path.home() / ".ragraf"
        check(Path(default["home"]).resolve() == expected.resolve(),
              "без RAGRAF_HOME домашний каталог — ~/.ragraf", default["home"])
        check(Path(default["catalog"]).resolve() == (expected / "catalog").resolve(),
              "состояние по умолчанию — ~/.ragraf/catalog", default["catalog"])

        print("\n" + "=" * 78)
        print(f"итог: пройдено {PASS}, провалено {FAIL}")
        print("=" * 78)
        return 0 if FAIL == 0 else 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
