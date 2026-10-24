#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Проверяльщик поставки RAGRAF: собранное дерево против манифеста и источника.

Проверка отвечает не на вопрос «файлы на месте?», а на вопрос «можно ли это
отдавать людям». Поэтому правил девять, и каждое ловит свой класс дефекта:

1. `presence`  — каждый файл манифеста есть в сборке;
2. `hash`      — содержимое совпадает с источником побайтово (сборка не правит);
3. `coverage`  — в сборке нет файлов вне манифеста (мусор не просочился);
4. `excluded`  — ни одного пути из списка исключений (защита от «втянули случайно»);
5. `syntax`    — скрипты и разметка разбираются: `*.py` компилируется, `*.ps1`
                 разбирается парсером PowerShell, `*.yml` — YAML, `*.json` — JSON;
6. `secrets`   — нет секретоносных ЗНАЧЕНИЙ (поиск по формам, а не по именам
                 файлов), значение в отчёт не печатается;
7. `encoding`  — все файлы без BOM и с LF, кроме явных исключений (`.bat`, `.cmd`);
8. `sources`   — таблица `docs/03_implementation/RELEASE-SOURCES.md` согласована
                 со сборкой: строка на файл, хэш в строке равен собранному,
                 признак «совпадает» не врёт.
9. `paths`     — в поставке нет путей машины автора: пути внутрь домашнего
                 каталога и внутрь приватного дерева. Заглушки вида
                 `C:\\Users\\<пользователь>` разрешены закрытым списком с причиной.

Измеренный дефект, ради которого появилось правило 6 (24.10.2026):
`tests/test_access_live_out.txt` — журнал прогона теста — содержит выданный
токен доступа панели. При наивной сборке `tests/**` целиком файл уехал бы
в публикацию. Теперь он исключён манифестом (с причиной), а правило 6 проверяет
остальное дерево; гейт отдельно доказывает, что правило 6 не молчит.

Границы, названные честно:

* проверка НЕ сверяется с GitHub: сети у неё нет, и делать вид, что она
  проверяет «свежесть публичного main», было бы ложью. Сверка с публикацией
  живёт в `docs/03_implementation/RELEASE-SOURCES.md`, а здесь проверяется
  согласие трёх вещей: файла, его хэша и строки таблицы;
* правило 6 ищет формы, а не смысл: строка «пароль вводит пользователь»
  нарушением не считается, а значение из 20+ символов после имени — считается;
* правило 7 смотрит на собранное дерево, а не на живое: расхождение обнаружится
  и без него, а здесь важно, что отдаваемые файлы соответствуют контракту LF.

Запуск:
    python tools/release/verify_release.py --tree %TEMP%\\ragraf-release-sbx
    python tools/release/verify_release.py --tree X --only secrets,hash

