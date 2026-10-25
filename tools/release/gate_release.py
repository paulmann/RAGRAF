#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Гейт поставки: доказывает, что проверяльщик не молчит.

Проверка, которая никогда не падала, ничего не стоит. Поэтому гейт собирает
годное дерево и по одному вносит в него порчи, требуя, чтобы проверяльщик
КАЖДЫЙ РАЗ падал — и падал именно тем правилом, которое отвечает за эту порчу.
После каждой порчи дерево восстанавливается из источника, и гейт проверяет,
что оно снова признаётся годным: иначе «падение» могло быть следствием
предыдущей порчи, а не текущей.

Шаг 0 — числа публикации. Помимо порч гейт требует, чтобы README называл
размеры АКТИВНОЙ ревизии. Чекер различает «отстал» (числа были верны на прошлой
ревизии — предупреждение) и «врёт» (такого состояния не было — ошибка), но
предупреждение обходится тем, что публикацию просто запускают. Поэтому здесь
отставание — отказ: на выход людям уходит только сверенный замер.

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
import json
import re
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
    parser.add_argument(
        "--check-numbers",
        action="store_true",
        help="только шаг 0: сверить числа README с активной ревизией и выйти",
    )
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


# Форма записи размеров индекса в README. Та же, что в чекере
# (tools/Invoke-ProjectDoctrine.ps1, Test-DocumentedNumbers): одна форма на два
# потребителя, иначе «чекер молчит» перестаёт что-либо значить для публикации.
NUMBERS_RE = re.compile(
    r"индекс RAGRAF — ([\d ]+?)\s*файл\w*,\s*([\d ]+?)\s*уз(?:ел|л)\w*\s+и\s+([\d ]+?)\s*р[её]б\w*"
)


def published_numbers(source: Path) -> tuple[int, int, int] | None:
    """Числа, которые README называет размером собственного индекса."""
    readme = source / "README.md"
    if not readme.exists():
        return None
    text = readme.read_text(encoding="utf-8", errors="replace").replace("\u00a0", " ")
    match = NUMBERS_RE.search(text)
    if not match:
        return None
    try:
        files, nodes, edges = (int(part.replace(" ", "")) for part in match.groups())
    except ValueError:  # pragma: no cover — форма совпала, а число не разобралось
        return None
    return files, nodes, edges


def check_numbers(source: Path) -> tuple[int, str]:
    """Числа README обязаны совпадать с АКТИВНОЙ ревизией — без варианта «отстал».

    Источник факта берётся штатным инструментом `tools/rev_snapshot.py`:
    SQL по истории ревизий живёт в одном месте, и публикация не заводит
    вторую редакцию того же запроса.
    """
    numbers = published_numbers(source)
    if numbers is None:
        return 1, ("README не называет размеры собственного индекса: публиковать "
                   "нечего сверять — снимок без числа не измерение")
    files, nodes, edges = numbers
    tool = source / "tools" / "rev_snapshot.py"
    config = source / "config" / "ragraf.yaml"
    if not tool.exists() or not config.exists():
        return 1, f"нет {tool} или {config}: числа README сверить не с чем"
    completed = subprocess.run(
        [PY, str(tool), "--project", str(config), "--match", f"{files},{nodes},{edges}"],
        capture_output=True, text=True, check=False,
    )
    payload = None
    for line in reversed((completed.stdout or "").splitlines()):
        if line.strip().startswith("{"):
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                payload = None
            break
    if completed.returncode != 0 or not payload or not payload.get("active"):
        detail = (payload or {}).get("error") or (completed.stderr or "").strip()
        return 1, f"история ревизий не прочитана: {detail[:300]}"
    active = payload["active"]
    stated = f"{files} / {nodes} / {edges}"
    actual = f"{active['files']} / {active['nodes']} / {active['edges']}"
    if stated == actual:
        return 0, (f"числа README совпадают с активной ревизией "
                   f"{active['rev_id']} ({actual})")
    # Различается только число рёбер — назвать замер, иначе отказ выглядит
    # ошибкой документа. Измерено 25.10.2026: 64 971 / 64 972 / 64 968 при
    # неизменном дереве (число разрешённых вызовов из файла CPG).
    edges_only = (files == int(active["files"]) and nodes == int(active["nodes"])
                  and edges != int(active["edges"]))
    note = (" (файлы и узлы совпадают с активной ревизией, различается только "
            "число рёбер; оно не воспроизводится между пересборами — замер "
            "25.10.2026: 64 971 / 64 972 / 64 968)" if edges_only else "")
    match = payload.get("match")
    if match:
        hint = (f"это состояние {match['rev_id']} ({match['created_at']}), то есть "
                f"отставание: обновить числа перед публикацией")
    else:
        hint = ("такого состояния не было ни в одной ревизии: документ называет "
                "состояние, которого не было")
    return 1, (f"README называет {stated}, активная ревизия {active['rev_id']} — "
               f"{actual}; {hint}{note}")


def verify(tree: Path, only: str, manifest: Path | None, source: Path) -> tuple[int, str]:
    args = ["--tree", str(tree), "--only", only, "--source", str(source)]
    if manifest is not None:
        args += ["--manifest", str(manifest)]
    return run("verify_release.py", *args)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    source = Path(args.source).resolve() if args.source else rel.default_source()
    manifest = Path(args.manifest).resolve() if args.manifest else None

    # --- 0. Числа публикации ---------------------------------------------
    # Чекер различает «отстал» и «врёт», но предупреждение об отставании
    # обходится тем, что публикацию запускают вручную. Здесь варианта «отстал»
    # нет: наружу уходит только сверенный с активной ревизией замер.
    numbers_code, numbers_message = check_numbers(source)
    if args.check_numbers:
        print(("OK: " if numbers_code == 0 else "FAIL: ") + numbers_message)
        return numbers_code
    print(f"числа публикации: код {numbers_code} — {numbers_message}")
    if numbers_code != 0:
        print("FAIL: публикация остановлена — числа README не совпадают с активной ревизией")
        return 1

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
