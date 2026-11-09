"""Установка RAGRAF в DeepSeek Harness как ПЛАГИНА, а не правкой профиля.

ЗАЧЕМ. До сих пор подключение жило блоком в `~/.dsh/profiles/<профиль>/cordis.patch.yml`
(запись `mcp-ragraf`) и отдельной записью моста хуков (`hooks-shared` на свод
`harness/all-hooks.json`). Замер 03.11.2026 показал, чем это кончается:

* бюджет инструкций — `agent-instructions.maxBytes` 65 536 против выросшего до
  71 230 байт `~/.dsh/AGENTS.md`: файл отбрасывался ЦЕЛИКОМ, и правило «сначала
  RAGRAF» не доезжало до модели ни в одном чате;
* режим гейта `warn` — гейт срабатывал и подсказывал, подсказку игнорировали;
* сервер стартовал с корпусом `RAGRAF`, потому что `cwd` записи MCP указывал на
  каталог RAGRAF, а сеанс шёл в другом проекте;
* обновление клиента 44.7.0 удалило пакет моста хуков из сборки, запись в
  профиле осталась и указывала в пустоту — хуки не вызывались НЕДЕЛЮ, и ни
  одна команда об этом не сообщила.

Поэтому установка переехала в пакет-плагин `integrations/dsh-plugin`
(`dsh plugin add`): пакет объявляет мост хуков своей зависимостью, поднимает
бюджет инструкций, ставит гейт в `deny` и остаётся версионированным и
удаляемым. Этот скрипт делает установку одной командой и ПРОВЕРЯЕТ её тем же
оракулом, что и клиент: `dsh --profile <p> --dump-config`.

Порядок (каждый шаг печатается):

  1. найти установку RAGRAF и интерпретатор (.venv рабочей копии);
  2. собрать `$DSH_HOME/ragraf/hooks.json` из проектных конфигов (xerj, bsg,
     RAGRAF) и переписать стадии RAGRAF через `dispatch.py`;
  3. положить `dispatch.py` и запись `install.json` в `$DSH_HOME/ragraf/`;
  4. скопировать пакет в `$DSH_HOME/ragraf/bundle/` и поставить его в профиль
     родной командой `dsh plugin --profile <p> add <каталог>`;
  5. СНЯТЬ прежние записи `mcp-ragraf` и `hooks-shared` из профиля — иначе в
     профиле окажутся две записи одного моста, а вторая не поднимается
     (измерено 21.10.2026: гейт не получал вызовов неделю);
  6. проверить `--dump-config`: записи есть, пути развернулись в существующие
     файлы, бюджет инструкций поднят.

Откат: копии правленых файлов лежат рядом с суффиксом `.bak-<метка>-dsh`, а
`dsh plugin --profile <p> remove ragraf-dsh` снимает пакет.

Коды возврата: 0 — установлено и проверено; 1 — проверка не прошла (причина
названа); 2 — установка не начиналась (нет клиента, профиля или RAGRAF).
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BUNDLE_SRC = REPO / "integrations" / "dsh-plugin"
DEFAULT_PROFILE = "desktop"
#: Стадии гейта, которые знает `tools/harness_gate.py`.
STAGES = ("session-start", "prompt", "pre-tool", "post-tool", "stop", "subagent-start",
          "subagent-stop", "selftest")
#: Стадии по умолчанию, если проектного `harness/hooks.json` нет (установка из
#: пакета или из дерева публикации). Тот же набор и с теми же таймаутами, что в
#: рабочей копии: без них гейт не существует вовсе, и это было бы тише любой
#: ошибки.
DEFAULT_STAGES = (("SessionStart", "session-start", 30),
                  ("UserPromptSubmit", "prompt", 20),
                  ("PreToolUse", "pre-tool", 60),
                  ("Stop", "stop", 30))
#: Записи профиля, которые заменяет пакет. Снимаются после успешной установки:
#: две записи одного моста не поднимаются обе.
LEGACY_IDS = ("mcp-ragraf", "hooks-shared")


# --------------------------------------------------------------------------- база


def dsh_home() -> Path:
    value = os.environ.get("DSH_HOME")
    if value:
        return Path(value)
    return Path(os.environ.get("USERPROFILE") or os.environ.get("HOME") or ".") / ".dsh"


def say(text: str) -> None:
    print(text, flush=True)


def short_path(path: Path | str) -> str:
    """8.3-путь: команда хука не может начинаться с кавычки, а пробел — может.

    Замер 02.11.2026: `pwsh -Command '"C:\\Program Files\\…"'` даёт ParserError,
    код 1. Поэтому все абсолютные пути в командах хуков приводятся к короткой
    форме без пробелов.
    """
    text = str(path)
    if os.name != "nt" or " " not in text:
        return text
    try:
        buffer = ctypes.create_unicode_buffer(1024)
        written = ctypes.windll.kernel32.GetShortPathNameW(text, buffer, 1024)
        if written:
            return buffer.value
    except Exception:  # noqa: BLE001 — короткий путь это удобство, не условие
        pass
    return text


def pick_python() -> str:
    forced = os.environ.get("RAGRAF_PYTHON")
    if forced and Path(forced).exists():
        return forced
    base = REPO / ".venv"
    for candidate in (base / "Scripts" / "python.exe", base / "bin" / "python"):
        if candidate.exists():
            return str(candidate)
    return sys.executable


def find_cli() -> tuple[list[str], dict] | None:
    """Команда запуска CLI клиента — С ПРОВЕРКОЙ, что она работает.

    Замер 03.11.2026: `dsh` в PATH оказался битым шимом от другого дерева
    (Cannot find module …@deepseek-ai/dsh/lib/bin.js), при этом установка
    сообщила об ошибке node, а не о том, что клиент выбран неверно. Поэтому
    кандидаты пробуются по очереди подкомандой `plugin --help`.

    Путь CLI внутри `app.asar` проверять как файл нельзя: это архив, и
    `Path.is_file()` на нём всегда False (замерено). Достаточно того, что сама
    сборка на месте — внутренний путь разрешает Electron.
    """
    forced = os.environ.get("DSH_CLI")
    candidates: list[tuple[list[str], dict]] = []
    if forced:
        candidates.append(([forced], {}))
    local = Path(os.environ.get("LOCALAPPDATA")
                 or (Path(os.environ.get("USERPROFILE") or ".") / "AppData" / "Local"))
    roots = [local / "Programs" / "DeepSeek Harness",
             Path(os.environ.get("PROGRAMFILES") or "C:/Program Files") / "DeepSeek Harness"]
    for root in roots:
        exe = root / "DeepSeek Harness.exe"
        archive = root / "resources" / "app.asar"
        cli = (archive / "dsh" / "node_modules" / "@deepseek-ai"
               / "dsh-desktop-host" / "lib" / "cli.js")
        if exe.is_file() and archive.is_file():
            candidates.append(([str(exe), "--expose-internals", str(cli)],
                               {"ELECTRON_RUN_AS_NODE": "1"}))
    on_path = shutil.which("dsh")
    if on_path:
        candidates.append(([on_path], {}))
    for argv, env in candidates:
        try:
            probe = subprocess.run([*argv, "plugin", "--help"], capture_output=True,
                                   text=True, encoding="utf-8", errors="replace",
                                   env={**os.environ, **env}, timeout=90)
        except Exception:  # noqa: BLE001 — кандидат просто не подходит
            continue
        if probe.returncode == 0 and "--profile" in (probe.stdout or ""):
            return argv, env
    return None


def run_cli(cli: tuple[list[str], dict], args: list[str], timeout: int = 240) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.update(cli[1])
    return subprocess.run([*cli[0], *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", env=env, timeout=timeout)


def store_arg(profile_dir: Path) -> list[str]:
    """Каталог хранилища pnpm: чужое хранилище ломает установку.

    Замер 02.11.2026: `node_modules` профиля собран pnpm 11.7.0 с хранилищем в
    `%LOCALAPPDATA%\\pnpm\\store\\v11`, а встроенный pnpm идёт в `~/.pnpm` и
    отказывается связывать. Поэтому storeDir читается из профиля.
    """
    meta = profile_dir / "node_modules" / ".modules.yaml"
    try:
        text = meta.read_text(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        return []
    match = re.search(r'"?storeDir"?\s*:\s*"((?:[^"\\]|\\.)+)"', text)
    if not match:
        return []
    # Значение в YAML записано с двойным экранированием (`C:\\\\Users` в файле),
    # поэтому снятие экранирования повторяется до стабилизации: одна замена давала
    # в аргументе pnpm удвоенные разделители (замер 03.11.2026).
    value = match.group(1)
    for _ in range(3):
        try:
            following = json.loads('"' + value + '"')
        except Exception:  # noqa: BLE001 — тогда оставляем как есть
            break
        if following == value:
            break
        value = following
    return [f"--config.store-dir={value}"]


# ------------------------------------------------------------------- хуки и файлы


def _source_files() -> list[tuple[str, Path]]:
    """Проектные конфиги хуков: порядок как у tools/merge_hooks.py."""
    return [
        ("xerj", REPO.parent / "tools" / "xerj" / "hooks.json"),
        ("bsg", REPO.parent / "tools" / "bsg" / "hooks.json"),
        ("ragraf", REPO / "harness" / "hooks.json"),
    ]


def _stage_of(command: str) -> str | None:
    for token in command.replace('"', " ").split():
        if token in STAGES:
            return token
    return None


def build_hooks(python: str, dispatch: Path, mode: str) -> dict:
    """Собрать один конфиг хуков: чужие стадии как есть, наши — через dispatch.

    Чужие (xerj, bsg) остаются дословно: RAGRAF не владеет их поведением.
    Наши переписываются на `dispatch.py`, чтобы путь к рабочей копии и режим
    гейта были заданы в одном месте и не разъезжались с установкой.
    """
    hooks: dict[str, list] = {}
    used: list[str] = []
    for label, path in _source_files():
        if not path.is_file():
            say(f"  источник хуков отсутствует, пропущен: {label} ({path})")
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            raise SystemExit(f"источник хуков не разобран: {label} ({path}): {exc}")
        used.append(label)
        for event, groups in (data.get("hooks") or {}).items():
            for group in groups:
                entries = []
                for hook in group.get("hooks") or []:
                    command = str(hook.get("command") or "")
                    stage = _stage_of(command) if label == "ragraf" else None
                    if stage:
                        command = (f"{short_path(python)} {short_path(dispatch)} "
                                   f"hook {stage} --mode={mode}")
                    else:
                        command = normalize_command(command)
                    entries.append({"type": "command", "timeout": hook.get("timeout", 60),
                                    "command": command} if command else {"type": "command"})
                item = {"hooks": entries}
                if group.get("matcher"):
                    item["matcher"] = group["matcher"]
                hooks.setdefault(event, []).append(item)
    if "ragraf" not in used:
        # Проектного файла стадий нет (дерево публикации, установка пакетом).
        # Стадии ставятся по встроенному набору: пустой hooks.json означал бы,
        # что гейта нет вовсе, — а это можно заметить только по поведению.
        say("  проектного harness/hooks.json нет — стадии взяты встроенные")
        used.append("ragraf (встроенные стадии)")
        for event, stage, timeout in DEFAULT_STAGES:
            group: dict = {"hooks": [{"type": "command", "timeout": timeout,
                                       "command": f"{short_path(python)} "
                                                  f"{short_path(dispatch)} hook {stage} "
                                                  f"--mode={mode}"}]}
            if stage == "pre-tool":
                group["matcher"] = ("pwsh|Bash|read|Read|grep|Grep|glob|Glob|"
                                    "mcp__ragraf__.*")
            hooks.setdefault(event, []).append(group)
    readme = [
        "Конфиг хуков DSH, собранный установщиком RAGRAF (tools/install_dsh_plugin.py).",
        "Источники (правка — в них, не здесь): " + ", ".join(used or ["—"]),
        "Одна запись моста на профиль: вторая запись того же плагина не поднимается",
        "(измерено 21.10.2026 — гейт не получал вызовов неделю).",
        f"Режим гейта RAGRAF: {mode}. Стадии идут через dispatch.py, поэтому путь",
        "к рабочей копии RAGRAF здесь не записан: его находит dispatch при вызове.",
        "Команды без кавычек: PowerShell не принимает строку, начинающуюся с кавычки",
        "(ParserError, код 1 — измерено 02.11.2026).",
    ]
    return {"_readme": readme, "sources": used, "hook_mode": mode, "hooks": hooks}


def normalize_command(command: str) -> str:
    r"""Убрать кавычки в НАЧАЛЕ команды хука.

    Зачем. Мост запускает команду через PowerShell, а PowerShell не принимает
    строку, начинающуюся с кавычки: `"C:\Program Files\PowerShell\7\pwsh.exe" …`
    даёт `ParserError`, код 1 (измерено 02.11.2026 — так падали не только наши
    хуки, но и xerj/bsg). Правка не трогает источники: путь с пробелами
    сокращается до 8.3, кавычки снимаются. Кавычки В СЕРЕДИНЕ строки остаются:
    PowerShell их понимает, и ломать нечего.
    """
    text = command.strip()
    if not text.startswith('"'):
        return command
    end = text.find('"', 1)
    if end < 0:
        return command
    executable = text[1:end]
    return (short_path(executable) + text[end + 1:]).strip()


def _backup(path: Path, label: str) -> Path | None:
    if not path.is_file():
        return None
    target = path.with_name(f"{path.name}.bak-{label}")
    shutil.copy2(path, target)
    return target


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


# ------------------------------------------------------------------ проверка


CHECK_PROFILE = "ragraf-check"
CHECK_MARKER = ".ragraf-check"


def prepare_check_profile(home: Path, profile_dir: Path) -> Path:
    """Копия профиля для проверки: CLI НЕ разбирает живой desktop-профиль.

    Замер 03.11.2026: `dsh --profile desktop --dump-config` отвечает
    `profile "desktop" is managed exclusively by the Electron application`.
    Поэтому оракул проверки — копия слоёв под своим именем: тот же YAML, тот же
    `!!js`, тот же загрузчик, а живой профиль не открывается вовсе. Копия
    помечается маркером: удаляется только то, что создали мы сами.
    """
    target = home / "profiles" / CHECK_PROFILE
    if target.exists():
        if not (target / CHECK_MARKER).is_file():
            say(f"  ВНИМАНИЕ: {target} существует и создан не нами — проверка отменена")
            return target
        shutil.rmtree(target)
    target.mkdir(parents=True)
    (target / CHECK_MARKER).write_text("создано tools/install_dsh_plugin.py\n", encoding="utf-8")
    for item in profile_dir.iterdir():
        if item.name in ("node_modules", ".plugin-manager"):
            continue
        if item.is_file():
            shutil.copy2(item, target / item.name)
    node_modules = profile_dir / "node_modules"
    if node_modules.is_dir():
        link = target / "node_modules"
        try:
            if os.name == "nt":
                subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(node_modules)],
                               capture_output=True, text=True, timeout=60)
            else:
                os.symlink(node_modules, link, target_is_directory=True)
        except Exception:  # noqa: BLE001 — без node_modules дамп всё равно покажет слои
            pass
    return target


def cleanup_check_profile(home: Path) -> None:
    target = home / "profiles" / CHECK_PROFILE
    if target.is_dir() and (target / CHECK_MARKER).is_file():
        shutil.rmtree(target, ignore_errors=True)


def dump_config(cli: tuple[list[str], dict], name: str) -> tuple[bool, str]:
    result = run_cli(cli, ["--profile", name, "--dump-config"])
    text = (result.stdout or "") + (result.stderr or "")
    return result.returncode == 0, text


def budget_report(text: str) -> dict:
    """Действующий бюджет инструкций из развёрнутого дерева профиля.

    Почему не «последний maxBytes». Замер 03.11.2026 на живом клиенте 44.7.0:
    записей `agent-instructions` в дереве НЕСКОЛЬКО — верхняя (её `dsh-base`
    ставит `disabled: true`) и по одной внутри каждого пресета агента. Сеанс
    идёт с пресетом `standard` (в стенограмме `"agentPreset":"standard"`),
    поэтому действует бюджет пресета — 65 536, а не тот, что стоит в верхней
    записи. Именно поэтому правка профиля `maxBytes: 131072` от 02.11.2026
    ничего не изменила: в стенограмме сеанса 020afb5f… осталось
    `Workspace instruction budget 65536 bytes: omitted ~/.dsh/AGENTS.md`.
    """
    rows: list[dict] = []
    lines = text.splitlines()
    for index, line in enumerate(lines):
        match = re.match(r"^(\s*)- id: agent-instructions\s*$", line)
        if not match:
            continue
        indent = len(match.group(1))
        budget, disabled = None, False
        for follower in lines[index + 1:]:
            if re.match(r"^\s{0,%d}- " % indent, follower) and follower.strip().startswith("- "):
                break
            found = re.match(r"\s*maxBytes:\s*(\d+)\s*$", follower)
            if found:
                budget = int(found.group(1))
            if re.match(r"\s*disabled:\s*true\s*$", follower):
                disabled = True
        rows.append({"indent": indent, "maxBytes": budget, "disabled": disabled})
    top = [r for r in rows if r["indent"] == 0 and not r["disabled"] and r["maxBytes"]]
    nested = [r for r in rows if r["indent"] > 0 and r["maxBytes"]]
    if top:
        return {"maxBytes": top[-1]["maxBytes"], "source": "запись профиля",
                "rows": rows}
    if nested:
        return {"maxBytes": min(r["maxBytes"] for r in nested),
                "source": "пресет агента (%d шт.)" % len(nested), "rows": rows}
    return {"maxBytes": None, "source": "не найдено", "rows": rows}


def effective_maxbytes(text: str) -> int | None:
    return budget_report(text)["maxBytes"]


def ensure_budget(cli: tuple[list[str], dict], profile: str, home: Path, label: str) -> str:
    """Поднять бюджет инструкций, если он меньше фактического AGENTS.md.

    Зачем трогать слой профиля, хотя пакет уже ставит 196 608. Слой профиля
    применяется ПОСЛЕДНИМ и перебивает пакет: если там осталось 65 536 (значение
    базового слоя), глобальный файл будет отброшен целиком, и правило «сначала
    RAGRAF» снова не доедет до модели. Замер 03.11.2026: 74 442 байта против
    бюджета 65 536 — именно так и случилось.
    """
    agents = home / "AGENTS.md"
    size = agents.stat().st_size if agents.is_file() else 0
    prepare_check_profile(home, home / "profiles" / profile)
    ok, text = dump_config(cli, CHECK_PROFILE)
    report = budget_report(text) if ok else {"maxBytes": None, "source": "не найдено"}
    budget = report["maxBytes"]
    if size == 0 or (budget is not None and budget >= size):
        return "бюджет инструкций в порядке (%d из %s)" % (size, budget)
    if report["source"].startswith("пресет"):
        # Пресеты править нельзя без последствий: переопределение заменяет весь
        # список дочерних плагинов, и новые инструменты от обновления клиента в
        # него уже не попадут (так сказано в README dsh-agent-preset). Поэтому
        # здесь только измерение, а замечание печатает verify.
        return "бюджет инструкций определяет пресет агента — см. замечание ниже"
    patch = home / "profiles" / profile / "cordis.patch.yml"
    target = max(196608, size + 8192)
    _backup(patch, label)
    lines = patch.read_text(encoding="utf-8").splitlines() if patch.is_file() else []
    changed = False
    for index, line in enumerate(lines):
        if re.match(r"\s*maxBytes:\s*\d+\s*$", line):
            lines[index] = f"    maxBytes: {target}"
            changed = True
    if not changed:
        lines += ["", "# Бюджет инструкций: глобальный AGENTS.md обязан доезжать до модели.",
                  "- id: agent-instructions",
                  "  name: '@deepseek-ai/dsh-agent-instructions'",
                  "  config:",
                  f"    maxBytes: {target}"]
    write_text(patch, "\n".join(lines).rstrip("\n") + "\n")
    return f"бюджет инструкций поднят до {target} (было {budget}, файл {size} байт)"


def verify(cli: tuple[list[str], dict], profile: str, home: Path,
           mode: str) -> tuple[bool, list[str], list[str]]:
    """Оракул проверки — сам клиент: развёрнутое дерево копии профиля.

    Возвращает (связка верна, ошибки, замечания). Бюджет инструкций — замечание,
    а не ошибка установки: пакет не может изменить бюджет пресета (а
    переопределять его — заморозить состав), и проваливать из-за этого уже
    установленную связку было бы неверно. В `check` замечание считается
    провалом — там это гейт здоровья, а не отчёт об установке.
    """
    problems: list[str] = []
    warnings: list[str] = []
    prepare_check_profile(home, home / "profiles" / profile)
    ok, text = dump_config(cli, CHECK_PROFILE)
    if not ok:
        first = (text.strip().splitlines() or ["—"])[0]
        problems.append("`--dump-config` не разобрал профиль: " + first)
        return False, problems, warnings
    dispatch = home / "ragraf" / "dispatch.py"
    hooks = home / "ragraf" / "hooks.json"
    for name, path in (("dispatch.py", dispatch), ("hooks.json", hooks)):
        if not path.is_file():
            problems.append(f"нет файла {path}")
    if dispatch.is_file() and "ragraf" not in text:
        problems.append("в дереве профиля нет записи, указывающей на dispatch.py")
    if hooks.is_file() and "hooks.json" not in text:
        problems.append("в дереве профиля нет записи, указывающей на hooks.json")
    if "hooks-ragraf" not in text:
        problems.append("запись моста хуков hooks-ragraf не попала в дерево профиля")
    if "mcp-ragraf" not in text:
        problems.append("запись MCP-сервера mcp-ragraf не попала в дерево профиля")
    budget = budget_report(text)
    agents = home / "AGENTS.md"
    size = agents.stat().st_size if agents.is_file() else 0
    if budget["maxBytes"] is None:
        problems.append("в дереве профиля нет ни одной записи maxBytes у agent-instructions")
    elif size and budget["maxBytes"] < size:
        warnings.append("AGENTS.md %d байт НЕ влезает в бюджет %d (%s): глобальные "
                        "правила будут отброшены целиком"
                        % (size, budget["maxBytes"], budget["source"]))
    if hooks.is_file() and f"mode={mode}" not in hooks.read_text(encoding="utf-8"):
        problems.append(f"в hooks.json нет стадий с режимом {mode}")
    return not problems, problems, warnings


# --------------------------------------------------------------- правка профиля


def strip_legacy(patch: Path, label: str) -> list[str]:
    """Убрать из слоя профиля записи, которые теперь даёт пакет.

    Правка узкая: снимается только блок, в котором назван один из LEGACY_IDS.
    Разделение на блоки учитывает комментарии: строки `#` в начале столбца
    принадлежат СЛЕДУЮЩЕЙ записи, а не предыдущей. Это не педантизм — замер
    03.11.2026: первая редакция относила хвостовые комментарии к удаляемому блоку
    и вместе с `mcp-ragraf` снесла пояснение к чужой записи `mcp-xerj` (сама
    запись уцелела, а описание её решений — нет).
    """
    if not patch.is_file():
        return []
    lines = patch.read_text(encoding="utf-8").splitlines()
    blocks: list[list[str]] = []
    pending: list[str] = []
    current: list[str] = []
    for line in lines:
        if line.startswith("#"):
            # Комментарий перед записью: копим и приклеиваем к следующему блоку.
            if current:
                blocks.append(current)
                current = []
            pending.append(line)
            continue
        if line.startswith("- ") or line.startswith("-\t"):
            if current:
                blocks.append(current)
            current = [*pending, line]
            pending = []
            continue
        current.append(line)
    if pending:
        current = [*current, *pending]
    if current:
        blocks.append(current)
    kept: list[list[str]] = []
    removed: list[str] = []
    for block in blocks:
        text = "\n".join(block)
        hit = next((i for i in LEGACY_IDS if re.search(rf"^\s*-?\s*id:\s*{re.escape(i)}\s*$",
                                                       text, re.M)), None)
        if hit:
            removed.append(hit)
        else:
            kept.append(block)
    if not removed:
        return []
    _backup(patch, label)
    write_text(patch, "\n".join("\n".join(b) for b in kept).rstrip("\n") + "\n")
    return removed


# ------------------------------------------------------------------- команды


def cmd_install(args: argparse.Namespace) -> int:
    label = time.strftime("%Y%m%d-%H%M%S") + "-dsh"
    home = dsh_home()
    profile_dir = home / "profiles" / args.profile
    say(f"RAGRAF: {REPO}")
    say(f"DSH_HOME: {home}")
    say(f"профиль: {args.profile} ({'есть' if profile_dir.is_dir() else 'НЕТ'})")
    if not BUNDLE_SRC.is_dir():
        say(f"ОШИБКА: нет каталога пакета {BUNDLE_SRC}")
        return 2
    cli = find_cli()
    if not cli:
        say("ОШИБКА: не найден CLI DeepSeek Harness (PATH `dsh` либо сборка приложения).")
        say("        Укажите DSH_CLI=<путь к dsh> и повторите.")
        return 2
    python = pick_python()
    say(f"интерфейс python: {python}")
    state = home / "ragraf"
    dispatch = state / "dispatch.py"
    hooks = state / "hooks.json"
    if args.dry_run:
        say("[dry-run] файлы не пишутся, профиль не меняется")
    else:
        state.mkdir(parents=True, exist_ok=True)
        for target in (dispatch, hooks):
            if target.is_file():
                _backup(target, label)
        shutil.copy2(BUNDLE_SRC / "scripts" / "dispatch.py", dispatch)
        data = build_hooks(python, dispatch, args.mode)
        write_text(hooks, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
        say(f"  записано: {dispatch}")
        say(f"  записано: {hooks} (источники: {', '.join(data['sources'])}, режим {args.mode})")

    bundle = state / "bundle"
    if not args.dry_run:
        if bundle.exists():
            shutil.rmtree(bundle)
        shutil.copytree(BUNDLE_SRC, bundle,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".git"))
        say(f"  пакет скопирован: {bundle}")

    version = json.loads((BUNDLE_SRC / "package.json").read_text(encoding="utf-8"))
    record = {"profile": args.profile, "ragraf_home": str(REPO), "python": python,
              "mode": args.mode, "installed_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
              "bundle": str(bundle), "bundle_version": version.get("version"),
              "plugin_version": version.get("version"),
              "source": str(BUNDLE_SRC)}
    if not args.dry_run:
        write_text(state / "install.json", json.dumps(record, ensure_ascii=False, indent=2) + "\n")

    add_args = ["plugin", "--profile", args.profile, "add", str(bundle) if not args.dry_run else str(BUNDLE_SRC)]
    add_args += store_arg(profile_dir)
    say("установка пакета: " + " ".join(add_args))
    if args.dry_run:
        say("[dry-run] установка не выполняется")
        return 0
    result = run_cli(cli, add_args, timeout=600)
    output = ((result.stdout or "") + (result.stderr or "")).strip()
    if result.returncode != 0:
        say("ОШИБКА установки пакета (код %d):" % result.returncode)
        say(output[-4000:])
        say("Профиль не изменён дальше; откат: dsh plugin --profile %s remove ragraf-dsh"
            % args.profile)
        return 2
    say("  пакет установлен")

    removed = strip_legacy(profile_dir / "cordis.patch.yml", label)
    if removed:
        say("  из слоя профиля сняты прежние записи: " + ", ".join(removed)
            + f" (копия рядом: cordis.patch.yml.bak-{label})")
    say("  " + ensure_budget(cli, args.profile, home, label))

    ok, problems, warnings = verify(cli, args.profile, home, args.mode)
    if not ok:
        say("ПРОВЕРКА НЕ ПРОШЛА:")
        for problem in problems:
            say("  - " + problem)
        backup = profile_dir / f"cordis.patch.yml.bak-{label}"
        if removed and backup.is_file():
            shutil.copy2(backup, profile_dir / "cordis.patch.yml")
            say(f"  слой профиля возвращён из копии {backup.name}")
        return 1
    say("ПРОВЕРКА ПРОШЛА: записи развернулись, файлы на месте, режим гейта " + args.mode)
    # Копия слоёв для проверки больше не нужна; при провале она остаётся как
    # материал для разбора (и удаляется только по маркеру).
    cleanup_check_profile(home)
    if warnings:
        say("")
        say("УСТАНОВЛЕНО, С ЗАМЕЧАНИЕМ (на связку не влияет, требует решения владельца):")
        for warning in warnings:
            say("  ! " + warning)
    say("Дальше нужен ПЕРЕЗАПУСК DeepSeek Harness: конфиг хуков читается один раз при старте.")
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    home = dsh_home()
    cli = find_cli()
    say(f"DSH_HOME: {home}")
    if not cli:
        say("ОШИБКА: не найден CLI DeepSeek Harness")
        return 2
    dispatch = home / "ragraf" / "dispatch.py"
    if dispatch.is_file():
        result = subprocess.run([pick_python(), str(dispatch), "doctor", "--profile", args.profile],
                                capture_output=True, text=True, encoding="utf-8", errors="replace")
        say(result.stdout.strip() or result.stderr.strip())
        doctor_ok = result.returncode == 0
    else:
        say("ОШИБКА: нет dispatch.py в $DSH_HOME/ragraf — установка не выполнялась")
        doctor_ok = False
    ok, problems, warnings = verify(cli, args.profile, home, args.mode)
    say("дерево профиля: " + ("совпадает" if ok else "РАСХОДИТСЯ"))
    for problem in problems:
        say("  - " + problem)
    for warning in warnings:
        say("  ! " + warning)
    if ok:
        cleanup_check_profile(home)
    return 0 if (ok and doctor_ok and not warnings) else 1


def cmd_remove(args: argparse.Namespace) -> int:
    cli = find_cli()
    if not cli:
        say("ОШИБКА: не найден CLI DeepSeek Harness")
        return 2
    result = run_cli(cli, ["plugin", "--profile", args.profile, "remove", "ragraf-dsh"], timeout=600)
    say(((result.stdout or "") + (result.stderr or "")).strip()[-2000:])
    say("Файлы $DSH_HOME/ragraf оставлены: они не мешают и нужны для повторной установки.")
    return 0 if result.returncode == 0 else 2


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="RAGRAF как плагин DeepSeek Harness")
    sub = ap.add_subparsers(dest="command", required=True)
    for name, help_text in (("install", "поставить пакет и проверить"),
                            ("check", "только проверить установку"),
                            ("remove", "снять пакет из профиля")):
        parser = sub.add_parser(name, help=help_text)
        parser.add_argument("--profile", default=DEFAULT_PROFILE)
        parser.add_argument("--mode", default="deny", choices=["deny", "ask", "warn", "off"])
        if name == "install":
            parser.add_argument("--dry-run", action="store_true",
                                help="ничего не писать и не менять, показать шаги")
    args = ap.parse_args(argv[1:])
    if args.command == "install":
        return cmd_install(args)
    if args.command == "check":
        return cmd_check(args)
    return cmd_remove(args)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
