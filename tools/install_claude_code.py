"""Установка интеграции RAGRAF в Claude Code: MCP, хуки, память, навыки, проверка.

Зачем установщик, а не инструкция «впишите вот это». У Claude Code интеграция
состоит из четырёх независимых мест, и каждое можно испортить молча:

  1. **MCP-сервер** — `~/.claude.json`, ключ `mcpServers` (область `user`).
     Запись делается командой `claude mcp add-json --scope user`, а не правкой
     файла: файл принадлежит продукту и содержит ещё состояние проектов.
  2. **Хуки** — `~/.claude/settings.json`, ключ `hooks` (события
     `SessionStart`, `UserPromptSubmit`, `PreToolUse`, `Stop`).
  3. **Память** — `~/.claude/CLAUDE.md`, раздел с правилами работы с индексом.
  4. **Навык** — `~/.claude/skills/ragraf-core/SKILL.md` (и `ragraf-secrets`).

Почему все четыре, а не только MCP. Замер по документам самого продукта и по
собранному хуку: сервер без правил — «инструмент есть, пользоваться не
обязательно»; правила без хука — «написано, но не проверено»; хук без метки
задачи — «обращения есть, а к какой задаче относятся, неизвестно». Числа и
поведение, а не намерения.

Что делает команда

    python tools/install_claude_code.py                 # установить и проверить
    python tools/install_claude_code.py --check         # только состояние
    python tools/install_claude_code.py --dry-run       # показать, что будет
    python tools/install_claude_code.py --remove        # убрать (с копиями)

Песочница: `--home <каталог>` переводит все пути (settings.json, CLAUDE.md,
навыки, состояние) в указанный каталог и НЕ зовёт `claude` — так установка
проверяется, не трогая рабочую учётную запись. Живой запуск без `--home`
пишет резервные копии рядом с каждым изменяемым файлом.

Границы, названные прямо:

  * **Смоук модели не делается.** `claude -p` без входа отвечает
    `Not logged in`; проверяется то, что от модели не зависит: сервер
    поднимается и отвечает на MCP-рукопожатие (`claude mcp get` → Connected),
    хук отвечает на пробный payload, настройки разбираются.
  * **`PreToolUse` не переписывает вызов.** Протокол разбирает `updatedInput`,
    но не применяет его, поэтому подсказка только предлагается текстом.
  * **Права выдаёт владелец.** Установщик добавляет в `permissions.allow`
    только `mcp__ragraf__*` — вызовы инструментов RAGRAF не спрашивают
    подтверждения. Права на правку файлов и запуск команд он не трогает.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
MCP_LAUNCHER = REPO / "tools" / "ragraf-mcp.py"
HOOK_SCRIPT = REPO / "tools" / "harness_gate.py"
SKILLS_SOURCE = REPO / "docs" / "skills"
SKILL_NAMES = ("ragraf-core", "ragraf-secrets")

#: Плагин Claude Code: каталог плагина и манифест маркетплейса в корне репозитория.
#: Оба лежат в дереве проекта, потому что репозиторий сам является маркетплейсом:
#: `claude plugin marketplace add <репозиторий>` читает `.claude-plugin/marketplace.json`.
PLUGIN_DIR = REPO / "plugin"
MARKETPLACE_FILE = REPO / ".claude-plugin" / "marketplace.json"
PLUGIN_SKILLS = PLUGIN_DIR / "skills"
#: Файлы навыка, которые идут в плагин. `VARIANTS.json` — объявление, а не текст
#: навыка: в поставке клиента он не нужен и только путает читателя.
PLUGIN_SKILL_FILES = ("reference/operations.md", "reference/repair.md")

#: Лицензионные файлы в папке плагина: каталог смотрит на лицензию ВНУТРИ плагина,
#: а не по ссылке в описании. Копии делает сборка, поэтому исходник — один.
PLUGIN_LEGAL_FILES = {
    "LICENSE": REPO / "LICENSE",
    "NOTICE": REPO / "NOTICE",
    "EULA.md": REPO / "community" / "EULA.md",
}

#: Начало и конец блока правил в `CLAUDE.md`: по ним установка и удаление
#: идемпотентны, а чужой текст вокруг блока не затрагивается.
MARK_BEGIN = "<!-- == RAGRAF: правила работы с индексом (добавлено установщиком) -->"
MARK_END = "<!-- == конец блока RAGRAF -->"

#: Имя сервера в конфигурации клиента. От него зависят имена инструментов:
#: `mcp__ragraf__ragraf_search` и т.д. — это же имя использует навык.
SERVER_NAME = "ragraf"

#: События хуков: имя события протокола, стадия скрипта, время ожидания, matcher.
#:
#: Matcher стоит только у `PreToolUse`: у остальных событий он не применяется
#: (документация продукта, раздел «Structure»). Набор в matcher повторяет тот,
#: что уже проверен в HARNESS: файловый поиск и чтение — то, что обязан
#: замениться обращением к индексу; `mcp__ragraf__.*` — чтобы вызовы индекса
#: тоже доходили до гейта (он считает их «индексом пользовались»).
STAGES: tuple[tuple[str, str, int, str], ...] = (
    ("SessionStart", "session-start", 30, ""),
    ("UserPromptSubmit", "prompt", 20, ""),
    ("PreToolUse", "pre-tool", 60, "pwsh|Bash|read|Read|grep|Grep|glob|Glob|mcp__ragraf__.*"),
    ("Stop", "stop", 30, ""),
)

#: Право, которое добавляется в `permissions.allow`. Одно: вызовы инструментов
#: RAGRAF. Права на правку и запуск команд остаются у владельца.
ALLOW_RULES = ("mcp__ragraf__*",)

STAMP = time.strftime("%Y%m%d-%H%M%S")


# ---------------------------------------------------------------------------
# Пути и значения
# ---------------------------------------------------------------------------


def user_home() -> Path:
    """Домашний каталог учётной записи: API, а не переменная окружения."""
    return Path(os.path.expanduser("~"))


def claude_home(override: str = "") -> Path:
    """Каталог настроек Claude Code: `CLAUDE_CONFIG_DIR`, `--home` или `~/.claude`."""
    if override:
        return Path(override).expanduser().resolve()
    env = os.environ.get("CLAUDE_CONFIG_DIR")
    if env:
        return Path(env).expanduser()
    return user_home() / ".claude"


def user_state_path(override: str = "") -> Path:
    """Файл состояния пользователя: `~/.claude.json` (там же живут MCP-серверы).

    ПОЧЕМУ НЕ `settings.json`. MCP-серверы области `user` продукт хранит в
    `~/.claude.json` (`claude mcp add --scope user` — замер 02.11.2026),
    а настройки хуков — в `~/.claude/settings.json`. Это два разных файла.
    """
    if override:
        return Path(override).expanduser().resolve()
    return user_home() / ".claude.json"


def interpreter() -> str:
    """Интерпретатор для сервера и хука: из `.venv` проекта, иначе текущий.

    Почему `.venv`. Клиент запускает дочерний процесс с урезанным окружением
    (измерено ранее на HARNESS: `fastembed`, `PyYAML`, `sqlite_vec`, `tree_sitter`
    достижимы только через `PYTHONUSERBASE`). Интерпретатор из `.venv` находит
    зависимости сам, без переменных окружения.
    """
    for candidate in (REPO / ".venv" / "Scripts" / "python.exe",
                      REPO / ".venv" / "bin" / "python"):
        if candidate.exists():
            return str(candidate)
    return sys.executable


def cmd_path(path: Path | str) -> str:
    """Путь в форме, пригодной для команды хука (без кавычек).

    Протокол хуков передаёт команду оболочке, а PowerShell не принимает
    строку, начинающуюся с кавычки (`ParserError`, код 1 — измерено
    02.11.2026 на HARNESS). Поэтому путь с пробелом сокращается до 8.3;
    если сократить не удалось — возвращается как есть, и это видно в проверке
    запуском, а не в догадке.
    """
    text = str(path)
    if " " not in text:
        return text
    if os.name == "nt":
        try:
            import ctypes

            buf = ctypes.create_unicode_buffer(1024)
            if ctypes.windll.kernel32.GetShortPathNameW(text, buf, 1024):
                return buf.value
        except Exception:  # noqa: BLE001 — сокращение не удалось, путь остаётся длинным
            return text
    return text


def hook_command(stage: str) -> str:
    return f"{cmd_path(interpreter())} {cmd_path(HOOK_SCRIPT)} {stage}"


def mcp_entry() -> dict[str, Any]:
    """Запись MCP-сервера для `.claude.json` (область `user`).

    `--agent claude-code` в аргументах: имя агента попадает в статистику
    RAGRAF (иначе вызовы смешались бы с `unknown`), `--agent-kind external` —
    семейство, чтобы разбивка «свои/сторонние» не считала клиента своим.
    """
    return {
        "type": "stdio",
        "command": interpreter(),
        "args": [str(MCP_LAUNCHER), "--agent", "claude-code",
                 "--agent-kind", "external"],
        "env": {"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"},
    }


def hooks_block() -> dict[str, Any]:
    """Блок `hooks` для `settings.json`: по одной записи на событие протокола."""
    hooks: dict[str, Any] = {}
    for event, stage, timeout, matcher in STAGES:
        entry: dict[str, Any] = {
            "hooks": [{"type": "command", "command": hook_command(stage),
                       "timeout": timeout}]
        }
        if matcher:
            entry["matcher"] = matcher
        hooks[event] = [entry]
    return hooks


# ---------------------------------------------------------------------------
# Чтение и запись с резервной копией
# ---------------------------------------------------------------------------


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8-sig")
    if not text.strip():
        return {}
    data = json.loads(text)
    return data if isinstance(data, dict) else {}


def backup(path: Path) -> Path | None:
    """Копия файла перед изменением: возвращает путь копии либо `None`."""
    if not path.exists():
        return None
    copy = path.with_name(f"{path.name}.bak-{STAMP}")
    shutil.copy2(path, copy)
    return copy


def write_text(path: Path, text: str) -> None:
    """Записать UTF-8 без BOM и с LF: файлы конфигурации читает не только этот продукт."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = text.replace("\r\n", "\n")
    path.write_bytes(data.encode("utf-8"))


