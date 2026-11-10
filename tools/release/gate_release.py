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

То же требование распространено на ПЕРЕВОДЫ README (этап И22, 27.10.2026):
числа индекса в каждом переводе сверяются с активной ревизией проверкой
`tools/check_translations.py` в режиме `--strict`, где отставание — находка.
Иначе перевод мог бы уехать читателю с числами прошлой ревизии при зелёном гейте.

И на строку реестра (этап И33, 28.10.2026): «по реестру на <дата>: N корпусов,
M проиндексированы» в шести документах сверяется с `catalog/catalog.json`
инструментом `tools/registry_line.py` в том же режиме `--strict`. Чекер
различает «отстал» и «врёт» и на отставание предупреждает; публикация — нет:
там отставание тоже отказ.

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
import atexit
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
import build_release as builder  # noqa: E402 - тот же каталог и тот же поиск журнала
import release_report as report_mod  # noqa: E402 - отчёт выпуска (X21)

PY = sys.executable


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="gate_release.py", description="Гейт поставки RAGRAF.")
    parser.add_argument("--keep", action="store_true", help="не убирать песочницу после прогона")
    parser.add_argument("--source", default=None, help="корень живого дерева")
    parser.add_argument("--manifest", default=None, help="манифест")
    parser.add_argument("--previous", default=None,
                        help="манифест описаний прошлого выпуска (ops_pins.json) "
                             "для сводки изменений")
    parser.add_argument("--allow-breaking", dest="allow_breaking", default="",
                        help="имена объектов, чьё ломающее изменение разрешено "
                             "(через запятую); требует --break-reason")
    parser.add_argument("--break-reason", dest="break_reason", default="",
                        help="почему ломающее изменение допущено (обязательно "
                             "при --allow-breaking)")
    parser.add_argument("--report", default="",
                        help="записать итог выпуска в markdown (без времени и путей)")
    parser.add_argument("--json-out", dest="json_out", default="",
                        help="записать итог выпуска в JSON (машинный ответ)")
    parser.add_argument("--json", dest="json_stdout", action="store_true",
                        help="напечатать JSON итога в stdout (человеческий вывод остаётся)")
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
    r"the RAGRAF index itself — ([\d ]+?)\s*files,\s*([\d ]+?)\s*nodes\s+and\s+([\d ]+?)\s*edges"
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
    # неизменном дереве (число разрешённых вызовов из файла CPG). Повторный замер
    # 29.10.2026 расхождения НЕ дал (три прогона — 76 103, затем два — 76 166),
    # поэтому сообщение называет дату замера, а не выдаёт прошлое за текущее;
    # проверка — tools/check_edges_repeatable.py.
    edges_only = (files == int(active["files"]) and nodes == int(active["nodes"])
                  and edges != int(active["edges"]))
    note = (" (файлы и узлы совпадают с активной ревизией, различается только "
            "число рёбер; замер 25.10.2026 дал разброс 64 971 / 64 972 / 64 968, "
            "повторный замер 29.10.2026 — одно число; проверить: "
            "tools/check_edges_repeatable.py)" if edges_only else "")
    match = payload.get("match")
    if match:
        hint = (f"это состояние {match['rev_id']} ({match['created_at']}), то есть "
                f"отставание: обновить числа перед публикацией")
    else:
        hint = ("такого состояния не было ни в одной ревизии: документ называет "
                "состояние, которого не было")
    return 1, (f"README называет {stated}, активная ревизия {active['rev_id']} — "
               f"{actual}; {hint}{note}")


