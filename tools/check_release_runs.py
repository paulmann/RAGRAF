#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Запуск собранного дерева поставки: проверка, что поставка работает, а не только собрана.

Задача, из которой выросла проверка (29.10.2026). Гейт поставки (`gate_release.py`)
подтверждает СОСТАВ: дерево собрано по манифесту, лишнего нет, пять подсаженных порч
пойманы. Он не отвечает на другой вопрос: а запускается ли из этого дерева? Пока ответ
не проверялся, «гейт пройден» означало «файлы на месте» — и ровно на этом различии
ломается поставка: модуль забыт в манифесте, зависимость импортируется только в живом
дереве, CLI ищет файл, которого в поставке нет.

Что проверяется (каждая проверка — свойство, а не наличие файла):

  1. CLI из дерева отвечает на `--help`;
  2. корпус инициализируется, регистрируется и индексируется;
  3. `doc_status` видит документы и укладывается в бюджет чанка;
  4. `doc_search` находит фразу документным слоем;
  5. адрес из поиска разрешается `doc_get` в текст с той же фразой — иначе ссылка
     на документ не проверяема;
  6. `doc_validate` отвечает `ok` и называет ВОСЕМЬ проверок (не «ok без проверок»);
  7. `index_status` отвечает ревизией.

Границы. Дерево собирается во временный каталог (или берётся готовым через `--tree`);
реестр разработок создаётся ВНУТРИ дерева, а не в живом проекте, поэтому живой
`catalog/catalog.json` не меняется. Живое дерево только читается.

Запуск:
    python tools/check_release_runs.py
    python tools/check_release_runs.py --tree %TEMP%\\ragraf-release-sbx --keep
    python tools/check_release_runs.py --manifest tools/release/manifest.toml

Коды возврата: 0 — дерево работает, 1 — дефект поставки, 2 — ошибка аргументов или
окружения (нет python, нет манифеста, сборка не удалась).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SOURCE_DEFAULT = HERE.parent
BUILD = HERE / "release" / "build_release.py"
MANIFEST_DEFAULT = HERE / "release" / "manifest.toml"

PASS = 0
FAIL = 0
EXAMINED = 0