def write_json(path: Path, data: dict[str, Any]) -> None:
    write_text(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def our_hooks(value: Any) -> bool:
    """Наша ли это запись хука: узнаётся по файлу скрипта в команде."""
    if not isinstance(value, dict):
        return False
    needle = str(HOOK_SCRIPT).replace("\\", "/").lower()
    for hook in value.get("hooks") or []:
        command = str((hook or {}).get("command") or "").replace("\\", "/").lower()
        if needle in command or "harness_gate.py" in command:
            return True
    return False


def merge_hooks(settings: dict[str, Any]) -> tuple[int, int]:
    """Идемпотентно вписать наши хуки. Возвращает (сколько добавлено, сколько снято).

    Свои записи снимаются и добавляются заново: так повторный запуск не плодит
    копии, а правка времени ожидания или matcher применяется. Чужие записи в тех
    же событиях не трогаются — у владельца могут быть свои хуки.
    """
    current = settings.get("hooks")
    hooks = dict(current) if isinstance(current, dict) else {}
    removed = 0
    for event, entries in list(hooks.items()):
        kept = []
        for entry in entries if isinstance(entries, list) else []:
            if our_hooks(entry):
                removed += 1
            else:
                kept.append(entry)
        if kept:
            hooks[event] = kept
        else:
            hooks.pop(event, None)
    added = 0
    for event, entries in hooks_block().items():
        existing = hooks.get(event)
        hooks[event] = (list(existing) if isinstance(existing, list) else []) + entries
        added += len(entries)
    # Порядок ключей фиксирован порядком событий протокола, а не алфавитом:
    # человек читает файл в том же порядке, в каком идут события.
    ordered = {event: hooks[event] for event, _, _, _ in STAGES if event in hooks}
    for event, entries in hooks.items():
        ordered.setdefault(event, entries)
    settings["hooks"] = ordered
    return added, removed


def merge_permissions(settings: dict[str, Any]) -> list[str]:
    """Добавить `mcp__ragraf__*` в `permissions.allow`; вернуть добавленные правила."""
    perms = settings.get("permissions")
    if not isinstance(perms, dict):
        perms = {}
    allow = perms.get("allow")
    allow = list(allow) if isinstance(allow, list) else []
    added = [rule for rule in ALLOW_RULES if rule not in allow]
    if added:
        perms["allow"] = allow + added
        settings["permissions"] = perms
    return added


def write_json_if_changed(path: Path, data: dict[str, Any]) -> tuple[bool, str]:
    """Записать JSON, только если содержимое отличается.

    Зачем проверка: повторный запуск установщика не должен плодить резервные
    копии и менять время правки файла, принадлежащего продукту. Замер
    02.11.2026: второй прогон подряд создавал `settings.json.bak-…` при
    побайтово том же содержимом.
    """
    text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    if path.exists() and path.read_text(encoding="utf-8-sig") == text:
        return False, ""
    copy = backup(path)
    write_text(path, text)
    return True, str(copy or "")


# ---------------------------------------------------------------------------
# Четыре части установки
# ---------------------------------------------------------------------------


def read_section() -> str:
    """Текст правил для `CLAUDE.md`: берётся из `integrations/claude-code/`."""
    source = REPO / "integrations" / "claude-code" / "CLAUDE-section.md"
    body = source.read_text(encoding="utf-8-sig").strip()
    return f"{MARK_BEGIN}\n{body}\n{MARK_END}\n"


def has_section(text: str) -> bool:
    return MARK_BEGIN in text and MARK_END in text


def strip_section(text: str) -> tuple[str, bool]:
    if not has_section(text):
        return text, False
    start = text.index(MARK_BEGIN)
    end = text.index(MARK_END) + len(MARK_END)
    rest = (text[:start].rstrip("\n") + "\n\n" + text[end:].lstrip("\n")).strip("\n")
    return (rest + "\n") if rest else "", True


def install_settings(home: Path, *, dry_run: bool, remove: bool = False) -> dict[str, Any]:
    """Хуки и права в `settings.json` (область `user`)."""
    target = home / "settings.json"
    settings = load_json(target)
    report: dict[str, Any] = {"path": str(target), "backup": None,
                              "added": 0, "removed": 0, "allow_added": []}
    if remove:
        hooks = settings.get("hooks")
        if isinstance(hooks, dict):
            removed = 0
            for event, entries in list(hooks.items()):
                kept = [e for e in (entries or []) if not our_hooks(e)]
                removed += len(entries or []) - len(kept)
                if kept:
                    hooks[event] = kept
                else:
                    hooks.pop(event, None)
            if hooks:
                settings["hooks"] = hooks
            else:
                settings.pop("hooks", None)
            report["removed"] = removed
        perms = settings.get("permissions")
        if isinstance(perms, dict) and isinstance(perms.get("allow"), list):
            allow = [r for r in perms["allow"] if r not in ALLOW_RULES]
            if allow:
                perms["allow"] = allow
            else:
                perms.pop("allow", None)
            if not perms:
                settings.pop("permissions", None)
    else:
        added, removed = merge_hooks(settings)
        report["added"], report["removed"] = added, removed
        report["allow_added"] = merge_permissions(settings)
    if dry_run:
        report["dry_run"] = True
        return report
    if settings:
        changed, copy = write_json_if_changed(target, settings)
        report["changed"], report["backup"] = changed, copy
    elif target.exists():
        report["backup"] = str(backup(target) or "")
        target.unlink()
        report["changed"] = True
    return report


def claude_cli() -> str:
    """Путь к `claude` в PATH либо пустая строка."""
    found = shutil.which("claude") or shutil.which("claude.cmd")
    return found or ""


def cli_add_server(cli: str, entry: dict[str, Any]) -> dict[str, Any]:
    """`claude mcp add-json` с заменой уже существующей записи.

    Почему с заменой. Команда продукта отказывает кодом 1 на уже
    существующем имени (`MCP server ragraf already exists in user config` —
    измерено 02.11.2026), а установщик обязан быть идемпотентным: повторный
    запуск не должен ни падать, ни оставлять старое значение. Поэтому при
    таком ответе запись снимается и добавляется заново — это и есть
    «обновить до текущих значений».
    """
    payload = json.dumps(entry, ensure_ascii=False)
    attempt = subprocess.run([cli, "mcp", "add-json", "--scope", "user",
                              SERVER_NAME, payload], capture_output=True, text=True,
                             timeout=120, encoding="utf-8", errors="replace")
    answer = (attempt.stdout or "") + (attempt.stderr or "")
    result = {"returncode": attempt.returncode, "stdout": answer.strip()[:400],
              "replaced": False}
    if attempt.returncode == 0:
        return result
    if "already exists" not in answer.lower():
        return result
    subprocess.run([cli, "mcp", "remove", SERVER_NAME, "-s", "user"],
                   capture_output=True, text=True, timeout=120,
                   encoding="utf-8", errors="replace")
    retry = subprocess.run([cli, "mcp", "add-json", "--scope", "user",
                            SERVER_NAME, payload], capture_output=True, text=True,
                           timeout=120, encoding="utf-8", errors="replace")
    result.update({"returncode": retry.returncode, "replaced": retry.returncode == 0,
                   "stdout": ((retry.stdout or "") + (retry.stderr or "")).strip()[:400]})
    return result


def claude_version() -> str:
    cli = claude_cli()
    if not cli:
        return ""
    try:
        out = subprocess.run([cli, "--version"], capture_output=True, text=True,
                             timeout=60)
    except Exception:  # noqa: BLE001 — версия не обязательна для установки
        return ""
    return (out.stdout or "").strip().splitlines()[0] if out.stdout else ""


def install_mcp(state_file: Path, *, dry_run: bool, use_cli: bool,
                remove: bool = False) -> dict[str, Any]:
    """Запись MCP-сервера в состояние пользователя.

    Основной путь — команда продукта (`claude mcp add-json --scope user`): она
    знает свой формат и сама проверяет результат. Прямая запись в
    `~/.claude.json` — запасной путь для песочницы и для машины без CLI.
    """
    entry = mcp_entry()
    report: dict[str, Any] = {"path": str(state_file), "entry": entry,
                              "via": "", "backup": None}
    if use_cli and remove:
        cli = claude_cli()
        if cli:
            res = subprocess.run([cli, "mcp", "remove", SERVER_NAME, "-s", "user"],
                                 capture_output=True, text=True, timeout=120)
            report["via"] = "cli"
            report["returncode"] = res.returncode
            report["stdout"] = (res.stdout or "").strip()[-400:]
            return report
    if use_cli and not remove:
        cli = claude_cli()
        if cli:
            if dry_run:
                report["via"] = "cli (dry-run)"
                report["command"] = [cli, "mcp", "add-json", "--scope", "user",
                                     SERVER_NAME, json.dumps(entry, ensure_ascii=False)]
                return report
            res = cli_add_server(cli, entry)
            report["via"] = "cli"
            report["returncode"] = res["returncode"]
            report["stdout"] = (("перезаписан: " if res.get("replaced") else "")
                               + str(res.get("stdout") or ""))[-400:]
            return report
    data = load_json(state_file)
    servers = data.get("mcpServers")
    servers = dict(servers) if isinstance(servers, dict) else {}
    if remove:
        if servers.pop(SERVER_NAME, None) is None:
            report["via"] = "file"
            report["stdout"] = "записи не было"
            return report
    else:
        servers[SERVER_NAME] = entry
    data["mcpServers"] = servers
    report["via"] = "file"
    if dry_run:
        report["dry_run"] = True
        return report
    report["backup"] = str(backup(state_file) or "")
    write_json(state_file, data)
    return report


def install_memory(home: Path, *, dry_run: bool, remove: bool = False) -> dict[str, Any]:
    target = home / "CLAUDE.md"
    text = target.read_text(encoding="utf-8-sig") if target.exists() else ""
    report: dict[str, Any] = {"path": str(target), "backup": None,
                              "action": ""}
    if remove:
        new, removed = strip_section(text)
        report["action"] = "снят" if removed else "блока не было"
        if removed and not dry_run:
            report["backup"] = str(backup(target) or "")
            if new.strip():
                write_text(target, new)
            else:
                target.unlink()
        return report
    if has_section(text):
        report["action"] = "уже есть"
        return report
    body = read_section()
    new = (text.rstrip("\n") + "\n\n" + body) if text.strip() else body
    report["action"] = "добавлен"
    if not dry_run:
        report["backup"] = str(backup(target) or "")
        write_text(target, new)
    return report


def install_skills(home: Path, *, dry_run: bool, remove: bool = False) -> dict[str, Any]:
    """Навыки в `~/.claude/skills/<имя>/` — копия проектных файлов навыка."""
    report: dict[str, Any] = {"root": str(home / "skills"), "skills": {}}
    for name in SKILL_NAMES:
        src = SKILLS_SOURCE / name
        dst = home / "skills" / name
        item: dict[str, Any] = {"source": str(src), "target": str(dst)}
        if not src.exists():
            item["action"] = "нет источника"
            report["skills"][name] = item
            continue
        if remove:
            existed = dst.exists()
            if existed and not dry_run:
                shutil.rmtree(dst)
            item["action"] = "убран" if existed else "не было"
            report["skills"][name] = item
            continue
        same = dst.exists() and (dst / "SKILL.md").exists() and (
            (dst / "SKILL.md").read_bytes() == (src / "SKILL.md").read_bytes())
        item["action"] = "совпадает" if same else ("обновлён" if dst.exists() else "установлен")
        if not same and not dry_run:
            dst.mkdir(parents=True, exist_ok=True)
            shutil.copytree(src, dst, dirs_exist_ok=True)
        report["skills"][name] = item
    if remove and not dry_run:
        # Пустой корень навыков после удаления убирается: иначе остаётся
        # каталог, который выглядит как установленный навык без содержимого.
        root = home / "skills"
        try:
            if root.exists() and not any(root.iterdir()):
                root.rmdir()
        except OSError:
            pass
    return report


# ---------------------------------------------------------------------------
# Проверка
# ---------------------------------------------------------------------------


def run_hook(stage: str, payload: dict[str, Any], timeout: int = 90) -> dict[str, Any]:
    """Прогнать хук так, как его запускает клиент: команда целиком, JSON в stdin."""
    command = hook_command(stage)
    started = time.perf_counter()
    try:
        res = subprocess.run(command, input=json.dumps(payload), capture_output=True,
                             text=True, timeout=timeout, shell=True,
                             encoding="utf-8", errors="replace")
    except Exception as exc:  # noqa: BLE001 — проверка не имеет права падать
        return {"stage": stage, "ok": False, "why": f"{type(exc).__name__}: {exc}"}
    seconds = round(time.perf_counter() - started, 2)
    parsed: dict[str, Any] = {}
    text = (res.stdout or "").strip()
    if text:
        try:
            data = json.loads(text)
            parsed = data if isinstance(data, dict) else {}
        except Exception:  # noqa: BLE001 — не JSON тоже результат проверки
            parsed = {}
    return {"stage": stage, "command": command, "returncode": res.returncode,
            "seconds": seconds, "stdout": text[:400],
            "stderr": (res.stderr or "").strip()[:400], "json": parsed,
            "ok": res.returncode == 0}


def check_hooks(cwd: str) -> list[dict[str, Any]]:
    """Проверки хуков на пробных payload: форма ответа, а не только код возврата."""
    checks: list[dict[str, Any]] = []
    session = run_hook("session-start", {
        "session_id": "ragraf-preflight", "transcript_path": "", "cwd": cwd,
        "hook_event_name": "SessionStart", "source": "startup"})
    out = (session.get("json") or {}).get("hookSpecificOutput") or {}
    checks.append({
        "name": "хук SessionStart отвечает hookEventName=SessionStart",
        "ok": bool(session.get("ok") and out.get("hookEventName") == "SessionStart"
                   and out.get("additionalContext")),
        "detail": (out.get("additionalContext") or session.get("stderr") or "")[:200],
    })
    pre = run_hook("pre-tool", {
        "session_id": "ragraf-preflight", "cwd": cwd, "hook_event_name": "PreToolUse",
        "tool_name": "Grep", "tool_input": {"pattern": "facade"}})
    pre_out = (pre.get("json") or {}).get("hookSpecificOutput") or {}
    checks.append({
        "name": "хук PreToolUse отдаёт решение по протоколу",
        "ok": bool(pre.get("ok") and pre_out.get("hookEventName") == "PreToolUse"),
        "detail": str(pre_out.get("permissionDecision") or pre.get("stderr") or "")[:200],
    })
    return checks


def check_mcp(*, use_cli: bool, state_file: Path) -> dict[str, Any]:
    """Состояние MCP-сервера: рукопожатие через CLI либо запись в файле."""
    if use_cli and claude_cli():
        try:
            res = subprocess.run([claude_cli(), "mcp", "get", SERVER_NAME],
                                 capture_output=True, text=True, timeout=180,
                                 encoding="utf-8", errors="replace")
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "detail": f"{type(exc).__name__}: {exc}"}
        text = (res.stdout or "") + (res.stderr or "")
        connected = "Connected" in text or "√" in text
        return {"ok": connected, "detail": text.strip()[:400], "via": "cli"}
    data = load_json(state_file)
    entry = (data.get("mcpServers") or {}).get(SERVER_NAME)
    return {"ok": bool(entry), "via": "file",
            "detail": json.dumps(entry, ensure_ascii=False)[:400] if entry else "записи нет"}


