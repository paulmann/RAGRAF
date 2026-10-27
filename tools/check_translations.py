#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Состояние переводов README: объявлено ДАННЫМИ, подтверждено деревом.

Задача, из которой проверка выросла (этапы И21 и И22, 27.10.2026). Этап И20
привёл переводы к решению «полный либо частичный», но решение держалось шапкой
файла и записью в `docs/03_implementation/RELEASE-SOURCES.md`, то есть не
проверялось ничем. Этап И21 сделал состояние проверяемым, но объявил его
словарём ВНУТРИ ЭТОГО ФАЙЛА: чтобы перевести очередной язык целиком, нужно было
править проверку — то есть проверка объявляла состояние, а не подтверждала его.
Этап И22 перевёл `README.de.md` целиком и потому перенёс объявление в данные:
`docs/03_implementation/translations.json` называет состояние каждого перевода,
а здесь оно только проверяется.

Состояний два, и каждое проверяется ФАКТОМ, а не доверием к шапке. Третье,
`mirror` (побайтовая копия основного документа), снято на этапе И23: основной
`README.md` переведён на английский, и русский файл перестал быть его копией —
он стал таким же переводом, как немецкий. Правило, которое никем не объявлено,
хуже отсутствующего: оно выглядит защитой, ничего не защищая.

1. `full` — перевод, приведённый к текущему продукту целиком. Проверяется:
   * признака неполноты нет (ни одного маркера, объявленного частичными
     переводами), иначе «полный» объявлен, а текст говорит обратное;
   * шапка называет состояние (полный перевод), версию продукта и основной
     документ — по образцам из данных;
   * структура совпадает с `README.md`: число разделов `##`, число подразделов
     `###` и профиль каждого раздела — сколько в нём блоков команд и строк
     таблиц. Переводятся слова, а не устройство документа, поэтому расхождение
     профиля — это пропавший раздел, таблица или пример, а не стиль;
   * каждая относительная ссылка `README.md` встречается и в переводе: полный
     перевод не может перестать называть документ, который называет оригинал.
3. `partial` — частичный перевод. Проверяется признак неполноты на своём языке
   и перечень недостающего: не менее объявленного порога пунктов.

Сверх состояния проверяются одинаковые для всех свойства: запрещённые пути
(`docs/ru/**`, `docs/DEFECTS.md`), русский заполнитель команды `<конфиг>`,
кириллица только в ярлыке «Русский», каждый блок команд непустой и каждая
относительная ссылка — включая изображения — разрешается против СОСТАВА
ПОСТАВКИ из `tools/release/manifest.toml`, а не против живого дерева.

**Числа индекса.** До этапа И22 у чисел в переводах сторожа не было: этап И21
обновил их вручную в шести файлах и записал это в отчёт. Теперь числа каждого
перевода сверяются с АКТИВНОЙ записью ревизии через штатный
`tools/rev_snapshot.py` — того же инструмента, которым сверяются числа `README.md`
(одна реализация чтения истории ревизий, а не вторая). Правило то же, что у
`Test-DocumentedNumbers`:

* числа равны активной ревизии — наблюдение;
* числа равны ОДНОЙ ИЗ ПРОШЛЫХ ревизий — предупреждение с её `rev_id` и датой:
  документ отстал, а не соврал (при `--strict` это отказ: на публикации уходит
  только сверенный замер);
* таких чисел не было ни в одной ревизии — находка: документ называет
  состояние, которого не было.

Ревизия, которая ещё запечатывается, снимком не является: сверка откладывается
явным наблюдением, а не выдаётся за PASS и не объявляется провалом (урок
25.10.2026: прогон в 12:51 получил нули и объявил ложный FAIL).

Одна порча — одна находка. Предусловия (нет файла, нет объявления, не читается
манифест, не прочитана ревизия) отделены от наблюдений, зависимые свойства не
проверяются при отсутствии того, от чего зависят, а строка, уже названная
русским заполнителем, не пересчитывается как «кириллица в блоке команд».

Границы, названные честно:

* качество перевода машинной проверке не поддаётся: проверяется соответствие
  структуры и чисел, а не смысл фраз. «Полный» здесь означает «приведён к
  текущему продукту и подтверждён структурой», а не «переведён без потерь»;
