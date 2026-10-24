#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Гейт поставки: доказывает, что проверяльщик не молчит.

Проверка, которая никогда не падала, ничего не стоит. Поэтому гейт собирает
годное дерево и по одному вносит в него порчи, требуя, чтобы проверяльщик
КАЖДЫЙ РАЗ падал — и падал именно тем правилом, которое отвечает за эту порчу.
После каждой порчи дерево восстанавливается из источника, и гейт проверяет,
что оно снова признаётся годным: иначе «падение» могло быть следствием
предыдущей порчи, а не текущей.

Пять обязательных порч (по одной на класс дефекта):

1. `hash`     — байт подменён: файл есть, имя верное, содержимое чужое;
2. `coverage` — лишний файл с «безобидным» расширением;
3. `presence` — файл удалён;
4. `secrets`  — в текстовый файл дописано секретоносное значение;
5. `paths`    — в текстовый файл дописан путь машины автора (домашний каталог).

Дополнительно (защита в глубину, в счёт четырёх не входит): путь из списка
исключений, положенный в сборку, — правило `excluded` обязано его поймать.

Приманки. «Ловится всё подряд» — такой же дефект, как «не ловится ничего»:

* соседний каталог `community.bak` с поддельным `CHANGELOG.md` — проверяльщик
  обязан смотреть только в собранное дерево, а не в соседей (не мешает);
* файл `trap.bak` внутри сборки — обязан быть пойман правилом `excluded`:
  имя похоже на рабочее, расширение своё (ловушка на «проверять по расширению»).

Запуск:
    python tools/release/gate_release.py
    python tools/release/gate_release.py --keep     (оставить песочницу и напечатать путь)

Коды возврата: 0 — все порчи пойманы, 1 — гейт провален, 2 — аргументы, 3 — окружение.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# Замер (24.10.2026): у вендоренного Python 3.13 этого окружения каталог
# скрипта НЕ попадает в sys.path — `import relcommon` падал с ModuleNotFoundError,
# хотя файл лежит рядом. Путь добавляется явно: конвейер обязан работать
# одинаково и от системного Python, и от вендоренного.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import relcommon as rel  # noqa: E402 - путь к модулю добавлен выше

PY = sys.executable


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="gate_release.py", description="Гейт поставки RAGRAF.")
    parser.add_argument("--keep", action="store_true", help="не убирать песочницу после прогона")
    parser.add_argument("--source", default=None, help="корень живого дерева")
    parser.add_argument("--manifest", default=None, help="манифест")
    return parser.parse_args(argv)


def run(script: str, *args: str) -> tuple[int, str]:
    command = [PY, str(Path(__file__).resolve().parent / script), *args]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    return completed.returncode, (completed.stdout or "") + (completed.stderr or "")


def first_failure(output: str) -> str:
    for line in output.splitlines():
        if line.startswith("FAIL: "):
            return line[:220]
    return "(проверяльщик не назвал ни одного нарушения — это и есть дефект)"