def code_version() -> str:
    """Версия RAGRAF из исходников — без импорта пакета.

    Импорт поднял бы зависимости (`fastembed`, `sqlite_vec`) ради одной строки;
    регулярное выражение читает ту же строку без запуска кода.
    """
    init = REPO / "src" / "ragraf" / "__init__.py"
    if not init.is_file():
        return ""
    found = re.search(r'__version__\s*=\s*"([^"]+)"', init.read_text(encoding="utf-8"))
    return found.group(1) if found else ""


def _set_manifest_version(path: Path, version: str) -> bool:
    """Вписать версию в манифест (plugin.json или marketplace.json)."""
    data = load_json(path)
    changed = False
    if data.get("version") != version:
        data["version"] = version
        changed = True
    for entry in data.get("plugins") or []:
        if isinstance(entry, dict) and entry.get("name") == SERVER_NAME \
                and entry.get("version") != version:
            entry["version"] = version
            changed = True
    if changed:
        write_json(path, data)
    return changed


def sync_plugin(*, dry_run: bool = False) -> dict:
    """Собрать плагин из единственных источников: навык и версия — из проекта.

    Почему сборка, а не второй экземпляр. Навык в плагине — копия того же файла,
    что лежит в `docs/skills`; расхождение между ними ловит `check_skill_variants`
    (вариант `claude-plugin`). Версия в двух манифестах должна совпадать с
    `src/ragraf/__init__.py`, иначе каталог покажет одну версию, а код — другую.
    """
    version = code_version()
    report: dict[str, Any] = {"version": version, "copied": [], "manifests": [],
                              "dry_run": dry_run}
    source = SKILLS_SOURCE / "ragraf-core"
    target = PLUGIN_SKILLS / "ragraf-core"
    for relative in PLUGIN_SKILL_FILES:
        src, dst = source / relative, target / relative
        if not src.is_file():
            report["copied"].append(f"нет источника: {src}")
            continue
        if dst.is_file() and dst.read_bytes() == src.read_bytes():
            continue
        report["copied"].append(str(dst.relative_to(REPO)))
        if not dry_run:
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(src.read_bytes())
    # Объявление варианта в плагин не идёт: это машинный файл проекта.
    stale = target / "VARIANTS.json"
    if stale.exists() and not dry_run:
        stale.unlink()
    for name, source in PLUGIN_LEGAL_FILES.items():
        destination = PLUGIN_DIR / name
        if not source.is_file():
            report["copied"].append(f"нет источника: {source}")
            continue
        if destination.is_file() and destination.read_bytes() == source.read_bytes():
            continue
        report["copied"].append(str(destination.relative_to(REPO)))
        if not dry_run:
            destination.write_bytes(source.read_bytes())
    for path in (PLUGIN_DIR / ".claude-plugin" / "plugin.json", MARKETPLACE_FILE):
        if not path.is_file():
            report["manifests"].append(f"нет манифеста: {path}")
            continue
        if not dry_run and version and _set_manifest_version(path, version):
            report["manifests"].append(str(path.relative_to(REPO)))
    return report