def check_translation_numbers(source: Path) -> tuple[int, str]:
    """Числа в ПЕРЕВОДАХ README обязаны совпадать с активной ревизией — без «отстал».

    Та же логика, что у чисел README, и тот же источник факта, но проверка не
    повторяется здесь второй редакцией: её выполняет `tools/check_translations.py`
    в режиме `--strict`, где отставание чисел становится находкой. Гейт требует
    от него код 0 и называет первую находку — иначе читатель перевода получил бы
    замер прошлой ревизии при зелёной публикации.
    """
    tool = source / "tools" / "check_translations.py"
    if not tool.exists():
        return 1, (f"нет {tool}: числа переводов сверить не с чем, а публикация без "
                   f"этой сверки выпускает непроверенный замер")
    completed = subprocess.run(
        [PY, str(tool), "--root", str(source), "--strict"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=False,
    )
    output = (completed.stdout or "") + (completed.stderr or "")
    if completed.returncode == 0:
        examined = re.search(r"осмотрено:\s*(\d+)", output)
        return 0, (f"числа переводов сверены с активной ревизией "
                   f"(осмотрено {examined.group(1) if examined else '?'} объектов)")
    problem = next((line.strip() for line in output.splitlines() if line.startswith("FAIL: ")),
                   "(проверка не назвала ни одного нарушения — это и есть дефект)")
    return 1, f"переводы: {problem[:300]}"


def check_registry_line(source: Path) -> tuple[int, str]:
    """Строка реестра в шести документах обязана совпадать с реестром — без «отстал».

    Чекер различает три исхода: «совпал» (молчание), «отстал» (предупреждение)
    и «врёт» (ошибка). На публикации варианта «отстал» нет: наружу уходит
    только сверенный замер, иначе вчерашняя дата реестра уезжает читателю при
    зелёном гейте.

    Сверка не повторяется здесь второй редакцией: разбор шести документов,
    журнал прежних состояний реестра и три исхода живут в
    `tools/registry_line.py`, а этот шаг требует от него код 0 в режиме
    `--strict` — том же, которым его проверяет тест цикла качества.
    """
    tool = source / "tools" / "registry_line.py"
    if not tool.exists():
        return 1, (f"нет {tool}: строку реестра сверить не с чем, а публикация без "
                   f"этой сверки выдаёт вчерашнюю дату реестра за сегодняшнюю")
    completed = subprocess.run(
        [PY, str(tool), "--root", str(source), "--verify", "--strict", "--json"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=False,
    )
    payload = None
    try:
        payload = json.loads((completed.stdout or "").strip() or "null")
    except json.JSONDecodeError:
        payload = None
    if not isinstance(payload, dict):
        detail = (completed.stderr or "").strip() or "пустой вывод"
        return 1, f"ответ tools/registry_line.py не разобран: {detail[:300]}"
    if payload.get("error"):
        return 1, f"строка реестра не сверена: {str(payload['error'])[:300]}"
    if completed.returncode == 0:
        registry = payload.get("registry") or {}
        return 0, (f"строка реестра сверена с catalog/catalog.json во всех "
                   f"{len(payload.get('documents') or [])} документах "
                   f"({registry.get('date')}: {registry.get('corpora')} корпусов, "
                   f"{registry.get('indexed')} проиндексированы)")
    findings = list(payload.get("problems") or []) + list(payload.get("lags") or [])
    first = (findings[0] if findings
             else "(инструмент не назвал ни одной находки — это и есть дефект)")
    return 1, f"строка реестра: {str(first)[:300]}"


def version_parts(text: object) -> tuple[int, int, int] | None:
    """Версия «X.Y.Z» → числа. Не разобралась — None, а не нули."""
    match = re.match(r"^\s*(\d+)\.(\d+)\.(\d+)", str(text or ""))
    if not match:
        return None
    major, minor, patch = (int(part) for part in match.groups())
    return (major, minor, patch)


def group_paths(paths: list[str]) -> dict[str, int]:
    """Вид изменения по тексту пути: значение / длина списка / ключ.

    Зачем группа, если есть сам путь: в отчёте нужно следующее число — «сколько
    изменений какого рода». По префиксу пути видно, ЧТО случилось, без единого
    значения из ответа.
    """
    counted: dict[str, int] = {}
    for path in paths:
        text = str(path)
        if "(длина" in text:
            kind = "длина списка"
        elif "(появилось)" in text:
            kind = "новый ключ"
        elif "(исчезло)" in text:
            kind = "исчез ключ"
        else:
            kind = "значение"
        counted[kind] = counted.get(kind, 0) + 1
    return counted


def check_samples_since(previous: Path | None, current: Path) -> tuple[int, str]:
    """Пробы двух выпусков: что изменилось в поведении между ними.

    Зачем отдельно от `check_behaviour`. Там — сверка с последней пробой того же
    дерева; здесь — сравнение ДВУХ ВЫПУСКОВ: «поведение изменилось у N операций с
    выпуска X». Это вопрос, который задают при обновлении, а не при правке.

    Только предупреждение: поведение не блокирует выпуск (так названо в доке и в
    `scope` ответа пробы).
    """
    if previous is None or not previous.is_file():
        return 0, "WARN: пробы не сравнивались: прошлого манифеста нет"
    try:
        before = json.loads(previous.read_text(encoding="utf-8"))
        after = json.loads(current.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        return 1, f"FAIL: пробы не прочитаны: {type(error).__name__}: {error}"
    old = ((before.get("samples") or {}).get("probes") or {})
    new = ((after.get("samples") or {}).get("probes") or {})
    if not old and not new:
        return 0, "WARN: ни в одном выпуске проб поведения нет"
    changed: list[str] = []
    added: list[str] = []
    removed: list[str] = []
    places: dict[str, list[str]] = {}
    for name in sorted(set(old) | set(new)):
        if name not in old:
            added.append(name)
            continue
        if name not in new:
            removed.append(name)
            continue
        old_values = [entry.get("value") for entry in old[name].get("entries") or []]
        new_values = [entry.get("value") for entry in new[name].get("entries") or []]
        if old_values != new_values:
            changed.append(name)
            known = [str(path) for path in (new[name].get("paths") or [])]
            if known:
                places[name] = known[:4]
    version = str(before.get("version") or "прошлого выпуска")
    if not changed and not removed:
        tail = f"; появились пробы: {', '.join(added)} " if added else ""
        return 0, (f"поведение с выпуска {version} не изменилось "
                   f"({len(new)} проб){tail}".rstrip())
    parts = []
    if changed:
        # Имя без места — половина ответа: в манифесте есть пути (без значений),
        # и отчёт называет их рядом с операцией.
        named = [f"{name}: {'; '.join(places[name])}" if places.get(name) else name
                 for name in changed[:6]]
        parts.append(f"изменилось у {len(changed)} ({'; '.join(named)})")
    if removed:
        parts.append(f"исчезло {len(removed)} ({', '.join(removed[:6])})")
    kinds: dict[str, int] = {}
    for name in changed:
        for kind, number in group_paths(places.get(name) or []).items():
            kinds[kind] = kinds.get(kind, 0) + number
    if kinds:
        parts.append("по видам: " + ", ".join(f"{kind} {number}"
                                              for kind, number in sorted(kinds.items())))
    return 0, (f"WARN: поведение с выпуска {version}: " + "; ".join(parts)
               + (f"; новые пробы: {', '.join(added[:6])}" if added else ""))


def check_behaviour(source: Path) -> tuple[int, str]:
    """Строка о разрешённом поведении в воротах — только предупреждение.

    Почему только предупреждение: поведение зависит от корпуса и данных, а
    сравнение всегда хотя бы немного шумное. Блокировать публикацию на нём —
    значит заставлять людей отключать проверку. Ворота читают ГОТОВЫЙ итог
    последней сверки (`contract_pin_sample`), сами проб не запускают: иначе
    гейт стал бы зависеть от живого индекса.
    """
    src = str((source / "src").resolve())
    if src not in sys.path:
        sys.path.insert(0, src)
    try:
        from ragraf import pin as pin_mod
    except Exception as error:  # noqa: BLE001 — причина называется
        return 0, f"WARN: пробы поведения не прочитаны: {type(error).__name__}: {error}"
    database = builder.control_db(source)
    if database is None:
        return 0, "WARN: пробы поведения: журнала корпуса нет"
    import sqlite3

    conn = sqlite3.connect(f"file:{Path(database).as_posix()}?mode=ro", uri=True)
    try:
        state = pin_mod.sample_state(conn)
    finally:
        conn.close()
    if not state.get("total"):
        return 0, ("WARN: пробы поведения не брались: `contract_pin_sample --sample-mode take`")
    if state.get("changed"):
        return 0, (f"WARN: поведение изменилось у {state['changed']} из {state['total']} "
                   f"проб — форма пина при этом могла не меняться")
    if not state.get("checked"):
        return 0, (f"WARN: пробы есть ({state['total']}), но ни одна не сверялась: "
                   f"`contract_pin_sample --sample-mode compare`")
    return 0, (f"поведение: {state['checked']} проб сверено, расхождений 0 "
               f"(всего проб {state['total']})")


def check_previous(previous: str | None, tree: Path, *, allowed: tuple[str, ...] = (),
                   reason: str = "") -> tuple[int, str]:
    """Шаг сводки с прошлым выпуском: без файла — предупреждение и проход.

    Отдельной функцией — чтобы правило «первый выпуск не блокируется» было
    проверяемым, а не жило в ветке `main`, которую набор не вызывает.
    """
    if not previous:
        return 0, ("WARN: --previous не задан: сводку с прошлым выпуском "
                   "сравнивать не с чем (первый выпуск)")
    return check_contract_diff(Path(previous).resolve(), tree / builder.PINS_ARTIFACT,
                               allowed=allowed, reason=reason)


def check_changes_since(source: Path, previous: Path | None) -> tuple[int, str]:
    """Сколько смен набора было с прошлого выпуска и кто их сделал.

    Зачем. Сводка отвечает «что изменилось», но не отвечает «кто и сколько раз
    менял» — а для выпуска это разные вопросы: пять смен одного объекта могут
    означать болтанку, которую лучше не публиковать.

    Ничего не решает: печатает и проходит. Решение остаётся за сводкой и
    человеком.
    """
    if previous is None or not previous.is_file():
        return 0, "WARN: смены с прошлого выпуска не считаны: прошлого манифеста нет"
    src = str((source / "src").resolve())
    if src not in sys.path:
        sys.path.insert(0, src)
    try:
        from ragraf import pin as pin_mod
    except Exception as error:  # noqa: BLE001 — причина называется
        return 0, f"WARN: смены не считаны: {type(error).__name__}: {error}"
    database = builder.control_db(source)
    if database is None:
        return 0, "WARN: смены не считаны: журнала корпуса нет"
    import sqlite3

    conn = sqlite3.connect(f"file:{Path(database).as_posix()}?mode=ro", uri=True)
    try:
        try:
            stamp = str(json.loads(previous.read_text(encoding="utf-8")).get("approved_at") or "")
        except (OSError, ValueError):
            stamp = ""
        rows = pin_mod.history(conn, limit=100000)
        since = [item for item in rows if not stamp or str(item["approved_at"]) > stamp]
        counted: dict[str, int] = {}
        for item in since:
            counted[item["approved_by"]] = counted.get(item["approved_by"], 0) + 1
    finally:
        conn.close()
    if not since:
        return 0, (f"смены с прошлого выпуска: нет (пин от {stamp or 'без отметки'} "
                   f"не менялся)")
    who = ", ".join(f"{name}: {number}" for name, number in
                    sorted(counted.items(), key=lambda pair: (-pair[1], pair[0]))[:5])
    return 0, f"смены с прошлого выпуска: {len(since)} ({who})"


def diff_names(previous: Path, current: Path) -> dict[str, Any]:
    """Имена изменённых объектов между выпусками — для машинного итога.

    Отдельно от `check_contract_diff`: та функция ВЫНОСИТ решение (можно ли
    выпускать), эта только называет имена. Смешивать решение и перечень в одной
    функции значило бы, что отчёт зависит от решения.
    """
    empty = {"ops_changed": [], "tools_changed": []}
    if not previous.is_file() or not current.is_file():
        return empty
    source = str((current.parent / "src").resolve())
    if source not in sys.path:
        sys.path.insert(0, source)
    try:
        from ragraf import contract_diff as diff_mod

        report = diff_mod.diff(previous, current)
    except Exception:  # noqa: BLE001 — перечень необязателен
        return empty
    result: dict[str, Any] = {"ops_changed": [], "tools_changed": []}
    for section, key in (("ops", "ops_changed"), ("tools", "tools_changed")):
        block = report.get(section) or {}
        names = [str(item) for item in block.get("removed") or []]
        names.extend(str(item.get("name")) for item in block.get("changed") or [])
        names.extend(str(item) for item in block.get("added") or [])
        result[key] = sorted({name for name in names if name})
    return result


def samples_changes(previous: Path, current: Path) -> list[dict[str, Any]]:
    """Пробы, разошедшиеся между выпусками, с местами — без значений."""
    if not previous.is_file() or not current.is_file():
        return []
    try:
        before = json.loads(previous.read_text(encoding="utf-8"))
        after = json.loads(current.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    old = ((before.get("samples") or {}).get("probes") or {})
    new = ((after.get("samples") or {}).get("probes") or {})
    changed: list[dict[str, Any]] = []
    for name in sorted(set(old) | set(new)):
        if name not in old or name not in new:
            continue
        old_values = [entry.get("value") for entry in old[name].get("entries") or []]
        new_values = [entry.get("value") for entry in new[name].get("entries") or []]
        if old_values != new_values:
            changed.append({"op": name,
                            "paths": [str(path) for path in (new[name].get("paths") or [])][:6]})
    return changed


def history_since(source: Path, previous: Path | None) -> dict[str, Any]:
    """Сколько смен набора было с прошлого выпуска и кем."""
    empty = {"total": 0, "authors": {}}
    if previous is None or not previous.is_file():
        return empty
    src = str((source / "src").resolve())
    if src not in sys.path:
        sys.path.insert(0, src)
    try:
        from ragraf import pin as pin_mod

        database = builder.control_db(source)
    except Exception:  # noqa: BLE001 — перечень необязателен
        return empty
    if database is None:
        return empty
    import sqlite3

    try:
        stamp = str(json.loads(previous.read_text(encoding="utf-8")).get("approved_at") or "")
    except (OSError, ValueError):
        stamp = ""
    conn = sqlite3.connect(f"file:{Path(database).as_posix()}?mode=ro", uri=True)
    try:
        rows = pin_mod.history(conn, limit=100000)
    except Exception:  # noqa: BLE001 — журнал мог быть старой схемы
        rows = []
    finally:
        conn.close()
    since = [item for item in rows if not stamp or str(item["approved_at"]) > stamp]
    authors: dict[str, int] = {}
    for item in since:
        authors[item["approved_by"]] = authors.get(item["approved_by"], 0) + 1
    return {"total": len(since), "authors": authors}


def check_contract_diff(previous: Path, current: Path, *, allowed: tuple[str, ...] = (),
                        reason: str = "") -> tuple[int, str]:
    """Сводка с прошлым выпуском и решение по версии и разрешению.

    Ломающее изменение разрешено только при смене МАЖОРНОЙ версии: минорная и
    патч обещают совместимость, и выпуск с поломкой контракта им противоречит.

    Явное разрешение (`--allow-breaking` + `--break-reason`) — второй путь: оно
    действует поимённо и требует причины, иначе «разрешить всё» станет тишиной.
    Разрешённое поимённо попадает в отчёт выпуска и в `note` пина: решение
    человека видно там, где им пользуются.

    Что НЕ делает: не правит версию и не откатывает правки.
    """
    if not previous.is_file():
        return 1, f"прошлого манифеста нет: {previous} — сравнивать не с чем"
    source = str((current.parent / "src").resolve())
    if source not in sys.path:
        sys.path.insert(0, source)
    try:
        from ragraf import contract_diff as diff_mod
    except Exception as error:  # noqa: BLE001 — причина называется в отказе
        return 1, f"сводка недоступна: {type(error).__name__}: {error}"
    try:
        report = diff_mod.diff(previous, current)
    except Exception as error:  # noqa: BLE001 — в том числе ContractDiffError
        return 1, f"сводка не собрана: {type(error).__name__}: {error}"
    totals = report.get("totals") or {}
    breaking = int(totals.get("breaking") or 0)
    entries: list[tuple[str, str]] = []
    for section, label in (("ops", "операция"), ("tools", "инструмент")):
        block = report.get(section) or {}
        for item in block.get("removed") or []:
            entries.append((str(item), f"{label} {item} исчез"))
        for item in block.get("changed") or []:
            if not item.get("breaking"):
                continue
            what = "; ".join(str(reason_item.get("what"))
                             for reason_item in item.get("reasons") or []
                             if reason_item.get("kind") == "breaking")
            entries.append((str(item.get("name")), f"{label} {item.get('name')}: {what}"))
    summary = (f"ломающих {breaking}, совместимых {totals.get('compatible')}, "
               f"всего изменённых {totals.get('changed')}")
    if not breaking:
        return 0, f"сводка: {summary} — {report.get('verdict')}"

    # Разрешённое поимённо отделяется от неразрешённого: первое — решение
    # человека с причиной, второе — то, о чём никто не договаривался.
    allowed_set = {str(item).strip() for item in allowed if str(item).strip()}
    if allowed_set and not str(reason).strip():
        return 1, ("разрешение ломающих без причины не принимается: "
                   "--allow-breaking требует --break-reason")
    denied = [text for name, text in entries if name not in allowed_set]
    granted = [text for name, text in entries if name in allowed_set]
    if granted and not denied:
        return 0, (f"сводка: {summary}; ломающее разрешено человеком "
                   f"({str(reason).strip()}): " + "; ".join(granted[:8]))
    detail = "; ".join(denied[:8]) or "(имена не названы)"
    granted_note = (f"; разрешено: {'; '.join(granted[:5])}" if granted else "")
    before = version_parts((report.get("before") or {}).get("version"))
    after = version_parts((report.get("after") or {}).get("version"))
    if before is None or after is None:
        return 1, (f"сводка: {summary}, но версия не разобрана "
                   f"({(report.get('before') or {}).get('version')!r} → "
                   f"{(report.get('after') or {}).get('version')!r}): решать нечем — "
                   f"выпуск остановлен; ломающее: {detail}{granted_note}")
    if after[0] > before[0]:
        return 0, (f"сводка: {summary}; мажорная версия {before[0]} → {after[0]} — "
                   f"ломающее разрешено, в отчёт выпуска: {detail}{granted_note}")
    return 1, (f"сводка: {summary}; версия {'.'.join(map(str, before))} → "
               f"{'.'.join(map(str, after))} не мажорная — выпуск остановлен; "
               f"ломающее: {detail}{granted_note}; исправление: поднять мажорную "
               f"версию в src/ragraf/__init__.py, откатить правку или разрешить "
               f"поимённо (--allow-breaking с --break-reason)")


def _plural(number: int, one: str, few: str, many: str) -> str:
    n = abs(int(number)) % 100
    if 11 <= n <= 14:
        return many
    n1 = n % 10
    if n1 == 1:
        return one
    if 2 <= n1 <= 4:
        return few
    return many


def _age_text(seconds: object) -> str:
    """Возраст пина по-человечески. Пусто — «возраст не назван», а не ноль."""
    if not isinstance(seconds, int) or isinstance(seconds, bool) or seconds < 0:
        return "возраст не назван"
    days = seconds // 86400
    if days:
        return f"принято {days} {_plural(days, 'день', 'дня', 'дней')} назад"
    hours = seconds // 3600
    if hours:
        return f"принято {hours} {_plural(hours, 'час', 'часа', 'часов')} назад"
    minutes = seconds // 60
    if minutes:
        return f"принято {minutes} {_plural(minutes, 'минуту', 'минуты', 'минут')} назад"
    return "принято только что"


def _pin_action(source: Path) -> str:
    """Одна команда исправления — та же, что в отказе операции.

    Текст берётся у `util.next_action`, а не пишется здесь второй раз: две
    копии одной команды расходятся ровно так же, как любые две копии.
    Если в дереве-источнике этого вида отказа ещё нет — общая форма.
    """
    config = source / "config" / "ragraf.yaml"
    arg = str(config) if config.is_file() else ""
    try:
        from ragraf import util as util_mod

        advice = util_mod.next_action("contract_pin_approve", arg,
                                      kind="pin_unaccepted")
        action = str(advice.get("action") or "")
        if action:
            return action
    except Exception:  # noqa: BLE001 — совет не имеет права ломать ворота
        pass
    tail = " query contract_pin_approve --all-ops --approved-by <кто>"
    if arg:
        return f"python tools/ragraf.py -p {config}" + tail
    return "python tools/ragraf.py" + tail


# Публичное имя для соседей: отчёт выпуска (`release.py`) называет возраст пина
# тем же текстом, что и ворота, иначе два места говорят разными словами.
age_text = _age_text


def check_contract_pin(source: Path) -> tuple[int, str]:
    """Контракт операций должен быть принят — иначе публиковать нечего.

    Почему это ворота, а не пожелание. Пин ставится человеком на релиз; если
    его нет, расхождение описания некому поймать и следующий выпуск уедет
    клиенту с другим контрактом и без единого события (замер 10.11.2026:
    `op_pin` = 0, `pin_event` = 0 на живом корпусе).

    Три состояния: `agreed` — проходим; `none` — ПРЕДУПРЕЖДЕНИЕ и проходим
    (первый выпуск без пина блокировать нельзя: подтверждать пока нечего);
    `diverged` и `partial` — отказ. Что важнее, решает `pin.state_payload`, а
    не эти ворота: расхождение перекрывает неполноту.
    """
    try:
        document = builder.pins_document(source)
    except Exception as error:  # noqa: BLE001 — причина называется в отказе
        return 1, f"описания операций не прочитаны: {type(error).__name__}: {error}"
    state = str(document.get("pin_state") or "none")
    ops_state = str(document.get("pin_state_ops") or "none")
    tools_state = str(document.get("pin_state_tools") or "none")
    total_ops = int(document.get("ops_total") or 0)
    total_tools = int(document.get("tools_total") or 0)
    from ragraf import pin as pin_mod

    age = _age_text(pin_mod.age_seconds(str(document.get("approved_at") or "")))
    if state == "agreed":
        return 0, (f"контракт принят: {total_ops} операций и {total_tools} инструментов, "
                   f"{age}")
    if state == "none":
        return 0, (f"ПРЕДУПРЕЖДЕНИЕ: пин не поставлен ни на одну из {total_ops} операций "
                   f"и ни на один из {total_tools} инструментов — выпущенный релиз не "
                   f"защищён от расхождения описания; команда: {_pin_action(source)}")
    if state == "diverged":
        parts = []
        names = list(document.get("diverged") or [])
        if names:
            parts.append("операции: " + ", ".join(names[:6]))
        tool_names = list(document.get("tool_diverged") or [])
        if tool_names:
            parts.append("инструменты: " + ", ".join(tool_names[:6]))
        detail = "; ".join(parts) or "(имена не названы)"
        return 1, (f"описание разошлось с пином: {detail} ({age}) — выпуск остановлен; "
                   f"команда: {_pin_action(source)}")
    if state == "partial":
        sides = []
        if ops_state != "agreed":
            sides.append(f"операции: {ops_state} (не подтверждено "
                         f"{document.get('unpinned_total')}, устарело "
                         f"{document.get('stale_total')})")
        if tools_state != "agreed":
            sides.append(f"инструменты: {tools_state} (не подтверждено "
                         f"{document.get('tool_unpinned_total')}, устарело "
                         f"{document.get('tool_stale_total')})")
        return 1, (f"контракт принят не целиком — " + "; ".join(sides) +
                   f" — выпуск остановлен; команда: {_pin_action(source)}")
    return 1, f"состояние пина {state!r} вне закрытого списка: это дефект, а не решение"


def _shipped_version(tree: Path) -> str:
    """Версия из КОДА поставки — одной функцией с сборщиком."""
    return builder.version_of(tree)


def check_report_artifact(tree: Path, manifest=None, previous: Path | None = None,
                          source: Path | None = None) -> tuple[int, str]:
    """Отчёт выпуска обязан лежать в поставке и совпадать с набором.

    Проверяется не «файл есть», а «файл говорит то же, что набор»: отчёт с
    чужим вердиктом опаснее отсутствующего — он утверждает, что выпуск проверен.
    Сравниваются байты с рендером по тому же `ops_pins.json`, поэтому подмена
    версии, вердикта или ручная правка ловятся одинаково.
    """
    path = tree / report_mod.REPORT_ARTIFACT
    if manifest is not None and report_mod.declared(manifest) and not path.is_file():
        return 1, (f"отчёт объявлен артефактом, но в сборке нет {path.name}: "
                   f"поставка без отчёта не выпускается")
    if not path.is_file():
        # Не объявлен — не требуем: манифест вправе не возить отчёт (обратная
        # совместимость со сборками до X21).
        return 0, f"отчёта в сборке нет ({path.name} не объявлен артефактом)"
    try:
        actual = path.read_bytes()
    except OSError as error:
        return 1, f"{path.name} не читается: {error}"
    try:
        expected = report_mod.render(tree, previous=previous,
                                     source=source).encode("utf-8")
    except Exception as error:  # noqa: BLE001 — причина называется в отказе
        return 1, f"отчёт не собирается по набору: {type(error).__name__}: {error}"
    if actual != expected:
        claims = report_mod.parse(actual.decode("utf-8", "replace"))
        truth = report_mod.parse(expected.decode("utf-8"))
        return 1, (f"{path.name} расходится с набором: заявлено "
                   f"«{claims.get('version') or '—'} / {claims.get('verdict') or '—'}», "
                   f"по набору «{truth.get('version') or '—'} / {truth.get('verdict') or '—'}»")
    if manifest is not None and not report_mod.declared(manifest):
        return 1, (f"{path.name} лежит в сборке, но не объявлен артефактом манифеста: "
                   f"проверка полноты сочтёт его мусором")
    claims = report_mod.parse(expected.decode("utf-8"))
    return 0, (f"{path.name}: версия {claims.get('version') or '—'}, "
               f"вердикт «{claims.get('verdict') or '—'}» — совпадает с набором")


def check_pins_artifact(tree: Path) -> tuple[int, str]:
    """Хеши в `ops_pins.json` обязаны совпадать с УПАКОВАННЫМ кодом.

    Артефакт собирается из живого дерева, а уезжает вместе с копией: если
    набор файлов или версия разошлись, пины следующего выпуска сравнивали бы
    не с тем кодом, который у клиента. Проверяется ФАЙЛ поставки против кода
    поставки, а не источника.
    """
    path = tree / builder.PINS_ARTIFACT
    if not path.is_file():
        return 1, (f"в сборке нет {builder.PINS_ARTIFACT}: релиз без описаний "
                   f"операций не выпускается")
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as error:
        return 1, f"{builder.PINS_ARTIFACT} не разбирается: {error}"
    from ragraf import pin as pin_mod

    problems: list[str] = []
    # Пробы (X15): имя пробы — это имя ОПЕРАЦИИ. Проба несуществующей операции
    # означала бы, что сравнивают не то, что выпущено.
    samples = (document.get("samples") or {}).get("probes") or {}
    unknown_samples = sorted(name for name in samples
                             if name not in (document.get("ops") or {}))
    if unknown_samples:
        problems.append("пробы: имена не из набора операций: "
                        + ", ".join(unknown_samples[:5]))
    for name, block in samples.items():
        entries = block.get("entries") if isinstance(block, dict) else None
        if not entries:
            problems.append(f"проба {name}: нет ни одной записи")
            continue
        if any(not str(entry.get("value") or "") for entry in entries
               if entry.get("status") == "sampled"):
            problems.append(f"проба {name}: у выполненной записи нет хеша")
    for label, shipped, key in (("операции", pin_mod.hashes(tree), "ops"),
                                ("инструменты", pin_mod.tool_hashes(tree), "tools")):
        stated = {str(name): str(value)
                  for name, value in (document.get(key) or {}).items()}
        if not stated:
            problems.append(f"{label}: в файле нет ни одной записи")
            continue
        missing = sorted(set(shipped) - set(stated))
        extra = sorted(set(stated) - set(shipped))
        changed = sorted(name for name in set(shipped) & set(stated)
                         if shipped[name] != stated[name])
        if missing:
            problems.append(f"{label}: нет в файле: {', '.join(missing[:5])}")
        if extra:
            problems.append(f"{label}: лишние в файле: {', '.join(extra[:5])}")
        if changed:
            problems.append(f"{label}: хеш расходится: {', '.join(changed[:5])}")
    version = _shipped_version(tree)
    stated_version = str(document.get("version") or "")
    if version and stated_version != version:
        problems.append(f"версия в файле {stated_version!r} против {version!r} в коде")
    if problems:
        return 1, "; ".join(problems)
    return 0, (f"{builder.PINS_ARTIFACT}: {len(document.get('ops') or {})} операций и "
               f"{len(document.get('tools') or {})} инструментов совпадают с кодом сборки, "
               f"версия {stated_version or '(не названа)'}, пин: {document.get('pin_state')}")


def verify(tree: Path, only: str, manifest: Path | None, source: Path) -> tuple[int, str]:
    args = ["--tree", str(tree), "--only", only, "--source", str(source)]
    if manifest is not None:
        args += ["--manifest", str(manifest)]
    return run("verify_release.py", *args)


def _short_list(items: list[str], limit: int = 3) -> str:
    """Перечень без длинного хвоста: «a, b, c и ещё 4»."""
    names = [str(item) for item in items]
    if len(names) <= limit:
        return ", ".join(names)
    return ", ".join(names[:limit]) + f" и ещё {len(names) - limit}"


def safe_version(source: Path) -> str:
    """Версия без исключения: итог обязан назвать её даже при остановке на шаге 1."""
    try:
        return builder.version_of(source)
    except Exception:  # noqa: BLE001 — версия может быть не прочитана
        return ""


def behaviour_state(document: dict) -> tuple[str, str]:
    """Что известно о ПОВЕДЕНИИ по манифесту: checked / partial / changed / unknown.

    Зачем отдельная функция. Ворота раньше печатали строку о пробах, но вердикт
    от неё не зависел: «все пробы деградированы» читалось как «всё хорошо».
    Здесь состояние вычисляется по манифесту и только по нему — его можно
    проверить подсадкой.
    """
    probes = ((document.get("samples") or {}).get("probes") or {})
    if not probes:
        return "unknown", "проб поведения нет"
    statuses = [str(entry.get("status") or "")
                for block in probes.values() for entry in block.get("entries") or []]
    if not statuses:
        return "unknown", "в пробах нет ни одной записи"
    sampled = sum(1 for status in statuses if status == "sampled")
    degraded = sum(1 for status in statuses if status == "sampled_degraded")
    changed = int((document.get("samples") or {}).get("changed") or 0)
    if changed:
        return "changed", f"поведение изменилось у {changed}"
    if not sampled:
        return "unknown", (f"ни одна проба не выполнена полностью "
                            f"(деградированных {degraded}, всего {len(statuses)})")
    if degraded:
        return "partial", (f"поведение сверено частично: полностью {sampled}, "
                            f"деградированных {degraded}")
    return "checked", f"поведение сверено по {sampled} пробам"


def verdict_of(state: dict) -> tuple[str, str, int]:
    """Вердикт машиной: ключ, человеческая строка, код возврата.

    Одна функция на три представления (stdout, markdown, JSON): если бы каждое
    считало вердикт само, они разошлись бы — та же ошибка, что была со строками
    шагов до X18.
    """
    blocks = list(state.get("blocks") or [])
    warnings = list(state.get("warnings") or [])
    behaviour = str(state.get("behaviour") or "unknown")
    if blocks:
        return "stopped", "остановлена: " + blocks[0], 1
    if behaviour == "unknown":
        return ("allowed_with_warnings",
                "разрешена с предупреждениями: поведение не проверено", 0)
    if behaviour in ("partial", "changed") and warnings:
        # Счёт отдельно от поведения: «2 предупреждения» при одном сообщении
        # и одном расхождении читалось бы как два сообщения.
        return ("allowed_with_warnings",
                f"разрешена с предупреждениями: {len(warnings)}, поведение {behaviour}", 0)
    if behaviour in ("partial", "changed"):
        return "allowed_with_warnings", f"разрешена с предупреждениями: поведение {behaviour}", 0
    if warnings:
        return "allowed_with_warnings", f"разрешена с предупреждениями: {len(warnings)}", 0
    return "allowed", "разрешена", 0


def render_summary(state: dict) -> tuple[int, list[str]]:
    """Итог выпуска: три состояния, что блокирует, что предупреждает, вердикт.

    Печатается ВСЕГДА и заканчивается одной строкой вердикта: читателю не надо
    собирать решение из пяти строк разных шагов.
    """
    blocks = list(state.get("blocks") or [])
    warnings = list(state.get("warnings") or [])
    behaviour = str(state.get("behaviour") or "unknown")
    behaviour_note = str(state.get("behaviour_note") or "")
    lines = ["", "итог выпуска", "-" * 60]
    lines.append(f"версия: {state.get('version') or '(не прочитана)'}")
    lines.append(f"состояния: описания {state.get('ops') or '?'}, "
                 f"инструменты {state.get('tools') or '?'}, "
                 f"поведение {behaviour}")
    if blocks:
        lines.append("блокирует: " + "; ".join(blocks))
    else:
        lines.append("блокирует: ничего")
    if warnings:
        lines.append("предупреждает: " + "; ".join(warnings[:6]))
    else:
        lines.append("предупреждает: ничего")
    actions = list(state.get("actions") or [])
    if actions:
        lines.append("действие: " + "; ".join(actions[:4]))
    changes = state.get("changes") or {}
    ops_changed = list(changes.get("ops_changed") or []) + list(changes.get("tools_changed") or [])
    sample_items = list(changes.get("samples_changed") or [])
    history = changes.get("history_since") or {}
    if ops_changed or sample_items or history.get("total"):
        parts = []
        if ops_changed:
            parts.append(f"описания: {_short_list(ops_changed)}")
        if sample_items:
            places = [f"{item.get('op')}: {', '.join(item.get('paths') or []) or 'место не названо'}"
                      for item in sample_items]
            parts.append(f"поведение: {_short_list(places)}")
        if history.get("total"):
            authors = ", ".join(f"{name}: {number}"
                                for name, number in sorted((history.get("authors") or {}).items(),
                                                           key=lambda pair: (-pair[1], pair[0]))[:3])
            parts.append(f"смен набора: {history['total']}" + (f" ({authors})" if authors else ""))
        lines.append("с прошлого выпуска: " + "; ".join(parts))
    _key, verdict, code = verdict_of(state)
    if behaviour_note:
        lines.append(f"поведение: {behaviour_note}")
    lines.append("вердикт: " + verdict)
    # Сводка не длиннее 15 строк: это отчёт для человека, который смотрит на
    # конец прогона. Перечни уже сокращены через «и ещё N».
    if len(lines) > 15:
        lines = lines[:14] + [lines[-1]]
    return code, lines


def summary_json(state: dict) -> dict:
    """Тот же итог машиной: JSON из одного и того же `state`.

    Поля стабильные и без времени: CI и панель читают `verdict` и `blocks`, а не
    разбирают текст. Схема названа числом — её смена видна клиенту.
    """
    key, text, code = verdict_of(state)
    return {
        "schema": 1,
        "version": str(state.get("version") or ""),
        "states": {
            "ops": str(state.get("ops") or ""),
            "tools": str(state.get("tools") or ""),
            "behaviour": str(state.get("behaviour") or "unknown"),
            "behaviour_note": str(state.get("behaviour_note") or ""),
        },
        "verdict": key,
        "verdict_text": text,
        "exit_code": code,
        "blocks": list(state.get("blocks") or []),
        "warnings": list(state.get("warnings") or []),
        "actions": list(state.get("actions") or []),
        "changes": {
            "ops_changed": list((state.get("changes") or {}).get("ops_changed") or []),
            "tools_changed": list((state.get("changes") or {}).get("tools_changed") or []),
            "samples_changed": list((state.get("changes") or {}).get("samples_changed") or []),
            "history_since": dict((state.get("changes") or {}).get("history_since") or {}),
        },
    }


def summary_markdown(state: dict) -> str:
    """Тот же итог в markdown — без времени сборки и без путей машины.

    Детерминизм важен как у манифеста: два прогона в одном состоянии должны
    давать одинаковые байты, иначе отчёт нельзя сравнить между выпусками.
    """
    code, lines = render_summary(state)
    body = ["# Итог выпуска", "", f"Версия: {state.get('version') or '(не прочитана)'}", "",
            "| что | состояние |", "|---|---|",
            f"| описания операций | {state.get('ops') or '?'} |",
            f"| описания инструментов | {state.get('tools') or '?'} |",
            f"| поведение | {state.get('behaviour') or 'unknown'} |", ""]
    body.append("## Что блокирует")
    body.extend([f"- {item}" for item in (state.get("blocks") or [])] or ["- ничего"])
    body.append("")
    body.append("## Что предупреждает")
    body.extend([f"- {item}" for item in (state.get("warnings") or [])] or ["- ничего"])
    body.append("")
    # Изменения с прошлого выпуска — тем же списком, что в текстовой сводке:
    # отчёт и вердикт называют одно и то же (X20).
    changes = state.get("changes") or {}
    names = list(changes.get("ops_changed") or []) + list(changes.get("tools_changed") or [])
    probes = list(changes.get("samples_changed") or [])
    history = changes.get("history_since") or {}
    if names or probes or history.get("total"):
        body.append("## Что изменилось с прошлого выпуска")
        if names:
            body.extend([f"- описание: {item}" for item in names])
        for item in probes:
            places = ", ".join(item.get("paths") or []) or "место не названо"
            body.append(f"- поведение: {item.get('op')} — {places}")
        if history.get("total"):
            authors = ", ".join(f"{name}: {number}" for name, number in
                                 sorted((history.get("authors") or {}).items()))
            body.append(f"- подтверждений набора: {history['total']}" +
                        (f" ({authors})" if authors else ""))
        body.append("")
    verdict = next((line for line in lines if line.startswith("вердикт:")), "вердикт: ?")
    body.append("## Вердикт")
    body.append("")
    body.append(verdict.split(": ", 1)[-1])
    body.append("")
    return "\n".join(body)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    source = Path(args.source).resolve() if args.source else rel.default_source()
    manifest = Path(args.manifest).resolve() if args.manifest else None
    # Разрешение ломающих проверяется ДО всего остального: «разрешить и промолчать»
    # — это и есть та тишина, против которой этап.
    allowed = tuple(item.strip() for item in str(args.allow_breaking or "").split(",")
                    if item.strip())
    # Состояние выпуска собирается по ходу и печатается ОДНИМ блоком в конце:
    # пять строк разных шагов требуют, чтобы решение собрал читатель.
    state: dict = {"blocks": [], "warnings": [], "actions": [],
                   "version": safe_version(source),
                   "ops": "", "tools": "", "behaviour": "", "behaviour_note": "",
                   # Перечни изменений заполняются, когда есть прошлый выпуск;
                   # ключи есть ВСЕГДА — клиенту не надо гадать, «пусто или не считали».
                   "changes": {"ops_changed": [], "tools_changed": [],
                               "samples_changed": [], "history_since": {"total": 0,
                                                                        "authors": {}}}}

    def finish(code: int) -> int:
        """Напечатать итог (всегда), затем markdown и/или JSON, если просили."""
        verdict_code, lines = render_summary(state)
        for line in lines:
            print(line)
        if args.report:
            target = Path(args.report).expanduser()
            if target.parent and str(target.parent):
                target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(summary_markdown(state), encoding="utf-8", newline="\n")
            print(f"отчёт записан: {target.name}")
        if args.json_out or getattr(args, "json_stdout", False):
            body = json.dumps(summary_json(state), ensure_ascii=False,
                              indent=2, sort_keys=True) + "\n"
            if args.json_out:
                target = Path(args.json_out).expanduser()
                if target.parent and str(target.parent):
                    target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(body.encode("utf-8"))
                print(f"машинный итог записан: {target.name}")
            if getattr(args, "json_stdout", False):
                print(body, end="")
        # Код возврата — из вердикта, а не из места выхода: одно состояние —
        # один вердикт, и он же код.
        return verdict_code if not code else max(code, verdict_code)
    if allowed and not str(args.break_reason or "").strip():
        print("FAIL: --allow-breaking требует --break-reason: разрешение без причины не принимается")
        return 2
    # --- 0. Числа публикации ---------------------------------------------
    # Чекер различает «отстал» и «врёт», но предупреждение об отставании
    # обходится тем, что публикацию запускают вручную. Здесь варианта «отстал»
    # нет: наружу уходит только сверенный с активной ревизией замер.
    numbers_code, numbers_message = check_numbers(source)
    if args.check_numbers:
        print(("OK: " if numbers_code == 0 else "FAIL: ") + numbers_message)
        if numbers_code != 0:
            return numbers_code
        # Строка реестра проверяется ДО переводов: её предмет — дата и число
        # корпусов в шести документах, и он не зависит от состава поставки.
        # Шаг переводов, наоборот, требует всей поставки (ссылки переводов
        # обязаны указывать на файлы манифеста) и в песочнице отказывает первым.
        registry_code, registry_message = check_registry_line(source)
        print(("OK: " if registry_code == 0 else "FAIL: ") + registry_message)
        if registry_code != 0:
            return registry_code
        words_code, words_message = check_translation_numbers(source)
        print(("OK: " if words_code == 0 else "FAIL: ") + words_message)
        return words_code
    print(f"числа публикации: код {numbers_code} — {numbers_message}")
    if numbers_code != 0:
        state["blocks"].append("числа README не совпадают с активной ревизией")
        state["actions"].append("выровнять числа README по активной ревизии")
        return finish(1)

    # Строка реестра: дата и число корпусов в шести документах. Здесь, в отличие
    # от чекера, отставание — отказ (ПРОМТ-И33). Проверяется до переводов по той
    # же причине, что и в режиме --check-numbers: её предмет от поставки не зависит.
    registry_code, registry_message = check_registry_line(source)
    print(f"строка реестра: код {registry_code} — {registry_message}")
    if registry_code != 0:
        state["blocks"].append("строка реестра не совпадает с реестром")
        state["actions"].append("обновить строку реестра в README")
        return finish(1)

    # Числа ПЕРЕВОДОВ: у них до этапа И22 сторожа не было, и отставший перевод
    # уезжал читателю при зелёной публикации (rg 'rev-0' по community/*.md).
    words_code, words_message = check_translation_numbers(source)
    print(f"числа переводов: код {words_code} — {words_message}")
    if words_code != 0:
        state["blocks"].append("числа переводов не совпадают с активной ревизией")
        state["actions"].append("выровнять числа переводов")
        return finish(1)

    # Контракт операций (X9). Проверяется ДО песочницы: предмет — принятый
    # человеком набор пинов, он от состава поставки не зависит.
    pins_code, pins_message = check_contract_pin(source)
    print(("OK: " if pins_code == 0 else "FAIL: ") + pins_message)
    if pins_code != 0:
        state["blocks"].append("контракт операций не принят или разошёлся")
        state["actions"].append(_pin_action(source))
        return finish(1)


    root = Path(tempfile.mkdtemp(prefix="ragraf-gate-"))
    # Уборка регистрируется СРАЗУ: до 29.10.2026 каталог убирался только на
    # успешном пути, и каждый провал гейта (а также `--check-numbers`) оставлял
    # песочницу на диске — мусор рос именно на неудачных прогонах.
    atexit.register(shutil.rmtree, root, ignore_errors=True)
    tree = root / "tree"
    failures: list[str] = []
    caught = 0

    build_args = ["--out", str(tree), "--source", str(source)]
    if manifest is not None:
        build_args += ["--manifest", str(manifest)]
    if args.previous:
        # Прошлый выпуск передаётся сборщику: без него отчёт не знает, что
        # изменилось, и раздел «изменения» исчез бы из поставки молча.
        build_args += ["--previous", str(Path(args.previous).resolve())]
    code, output = run("build_release.py", *build_args)
    print(f"сборка в песочницу: код {code}, {tree}")
    if code != 0:
        state["blocks"].append("сборка в песочницу не удалась")
        print(output)
        return finish(1)

    # Артефакт описаний проверяется сразу после сборки: пины следующего
    # выпуска сравниваются с тем кодом, который уехал, а не с источником.
    report_code, report_message = check_report_artifact(
        tree, manifest, Path(args.previous).resolve() if args.previous else None, source)
    print(("OK: " if report_code == 0 else "FAIL: ") + report_message)
    if report_code != 0:
        state["blocks"].append("отчёт выпуска расходится с набором")
        state["actions"].append("пересобрать RELEASE-REPORT.md")
        return finish(1)
    artifact_code, artifact_message = check_pins_artifact(tree)
    print(("OK: " if artifact_code == 0 else "FAIL: ") + artifact_message)
    if artifact_code != 0:
        state["blocks"].append("описания операций в сборке не совпадают с её кодом")
        state["actions"].append("пересобрать ops_pins.json")
        return finish(1)
    # Состояния для итога берутся ИЗ АРТЕФАКТА, а не из сообщений шагов: это
    # единственное место, где описания и пробы записаны вместе.
    artifact_doc: dict = {}
    try:
        artifact_doc = json.loads((tree / builder.PINS_ARTIFACT).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        artifact_doc = {}
    state["version"] = str(artifact_doc.get("version") or "")
    state["ops"] = str(artifact_doc.get("pin_state_ops") or "")
    state["tools"] = str(artifact_doc.get("pin_state_tools") or "")
    behaviour, behaviour_note = behaviour_state(artifact_doc)
    state["behaviour"] = behaviour
    state["behaviour_note"] = behaviour_note

    # Сводка с прошлым выпуском (X11). Без --previous сравнивать не с чем: это
    # предупреждение, а не отказ — первый выпуск блокировать нечем.
    diff_code, diff_message = check_previous(args.previous, tree, allowed=allowed,
                                             reason=str(args.break_reason or ""))
    print(("OK: " if diff_code == 0 else "FAIL: ") + diff_message)
    if diff_code != 0:
        state["blocks"].append("ломающее изменение в несовместимой версии")
        state["actions"].append("поднять мажорную версию или разрешить поимённо")
        return finish(1)
    changes_code, changes_message = check_changes_since(
        tree, Path(args.previous).resolve() if args.previous else None)
    print(("OK: " if changes_code == 0 else "FAIL: ") + changes_message)
    if changes_message.startswith("WARN"):
        state["warnings"].append(changes_message.replace("WARN: ", ""))
    behaviour_code, behaviour_message = check_behaviour(tree)
    print(("OK: " if behaviour_code == 0 else "FAIL: ") + behaviour_message)
    if behaviour_message.startswith("WARN"):
        state["warnings"].append(behaviour_message.replace("WARN: ", ""))
    samples_code, samples_message = check_samples_since(
        Path(args.previous).resolve() if args.previous else None,
        tree / builder.PINS_ARTIFACT)
    print(("OK: " if samples_code == 0 else "FAIL: ") + samples_message)
    if samples_message.startswith("WARN"):
        state["warnings"].append(samples_message.replace("WARN: ", ""))
    # Перечни изменений считаются по тем же двум манифестам, что и проверки выше:
    # третьего источника «что изменилось» не заводится.
    previous_path = Path(args.previous).resolve() if args.previous else None
    if previous_path is not None:
        names = diff_names(previous_path, tree / builder.PINS_ARTIFACT)
        state["changes"]["ops_changed"] = names["ops_changed"]
        state["changes"]["tools_changed"] = names["tools_changed"]
        state["changes"]["samples_changed"] = samples_changes(previous_path,
                                                             tree / builder.PINS_ARTIFACT)
        state["changes"]["history_since"] = history_since(tree, previous_path)
    if samples_code != 0:
        state["blocks"].append("пробы поведения не прочитаны")
        return finish(1)

    manifest_path = manifest or (source / rel.MANIFEST_DEFAULT)
    plan = rel.collect(source, rel.load_manifest(manifest_path, source))
    targets = sorted(plan.files)
    if not targets:
        state["blocks"].append("манифест не дал ни одного файла")
        return finish(1)

    code, output = verify(tree, "all", manifest, source)
    print(f"годная сборка: код {code}, {output.strip().splitlines()[-3] if output.strip() else '—'}")
    if code != 0:
        print(output)
        state["blocks"].append("годная сборка не признана годной")
        return finish(1)

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

    # --- Запуск поставки: дерево не только собрано, но и работает ---------
    # До этого шага гейт подтверждал СОСТАВ. Поставка ломается не составом, а
    # отсутствием модуля в манифесте: файлы на месте, а CLI из дерева не поднимается,
    # и узнаёт об этом читатель. Замер 29.10.2026: подсадка «нет src/ragraf/docread в
    # манифесте» проходила состав незамеченной и валила только запуск — 10 свойств.
    runs_tool = source / "tools" / "check_release_runs.py"
    if not runs_tool.exists():
        failures.append("нет tools/check_release_runs.py: запуск поставки не проверяется")
        print("[6/6] запуск поставки: НЕ ПРОВЕРЕН — нет tools/check_release_runs.py")
    else:
        completed = subprocess.run(
            [PY, str(runs_tool), "--tree", str(tree), "--manifest", str(manifest_path)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", check=False,
        )
        runs_output = (completed.stdout or "") + (completed.stderr or "")
        runs_line = next((line.strip() for line in runs_output.splitlines()
                          if "ИТОГ" in line), "")
        if completed.returncode == 0:
            print(f"[6/6] запуск поставки: дерево работает — {runs_line}")
        else:
            failures.append(f"дерево не запускается: {runs_line or runs_output.strip()[:160]}")
            print(f"[6/6] запуск поставки: НЕ РАБОТАЕТ — {runs_line}")
            for line in runs_output.splitlines():
                if "[FAIL]" in line:
                    print(f"    {line.strip()[:150]}")

    code, output = verify(tree, "all", manifest, source)
    if code != 0:
        failures.append("после снятия всех порч дерево не вернулось в годное состояние")
        print(output)

    print()
    print(f"порч поймано: {caught} из 5 | шагов гейта: 6 (5 порч + проверка запуска)")
    print(f"приманок отработано: 2 из 2")
    if failures:
        for problem in failures:
            print(f"FAIL: {problem}")
        state["blocks"].extend(failures[:3])
        return finish(1)
    if caught != 5:
        state["blocks"].append("поймано не пять порч — гейт не доказан")
        return finish(1)
    if args.keep:
        # Явное решение оставить: снимаем регистрацию уборки, а не полагаемся на
        # то, что она не сработает.
        atexit.unregister(shutil.rmtree)
        print(f"песочница оставлена: {root}")
    print("гейт: пройден")
    return finish(0)


if __name__ == "__main__":
    sys.exit(main())