def verify(tree: Path, only: str, manifest: Path | None, source: Path) -> tuple[int, str]:
    args = ["--tree", str(tree), "--only", only, "--source", str(source)]
    if manifest is not None:
        args += ["--manifest", str(manifest)]
    return run("verify_release.py", *args)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    source = Path(args.source).resolve() if args.source else rel.default_source()
    manifest = Path(args.manifest).resolve() if args.manifest else None

    root = Path(tempfile.mkdtemp(prefix="ragraf-gate-"))
    tree = root / "tree"
    failures: list[str] = []
    caught = 0

    build_args = ["--out", str(tree), "--source", str(source)]
    if manifest is not None:
        build_args += ["--manifest", str(manifest)]
    code, output = run("build_release.py", *build_args)
    print(f"сборка в песочницу: код {code}, {tree}")
    if code != 0:
        print(output)
        return 1

    manifest_path = manifest or (source / rel.MANIFEST_DEFAULT)
    plan = rel.collect(source, rel.load_manifest(manifest_path, source))
    targets = sorted(plan.files)
    if not targets:
        print("FAIL: манифест не дал ни одного файла — гейт проверять нечего")
        return 1

    code, output = verify(tree, "all", manifest, source)
    print(f"годная сборка: код {code}, {output.strip().splitlines()[-3] if output.strip() else '—'}")
    if code != 0:
        print(output)
        print("FAIL: годная сборка не признана годной — остальные порчи доказывали бы ничего")
        return 1

    # --- 1. Подмена байта -------------------------------------------------
    victim = next((name for name in targets if name.endswith(".md")), targets[0])
    saved = (tree / victim).read_bytes()
    mutated = bytearray(saved)
    index = next((i for i, byte in enumerate(mutated) if 65 <= byte <= 122), 0)
    mutated[index] = 66 if mutated[index] != 66 else 67
    (tree / victim).write_bytes(bytes(mutated))
    code, output = verify(tree, "hash", manifest, source)
    if code == 1:
        caught += 1
        print(f"[1/5] подмена байта в {victim}: ПОЙМАНО — {first_failure(output)}")
    else:
        failures.append(f"подмена байта в {victim} не поймана (код {code})")
        print(f"[1/5] подмена байта в {victim}: НЕ ПОЙМАНО")
    (tree / victim).write_bytes(saved)
    code, output = verify(tree, "hash", manifest, source)
    if code != 0:
        failures.append("после восстановления правило hash осталось красным — восстановление неполное")

    # --- 2. Лишний файл ---------------------------------------------------
    intruder = tree / "injected-report.txt"
    intruder.write_text("лишний файл, которого нет в манифесте\n", encoding="utf-8")
    code, output = verify(tree, "coverage", manifest, source)
    if code == 1:
        caught += 1
        print(f"[2/5] лишний файл injected-report.txt: ПОЙМАНО — {first_failure(output)}")
    else:
        failures.append(f"лишний файл не пойман (код {code})")
        print("[2/5] лишний файл injected-report.txt: НЕ ПОЙМАНО")
    intruder.unlink()
    code, output = verify(tree, "coverage", manifest, source)
    if code != 0:
        failures.append("после удаления лишнего файла правило coverage осталось красным")

    # --- 3. Удалённый файл ------------------------------------------------
    removed = next((name for name in targets if name.startswith("docs/")), targets[-1])
    copy = (tree / removed).read_bytes()
    (tree / removed).unlink()
    code, output = verify(tree, "presence", manifest, source)
    if code == 1:
        caught += 1
        print(f"[3/5] удалённый файл {removed}: ПОЙМАНО — {first_failure(output)}")
    else:
        failures.append(f"удаление {removed} не поймано (код {code})")
        print(f"[3/5] удалённый файл {removed}: НЕ ПОЙМАНО")
    (tree / removed).write_bytes(copy)
    code, output = verify(tree, "presence", manifest, source)
    if code != 0:
        failures.append("после возврата файла правило presence осталось красным")

    # --- 4. Секретоносное значение ---------------------------------------
    polluted = next((name for name in targets if name.endswith(".md")), targets[0])
    baseline = (tree / polluted).read_bytes()
    # Значение синтетическое и живёт только в песочнице этой проверки.
    synthetic = "token = " + "Zx9" * 8 + "\n"
    (tree / polluted).write_bytes(baseline + synthetic.encode("utf-8"))
    code, output = verify(tree, "secrets", manifest, source)
    if code == 1:
        caught += 1
        print(f"[4/5] секрет-строка в {polluted}: ПОЙМАНО — {first_failure(output)}")
    else:
        failures.append(f"секретоносное значение в {polluted} не поймано (код {code})")
        print(f"[4/5] секрет-строка в {polluted}: НЕ ПОЙМАНО")
    (tree / polluted).write_bytes(baseline)
    code, output = verify(tree, "secrets", manifest, source)
    if code != 0:
        failures.append("после очистки правило secrets осталось красным")

    # --- 5. Путь машины автора --------------------------------------------
    # Значение синтетическое: буква диска берётся у машины прогона, имя
    # пользователя вымышленное. В самом файле гейта пути не видно — иначе
    # правило 9 ловило бы эту проверку как нарушителя, и гейт пришлось бы
    # выводить из-под собственного правила.
    polluted = next((name for name in targets if name.endswith(".md")), targets[0])
    baseline = (tree / polluted).read_bytes()
    synthetic = (
        "\nПуть автора: " + (Path.home().drive or "C:") + "\\Users\\operator\\AppData\\Local\\Temp\n"
    )
    (tree / polluted).write_bytes(baseline + synthetic.encode("utf-8"))
    code, output = verify(tree, "paths", manifest, source)
    if code == 1:
        caught += 1
        print(f"[5/5] путь машины в {polluted}: ПОЙМАНО — {first_failure(output)}")
    else:
        failures.append(f"путь машины автора в {polluted} не пойман (код {code})")
        print(f"[5/5] путь машины в {polluted}: НЕ ПОЙМАНО")
    (tree / polluted).write_bytes(baseline)
    code, output = verify(tree, "paths", manifest, source)
    if code != 0:
        failures.append("после очистки правило paths осталось красным")

    # --- Защита в глубину: исключённый путь -------------------------------
    excluded_probe = tree / "docs/AGENTS-context.md"
    excluded_probe.parent.mkdir(parents=True, exist_ok=True)
    excluded_probe.write_text("внутренний документ, попавший в сборку\n", encoding="utf-8")
    code, output = verify(tree, "excluded", manifest, source)
    extra = code == 1
    print(f"[+] исключённый путь docs/AGENTS-context.md: {'ПОЙМАНО' if extra else 'НЕ ПОЙМАНО'} — {first_failure(output)}")
    if not extra:
        failures.append("путь из списка исключений не пойман правилом excluded")
    excluded_probe.unlink()

    # --- Приманки ---------------------------------------------------------
    neighbour = root / "community.bak"
    neighbour.mkdir(exist_ok=True)
    (neighbour / "CHANGELOG.md").write_text("подделка в соседнем каталоге\n", encoding="utf-8")
    code, output = verify(tree, "all", manifest, source)
    if code != 0:
        failures.append("соседний каталог community.bak помешал проверке — проверяльщик смотрит не туда")
        print(f"[приманка] соседний каталог community.bak: помешал ({first_failure(output)})")
    else:
        print("[приманка] соседний каталог community.bak: не помешал — зелено")

    trap = tree / "trap.bak"
    trap.write_text("файл с похожим именем и рабочим расширением\n", encoding="utf-8")
    code, output = verify(tree, "excluded", manifest, source)
    if code == 1:
        print(f"[приманка] trap.bak внутри сборки: поймана правилом excluded — {first_failure(output)}")
    else:
        failures.append("trap.bak внутри сборки не поймана правилом excluded")
        print("[приманка] trap.bak внутри сборки: НЕ ПОЙМАНА")
    trap.unlink()

    code, output = verify(tree, "all", manifest, source)
    if code != 0:
        failures.append("после снятия всех порч дерево не вернулось в годное состояние")
        print(output)

    print()
    print(f"порч поймано: {caught} из 5")
    print(f"приманок отработано: 2 из 2")
    if failures:
        for problem in failures:
            print(f"FAIL: {problem}")
        return 1
    if caught != 5:
        print("FAIL: поймано не пять порч — гейт не доказан")
        return 1
    if args.keep:
        print(f"песочница оставлена: {root}")
    else:
        shutil.rmtree(root, ignore_errors=True)
    print("гейт: пройден")
    return 0


if __name__ == "__main__":
    sys.exit(main())