def plugin_info() -> dict:
    """Состояние плагина: манифесты разбираются, версии сходятся, навык тот же."""
    info: dict[str, Any] = {"plugin": str(PLUGIN_DIR), "ok": True, "why": []}
    try:
        manifest = load_json(PLUGIN_DIR / ".claude-plugin" / "plugin.json")
        market = load_json(MARKETPLACE_FILE)
    except Exception as exc:  # noqa: BLE001 — сломанный манифест — находка
        info["ok"] = False
        info["why"].append(f"манифест не разобрался: {type(exc).__name__}: {exc}")
        return info
    version = code_version()
    if manifest.get("name") != SERVER_NAME:
        info["ok"] = False
        info["why"].append(f"имя плагина {manifest.get('name')!r} вместо {SERVER_NAME!r}")
    if version and manifest.get("version") != version:
        info["ok"] = False
        info["why"].append(f"версия плагина {manifest.get('version')} вместо {version}")
    listed = [p for p in market.get("plugins") or [] if isinstance(p, dict)
              and p.get("name") == SERVER_NAME]
    if not listed:
        info["ok"] = False
        info["why"].append("маркетплейс не объявляет плагин ragraf")
    elif version and listed[0].get("version") != version:
        info["ok"] = False
        info["why"].append(f"версия в маркетплейсе {listed[0].get('version')} вместо {version}")
    if not (PLUGIN_SKILLS / "ragraf-core" / "SKILL.md").is_file():
        info["ok"] = False
        info["why"].append("в плагине нет SKILL.md")
    for name in PLUGIN_LEGAL_FILES:
        if not (PLUGIN_DIR / name).is_file():
            info["ok"] = False
            info["why"].append(f"в плагине нет {name}")
    if not all((PLUGIN_SKILLS / "ragraf-core" / relative).is_file()
               for relative in PLUGIN_SKILL_FILES):
        info["ok"] = False
        info["why"].append("навык в плагине неполон")
    info["version"] = manifest.get("version")
    return info