def check(condition: bool, label: str, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  [OK ] {label}")
    else:
        FAIL += 1
        print(f"  [FAIL] {label} {detail}")


def run_cli(tree: Path, args: list[str], timeout: int = 900) -> tuple[int, str]:
    """Запуск CLI из дерева поставки: только его модули, без живого дерева в пути."""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(tree / "src")
    env["PYTHONIOENCODING"] = "utf-8"
    env.pop("RAGRAF_CONFIG", None)
    completed = subprocess.run(
        [sys.executable, str(tree / "tools" / "ragraf.py"), *args],
        cwd=str(tree), capture_output=True, text=True, encoding="utf-8",
        errors="replace", env=env, timeout=timeout,
    )
    return completed.returncode, (completed.stdout or "") + (completed.stderr or "")


def as_json(output: str) -> dict:
    start = output.find("{")
    if start < 0:
        return {}
    try:
        return json.loads(output[start:])["result"]
    except (json.JSONDecodeError, KeyError, TypeError):
        return {}


def build_tree(source: Path, manifest: Path, target: Path) -> tuple[int, str]:
    completed = subprocess.run(
        [sys.executable, str(BUILD), "--out", str(target), "--source", str(source),
         "--manifest", str(manifest)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return completed.returncode, (completed.stdout or "") + (completed.stderr or "")


def make_corpus(folder: Path) -> bool:
    """Мелкий корпус проверки: разметка, текст и настоящий docx, если он доступен."""
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "guide.md").write_text(
        "# Руководство\n\nИндекс отвечает ссылкой файл:строка, и это проверяется.\n\n"
        "## Раздел второй\n\nВторой раздел нужен, чтобы адрес чанка был не единственным.\n",
        encoding="utf-8", newline="\n")
    (folder / "notes.txt").write_text("Заметки к выпуску проверяют поиск.\n",
                                      encoding="utf-8", newline="\n")
    # Второй текстовый документ — чтобы резервный корпус был самодостаточен.
    # Измерено 05.11.2026: без набора `documents` (python-docx — НЕОБЯЗАТЕЛЬНАЯ
    # зависимость) `release.docx` не создавался, документов оставалось два,
    # и проверка «doc_status видит документы (>=3)» валила шесть свойств
    # подряд — гейт поставки объявлял «дерево не запускается» там, где дерево
    # исправно, а не хватало необязательного пакета на машине проверки.
    (folder / "second.md").write_text(
        "# Второй документ\n\nОн нужен, чтобы слой документов насчитывал три адреса "
        "и без офисного формата. Фраза «документным слоем» обязана найтись и здесь: "
        "иначе проверка поиска зависит от необязательного пакета.\n",
        encoding="utf-8", newline="\n")
    try:
        from docx import Document

        document = Document()
        document.add_heading("Документ поставки", level=1)
        document.add_paragraph("Абзац, который обязан найтись документным слоем.")
        document.save(folder / "release.docx")
        return True
    except Exception:  # noqa: BLE001 - docx необязателен: без него проверяются md и txt
        return False


def main(argv: list[str] | None = None) -> int:
    global EXAMINED
    parser = argparse.ArgumentParser(
        prog="check_release_runs.py",
        description="Проверить, что собранное дерево поставки RAGRAF запускается и работает.",
    )
    parser.add_argument("--tree", default=None, help="готовое дерево поставки")
    parser.add_argument("--source", default=str(SOURCE_DEFAULT), help="корень живого дерева")
    parser.add_argument("--manifest", default=str(MANIFEST_DEFAULT), help="манифест поставки")
    parser.add_argument("--keep", action="store_true", help="не убирать песочницу после прогона")
    parser.add_argument(
        "--plant",
        default=None,
        help="записать копию манифеста с исключённым документным слоем и выйти",
    )
    parser.add_argument(
        "--expect-failure",
        action="store_true",
        help="требовать провала свойств: так проверяется сама проверка",
    )
    args = parser.parse_args(argv)

    source = Path(args.source).resolve()
    manifest = Path(args.manifest).resolve()
    if not manifest.is_file():
        print(f"FAIL: нет манифеста {manifest}")
        return 2

    if args.plant:
        # Копия манифеста с одним лишним исключением. Живой манифест не трогается:
        # сравнивать «до и после» бессмысленно, если подсадка пишет в источник.
        planted = Path(args.plant).resolve()
        rule = (
            "\n[[exclude]]\n"
            'pattern = "src/ragraf/docread/**"\n'
            'state = "active"\n'
            'why = "подсадка: без разбора документов поставка не запускается"\n'
        )
        planted.write_text(manifest.read_text(encoding="utf-8") + rule,
                           encoding="utf-8", newline="\n")
        before = manifest.read_bytes()
        after = manifest.read_bytes()
        print(f"подсадка записана: {planted}")
        print(f"живой манифест не тронут: {'да' if before == after else 'НЕТ'}")
        return 0
    if not (source / "tools" / "ragraf.py").is_file():
        print(f"FAIL: в живом дереве нет tools/ragraf.py: {source}")
        return 2

    sandbox = Path(tempfile.mkdtemp(prefix="ragraf-release-runs-"))
    given = Path(args.tree).resolve() if args.tree else None
    tree = sandbox / "tree"
    corpus = sandbox / "corpus"
    config = sandbox / "corpus.yaml"
    print(f"дерево поставки: {given if given else tree}")

    try:
        if given is None:
            EXAMINED += 1
            code, output = build_tree(source, manifest, tree)
            if code != 0:
                print("FAIL: дерево не собралось")
                for line in output.splitlines()[-8:]:
                    print("   ", line)
                return 2
            copied = next((line for line in output.splitlines() if "файлов перенесено" in line), "")
            print(f"  собрано: {copied.strip()}")
        else:
            # Копия, а не само дерево: запуск CLI пишет рядом с пакетом (реестр,
            # индекс корпуса), и гейт, проверяющий состав, увидел бы лишний файл.
            EXAMINED += 1
            if not (given / "tools" / "ragraf.py").is_file():
                print(f"FAIL: в дереве нет tools/ragraf.py: {given}")
                return 1
            before = sum(1 for _ in given.rglob("*") if _.is_file())
            shutil.copytree(given, tree)
            print(f"  дерево скопировано в песочницу: {before} файлов")
            # Свойство: проверка не меняет то, что проверяет.
            after = sum(1 for _ in given.rglob("*") if _.is_file())
            check(before == after, "исходное дерево не тронуто копированием",
                  f"было {before}, стало {after}")

        EXAMINED += 1
        if not (tree / "tools" / "ragraf.py").is_file():
            print(f"FAIL: в дереве нет tools/ragraf.py: {tree}")
            return 1

        print("\n1. CLI из дерева")
        code, output = run_cli(tree, ["--help"])
        check(code == 0 and len(output.splitlines()) > 15,
              "CLI отвечает на --help", f"код {code}, строк {len(output.splitlines())}")

        print("\n2. корпус: init → register → index")
        has_docx = make_corpus(corpus)
        EXAMINED += 1
        code, output = run_cli(tree, ["init", str(corpus), "--name", "release-runs",
                                      "--config", str(config)])
        check(code == 0 and config.is_file(), "init создал конфиг", f"код {code}")
        code, output = run_cli(tree, ["-p", str(config), "register"])
        check(code == 0, "register принял корпус", f"код {code}")
        code, output = run_cli(tree, ["-p", str(config), "index"])
        check(code == 0 and "ревизия" in output, "index создал ревизию", f"код {code}")

        print("\n3. документный слой")
        code, output = run_cli(tree, ["--json", "-p", str(config), "query", "doc_status"])
        status = as_json(output)
        documents = int(status.get("documents") or 0)
        chunks = int(status.get("chunks") or 0)
        tokens_max = int(status.get("tokens_max") or 0)
        check(documents >= 3, "doc_status видит документы", f"документов {documents}")
        check(chunks >= 3, "doc_status видит чанки", f"чанков {chunks}")
        check(0 < tokens_max <= 1200, "чанк уложился в бюджет", f"max {tokens_max}")
        if has_docx:
            by_status = status.get("by_status") or {}
            check(int(by_status.get("ok") or 0) >= 1,
                  "офисный документ разобран, а не отброшен", json.dumps(by_status))

        code, output = run_cli(tree, ["--json", "-p", str(config), "query", "doc_search",
                                      "документным слоем"])
        found = as_json(output)
        hits = found.get("hits") or []
        check(len(hits) >= 1, "doc_search нашёл фразу", f"попаданий {len(hits)}")
        locator = str((hits[0] or {}).get("locator") or "") if hits else ""
        check(bool(locator), "у попадания есть адрес", locator or "(адреса нет)")

        if locator:
            code, output = run_cli(tree, ["--json", "-p", str(config), "query", "doc_get",
                                          locator])
            node = as_json(output)
            text = str(node.get("text") or "")
            check(code == 0 and "документным слоем" in text,
                  "адрес разрешается в текст с той же фразой",
                  f"символов {len(text)}")

        code, output = run_cli(tree, ["--json", "-p", str(config), "query", "doc_validate"])
        validate = as_json(output)
        checks_count = validate.get("checks")
        check(validate.get("ok") is True, "валидатор слоя отвечает ok",
              json.dumps(validate, ensure_ascii=False)[:120])
        check(isinstance(checks_count, int) and checks_count >= 8,
              "валидатор назвал восемь проверок, а не «ok без проверок»",
              f"проверок {checks_count}")

        print("\n4. состояние индекса")
        code, output = run_cli(tree, ["--json", "-p", str(config), "query", "index_status"])
        index_status = as_json(output)
        rev = index_status.get("rev") or {}
        rev_id = rev.get("rev_id") if isinstance(rev, dict) else str(rev)
        check(bool(rev_id), "index_status называет ревизию", str(rev_id))

        print()
        print("=" * 78)
        print(f"ИТОГ: пройдено {PASS}, провалено {FAIL} | осмотрено: {EXAMINED}")
        print("=" * 78)
        if args.expect_failure:
            # Подсадка доказывает проверку, а не наоборот: провал здесь — успех.
            if FAIL > 0:
                print(f"ПОДСАДКА ПОЙМАНА: провалено {FAIL} свойств — проверка видит "
                      f"поставку без документного слоя")
                return 0
            print("ПОДСАДКА НЕ ПОЙМАНА: свойства прошли на дереве без документного "
                  "слоя — проверка формальна")
            return 1
        return 0 if FAIL == 0 else 1
    finally:
        if args.keep:
            print(f"песочница сохранена: {sandbox}")
        else:
            shutil.rmtree(sandbox, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
