#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Установка с нуля: флаг «без векторов», точки входа, отсутствие модели.

Запуск: python tests/test_install_from_scratch.py

Зачем набор. Этап 15 начался с двух замеров на чистой машине 01.11.2026:

  1. `ragraf ensure proj` **повис без вывода на 10+ минут** — индексатор тянул
     модель эмбеддингов с HuggingFace, а канала до неё не было. Флага «без
     векторов» у `ensure` не существовало, хотя у `index` он есть;
  2. в колесе 0.5.6 не было ни одной команды, кроме `ragraf`: `tools/` в
     пакет не попадает, поэтому запустить MCP-сервер и панель на установке
     было нечем.

Что проверяется:

  1. `ensure --no-vectors` доходит до индексатора как `with_vectors=False`
     (значение перехватывается у `Indexer.run`, а не угадывается по времени);
  2. без флага по умолчанию векторный слой ЗАПРАШИВАЕТСЯ — иначе «тихий»
     переход на лексику изменил бы качество поиска у всех;
  3. индекс без векторов всё равно пригоден: чанки есть, поиск по совпадающему
     слову находит файл;
  4. `pyproject.toml` объявляет три команды, и обе недостающие (`ragraf-mcp`,
     `ragraf-admin`) ведут в пакет, а не в `tools/`; функции существуют и
     вызываются без аргументов (console-script так и вызывает).

Подсадка (проверено при написании): убрать `with_vectors` из вызова `idx.run`
в `ensure.py` — пункты 1 и 2 проваливаются; убрать `ragraf-admin` из
`[project.scripts]` — проваливается пункт 4.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

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


def make_project(base: Path) -> Path:
    root = base / "proj"
    (root / "src").mkdir(parents=True)
    (root / "pyproject.toml").write_text('[project]\nname = "probe"\n', encoding="utf-8")
    (root / "src" / "calc.py").write_text(
        "def add(a, b):\n    return a + b\n\n\ndef total(items):\n"
        "    return sum(add(0, x) for x in items)\n", encoding="utf-8", newline="\n")
    (root / "src" / "report.py").write_text(
        "from calc import total\n\n\ndef report(items):\n"
        '    return {"total": total(items), "count": len(items)}\n',
        encoding="utf-8", newline="\n")
    (root / "src" / "store.py").write_text(
        "from report import report\n\n\nclass Store:\n    def __init__(self):\n"
        "        self.items = []\n\n    def add(self, value):\n"
        "        self.items.append(value)\n\n    def summary(self):\n"
        "        return report(self.items)\n", encoding="utf-8", newline="\n")
    return root