def install_plugin(*, dry_run: bool = False, remove: bool = False) -> dict:
    """Установка через маркетплейс: одна команда вместо четырёх мест.

    Почему маркетплейс, а не копирование файлов. Плагин — штатный способ поставки
    Claude Code: он сам разложит MCP-сервер, хуки, навык и команды, покажет
    состав в `claude plugin details` и обновится при `marketplace update`.
    """
    cli = claude_cli()
    report: dict[str, Any] = {"via": "cli" if cli else "нет CLI", "steps": []}
    if not cli:
        return report
    commands = ([[cli, "plugin", "uninstall", f"{SERVER_NAME}@{SERVER_NAME}"]]
                if remove else
                [[cli, "plugin", "marketplace", "add", str(REPO)],
                 [cli, "plugin", "install", f"{SERVER_NAME}@{SERVER_NAME}"]])
    for command in commands:
        if dry_run:
            report["steps"].append(" ".join(command))
            continue
        try:
            res = subprocess.run(command, capture_output=True, text=True, timeout=300,
                                 encoding="utf-8", errors="replace")
        except Exception as exc:  # noqa: BLE001 — отказ команды должен быть виден
            report["steps"].append(f"{command[2]}: ошибка {type(exc).__name__}: {exc}")
            continue
        answer = ((res.stdout or "") + (res.stderr or "")).strip()
        report["steps"].append(f"{command[2]}: код {res.returncode} — {answer[:200]}")
    return report