* порядок разделов по смыслу не сверяется (у языков он выражается разными
  словами) — сверяется профиль каждого раздела по порядку;
* подпись `rev-XXXX` в тексте перевода информационная: сверяются числа, как и в
  `Test-DocumentedNumbers`. Иначе каждая переиндексация требовала бы правки
  пяти документов ради подписи, а не ради измерения;
* проверяется НАЛИЧИЕ цели ссылки, а не её содержимое: картинка может быть
  устаревшей, и это не нарушение состояния перевода.

Запуск:
    python tools/check_translations.py [--root <дерево>] [--project <конфиг>] [--strict]
Коды возврата: 0 — свойства выполняются, 1 — есть нарушения (в том числе «ноль
осмотренных объектов»: это провал, а не PASS), 2 — не прочитаны аргументы.
"""

from __future__ import annotations

import argparse
import json
import posixpath
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
MAIN = "README.md"
STATES_FILE = "docs/03_implementation/translations.json"

#: Пути, которых в поставке нет: ссылка на них — обещание 404.
FORBIDDEN_LINKS = ("docs/ru/", "docs/DEFECTS.md")

#: Заполнитель команды, оставшийся с русского оригинала.
FORBIDDEN_PLACEHOLDERS = ("<конфиг>", "-конфиг-")

#: Ссылка И изображение: `[т](цель)` и `![т](цель)`. Прежний замер этапа И20
#: исключал изображения (`(?<!!)`), поэтому битая обложка не находилась.
LINK = re.compile(r"!?\[[^\]\n]*\]\(([^)\n]+)\)")
FENCE = re.compile(r"^\s*(```|~~~)")
CYRILLIC = re.compile(r"[\u0400-\u04FF]")
SWITCHER_LABEL = "Русский"
REMOTE_PREFIXES = ("http://", "https://", "mailto:", "#", "ftp://")

DEFAULT_HEADER_LINES = 13
DEFAULT_MIN_GAPS = 3

EXAMINED = 0
FAILURES: list[str] = []
WARNINGS: list[str] = []
OBSERVED: list[str] = []
STRICT = False


def expect(condition: bool, message: str, ok_note: str | None = None) -> None:
    """Учесть одно наблюдение; при нарушении запомнить находку."""
    global EXAMINED
    EXAMINED += 1
    if condition:
        if ok_note:
            OBSERVED.append(ok_note)
    else:
        FAILURES.append(message)


def fail(message: str) -> None:
    """Предусловие нарушено: находка без наблюдения (осматривать было нечего)."""
    FAILURES.append(message)


def warn(message: str) -> None:
    """Отставание, а не ложь. При --strict становится находкой."""
    if STRICT:
        FAILURES.append(message)
    else:
        WARNINGS.append(message)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def flat(text: str) -> str:
    """Неразрывные пробелы приводятся к обычным: числа печатают и так, и так."""
    return text.replace("\u00a0", " ")


def header_of(lines: list[str], size: int) -> str:
    """Шапка: первые строки после заголовка первого уровня."""
    return "\n".join(lines[1 : 1 + size])


# --- объявление состояний ---------------------------------------------------


def load_states(root: Path) -> tuple[dict | None, list[str]]:
    """Объявление состояний из данных. None — объявления нет или оно не читается."""
    path = root / STATES_FILE
    if not path.is_file():
        return None, [
            f"нет {STATES_FILE} — состояние переводов не объявлено данными, "
            f"проверять нечего (объявление в коде запрещено: иначе следующий "
            f"полный перевод нельзя объявить, не правя проверку)"
        ]
    try:
        data = json.loads(read(path))
    except json.JSONDecodeError as error:
        return None, [f"{STATES_FILE} не разобран: {error}"]
    states = data.get("states")
    if not isinstance(states, list) or not states:
        return None, [f"{STATES_FILE}: список состояний пуст — проверять нечего"]
    for index, state in enumerate(states):
        if not isinstance(state, dict) or "path" not in state or "state" not in state:
            return None, [f"{STATES_FILE}: запись {index + 1} не называет путь или состояние"]
        if state["state"] not in ("full", "partial"):
            return None, [
                f"{STATES_FILE}: у «{state.get('path')}» состояние "
                f"{state['state']!r}, а известно два: full, partial"
            ]
    return data, []


# --- структура и числа ------------------------------------------------------


def section_profile(lines: list[str]) -> list[tuple[int, int]]:
    """Профиль разделов: (блоков команд, строк таблиц) на каждый заголовок `##`."""
    profile: list[tuple[int, int]] = []
    started = False
    current = [0, 0]
    in_fence = False
    for line in lines:
        if line.startswith("## "):
            if started:
                profile.append((current[0], current[1]))
            started = True
            current = [0, 0]
            continue
        if not started:
            continue
        if FENCE.match(line):
            if not in_fence:
                current[0] += 1
            in_fence = not in_fence
            continue
        if line.startswith("|"):
            current[1] += 1
    if started:
        profile.append((current[0], current[1]))
    return profile


def POSIX_BASENAME(path: str) -> str:
    """Имя файла без каталога — для сравнения «ссылка ведёт на самого себя»."""
    return path.rsplit("/", 1)[-1]

def count_headings(lines: list[str], prefix: str) -> int:
    return sum(1 for line in lines if line.startswith(prefix))


def relative_targets(text: str) -> list[tuple[int, str]]:
    """Относительные ссылки и изображения файла: (строка, цель)."""
    found: list[tuple[int, str]] = []
    for index, line in enumerate(text.splitlines(), 1):
        for target in LINK.findall(line):
            target = target.strip()
            if target.lower().startswith(REMOTE_PREFIXES):
                continue
            found.append((index, target))
    return found


def numbers_of(text: str, pattern: str) -> tuple[int, int, int] | None:
    """Числа индекса по образцу перевода: (файлов, узлов, рёбер)."""
    match = re.search(pattern, flat(text))
    if not match:
        return None
    try:
        files, nodes, edges = (int(part.replace(" ", "")) for part in match.groups()[:3])
    except (ValueError, TypeError):  # pragma: no cover — образец совпал, число не разобралось
        return None
    return files, nodes, edges


def run_snapshot(root: Path, project: Path, match: str | None = None) -> tuple[dict | None, str]:
    """Ответ `tools/rev_snapshot.py`: (запись, причина отказа)."""
    tool = root / "tools" / "rev_snapshot.py"
    if not tool.is_file():
        return None, f"нет {tool.relative_to(root).as_posix()} — активную запись ревизии не прочитать"
    if not project.is_file():
        return None, (
            f"нет {project} — запись ревизии не прочитать: числа переводов сверять не с чем"
        )
    command = [sys.executable, str(tool), "--project", str(project)]
    if match:
        command += ["--match", match]
    completed = subprocess.run(
        command, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False
    )
    payload = None
    for line in reversed((completed.stdout or "").splitlines()):
        if line.strip().startswith("{"):
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                payload = None
            break
    if payload is None:
        detail = (completed.stderr or completed.stdout or "").strip().splitlines()
        return None, f"снимок ревизии не разобран: {detail[-1][:200] if detail else 'пустой ответ'}"
    return payload, ""


# --- свойства ---------------------------------------------------------------


def delivered_targets(root: Path) -> tuple[set[str] | None, list[str]]:
    """Пути поставки (цели манифеста). None — состав поставки неизвестен."""
    release = root / "tools" / "release"
    if not (release / "manifest.toml").is_file():
        return None, [f"нет tools/release/manifest.toml — состав поставки неизвестен, "
                      f"ссылки переводов не проверены"]
    sys.path.insert(0, str(release))
    try:
        import relcommon as rel  # noqa: PLC0415 - путь к общему модулю добавлен выше
        manifest = rel.load_manifest(root / "tools/release/manifest.toml", root)
        plan = rel.collect(root, manifest)
    except Exception as error:  # pragma: no cover - сломанный манифест
        return None, [f"манифест поставки не разобран: {error} — ссылки не проверены"]
    return set(plan.files), []


def forbidden_path(target: str) -> bool:
    """Ссылка ведёт на путь, которого в поставке нет по решению (docs/ru, DEFECTS)."""
    clean = posixpath.normpath(target.split("#", 1)[0].strip())
    return any(clean.startswith(item) or clean == item.rstrip("/")
               for item in FORBIDDEN_LINKS)


def resolve(target: str, delivered: set[str]) -> bool:
    """Разрешить относительную ссылку против состава поставки."""
    clean = target.split("#", 1)[0].strip().strip("<>").strip()
    if not clean:
        return True  # ссылка на якорь внутри того же файла
    clean = posixpath.normpath(clean.replace("\\", "/"))
    if clean.startswith("..") or clean.startswith("/"):
        return False
    if clean in delivered:
        return True
    # Ссылка на каталог: цель существует, если в нём есть файлы поставки. Иначе
    # каждая ссылка на `docs/skills/` была бы провалом.
    prefix = clean.rstrip("/") + "/"
    return any(path.startswith(prefix) for path in delivered)


def check_header(relative: str, header: str, requires: list[dict]) -> None:
    """Шапка называет то, что объявлено данными. Одна находка на все образцы."""
    missing = [item["name"] for item in requires if not re.search(item["pattern"], header)]
    expect(
        not missing,
        f"{relative}: шапка не называет: {'; '.join(missing)} — по шапке состояние не "
        f"отличить от другого",
    )


def check_no_partial_marker(relative: str, header: str, markers: list[tuple[str, str]]) -> None:
    """У полного перевода нет ни одного признака неполноты. Одна находка на все маркеры."""
    present = [f"{language}: «{marker}»" for language, marker in markers if marker in header]
    expect(
        not present,
        f"{relative}: в шапке стоит признак неполноты ({'; '.join(present)}), а перевод "
        f"объявлен полным",
    )


def check_link_coverage(relative: str, text: str, main_text: str) -> None:
    """Полный перевод называет все документы, которые называет основной.

    Ссылка на самого себя не требуется: в списке целей `README.md` стоит
    `README.ru.md` (переключатель языка), и требовать её от русского файла
    значило бы требовать самоссылку — формальность вместо свойства.
    """
    own = POSIX_BASENAME(relative)
    required = {
        target.split("#", 1)[0].strip()
        for _, target in relative_targets(main_text)
        if target.split("#", 1)[0].strip() != own
    }
    present = {target.split("#", 1)[0].strip() for _, target in relative_targets(text)}
    missing = sorted(target for target in required if target and target not in present)
    expect(
        not missing,
        f"{relative}: полный перевод не называет {len(missing)} документ(ов) из {MAIN}: "
        f"{', '.join(missing[:5])}",
    )


def active_revision(payload: dict | None) -> dict | None:
    """Активная запись ревизии из ответа инструмента.

    Две формы ответа: `tools/rev_snapshot.py` без `--match` печатает саму запись
    (`rev_id`, `files`, `nodes`, `edges`), с `--match` — обёртку
    (`queried`, `active`, `match`). Обе означают одно и то же измерение, и
    разбирать их в двух местах значило бы завести две правды об одном ответе.
    Запись, которая ещё запечатывается, содержит `status`, а не размеры: это
    не снимок, и путать его с нулями нельзя.
    """
    if not payload:
        return None
    candidate = payload.get("active") if "active" in payload else payload
    if isinstance(candidate, dict) and {"files", "nodes", "edges"} <= set(candidate):
        return candidate
    return None


def check_numbers(relative: str, text: str, state: dict, snapshot: dict | None,
                  snapshot_problem: str, root: Path, project: Path) -> None:
    """Числа индекса в переводе — против активной записи ревизии."""
    pattern = state.get("numbers_pattern")
    if not pattern:
        fail(f"{relative}: числа не объявлены в {STATES_FILE} — сверять нечем, "
             f"а число без сторожа стареет молча")
        return
    numbers = numbers_of(text, pattern)
    if numbers is None:
        fail(f"{relative}: числа индекса не найдены по объявленному образцу — "
             f"документ перестал называть размер собственного индекса")
        return
    if snapshot_problem:
        expect(True, "", ok_note=f"{relative}: {snapshot_problem}")
        return
    active = active_revision(snapshot)
    if not active:
        status = (snapshot or {}).get("status")
        if status == "in_progress":
            expect(True, "", ok_note=(
                f"{relative}: ревизия {(snapshot or {}).get('rev_id')} ещё запечатывается — "
                f"сверка чисел отложена, а не объявлена провалом"
            ))
            return
        fail(f"{relative}: активная ревизия не прочитана — числа перевода сверить не с чем")
        return
    stated = numbers
    actual = (int(active["files"]), int(active["nodes"]), int(active["edges"]))
    if stated == actual:
        expect(True, "", ok_note=(
            f"{relative}: числа {stated[0]} / {stated[1]} / {stated[2]} совпадают с активной "
            f"ревизией {active['rev_id']}"
        ))
        return
    match_payload, problem = run_snapshot(
        root, project, match=f"{stated[0]},{stated[1]},{stated[2]}"
    )
    where = (match_payload or {}).get("match")
    if problem or not match_payload:
        fail(f"{relative}: числа {stated[0]} / {stated[1]} / {stated[2]} не совпадают с "
             f"активной ревизией {active['rev_id']} ({actual[0]} / {actual[1]} / {actual[2]}), "
             f"а историю ревизий прочитать не удалось: {problem}")
        return
    if where:
        warn(f"{relative}: числа {stated[0]} / {stated[1]} / {stated[2]} верны на "
             f"{where['rev_id']} ({where['created_at']}), активная ревизия "
             f"{active['rev_id']} — {actual[0]} / {actual[1]} / {actual[2]}: это отставание, "
             f"обновить перед публикацией")
    else:
        fail(f"{relative}: числа {stated[0]} / {stated[1]} / {stated[2]} — такого состояния "
             f"не было ни в одной из {match_payload.get('rows')} ревизий "
             f"({match_payload.get('oldest')}…{match_payload.get('newest')}); активная ревизия "
             f"{active['rev_id']} — {actual[0]} / {actual[1]} / {actual[2]}: документ называет "
             f"состояние, которого не было")


def check_common(relative: str, lines: list[str], text: str,
                 delivered: set[str] | None, native_cyrillic: bool = False) -> list[int]:
    """Свойства, одинаковые для любого состояния перевода.

    Два из них — «кириллица вне ярлыка» и «русский заполнитель в командах» —
    ловят ОСТАТКИ русского оригинала в переводе на другой язык. Для перевода, у
    которого русский и есть язык (`native_cyrillic` в объявлении), они
    превращаются в наблюдение: иначе правило объявляло бы дефектом сам файл.
    """
    for forbidden in FORBIDDEN_LINKS:
        hits = [index for index, line in enumerate(lines, 1) if forbidden in line]
        expect(
            not hits,
            f"{relative}:{hits[0] if hits else 0}: ссылка на {forbidden} — такого пути в "
            f"поставке нет (всего {len(hits)})",
        )

    placeholder_lines = [
        index
        for index, line in enumerate(lines, 1)
        if any(item in line for item in FORBIDDEN_PLACEHOLDERS)
    ]
    if not native_cyrillic:
        expect(
            not placeholder_lines,
            f"{relative}:{placeholder_lines[0] if placeholder_lines else 0}: в примерах команд "
            f"остался русский заполнитель (строк {len(placeholder_lines)})",
        )

    if native_cyrillic:
        expect(True, "", ok_note=(
            f"{relative}: кириллица — язык этого перевода, правило о кириллице вне "
            f"ярлыка к нему не применяется"
        ))
        expect(True, "", ok_note=(
            f"{relative}: заполнитель <конфиг> — часть русской команды, правило о "
            f"заполнителе к этому переводу не применяется"
        ))
        in_fence = False
        code_blocks = 0
        for line in lines:
            if FENCE.match(line):
                in_fence = not in_fence
                if in_fence:
                    code_blocks += 1
        expect(
            code_blocks > 0,
            f"{relative}: не найдено ни одного блока команд — проверять нечего, а это "
            f"документ с примерами",
        )
        links = relative_targets(text)
        if delivered is None:
            expect(
                False,
                f"{relative}: ссылки не проверены — состав поставки неизвестен "
                f"(переводов со ссылками {len(links)})",
            )
        else:
            for index, target in links:
                if forbidden_path(target):
                    continue
                expect(
                    resolve(target, delivered),
                    f"{relative}:{index}: ссылка {target!r} не указывает на файл поставки",
                )
        return placeholder_lines

    in_fence = False
    code_blocks = 0
    code_cyrillic: list[tuple[int, str]] = []
    outside: list[tuple[int, str]] = []
    for index, line in enumerate(lines, 1):
        if FENCE.match(line):
            in_fence = not in_fence
            if in_fence:
                code_blocks += 1
            continue
        if index in placeholder_lines:
            continue  # уже названо заполнителем: второй раз не считаем
        if not CYRILLIC.search(line) or SWITCHER_LABEL in line:
            continue
        (code_cyrillic if in_fence else outside).append((index, line))

    expect(
        not code_cyrillic,
        f"{relative}:{code_cyrillic[0][0] if code_cyrillic else 0}: кириллица в блоке "
        f"команд ({len(code_cyrillic)} строк) — пример команды остался на русском",
    )
    expect(
        not outside,
        f"{relative}:{outside[0][0] if outside else 0}: кириллица вне ярлыка «Русский» "
        f"в {len(outside)} строках, первая: {outside[0][1][:80]!r}" if outside
        else f"{relative}: кириллица только в ярлыке «Русский»",
    )
    expect(
        code_blocks > 0,
        f"{relative}: не найдено ни одного блока команд — проверять нечего, а это "
        f"перевод с примерами",
    )

    links = relative_targets(text)
    if delivered is None:
        expect(
            False,
            f"{relative}: ссылки не проверены — состав поставки неизвестен "
            f"(переводов со ссылками {len(links)})",
        )
    else:
        for index, target in links:
            # Запрещённые пути уже названы выше: одна порча — одна находка.
            if forbidden_path(target):
                continue
            expect(
                resolve(target, delivered),
                f"{relative}:{index}: ссылка {target!r} не указывает на файл поставки",
            )
    return placeholder_lines


def resolve_source(root: Path, declared: str) -> Path:
    """Файл перевода: в живом дереве `community/…`, в поставке — в корне."""
    direct = root / declared
    if direct.is_file():
        return direct
    return root / Path(declared).name


def check_state(root: Path, state: dict, markers: list[tuple[str, str]],
                main_text: str | None, main_lines: list[str] | None,
                delivered: set[str] | None, snapshot: dict | None, snapshot_problem: str,
                project: Path) -> None:
    declared = state["path"]
    kind = state["state"]
    path = resolve_source(root, declared)
    if not path.is_file():
        fail(f"нет {declared} — перевод {state.get('language')} отсутствует, "
             f"а состояние объявлено")
        return
    text = read(path)
    lines = text.splitlines()
    size = int(state.get("header_lines") or DEFAULT_HEADER_LINES)
    header = header_of(lines, size)

    if kind == "full":
        check_no_partial_marker(declared, header, markers)
        check_header(declared, header, state.get("header_requires") or [])
        if main_lines is None or main_text is None:
            expect(True, "", ok_note=f"{declared}: структура не сверялась — нет {MAIN}")
        else:
            sections = count_headings(lines, "## ")
            main_sections = count_headings(main_lines, "## ")
            expect(
                sections == main_sections,
                f"{declared}: разделов ## {sections}, а в {MAIN} их {main_sections}: "
                f"раздел пропал или добавился",
            )
            subsections = count_headings(lines, "### ")
            main_subsections = count_headings(main_lines, "### ")
            expect(
                subsections == main_subsections,
                f"{declared}: подразделов ### {subsections}, а в {MAIN} их {main_subsections}",
            )
            profile = section_profile(lines)
            main_profile = section_profile(main_lines)
            difference = next(
                (
                    (index, left, right)
                    for index, (left, right) in enumerate(zip(profile, main_profile))
                    if left != right
                ),
                None,
            )
            if difference is not None:
                index, left, right = difference
                expect(
                    False,
                    f"{declared}: в разделе {index + 1} блоков команд {left[0]} и строк таблиц "
                    f"{left[1]}, а в {MAIN} — {right[0]} и {right[1]}: раздел переведён не целиком",
                )
            else:
                expect(
                    len(profile) == len(main_profile),
                    f"{declared}: разделов с содержимым {len(profile)}, а в {MAIN} их "
                    f"{len(main_profile)}",
                )
            check_link_coverage(declared, text, main_text)
        check_common(declared, lines, text, delivered,
                     bool(state.get("native_cyrillic")))

    else:  # partial
        marker = state.get("partial_marker") or ""
        presented = marker in header
        expect(
            presented,
            f"{declared}: в шапке нет признака неполноты «{marker}» — частичный перевод "
            f"выдаётся за полный",
        )
        if presented:
            gaps = state.get("_gaps") or []
            named = [name for name, pattern in gaps if re.search(pattern, header)]
            threshold = int(state.get("min_gaps") or DEFAULT_MIN_GAPS)
            expect(
                len(named) >= threshold,
                f"{declared}: шапка называет недостающих пунктов {len(named)} из {len(gaps)} "
                f"({', '.join(named) or 'ни одного'}), а требуется не менее {threshold}: "
                f"частичность без перечня — обещание, а не состояние",
            )
        else:
            # Зависимое свойство: без шапки перечень проверять не в чем. Находка о
            # шапке уже есть, и вторая про тот же дефект была бы счётом копий.
            expect(
                True,
                "",
                ok_note=(
                    f"{declared}: перечень недостающего не проверялся — шапки нет, "
                    f"об этом одна находка, а не две"
                ),
            )
        check_common(declared, lines, text, delivered,
                     bool(state.get("native_cyrillic")))

    check_numbers(declared, text, state, snapshot, snapshot_problem, root, project)


def main() -> int:
    global STRICT
    parser = argparse.ArgumentParser(description="Состояние переводов README")
    parser.add_argument("--root", default=str(REPO), help="корень проверяемого дерева")
    parser.add_argument(
        "--project",
        default=None,
        help="конфиг корпуса для записи ревизии (по умолчанию <root>/config/ragraf.yaml)",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="отставание чисел считать находкой (режим публикации: tools/release/gate_release.py)",
    )
    args = parser.parse_args()
    STRICT = bool(args.strict)
    root = Path(args.root)
    project = Path(args.project) if args.project else root / "config" / "ragraf.yaml"

    data, problems = load_states(root)
    for problem in problems:
        fail(problem)

    main_path = root / MAIN
    main_text = read(main_path) if main_path.is_file() else None
    main_lines = main_text.splitlines() if main_text is not None else None
    if main_lines is None:
        fail(f"нет {MAIN} — сверять переводы не с чем")

    delivered, delivery_problems = delivered_targets(root)
    for problem in delivery_problems:
        fail(problem)

    snapshot: dict | None = None
    snapshot_problem = ""
    if data is not None:
        parsed, snapshot_problem = run_snapshot(root, project)
        snapshot = parsed
        if parsed is not None and not parsed.get("active"):
            snapshot_problem = ""

    if data is not None:
        gaps = [
            (item["name"], item["pattern"])
            for item in (data.get("gaps") or [])
            if isinstance(item, dict) and "name" in item and "pattern" in item
        ]
        # Перечень признаков неполноты — свойство ЯЗЫКА, а не текущего состояния:
        # иначе маркер языка, ставшего полным, выпадает из перечня, и вернуть его
        # в файл можно незаметно (измерено на порче «вернуть маркер в de»).
        markers = [
            (item.get("language", "?"), item["marker"])
            for item in (data.get("incomplete_markers") or [])
            if isinstance(item, dict) and item.get("marker")
        ]
        if not markers:
            markers = [
                (state.get("language", "?"), state["partial_marker"])
                for state in data["states"]
                if state.get("partial_marker")
            ]
        if not gaps:
            fail(f"{STATES_FILE}: перечень недостающих пунктов не объявлен — "
                 f"частичность нечем проверить")
        for state in data["states"]:
            state["_gaps"] = gaps
            check_state(root, state, markers, main_text, main_lines, delivered,
                        snapshot, snapshot_problem, project)
            print(f"перевод: {state['path']}")
            print(f"состояние: {state['state']}"
                  + (f" (версия {state['version']})" if state.get("version") else ""))

    if EXAMINED == 0:
        fail("ни одного объекта не осмотрено: «ноль проверенного» — это не PASS")
    report()
    return 1 if FAILURES else 0


def report() -> None:
    print(
        "граница: состояние подтверждается структурой разделов, ссылками и числами; "
        "смысл перевода машинной проверке не поддаётся и здесь не проверяется"
    )
    print(f"объявление состояний: {STATES_FILE}")
    for note in OBSERVED:
        print(f"наблюдение: {note}")
    for warning in WARNINGS:
        print(f"WARN: {warning}")
    for failure in FAILURES:
        print(f"FAIL: {failure}")
    print(f"осмотрено: {EXAMINED}")
    if WARNINGS and not FAILURES:
        print(f"предупреждений: {len(WARNINGS)}")
    if FAILURES:
        print(f"нарушений: {len(FAILURES)}")


if __name__ == "__main__":
    sys.exit(main())