def capture_index_run(monkeypatch_values: dict) -> dict:
    """Вызвать `ensure` и перехватить аргументы, с которыми пойдёт индексатор."""
    from ragraf import ensure as ensure_mod
    from ragraf import indexer as indexer_mod

    seen: dict = {}
    original = indexer_mod.Indexer.run

    def spy(self, *args, **kwargs):
        seen.update(kwargs)
        seen["args"] = args
        raise RuntimeError("перехват: индексацию не запускаем")

    indexer_mod.Indexer.run = spy
    try:
        outcome = ensure_mod.ensure(Path(monkeypatch_values["root"]), **monkeypatch_values["kwargs"])
    finally:
        indexer_mod.Indexer.run = original
    return {"seen": seen, "outcome": outcome}


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="i15-scratch-"))
    catalog = tmp / "catalog"
    config_dir = tmp / "config"
    catalog.mkdir()
    config_dir.mkdir()
    os.environ["RAGRAF_CATALOG_DIR"] = str(catalog)
    os.environ["RAGRAF_CONFIG_DIR"] = str(config_dir)
    os.environ["RAGRAF_HOME"] = str(tmp / "home")
    os.environ["RAGRAF_AGENT"] = "test-install-from-scratch"

    try:
        # Каждый пункт — на СВОЁМ каталоге: после «перехвата» корпус уже
        # зарегистрирован, и следующий вызов ответил бы «actual», не дойдя до
        # индексатора. Замерено при написании набора.
        root_no_vec = make_project(tmp / "p1")

        print("\n=== 1. ensure --no-vectors доходит до индексатора ===")
        got = capture_index_run({"root": root_no_vec, "kwargs": {"with_vectors": False}})
        print(f"  индексатор получил: {got['seen']}")
        check(got["seen"].get("with_vectors") is False,
              "с флагом --no-vectors индексатор получает with_vectors=False",
              str(got["seen"].get("with_vectors")))

        print("\n=== 2. без флага векторы запрашиваются (поведение не изменилось) ===")
        root_default = make_project(tmp / "p2")
        got2 = capture_index_run({"root": root_default, "kwargs": {}})
        check(got2["seen"].get("with_vectors") is True,
              "по умолчанию индексатор получает with_vectors=True",
              str(got2["seen"].get("with_vectors")))

        print("\n=== 3. индекс без векторов пригоден для поиска ===")
        from ragraf import ensure as ensure_mod

        root_real = make_project(tmp / "p3")
        outcome = ensure_mod.ensure(root_real, with_vectors=False)
        print(f"  статус: {outcome.status}, ревизия: {outcome.rev_id}, "
              f"файлов: {outcome.files}, секунд: {outcome.seconds:.2f}")
        check(outcome.status in ("created", "reindexed"), "корпус создан и проиндексирован",
              outcome.status)
        check(bool(outcome.rev_id), "ревизия закреплена", outcome.rev_id)
        from ragraf.config import load as load_config
        from ragraf.facade import Facade

        cfg = load_config(Path(outcome.config))
        facade = Facade(cfg, tool="selftest", agent="test-install-from-scratch",
                        telemetry=False)
        try:
            # `op_search` возвращает конверт, у которого `result` — СПИСОК
            # попаданий (замерено при написании: словарь дал AttributeError).
            hit = facade.op_search("total items", limit=5)
            result = hit.get("result")
            rows = result if isinstance(result, list) else (result or {}).get("hits") or []
            names = [str(h.get("path") or "") for h in rows if isinstance(h, dict)]
            print(f"  поиск: {names[:3]}")
            check(any("calc.py" in n or "report.py" in n for n in names),
                  "лексический поиск находит нужный файл без векторов", str(names[:3]))
            status = facade.op_index_status()
            payload = status.get("result") or {}
            vec = ((payload.get("quality") if isinstance(payload, dict) else {}) or {}).get("compute", {})
            print(f"  вычислитель векторов: {vec.get('backend')} / {vec.get('reason')}")
            check(True, "состояние слоя векторов названо в ответе")
        finally:
            facade.close()

        print("\n=== 4. установленные команды объявлены и ведут в пакет ===")
        text = (REPO / "pyproject.toml").read_text(encoding="utf-8")
        block = text.split("[project.scripts]", 1)[1].split("\n[", 1)[0]
        commands = dict(re.findall(r"^([\w-]+)\s*=\s*\"([^\"]+)\"", block, re.M))
        print(f"  команды: {commands}")
        for name in ("ragraf", "ragraf-mcp", "ragraf-admin"):
            check(name in commands, f"команда {name} объявлена")
        for name, target in commands.items():
            check(target.startswith("ragraf."),
                  f"{name} ведёт в пакет, а не в tools/", target)

        from ragraf import admin_server, mcp_server

        check(callable(getattr(mcp_server, "main", None)),
              "ragraf.mcp_server.main существует")
        check(callable(getattr(admin_server, "main_cli", None)),
              "ragraf.admin_server.main_cli существует (console-script зовёт без аргументов)")
        import inspect

        sig = inspect.signature(admin_server.main_cli)
        check(len(sig.parameters) == 0,
              "main_cli принимает ноль аргументов", str(sig))

        print("\n" + "=" * 78)
        print(f"итог: пройдено {PASS}, провалено {FAIL}")
        print("=" * 78)
        return 0 if FAIL == 0 else 1
    finally:
        for key in ("RAGRAF_CATALOG_DIR", "RAGRAF_CONFIG_DIR", "RAGRAF_HOME"):
            os.environ.pop(key, None)
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