def check_plugin_mcp() -> dict[str, Any]:
    """Отвечает ли MCP-сервер, поставленный плагином (без входа в учётную запись)."""
    cli = claude_cli()
    if not cli:
        return {"ok": False, "detail": "CLI claude не найден"}
    try:
        res = subprocess.run([cli, "mcp", "list"], capture_output=True, text=True,
                             timeout=300, encoding="utf-8", errors="replace")
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "detail": f"{type(exc).__name__}: {exc}"}
    lines = [line for line in ((res.stdout or "") + (res.stderr or "")).splitlines()
             if "plugin:ragraf" in line]
    connected = any("Connected" in line or "√" in line for line in lines)
    return {"ok": connected, "detail": (lines[0].strip()[:300] if lines
                                          else "сервер плагина не найден в списке")}


def verify(home: Path, state_file: Path, *, use_cli: bool, cwd: str) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    settings_path = home / "settings.json"
    ok_json, detail = True, ""
    try:
        settings = load_json(settings_path) if settings_path.exists() else {}
    except Exception as exc:  # noqa: BLE001
        ok_json, detail, settings = False, f"{type(exc).__name__}: {exc}", {}
    checks.append({"name": "settings.json разбирается", "ok": ok_json,
                   "detail": detail or str(settings_path)})
    hooks = settings.get("hooks") if isinstance(settings.get("hooks"), dict) else {}
    missing = [event for event, _, _, _ in STAGES
               if not any(our_hooks(e) for e in (hooks.get(event) or []))]
    checks.append({"name": "все четыре события хуков на месте", "ok": not missing,
                   "detail": ("нет: " + ", ".join(missing)) if missing else "4/4"})
    allow = ((settings.get("permissions") or {}).get("allow") or [])
    checks.append({"name": "право mcp__ragraf__* выдано", "ok": "mcp__ragraf__*" in allow,
                   "detail": ", ".join(map(str, allow))[:200] or "пусто"})
    memory = home / "CLAUDE.md"
    text = memory.read_text(encoding="utf-8-sig") if memory.exists() else ""
    checks.append({"name": "раздел правил в CLAUDE.md на месте",
                   "ok": has_section(text), "detail": str(memory)})
    skills_ok = all((home / "skills" / name / "SKILL.md").exists() for name in SKILL_NAMES)
    checks.append({"name": "навыки развёрнуты", "ok": skills_ok,
                   "detail": ", ".join(SKILL_NAMES)})
    checks.extend(check_hooks(cwd))
    mcp = check_mcp(use_cli=use_cli, state_file=state_file)
    # Имя проверки зависит от способа: «рукопожатие» без модели бывает только
    # через CLI, который сам запускает сервер. Проверка файла рукопожатием не
    # является, и называть её так значило бы выдать запись за соединение.
    handshake = bool(mcp.get("via") == "cli")
    checks.append({"name": ("MCP-сервер отвечает на рукопожатие" if handshake
                            else "MCP-сервер записан в состояние клиента"),
                   "ok": bool(mcp.get("ok")),
                   "detail": str(mcp.get("detail") or "")[:300]})
    # Плагин — отдельный способ поставки; его проверки необязательные: ручная
    # установка без плагина — законный режим, и валить её из-за другого способа
    # значило бы требовать оба сразу.
    info = plugin_info()
    checks.append({"name": "манифесты плагина сходятся с кодом", "ok": bool(info["ok"]),
                   "optional": True,
                   "detail": "версия " + str(info.get("version") or "?") + (
                       "; " + "; ".join(info["why"]) if info["why"] else "; плагин собран")})
    plugin_mcp = check_plugin_mcp()
    checks.append({"name": "MCP-сервер плагина отвечает", "ok": bool(plugin_mcp["ok"]),
                   "optional": True, "detail": str(plugin_mcp["detail"])[:300]})
    required = [c for c in checks if not c.get("optional")]
    return {"checks": checks, "ok": all(c["ok"] for c in required), "mcp": mcp}


