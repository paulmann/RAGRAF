"""Поддержка моста хуков в HARNESS 44.7.0: то, что ломалось, проверяется заново.

Почему набор существует
    После обновления HARNESS (44.7.0, пакеты 0.2.1-rc.5) мост хуков
    `@deepseek-ai/dsh-hooks-claude-code` перестал входить в сборку, запись
    `hooks-shared` в профиле повисла на отсутствующем пакете, и гейт RAGRAF
    молча не вызывался. Плюс два собственных дефекта: команда хука начиналась с
    кавычки (PowerShell: `ParserError`, код 1) и вывод падал `UnicodeEncodeError`
    на cp1252 — обработчик по правилу fail-open сообщал «вызов разрешён».

Что проверяется
    1. ФОРМА КОМАНДЫ — сборка без кавычек, сокращение пути, стража разбора.
    2. КОНФИГУРАЦИЯ ХУКОВ — три события, матчер, отсутствие кавычек в своде.
    3. СКРИПТ ХУКА — режим берётся из ядра (а не из своей константы) и вывод
       переживает cp1252 без потери ответа.
    4. СОСТОЯНИЕ УСТАНОВКИ — на пустом каталоге клиента отчёт честный: моста
       нет, действия названы; установка в режиме `dry_run` ничего не меняет.

Состояние гейта на время проверки подменяется копией и восстанавливается:
иначе проверка SessionStart оставляла бы в `catalog/gate_state.json` сеансы,
которых не было (измерено: десять записей `anon-*` от такой проверки).

Стиль — самостоятельный скрипт с `check`, как остальные проверки проекта.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf import gate as gate_mod  # noqa: E402
from ragraf import harness  # noqa: E402

PY = str(REPO / ".venv" / "Scripts" / "python.exe")
HOOK = str(REPO / "tools" / "harness_gate.py")
STATE = REPO / "catalog" / "gate_state.json"

PASS = 0
FAIL = 0


def check(condition: bool, title: str, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  [OK ] {title}")
    else:
        FAIL += 1
        print(f"  [FAIL] {title}" + (f"\n      {detail}" if detail else ""))


def run_hook(stage: str, payload: dict | str, extra_env: dict | None = None) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.update(extra_env or {})
    text = payload if isinstance(payload, str) else json.dumps(payload)
    return subprocess.run([PY, HOOK, stage], input=text, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=300, env=env)


def section(title: str) -> None:
    print()
    print(title)


def main() -> int:
    print("=" * 78)
    print("МОСТ ХУКОВ HARNESS: форма команд, конфигурация, вывод скрипта, состояние")
    print("=" * 78)

    section("1. Команда хука: кавычек быть не должно")
    plain = harness.safe_command([r"C:\work\proj\.venv\Scripts\python.exe",
                                  r"C:\work\proj\tools\harness_gate.py", "pre-tool"],
                                 launcher_dir=REPO / "harness" / "bin")
    check(plain["form"] == "plain", "пути без пробелов → форма plain", plain["form"])
    check('"' not in plain["command"], "в команде нет двойных кавычек", plain["command"])

    spaced = harness.safe_command([r"C:\Program Files\PowerShell\7\pwsh.exe",
                                   r"C:\work\tools\session-hook.ps1"],
                                  launcher_dir=REPO / "harness" / "bin")
    check(spaced["form"] in ("short", "launcher"),
          "путь с пробелом сокращается (8.3) либо обходится пусковым файлом", spaced["form"])
    check(spaced["command"] != "" and '"' not in spaced["command"],
          "команда с пробелом тоже без кавычек", spaced["command"])

    old = ('"C:\\work\\proj\\.venv\\Scripts\\python.exe" '
           '"C:\\work\\proj\\tools\\harness_gate.py" session-start')
    new = ('C:\\work\\proj\\.venv\\Scripts\\python.exe '
           'C:\\work\\proj\\tools\\harness_gate.py session-start')
    old_result = harness.parse_only(old)
    new_result = harness.parse_only(new)
    check(not old_result["ok"],
          "стража ловит прежнюю форму с кавычками (ParserError)", str(old_result)[:160])
    check(new_result["ok"], "собранная форма разбирается PowerShell", str(new_result)[:160])

    section("2. Конфигурация хуков и свод")
    sys.path.insert(0, str(REPO / "tools"))
    import install_harness_gate as installer  # noqa: E402

    config = installer.hooks_config()
    events = sorted((config.get("hooks") or {}).keys())
    check(events == ["PreToolUse", "SessionStart", "Stop"],
          "три события протокола", str(events))
    commands = [hook["command"] for groups in config["hooks"].values()
                for group in groups for hook in group["hooks"]]
    check(all('"' not in command for command in commands),
          "ни одной кавычки в командах конфигурации", str(commands)[:200])
    pretool = config["hooks"]["PreToolUse"][0]
    check(pretool.get("matcher") == installer.PRETOOL_MATCHER,
          "у PreToolUse есть матчер инструментов", str(pretool.get("matcher"))[:80])

    merged = REPO / "harness" / "all-hooks.json"
    if merged.is_file():
        data = json.loads(merged.read_text(encoding="utf-8"))
        merged_commands = [hook.get("command", "") for groups in data["hooks"].values()
                           for group in groups for hook in group["hooks"]]
        quoted = [c for c in merged_commands if '"' in c]
        check(not quoted, f"в своде нет кавычек ({len(merged_commands)} команд)", str(quoted)[:200])
    else:
        check(False, "свод harness/all-hooks.json отсутствует", str(merged))

    section("3. Скрипт хука: режим и вывод")
    proc = run_hook("selftest", "")
    info: dict = {}
    try:
        info = json.loads(proc.stdout or "{}")
    except ValueError:
        info = {}
    check(proc.returncode == 0, "selftest завершается нулём", (proc.stderr or "")[:200])
    check(info.get("mode") == gate_mod.DEFAULT_MODE,
          "режим по умолчанию берётся из ядра (warn), а не из своей константы",
          f"хук={info.get('mode')} ядро={gate_mod.DEFAULT_MODE}")
    check(sorted(info.get("modes") or []) == sorted(gate_mod.MODES),
          "перечень режимов совпадает с ядром", str(info.get("modes")))

    # cp1252 — кодировка, на которой прежний хук падал UnicodeEncodeError.
    legacy = run_hook("selftest", "", {"PYTHONIOENCODING": "cp1252"})
    check(legacy.returncode == 0, "selftest переживает PYTHONIOENCODING=cp1252",
          (legacy.stderr or "")[:200])
    legacy_info = json.loads(legacy.stdout or "{}") or {}
    check(legacy_info.get("mode") == gate_mod.DEFAULT_MODE,
          "под cp1252 ответ всё равно собран (JSON разобран)", str(legacy.stdout)[:160])

    backup = None
    if STATE.is_file():
        backup = STATE.with_name(STATE.name + f".test-bak-{os.getpid()}")
        shutil.copy2(STATE, backup)
    try:
        started = run_hook("session-start", {"hook_event_name": "SessionStart",
                                            "session_id": "test-bridge-utf8",
                                            "cwd": ""},
                           {"PYTHONIOENCODING": "cp1252"})
        check(started.returncode == 0,
              "session-start в cp1252 не падает (прежний дефект — код 0 без ответа)",
              (started.stderr or "")[:200])
        payload = json.loads(started.stdout or "{}")
        hook_out = (payload.get("hookSpecificOutput") or {})
        check(hook_out.get("hookEventName") == "SessionStart",
              "session-start отдаёт hookEventName=SessionStart", str(payload)[:160])
        check("RAGRAF" in str(hook_out.get("additionalContext") or ""),
              "в ответе есть текст гейта (русский текст не потерян)", str(payload)[:160])
    finally:
        if backup and backup.is_file():
            shutil.copy2(backup, STATE)
            backup.unlink()

    section("4. Состояние установки: честный отчёт и безопасный dry-run")
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp) / "dshhome"
        profile = home / "profiles" / "desktop"
        profile.mkdir(parents=True)
        (profile / "package.json").write_text('{"name": "dsh-profile-desktop"}',
                                              encoding="utf-8")
        (profile / "cordis.patch.yml").write_text("[]\n", encoding="utf-8")
        empty = harness.state("desktop", home)
        check(empty["bridge"]["installed"] == "",
              "на пустом профиле мост не найден (а не выдуман)", str(empty["bridge"]))
        check(len(empty["actions"]) >= 1, "действия названы", str(empty["actions"])[:200])
        check(empty["entry"]["present"] is False, "записи моста в патче нет", str(empty["entry"]))

        modules = profile / "node_modules" / ".modules.yaml"
        modules.parent.mkdir(parents=True)
        modules.write_text('{"storeDir": "C:\\\\store\\\\v11", "packageManager": "pnpm@11.7.0"}',
                           encoding="utf-8")
        store = harness.profile_store_dir(profile)
        check(store == r"C:\store\v11", "хранилище pnpm читается из профиля", store)

        dry = harness.install_bridge("desktop", home, dry_run=True)
        command = " ".join(dry.get("command") or [])
        check(dry.get("dry_run") is True and "add" in command,
              "dry-run показывает команду установки", command[:200])
        check("dsh-hook-protocol" in command and "dsh-hooks-claude-code" in command,
              "в команде и мост, и протокол хуков", command[:200])
        check("--config.store-dir" in command,
              "хранилище профиля передаётся pnpm (иначе отказ на чужой версии)", command[:240])
        check(not (profile / "package.json.bak").exists(),
              "dry-run не создаёт резервных копий (профиль не тронут)")

    print()
    print("=" * 78)
    print(f"ИТОГ: пройдено {PASS}, провалено {FAIL}")
    print("=" * 78)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