Коды возврата: 0 — годно, 1 — дефекты, 2 — ошибка аргументов, 3 — окружение.
"""

from __future__ import annotations

import argparse
import json
import re
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

RULES = (
    "presence",
    "hash",
    "coverage",
    "excluded",
    "syntax",
    "secrets",
    "encoding",
    "sources",
    "paths",
)

# Формы секретоносных ЗНАЧЕНИЙ. Список намеренно узкий: широкий шаблон
# («любое слово длиннее 16 символов») нашёл бы сотни имён функций и обесценил
# проверку. Каждая форма ниже либо узнаётся однозначно, либо не входит сюда.
SECRET_FORMS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("закрытый ключ", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("ключ AWS", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("токен GitHub", re.compile(r"\b(?:ghp_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9_]{40,})\b")),
    ("ключ OpenAI-подобный", re.compile(r"\bsk-[A-Za-z0-9]{32,}\b")),
    ("токен Slack", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    (
        "значение после имени учётного поля",
        re.compile(
            # Приставка перед именем поля допускается: `MYSQL_PASSWORD`,
            # `DB_TOKEN`, `ADMIN_SECRET` — это те же учётные поля. До 24.10.2026
            # шаблон требовал границу слова прямо перед именем и поэтому НЕ
            # ловил `MYSQL_PASSWORD = "…"` — измеренный дефект: настоящее
            # значение доступа к тестовой СУБД rule не находил, а три подставных
            # значения в проверках отказа находил.
            r"(?i)\b[A-Za-z0-9_]*?(?:access[_-]?token|refresh[_-]?token|bootstrap[_-]?token|"
            r"session[_-]?token|api[_-]?key|apikey|client[_-]?secret|secret[_-]?key|"
            r"password|passwd|pwd|token|secret|"
            r"токен\s+доступа|токен|пароль)\b\s*[:=]\s*[\"']?([A-Za-z0-9_\-+/=]{16,})"
        ),
    ),
)

# Подставные значения в проверках отказа. Задача: файлы, которые проверяют
# ОТКАЗ («неверный токен → 403», «неверное значение доступа → проба не идёт»),
# обязаны содержать какое-то значение — и оно неизбежно имеет форму учётного.
# Убирать такие строки ради зелёной проверки значило бы ослаблять сами проверки.
#
# Поэтому значение объявляется подставным ПРЯМО В ТЕКСТЕ маркером, а маркер
# действует только в перечисленных ниже файлах: новый файл не может
# самоосвободиться — его обязан внести сюда человек с причиной. Обратное тоже
# проверяется: маркер в файле, которого нет в списке, — провал; файл из списка
# без маркера — провал (список не стареет молча).
PLACEHOLDER_MARK = "ragraf-release: placeholder-value"

# Файл самой проверки объявляет маркер константой, поэтому его строки не
# считаются декларацией: иначе правило находило бы маркер в самом себе.
MARKER_OWNER = "tools/release/verify_release.py"

PLACEHOLDER_FILES: dict[str, str] = {
    "tools/check_admin.py": "заведомо неверный токен в проверке отказа 403",
    "tools/fix_admin_check.py": "тот же неверный токен, перенесённый строкой патча",
    "tests/test_db_index.py": "заведомо неверное значение доступа в проверке отказа",
}

TEXT_SUFFIXES = {
    "", ".py", ".ps1", ".psm1", ".psd1", ".md", ".txt", ".yml", ".yaml", ".json",
    ".toml", ".cfg", ".ini", ".css", ".js", ".mjs", ".html", ".cff", ".sh",
    ".bat", ".cmd", ".cff", ".in", ".rst", ".sql",
}

CRLF_EXEMPT = {".bat", ".cmd"}

# Пути машины автора. Формулировка та же, что в tools/check_community_wrapper.py:
# нарушение — путь ВНУТРЬ каталога, то есть с сегментом после имени. Совпадение
# не вкусовое: одна и та же форма обязана ловиться одинаково в обвязке и в
# поставке, иначе «в обвязке чисто» перестаёт что-либо значить. Отличие одно —
# здесь проверяется вся сборка, а не 18 файлов обвязки.
PATH_FORMS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "путь внутрь домашнего каталога",
        re.compile(r"(?i)\b[A-Z]:[\\/]+Users[\\/]+(?P<segment>[^\\/\s`\"'|]+)[\\/]"),
    ),
    (
        "путь внутрь приватного дерева",
        re.compile(
            r"(?i)\b[A-Z]:[\\/]+(?:AutoClaw|1st_RdpMON)[\\/]+(?P<segment>[^\\/\s`\"'|]+)[\\/]"
        ),
    ),
)

# Заглушки: не чужое имя, а место, куда читатель подставит своё. Список закрыт,
# и у каждой записи есть причина — иначе «разрешённое имя» станет лазейкой,
# через которую вернётся настоящее.
PATH_PLACEHOLDERS: dict[str, str] = {
    "<пользователь>": "заглушка вместо имени пользователя (документация и примеры)",
    "<user>": "заглушка вместо имени пользователя",
    "<имя>": "заглушка вместо имени пользователя",
    "demo": "вымышленное имя в примере контракта knowledge_search",
    "...": "сокращение вместо продолжения пути в иллюстрации",
    "…": "сокращение вместо продолжения пути в иллюстрации",
}


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="verify_release.py",
        description="Проверить собранное дерево поставки RAGRAF против манифеста.",
    )
    parser.add_argument("--tree", required=True, help="каталог собранного дерева")
    parser.add_argument("--manifest", default=None, help="манифест (по умолчанию — в живом дереве)")
    parser.add_argument("--source", default=None, help="корень живого дерева")
    parser.add_argument(
        "--only",
        default="all",
        help="какие правила применять (по умолчанию all; список через запятую)",
    )
    return parser.parse_args(argv)


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def check_presence(ctx: "Context") -> list[str]:
    """Каждый путь поставки существует в сборке.

    Первая версия правила проверяла только «каталог манифеста дал хотя бы один
    файл» и пропускала удаление отдельного файла внутри каталога. Гейт это
    поймал: удаление docs/00_plan/PLAN-IAM-YOU-GLOBAL-MAP.md прошло как годное
    дерево (измерено 24.10.2026, порча 3 из 4). Правильная формулировка — не
    «каталог непуст», а «каждый разрешённый путь на месте»: список путей уже
    посчитан из манифеста, по нему и проверяем.
    """
    problems = []
    for target in sorted(ctx.expected):
        ctx.count()
        if target not in ctx.tree_files:
            problems.append(f"{target}: нет в сборке (источник {ctx.plan.files[target]})")
    for include in ctx.manifest.includes:
        if not include.is_dir:
            continue
        ctx.count()
        hit = any(target.startswith(include.target + "/") for target in ctx.plan.files)
        if not hit:
            problems.append(f"{include.source}: каталог манифеста не дал ни одного файла поставки")
    return problems


def check_hash(ctx: "Context") -> list[str]:
    problems = []
    for target in sorted(ctx.expected):
        path = ctx.tree / target
        ctx.count()
        if not path.is_file():
            continue  # уже названо правилом presence
        actual = rel.sha256_file(path)
        ctx.count()
        if actual != ctx.expected[target]:
            problems.append(
                f"{target}: содержимое сборки не совпадает с источником "
                f"({actual[:12]}… против {ctx.expected[target][:12]}…)"
            )
    return problems


def check_coverage(ctx: "Context") -> list[str]:
    problems = []
    allowed = set(ctx.expected) | set(ctx.manifest.artifacts)
    for path in sorted(ctx.tree_files):
        if path in allowed:
            continue
        ctx.count()
        problems.append(f"{path}: файл в сборке, но его нет в манифесте — мусор просочился")
    if not problems:
        ctx.count()
    return problems


def check_excluded(ctx: "Context") -> list[str]:
    problems = []
    for target in sorted(ctx.tree_files):
        ctx.count()
        for exclude in ctx.manifest.excludes:
            if exclude.regex.match(target):
                problems.append(
                    f"{target}: попал в сборку, хотя исключён правилом {exclude.pattern} ({exclude.why})"
                )
                break
    return problems



def _yaml_load(text: str) -> None:
    """Разбор YAML с терпимостью к тегам инструментов (`!!python/name:…`).

    Граница названа честно: `mkdocs.yml` содержит тег
    `tag:yaml.org,2002:python/name:markdown.extensions.toc.slugify_unicode`,
    который `SafeLoader` отвергает. Файл при этом исправен — его читает mkdocs,
    а не мы. Отказ от такого файла был бы ложным FAIL, поэтому неизвестные
    python-теги принимаются как строка, а структурные ошибки YAML по-прежнему
    ловятся.
    """
    import yaml  # noqa: PLC0415 - нужен только этому правилу

    class Tolerant(yaml.SafeLoader):
        pass

    Tolerant.add_multi_constructor(
        "tag:yaml.org,2002:python/name:", lambda loader, suffix, node: suffix
    )
    Tolerant.add_multi_constructor("!", lambda loader, suffix, node: None)
    yaml.load(text, Loader=Tolerant)  # noqa: S506 - за основу взят SafeLoader

def check_syntax(ctx: "Context") -> list[str]:
    problems = []
    for target in sorted(ctx.tree_files):
        path = ctx.tree / target
        suffix = path.suffix.lower()
        ctx.count()
        if suffix == ".py":
            try:
                compile(read_text(path), str(path), "exec")
            except SyntaxError as error:
                problems.append(f"{target}:{error.lineno}: Python не разбирается — {error.msg}")
        elif suffix in {".yml", ".yaml", ".cff"}:
            try:
                import yaml  # noqa: PLC0415 - нужен только этому правилу
            except ModuleNotFoundError:
                problems.append("NOT-IMPLEMENTED: нет PyYAML — проверка YAML не выполнена")
                return problems
            try:
                _yaml_load(read_text(path))
            except Exception as error:  # noqa: BLE001 - сообщение YAML бывает разным
                problems.append(f"{target}: YAML не разбирается — {str(error).splitlines()[0]}")
        elif suffix == ".json":
            try:
                json.loads(read_text(path))
            except Exception as error:  # noqa: BLE001
                problems.append(f"{target}: JSON не разбирается — {error}")
    problems.extend(_check_powershell(ctx))
    return problems


def _check_powershell(ctx: "Context") -> list[str]:
    scripts = [target for target in sorted(ctx.tree_files) if target.lower().endswith((".ps1", ".psm1", ".psd1"))]
    if not scripts:
        ctx.count()
        return ["NOT-IMPLEMENTED: в сборке нет ни одного PowerShell-скрипта — это подозрительно, а не «чисто»"]

    workdir = Path(tempfile.mkdtemp(prefix="ragraf-verify-"))
    runner = workdir / "parse.ps1"
    # Список файлов передаётся ОДНОЙ строкой через запятую и разбирается внутри.
    # Замер: `-File script.ps1 -Files a b c` связывает в параметр только `a`,
    # остальные становятся позиционными и дают ошибку привязки — то есть проверка
    # отчиталась бы «разобрано 1» и промолчала про остальные.
    body = (
        "param([string]$Root, [string]$List)\n"
        "$bad = 0\n"
        "$files = @($List -split ',' | Where-Object { $_ })\n"
        "foreach ($rel in $files) {\n"
        "  $err = $null\n"
        "  $path = Join-Path $Root $rel\n"
        "  $null = [System.Management.Automation.Language.Parser]::ParseFile($path, [ref]$null, [ref]$err)\n"
        "  if ($err.Count) { $err | ForEach-Object { Write-Output \"PARSEFAIL $rel $($_.Extent.StartLineNumber) $($_.Message)\" }; $bad++ }\n"
        "}\n"
        "Write-Output \"PSPARSED $($files.Count) $bad\"\n"
        "exit 0\n"
    )
    runner.write_bytes(body.encode("utf-8"))
    try:
        completed = subprocess.run(
            ["pwsh", "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(runner),
             "-Root", str(ctx.tree), "-List", ",".join(scripts)],
            capture_output=True, text=True, timeout=600, check=False,
        )
    except FileNotFoundError:
        return ["NOT-IMPLEMENTED: pwsh не найден в PATH — разбор PowerShell не выполнен"]
    output = (completed.stdout or "") + (completed.stderr or "")
    problems = []
    for line in output.splitlines():
        if line.startswith("PARSEFAIL "):
            _, name, lineno, message = (line.split(" ", 3) + ["", ""])[:4]
            ctx.count()
            problems.append(f"{name}:{lineno}: PowerShell не разбирается — {message}")
    parsed = re.search(r"PSPARSED (\d+) (\d+)", output)
    if parsed is None:
        problems.append("NOT-IMPLEMENTED: разбор PowerShell не отчитался числом файлов")
    else:
        ctx.add(int(parsed.group(1)))
        if int(parsed.group(2)) != len(problems):
            problems.append(
                f"разбор PowerShell: парсер сообщил {parsed.group(2)} дефектов, а разобрано "
                f"{len(problems)} — расхождение отчёта и факта"
            )
    return problems


def _placeholder_lines(target: str, text: str) -> tuple[set[int], list[str]]:
    """Строки, где значение объявлено подставным, и нарушения самой декларации."""
    problems: list[str] = []
    marked: set[int] = set()
    if target == MARKER_OWNER:
        return marked, problems
    lines = text.splitlines()
    for index, line in enumerate(lines, 1):
        if PLACEHOLDER_MARK not in line:
            continue
        if target not in PLACEHOLDER_FILES:
            problems.append(
                f"{target}:{index}: маркер подставного значения в файле, которого нет "
                f"в PLACEHOLDER_FILES — объявить значение подставным можно только с причиной"
            )
            continue
        # Маркер стоит на строке значения, строкой выше или строкой ниже: значение
        # бывает внутри строкового литерала, где комментарий поставить некуда.
        marked.update({index - 1, index, index + 1})
    if target in PLACEHOLDER_FILES and not marked:
        problems.append(
            f"{target}: файл объявлен в PLACEHOLDER_FILES ({PLACEHOLDER_FILES[target]}), "
            f"но маркера {PLACEHOLDER_MARK!r} в нём нет — список устарел"
        )
    return marked, problems


def check_secrets(ctx: "Context") -> list[str]:
    problems = []
    for target in sorted(ctx.tree_files):
        path = ctx.tree / target
        if path.suffix.lower() not in TEXT_SUFFIXES and path.suffix:
            ctx.count()
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            ctx.count()
            continue
        ctx.count()
        marked, declaration = _placeholder_lines(target, text)
        problems.extend(declaration)
        for name, pattern in SECRET_FORMS:
            for match in pattern.finditer(text):
                value = match.group(1) or ""
                # Граница названа: значение, состоящее только из цифр, не считается
                # секретоносным. Иначе правило ловило бы счётчики и отметки времени,
                # а цена ложного провала здесь — остановленная публикация.
                if value.isdigit():
                    continue
                line_no = text[: match.start()].count("\n") + 1
                if line_no in marked:
                    continue
                # Значение не печатается НИКОГДА: отчёт читают люди и журналы.
                problems.append(
                    f"{target}:{line_no}: найдено секретоносное значение ({name}), значение скрыто"
                )
                break
            else:
                continue
            break
    return problems


def check_encoding(ctx: "Context") -> list[str]:
    """Контракт файла: без BOM и с LF — но только для ТЕКСТОВЫХ файлов.

    Граница проверена замером, а не выбрана по вкусу: при первом прогоне
    правило красило двоичные файлы (индекс фикстуры, граф свойств кода) за то,
    что в них случайно встретилась пара байт 0x0D 0x0A. Двоичный файл не имеет
    переводов строк, и требовать от него LF — значит выдавать ложный FAIL.
    Поэтому проверяются расширения из TEXT_SUFFIXES; остальные считаются
    двоичными и только считаются.
    """
    problems = []
    skipped_exempt = 0
    skipped_binary = 0
    for target in sorted(ctx.tree_files):
        path = ctx.tree / target
        if path.suffix.lower() in CRLF_EXEMPT:
            skipped_exempt += 1
            continue
        if path.suffix.lower() not in TEXT_SUFFIXES:
            skipped_binary += 1
            continue
        data = path.read_bytes()
        ctx.count()
        marks = []
        if data.startswith(b"\xef\xbb\xbf"):
            marks.append("BOM")
        if b"\r\n" in data:
            marks.append("CRLF")
        if marks:
            problems.append(f"{target}: нарушение контракта файла — {' '.join(marks)}")
    ctx.add(skipped_exempt + skipped_binary)
    return problems


def parse_sources_table(text: str) -> tuple[list[dict[str, str]], list[str]]:
    """Разобрать таблицу источников, начиная с её шапки.

    Первая версия правила читала ЛЮБУЮ строку из шести ячеек — и на живом
    документе поймала таблицы разделов 2 и 7 (измерено: пять «файлов» с именами
    «версия», «живое дерево (было)», «старая сборочная копия»). Ошибка была не
    в документе, а в разборе: таблиц в документе несколько, и нужна та, у которой
    шапка называет столбец «sha256 в публичном main».
    """
    rows: list[dict[str, str]] = []
    problems: list[str] = []
    lines = text.splitlines()
    start = 0
    for index, line in enumerate(lines):
        cells = [cell.strip().lower() for cell in line.strip().strip("|").split("|")]
        if line.strip().startswith("|") and "sha256 в публичном main" in cells:
            start = index + 1
            break
    else:
        return rows, ["таблица источников не найдена: нет шапки со столбцом «sha256 в публичном main»"]

    for line in lines[start:]:
        stripped = line.strip()
        if not stripped.startswith("|"):
            break  # таблица кончилась
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if len(cells) != 6:
            continue
        if set(cells[0]) <= set("-: "):
            continue
        rows.append(
            {
                "file": cells[0],
                "source": cells[1],
                "hash_main": cells[2],
                "hash_tree": cells[3],
                "same": cells[4],
                "target": cells[5],
            }
        )
    if not rows:
        problems.append("таблица источников не разобрана: ни одной строки данных")
    return rows, problems


def check_sources(ctx: "Context") -> list[str]:
    document = ctx.source / "docs/03_implementation/RELEASE-SOURCES.md"
    if not document.is_file():
        return ["NOT-IMPLEMENTED: нет docs/03_implementation/RELEASE-SOURCES.md — сверять нечего"]
    rows, problems = parse_sources_table(read_text(document))
    seen: dict[str, str] = {}
    for row in rows:
        ctx.count()
        name = row["file"]
        if name in seen:
            problems.append(f"{name}: две строки таблицы источников на один файл")
            continue
        seen[name] = row["target"]
        if not (ctx.source / name).is_file():
            problems.append(f"{name}: строка таблицы источников ссылается на отсутствующий файл")
            continue
        target = row["target"]
        built = ctx.tree / target
        ctx.count()
        if not built.is_file():
            problems.append(f"{target}: строка таблицы называет путь поставки, которого нет в сборке")
            continue
        actual = rel.sha256_file(built)
        ctx.count()
        if actual != row["hash_tree"].lower():
            problems.append(
                f"{name}: хэш в таблице источников ({row['hash_tree'][:12]}…) не равен собранному "
                f"({actual[:12]}…) — таблица описывает другое содержимое"
            )
        if row["same"] == "да":
            ctx.count()
            if row["hash_main"].lower() != actual:
                problems.append(f"{name}: записано «совпадает с main», но хэши различны")
        elif row["same"] == "нет":
            ctx.count()
            if row["hash_main"].lower() == actual:
                problems.append(f"{name}: записано «не совпадает с main», но хэши равны")

    # Сверка двух источников истины. Таблица обязана покрывать всю обвязку
    # (иначе «перенесли и забыли»), а каждая её строка — относиться к файлу,
    # который действительно входит в поставку (иначе таблица описывает файлы,
    # которых в поставке нет). Проверять «строка = путь включения» нельзя:
    # источники вида scripts/** перечислены каталогом, а строки — по файлам,
    # и правильная формулировка именно «файл покрыт манифестом».
    covered = {path.relative_to(ctx.source).as_posix() for path in ctx.plan.files.values()}
    wrapper_sources = {
        include.source for include in ctx.manifest.includes if include.kind == "wrapper"
    }
    ctx.count()
    for missing in sorted(wrapper_sources - set(seen)):
        problems.append(f"в манифесте есть обвязка, которой нет в таблице источников: {missing}")
    ctx.count()
    for extra in sorted(set(seen) - covered):
        problems.append(f"таблица источников описывает файл, которого нет в поставке: {extra}")
    return problems


def check_paths(ctx: "Context") -> list[str]:
    r"""Правило 9: в поставке нет путей машины автора.

    Свойство, а не написание: проверяется КАЖДАЯ строка каждого текстового
    файла сборки, поэтому новый отчёт с путём автора ловится тем же правилом,
    что и старый, а не только известные заранее места.

    Почему нужен сегмент после имени. `RAGRAF` — публичный корень
    продукта (имя проекта и есть имя продукта, так его называет и пример
    установщика), он разрешён. Путь ВНУТРЬ этого дерева выдаёт машину автора и
    на другой машине не существует. Путь внутрь `C:\Users\<пользователь>\` не
    переносим никогда.

    Что печатается при провале: файл, строка, класс нарушения и САМ СЕГМЕНТ
    (имя пользователя или каталога), а не весь путь: отчёт читают люди и
    журналы, и повторять в нём утечку незачем.
    """
    problems = []
    placeholders = {name.lower() for name in PATH_PLACEHOLDERS}
    for target in sorted(ctx.tree_files):
        path = ctx.tree / target
        if path.suffix.lower() not in TEXT_SUFFIXES and path.suffix:
            ctx.count()
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            ctx.count()
            continue
        ctx.count()
        for name, pattern in PATH_FORMS:
            found = False
            for match in pattern.finditer(text):
                segment = match.group("segment")
                if segment.lower() in placeholders:
                    continue
                line_no = text[: match.start()].count("\n") + 1
                problems.append(f"{target}:{line_no}: {name}: сегмент {segment!r}")
                found = True
            if found:
                break
    return problems


CHECKS = {
    "presence": check_presence,
    "hash": check_hash,
    "coverage": check_coverage,
    "excluded": check_excluded,
    "syntax": check_syntax,
    "secrets": check_secrets,
    "encoding": check_encoding,
    "sources": check_sources,
    "paths": check_paths,
}


class Context:
    def __init__(self, tree: Path, source: Path, manifest: rel.Manifest) -> None:
        self.tree = tree
        self.source = source
        self.manifest = manifest
        self.plan = rel.collect(source, manifest)
        self.expected = {
            target: rel.sha256_file(path) for target, path in self.plan.files.items()
        }
        self.tree_files = {
            path.relative_to(tree).as_posix() for path in tree.rglob("*") if path.is_file()
        }
        self.examined = 0

    def count(self) -> None:
        self.examined += 1

    def add(self, amount: int) -> None:
        self.examined += amount


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])

    source = Path(args.source).resolve() if args.source else rel.default_source()
    tree = Path(args.tree).expanduser().resolve()
    manifest_path = Path(args.manifest).resolve() if args.manifest else source / rel.MANIFEST_DEFAULT

    if not tree.is_dir():
        return rel.fail(f"нет каталога сборки: {tree}", 2)
    if not manifest_path.is_file():
        return rel.fail(f"нет манифеста: {manifest_path}", 2)

    selected = [item.strip() for item in args.only.split(",") if item.strip()]
    if args.only != "all":
        unknown = [item for item in selected if item not in CHECKS]
        if unknown:
            return rel.fail(f"неизвестные правила: {unknown}; известные: {list(RULES)}", 2)
    else:
        selected = list(RULES)

    try:
        manifest = rel.load_manifest(manifest_path, source)
    except Exception as error:  # noqa: BLE001
        return rel.fail(f"манифест не разбирается: {error}", 1)

    ctx = Context(tree, source, manifest)
    if ctx.plan.problems:
        for problem in ctx.plan.problems:
            print(f"FAIL: {problem}")
        return 1

    failures: list[str] = []
    for name in selected:
        found = CHECKS[name](ctx)
        for problem in found:
            failures.append(f"[{name}] {problem}")

    for problem in failures:
        print(f"FAIL: {problem}")
    print(f"правил применено: {len(selected)}")
    print(f"файлов в сборке: {len(ctx.tree_files)}")
    print(f"осмотрено: {ctx.examined}")
    if failures:
        print(f"нарушений: {len(failures)}")
        return 1
    if ctx.examined == 0:
        print("FAIL: ни одного объекта не осмотрено — это ложный PASS")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