# ---------------------------------------------------------------------------
# Команды
# ---------------------------------------------------------------------------


def print_checks(checks: list[dict[str, Any]]) -> None:
    for item in checks:
        if item.get("optional"):
            mark = "ЕСТЬ" if item["ok"] else "НЕТ "
            suffix = " (необязательная)"
        else:
            mark = "OK  " if item["ok"] else "НЕТ "
            suffix = ""
        print(f"  {mark}{item['name']} — {item['detail']}{suffix}")


def do_plugin(args: argparse.Namespace) -> int:
    """Собрать плагин и поставить его через маркетплейс.

    Этот режим и ручная установка несовместимы по хукам: обе добавляют одни и те
    же четыре события, и они сработали бы дважды. Флаг `--replace` снимает ручную
    установку перед установкой плагина (копии файлов остаются).
    """
    if getattr(args, "replace", False):
        home = claude_home(args.home)
        state_file = Path(args.state).expanduser() if args.state else user_state_path()
        cleared = install_settings(home, dry_run=args.dry_run, remove=True)
        print(f"[claude] ручные хуки сняты: {cleared['removed']}")
        install_mcp(state_file, dry_run=args.dry_run,
                    use_cli=bool(args.cli and not args.home), remove=True)
        print("[claude] ручной MCP-сервер снят")
        install_memory(home, dry_run=args.dry_run, remove=True)
        install_skills(home, dry_run=args.dry_run, remove=True)
        print("[claude] ручные правила и навыки сняты (плагин даёт свои)")
    print(f"[claude] плагин: {PLUGIN_DIR}")
    print(f"[claude] маркетплейс: {MARKETPLACE_FILE}")
    synced = sync_plugin(dry_run=args.dry_run)
    print(f"  версия: {synced['version'] or 'не найдена'}")
    print("  навык: " + ("обновлён: " + ", ".join(synced["copied"]) if synced["copied"]
                       else "уже совпадает с docs/skills"))
    print("  манифесты: " + ("обновлены: " + ", ".join(synced["manifests"])
                           if synced["manifests"] else "без изменений"))
    report = install_plugin(dry_run=args.dry_run)
    print(f"  установка: {report['via']}")
    for step in report["steps"]:
        print(f"    {step}")
    if args.dry_run:
        print("[claude] сухой прогон: ничего не записано")
        return 0
    info = plugin_info()
    print("  сборка: " + ("сходится с кодом" if info["ok"] else "; ".join(info["why"])))
    mcp = check_plugin_mcp()
    print(f"  MCP плагина: {'Connected' if mcp['ok'] else 'НЕ отвечает'} — {mcp['detail'][:160]}")
    details = details_text()
    if details:
        print("[claude] состав плагина:")
        for line in details.splitlines()[:14]:
            print("  " + line)
    return 0 if (info["ok"] and mcp["ok"]) else 1


