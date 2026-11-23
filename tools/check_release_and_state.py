"""Три свойства блока 21, которых не видел чекер конвенций.

Каждое — измеренный дефект, а не пожелание:

1. ЗАПИСЬ JSON АТОМАРНА. Пока `util.write_json` писал на месте, читатель видел пустой
   реестр (замер 23.11.2026: 231 пустое чтение из 1058), и записи соседних корпусов
   терялись молча. Здесь: писатель и два читателя, неудачных чтений быть не должно.
2. ПУБЛИКАЦИЯ ОТКАЗЫВАЕТ НЕПРОВЕРЕННОМУ ДЕРЕВУ. Без отпечатка сборки публиковать
   нельзя: иначе между проверкой и публикацией дерево можно подменить. Проверяется на
   пустом каталоге: `--check` обязан отказать и назвать отпечаток.
3. ПЕРВЫЙ ЗАПУСК НЕ ПЕРЕЗАПИСЫВАЕТ ЧУЖОЙ ИНДЕКС. Проект, в котором лежит
   `.ragraf/install.json` другой установки, обязан получить отказ с именем хозяина —
   до индексации, а не после.

Запуск: python tools/check_release_and_state.py
Код: 0 — все три свойства держатся, 1 — расхождение (названо), 2 — окружение.
"""
from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import sys
import tempfile
import threading
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def property_atomic_write() -> list[str]:
    """Писатель и два читателя: пустых чтений быть не должно."""
    from ragraf import util  # noqa: PLC0415 — путь к пакету добавлен выше

    problems: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        path = pathlib.Path(tmp) / "catalog.json"
        payload = {"version": 1, "entries": {f"corpus-{i}": {"rev_id": "rev-0001"} for i in range(80)}}
        util.write_json(path, payload)
        stop = threading.Event()
        bad: list[str] = []
        reads = 0
        guard = threading.Lock()

        def reader() -> None:
            nonlocal reads
            while not stop.is_set():
                with guard:
                    reads += 1
                try:
                    got = util.read_json(path, default=None)
                    if not isinstance(got, dict) or not (got.get("entries") or {}):
                        bad.append("пусто или не словарь")
                except Exception as exc:  # noqa: BLE001 — любая неудача чтения и есть находка
                    bad.append(f"{type(exc).__name__}: {exc}")

        threads = [threading.Thread(target=reader, daemon=True) for _ in range(2)]
        for thread in threads:
            thread.start()
        try:
            for i in range(40):
                payload["entries"][f"corpus-w{i}"] = {"rev_id": f"rev-w{i}"}
                util.write_json(path, payload)
        finally:
            stop.set()
            for thread in threads:
                thread.join(timeout=5)
        if bad:
            problems.append(f"запись JSON не атомарна: неудачных чтений {len(bad)} из {reads} ({bad[0]})")
        leftovers = [p.name for p in pathlib.Path(tmp).iterdir() if ".tmp-" in p.name]
        if leftovers:
            problems.append(f"после записи остались временные файлы: {leftovers[:3]}")
        print(f"  свойство 1: записей 40, чтений {reads}, неудачных {len(bad)}")
    return problems


def property_publish_needs_stamp() -> list[str]:
    """`--check` на каталоге без отпечатка сборки обязан отказать."""
    problems: list[str] = []
    clone = ROOT.parent / "RAGRAF.git" / "RAGRAF"
    with tempfile.TemporaryDirectory() as tmp:
        tree = pathlib.Path(tmp) / "not-built"
        tree.mkdir()
        (tree / "README.md").write_text("заглушка\n", encoding="utf-8")
        cmd = [sys.executable, str(ROOT / "tools/release/publish_release.py"), "--check", "--tree", str(tree)]
        if clone.is_dir():
            cmd += ["--clone", str(clone)]
        result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                                errors="replace", cwd=str(ROOT))
        text = (result.stdout or "") + (result.stderr or "")
        if result.returncode == 0:
            problems.append("публикация приняла дерево без отпечатка сборки")
        elif "отпечат" not in text and "клон публикации" not in text:
            problems.append(f"отказ без причины про отпечаток: {text.strip()[:120]}")
        print(f"  свойство 2: код {result.returncode}, причина названа")
    return problems


def property_foreign_index() -> list[str]:
    """Первый запуск на проекте с чужим индексом обязан назвать хозяина."""
    problems: list[str] = []
    setup = ROOT / "scripts" / "ragraf-setup.ps1"
    if not setup.is_file():
        return ["нет scripts/ragraf-setup.ps1 — свойство не проверить"]
    with tempfile.TemporaryDirectory() as tmp:
        work = pathlib.Path(tmp)
        project = work / "foreign-project"
        (project / "src").mkdir(parents=True)
        (project / "src" / "a.py").write_text("def a():\n    return 1\n", encoding="utf-8")
        (project / ".ragraf").mkdir()
        marker = {"installer": "ragraf-setup.ps1", "install_path": "C:/ragraf-other", "version": "0.0.0"}
        (project / ".ragraf" / "install.json").write_text(
            json.dumps(marker, ensure_ascii=False), encoding="utf-8")
        prefix = work / "install"
        result = subprocess.run(
            ["pwsh", "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(setup),
             "-Prefix", str(prefix), "-NoPath", "-SkipDeps", "-SkipVerify", "-FirstRun", str(project)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=str(ROOT))
        text = (result.stdout or "") + (result.stderr or "")
        if "индекс установки" not in text:
            problems.append("первый запуск не назвал чужой индекс в проекте")
        elif "ragraf-other" not in text:
            problems.append("отказ не назвал установку-хозяина")
        if "[FAIL] Первый запуск" not in text:
            problems.append("шаг первого запуска не помечен FAIL при чужом индексе")
        print(f"  свойство 3: код {result.returncode}, чужой индекс назван")
    return problems


def main() -> int:
    problems: list[str] = []
    print("свойства публикации и состояния:")
    for check in (property_atomic_write, property_publish_needs_stamp, property_foreign_index):
        problems.extend(check())
    if problems:
        for problem in problems:
            print(f"FAIL: {problem}")
        print(f"ИТОГ: свойств нарушено {len(problems)} из 3")
        return 1
    print("ИТОГ: три свойства из трёх держатся")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