def details_text() -> str:
    """`claude plugin details` — состав и цена в контексте (для отчёта)."""
    cli = claude_cli()
    if not cli:
        return ""
    try:
        res = subprocess.run([cli, "plugin", "details", SERVER_NAME], capture_output=True,
                             text=True, timeout=180, encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001 — состав не обязателен для установки
        return ""
    return (res.stdout or "").strip()


def do_install(args: argparse.Namespace) -> int:
    if args.plugin:
        return do_plugin(args)
    home = claude_home(args.home)
    state_file = Path(args.state).expanduser() if args.state else (
        (home / "claude.json") if args.home else user_state_path())
    use_cli = bool(args.cli and not args.home)
    cwd = args.cwd or str(REPO)
    print(f"[claude] каталог настроек: {home}")
    print(f"[claude] состояние пользователя: {state_file}")
    print(f"[claude] интерпретатор: {interpreter()}")
    settings_report = install_settings(home, dry_run=args.dry_run)
    print(f"  хуки: добавлено {settings_report['added']}, снято {settings_report['removed']}"
          + (", файл не менялся" if settings_report.get("changed") is False else "")
          + (f", копия {settings_report['backup']}" if settings_report.get("backup") else ""))
    if settings_report.get("allow_added"):
        print(f"  права: добавлено {', '.join(settings_report['allow_added'])}")
    mcp_report = install_mcp(state_file, dry_run=args.dry_run, use_cli=use_cli)
    print(f"  MCP: {mcp_report.get('via')} — {mcp_report.get('stdout') or 'записано'}")
    if mcp_report.get("returncode") not in (None, 0):
        print(f"  MCP: ошибка {mcp_report.get('returncode')}: {mcp_report.get('stderr')}")
    memory_report = install_memory(home, dry_run=args.dry_run)
    print(f"  память: {memory_report['action']} — {memory_report['path']}")
    if args.skills:
        skills_report = install_skills(home, dry_run=args.dry_run)
        for name, item in skills_report["skills"].items():
            print(f"  навык {name}: {item['action']}")
    else:
        print("  навыки: пропущены (--no-skills)")
    if args.dry_run:
        print("[claude] сухой прогон: ничего не записано")
        return 0
    result = verify(home, state_file, use_cli=use_cli, cwd=cwd)
    print("[claude] проверка:")
    print_checks(result["checks"])
    return 0 if result["ok"] else 1


def do_check(args: argparse.Namespace) -> int:
    home = claude_home(args.home)
    state_file = Path(args.state).expanduser() if args.state else (
        (home / "claude.json") if args.home else user_state_path())
    use_cli = bool(args.cli and not args.home)
    result = verify(home, state_file, use_cli=use_cli, cwd=args.cwd or str(REPO))
    print(f"[claude] версия клиента: {claude_version() or 'не найдена в PATH'}")
    print_checks(result["checks"])
    print(f"[claude] итог: {'ГОТОВО' if result['ok'] else 'НЕ ГОТОВО'}")
    return 0 if result["ok"] else 1


def do_remove(args: argparse.Namespace) -> int:
    home = claude_home(args.home)
    state_file = Path(args.state).expanduser() if args.state else (
        (home / "claude.json") if args.home else user_state_path())
    use_cli = bool(args.cli and not args.home)
    print(f"[claude] удаление из {home}")
    settings_report = install_settings(home, dry_run=args.dry_run, remove=True)
    print(f"  хуки: снято {settings_report['removed']}"
          + (", файл не менялся" if settings_report.get("changed") is False else "")
          + (f", копия {settings_report['backup']}" if settings_report.get("backup") else ""))
    mcp_report = install_mcp(state_file, dry_run=args.dry_run, use_cli=use_cli, remove=True)
    print(f"  MCP: {mcp_report.get('via')} — {mcp_report.get('stdout') or 'снято'}")
    memory_report = install_memory(home, dry_run=args.dry_run, remove=True)
    print(f"  память: {memory_report['action']}")
    skills_report = install_skills(home, dry_run=args.dry_run, remove=True)
    for name, item in skills_report["skills"].items():
        print(f"  навык {name}: {item['action']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="install_claude_code",
        description="Интеграция RAGRAF в Claude Code: MCP, хуки, правила, навыки")
    parser.add_argument("--check", action="store_true", help="только состояние")
    parser.add_argument("--remove", action="store_true", help="убрать интеграцию")
    parser.add_argument("--dry-run", action="store_true", help="показать, ничего не меняя")
    parser.add_argument("--home", default="", help="каталог настроек (песочница)")
    parser.add_argument("--state", default="", help="файл состояния пользователя")
    parser.add_argument("--cwd", default="", help="каталог для проверки хуков")
    parser.add_argument("--no-skills", dest="skills", action="store_false",
                        help="не разворачивать навыки")
    parser.add_argument("--plugin", action="store_true",
                        help="поставить плагином через маркетплейс (вместо ручной установки)")
    parser.add_argument("--sync-plugin", action="store_true",
                        help="только собрать плагин из docs/skills и версии кода")
    parser.add_argument("--replace", action="store_true",
                        help="с --plugin: снять ручную установку (хуки, MCP, правила, навыки)")
    parser.add_argument("--no-cli", dest="cli", action="store_false",
                        help="не звать claude, писать файл самому")
    parser.set_defaults(skills=True, cli=True)
    args = parser.parse_args(argv)
    if args.sync_plugin:
        synced = sync_plugin(dry_run=args.dry_run)
        print(f"[claude] плагин собран: версия {synced['version'] or '?'}, "
              f"скопировано {len(synced['copied'])}, манифестов обновлено {len(synced['manifests'])}")
        info = plugin_info()
        print("  " + ("сходится с кодом" if info["ok"] else "; ".join(info["why"])))
        return 0 if info["ok"] else 1
    if args.check:
        return do_check(args)
    if args.remove:
        return do_remove(args)
    return do_install(args)


if __name__ == "__main__":
    raise SystemExit(main())
